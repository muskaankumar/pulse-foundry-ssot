"""
Repository — the bridge between the pipeline's in-memory dataclasses and the
database tables.

Every public function takes a SQLAlchemy Session and an org_id. The pipeline
code (entity_resolver, conflict_detector, etc.) stays untouched — it still
produces lists of dataclasses. This module handles the translation:

    dataclass → DB row   (write_* functions, called after the pipeline runs)
    DB row → dataclass   (load_* functions, called when a report endpoint needs data)

This keeps the pipeline pure (no import of sqlalchemy anywhere in app/reconcile
or app/domain) and the DB logic in one place.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import models as m
from app.models.entities import (
    Conflict as ConflictDC,
    FacilityEntity,
    NormalizedRecord as NormDC,
    PersonEntity,
    RawRecord as RawDC,
)


# ---------------------------------------------------------------------------
# Layer 1 — Raw records
# ---------------------------------------------------------------------------

def file_already_ingested(db: Session, org_id: str, content: bytes) -> bool:
    """Check by SHA-256 whether this exact file was already uploaded."""
    h = hashlib.sha256(content).hexdigest()
    return db.execute(
        select(m.IngestionLog.id).where(
            m.IngestionLog.org_id == org_id, m.IngestionLog.file_hash == h
        )
    ).first() is not None


def write_ingestion(
    db: Session,
    org_id: str,
    filename: str,
    source_type: str,
    record_count: int,
    file_content: bytes,
) -> str:
    """Log an ingestion event. Returns the log row's id."""
    log = m.IngestionLog(
        org_id=org_id,
        filename=filename,
        source_type=source_type,
        record_count=record_count,
        file_hash=hashlib.sha256(file_content).hexdigest(),
    )
    db.add(log)
    db.flush()
    return log.id


def write_raw_records(
    db: Session,
    org_id: str,
    records: list[RawDC],
    ingestion_log_id: str,
) -> None:
    """Persist Layer 1 rows. Uses the dataclass's own id as the PK so
    cross-layer references stay consistent."""
    db.add_all([
        m.RawRecord(
            id=r.id,
            org_id=org_id,
            source=r.source,
            source_file=r.source_file,
            data=r.data,
            ingested_at=_parse_ingested_at(r.ingested_at),
            ingestion_log_id=ingestion_log_id,
        )
        for r in records
    ])
    db.flush()


# ---------------------------------------------------------------------------
# Layer 2 — Normalized records
# ---------------------------------------------------------------------------

def _parse_ingested_at(value) -> datetime:
    """RawRecord.ingested_at is stored as an ISO string on the dataclass
    (see app/models/entities.py), but the DB column is a real DateTime —
    SQLite rejects a plain string. Parse it, with a safe fallback."""
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return datetime.utcnow()


def write_normalized_records(
    db: Session,
    org_id: str,
    records: list[NormDC],
) -> None:
    db.add_all([
        m.NormalizedRecord(
            id=r.id,
            org_id=org_id,
            raw_record_id=r.raw_record_id,
            source=r.source,
            data=r.data,
            confidence=r.confidence,
            normalization_log=r.normalization_log,
        )
        for r in records
    ])
    db.flush()


# ---------------------------------------------------------------------------
# Layers 3-4 — Reconciliation output (persons, facilities, conflicts, edges)
# ---------------------------------------------------------------------------

def write_reconciliation(
    db: Session,
    org_id: str,
    persons: list[PersonEntity],
    facilities: list[FacilityEntity],
    conflicts: list[ConflictDC],
    match_edges: list[dict] | None = None,
) -> str:
    """Write one reconciliation run's output atomically.

    - Creates a ReconciliationRun row
    - Writes all persons (with their source/schedule join rows)
    - Writes all facilities
    - Writes all conflicts
    - Optionally writes match edges (the pairwise scores)
    - Marks the run as completed

    Returns the run id.
    """
    run = m.ReconciliationRun(
        org_id=org_id,
        raw_count=0,
        normalized_count=0,
        person_count=len(persons),
        facility_count=len(facilities),
        conflict_count=len(conflicts),
        status="running",
    )
    db.add(run)
    db.flush()

    # Persons + join tables
    for p in persons:
        person = m.Person(
            id=p.id,
            org_id=org_id,
            run_id=run.id,
            canonical_name=p.canonical_name,
            first_name=p.first_name,
            last_name=p.last_name,
            employee_id=p.employee_id,
            job_title=p.job_title,
            facility=p.facility,
            phone=p.phone,
            license_number=p.license_number,
            license_type=p.license_type,
            license_expiration=p.license_expiration,
            hire_date=p.hire_date,
            match_confidence=p.match_confidence,
            sources=p.sources,
            field_statuses={k: v.value if hasattr(v, "value") else str(v)
                            for k, v in p.field_statuses.items()},
            resolution_log=p.resolution_log,
            match_evidence=p.match_evidence,
        )
        db.add(person)
        db.flush()

        for rid in p.source_records:
            db.add(m.PersonSourceRecord(person_id=person.id, raw_record_id=rid))
        for rid in p.schedule_records:
            db.add(m.PersonScheduleRecord(person_id=person.id, raw_record_id=rid))

    # Facilities
    for f in facilities:
        db.add(m.Facility(
            id=f.id, org_id=org_id, run_id=run.id,
            canonical_name=f.canonical_name, aliases=f.aliases,
        ))

    # Conflicts
    for c in conflicts:
        db.add(m.Conflict(
            id=c.id, org_id=org_id, run_id=run.id, person_id=c.person_id,
            field_name=c.field_name,
            status=c.status.value if hasattr(c.status, "value") else str(c.status),
            severity=c.severity.value if hasattr(c.severity, "value") else int(c.severity),
            severity_label=c.severity.name if hasattr(c.severity, "name") else str(c.severity),
            values_by_source=c.values_by_source,
            description=c.description,
            resolved=c.resolved,
            resolution_note=c.resolution_note,
        ))

    # Match edges (optional — the pairwise scores the resolver computed)
    for edge in (match_edges or []):
        db.add(m.MatchEdge(
            org_id=org_id, run_id=run.id,
            record_a_id=edge["record_a_id"],
            record_b_id=edge["record_b_id"],
            score=edge["score"],
            license_exact=edge.get("license_exact", 0),
            name_fuzzy=edge.get("name_fuzzy", 0),
            facility_role=edge.get("facility_role", 0),
            signals=edge.get("signals", []),
            merged=edge.get("merged", True),
        ))

    run.completed_at = datetime.utcnow()
    run.status = "completed"
    db.flush()
    return run.id


# ---------------------------------------------------------------------------
# Reading back — DB rows → pipeline dataclasses
# ---------------------------------------------------------------------------

def load_raw_records(db: Session, org_id: str) -> list[RawDC]:
    rows = db.execute(
        select(m.RawRecord).where(m.RawRecord.org_id == org_id)
        .order_by(m.RawRecord.ingested_at)
    ).scalars().all()
    return [
        RawDC(id=r.id, source=r.source, source_file=r.source_file,
              data=r.data, ingested_at=r.ingested_at)
        for r in rows
    ]


def load_normalized_records(db: Session, org_id: str) -> list[NormDC]:
    rows = db.execute(
        select(m.NormalizedRecord).where(m.NormalizedRecord.org_id == org_id)
    ).scalars().all()
    return [
        NormDC(id=r.id, raw_record_id=r.raw_record_id, source=r.source,
               data=r.data, confidence=r.confidence,
               normalization_log=r.normalization_log)
        for r in rows
    ]


def load_latest_run(db: Session, org_id: str) -> str | None:
    """Return the most recent completed reconciliation run id, or None."""
    row = db.execute(
        select(m.ReconciliationRun.id)
        .where(m.ReconciliationRun.org_id == org_id,
               m.ReconciliationRun.status == "completed")
        .order_by(m.ReconciliationRun.completed_at.desc())
        .limit(1)
    ).scalar()
    return row


def load_persons(db: Session, org_id: str, run_id: str | None = None) -> list[PersonEntity]:
    """Load persons from a specific run (or the latest).
    Reconstructs the dataclass including source_records and schedule_records
    from the join tables."""
    from app.models.entities import FieldStatus

    if run_id is None:
        run_id = load_latest_run(db, org_id)
    if not run_id:
        return []

    rows = db.execute(
        select(m.Person).where(m.Person.run_id == run_id)
    ).scalars().all()

    persons = []
    for r in rows:
        source_ids = [
            link.raw_record_id for link in
            db.execute(select(m.PersonSourceRecord)
                       .where(m.PersonSourceRecord.person_id == r.id)).scalars().all()
        ]
        schedule_ids = [
            link.raw_record_id for link in
            db.execute(select(m.PersonScheduleRecord)
                       .where(m.PersonScheduleRecord.person_id == r.id)).scalars().all()
        ]

        p = PersonEntity(
            id=r.id, canonical_name=r.canonical_name,
            first_name=r.first_name, last_name=r.last_name,
            employee_id=r.employee_id, job_title=r.job_title,
            facility=r.facility, phone=r.phone,
            license_number=r.license_number, license_type=r.license_type,
            license_expiration=r.license_expiration, hire_date=r.hire_date,
            match_confidence=r.match_confidence,
        )
        p.source_records = source_ids
        p.schedule_records = schedule_ids
        p.sources = r.sources or []
        p.match_evidence = r.match_evidence or []
        p.resolution_log = r.resolution_log or []
        # Restore FieldStatus enums from stored strings
        for k, v in (r.field_statuses or {}).items():
            try:
                p.field_statuses[k] = FieldStatus(v)
            except (ValueError, KeyError):
                pass
        persons.append(p)

    return persons


def load_facilities(db: Session, org_id: str, run_id: str | None = None) -> list[FacilityEntity]:
    if run_id is None:
        run_id = load_latest_run(db, org_id)
    if not run_id:
        return []
    rows = db.execute(
        select(m.Facility).where(m.Facility.run_id == run_id)
    ).scalars().all()
    return [FacilityEntity(id=r.id, canonical_name=r.canonical_name, aliases=r.aliases or [])
            for r in rows]


def load_conflicts(db: Session, org_id: str, run_id: str | None = None) -> list[ConflictDC]:
    from app.models.entities import ConflictSeverity, FieldStatus

    if run_id is None:
        run_id = load_latest_run(db, org_id)
    if not run_id:
        return []
    rows = db.execute(
        select(m.Conflict).where(m.Conflict.run_id == run_id)
        .order_by(m.Conflict.severity)
    ).scalars().all()
    out = []
    for r in rows:
        c = ConflictDC(
            id=r.id, person_id=r.person_id, field_name=r.field_name,
            description=r.description, resolved=r.resolved,
            resolution_note=r.resolution_note or "",
            values_by_source=r.values_by_source or {},
        )
        try:
            c.status = FieldStatus(r.status)
        except (ValueError, KeyError):
            pass
        try:
            c.severity = ConflictSeverity(r.severity)
        except (ValueError, KeyError):
            pass
        out.append(c)
    return out


def load_ingestion_log(db: Session, org_id: str) -> list[dict]:
    rows = db.execute(
        select(m.IngestionLog).where(m.IngestionLog.org_id == org_id)
        .order_by(m.IngestionLog.ingested_at)
    ).scalars().all()
    return [{"filename": r.filename, "source_type": r.source_type,
             "record_count": r.record_count, "ingested_at": str(r.ingested_at)}
            for r in rows]


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

def reset_org(db: Session, org_id: str) -> None:
    """Delete all data for an organization. Cascades handle the join tables."""
    for table in (m.Conflict, m.PersonScheduleRecord, m.PersonSourceRecord,
                  m.MatchEdge, m.Person, m.Facility, m.ReconciliationRun,
                  m.NormalizedRecord, m.RawRecord, m.IngestionLog):
        if hasattr(table, "org_id"):
            db.execute(delete(table).where(table.org_id == org_id))
        elif hasattr(table, "person_id"):
            # Join tables without org_id — cascaded by Person FK
            pass
    db.flush()
