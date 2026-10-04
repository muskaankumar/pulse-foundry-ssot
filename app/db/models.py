"""
Database models for the Single Source of Truth system.

Four layers, each its own table group:

    Layer 1  raw_records           What the file said, byte-for-byte
    Layer 2  normalized_records    Cleaned, per-source, FK back to Layer 1
    Layer 3  persons, facilities   Resolved entities, linked to Layers 1-2 via join tables
    Layer 4  conflicts             Derived from Layer 3, recomputed on each reconciliation run

Two bookkeeping tables tie everything together:
    ingestion_logs    one row per file uploaded
    reconciliation_runs    one row per time the pipeline ran

Every table carries an org_id so the same database can hold multiple
organizations without mixing their data. At Harborview's scale this is
overkill, but it costs one column per table and means the schema doesn't
change when Pulse Foundry onboards a second client.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON, Boolean, Column, DateTime, Enum, Float, ForeignKey, Index, Integer,
    String, Text, UniqueConstraint, create_engine,
)
from sqlalchemy.orm import DeclarativeBase, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Bookkeeping
# ---------------------------------------------------------------------------

class IngestionLog(Base):
    """One row per file upload. Lets you answer 'which files have been loaded
    for this org' and 'when was the last upload' without scanning raw_records."""

    __tablename__ = "ingestion_logs"

    id = Column(String(36), primary_key=True, default=_uuid)
    org_id = Column(String(64), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    source_type = Column(String(32), nullable=False)        # hr | payroll | licenses | schedule
    record_count = Column(Integer, nullable=False, default=0)
    file_hash = Column(String(64), nullable=True)           # SHA-256 of the file content — dedup guard
    ingested_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("org_id", "file_hash", name="uq_org_file"),
    )


class ReconciliationRun(Base):
    """One row per pipeline run. Every person and conflict produced by that run
    points back here, so you can compare two runs or roll back to an earlier one."""

    __tablename__ = "reconciliation_runs"

    id = Column(String(36), primary_key=True, default=_uuid)
    org_id = Column(String(64), nullable=False, index=True)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    raw_count = Column(Integer, default=0)
    normalized_count = Column(Integer, default=0)
    person_count = Column(Integer, default=0)
    facility_count = Column(Integer, default=0)
    conflict_count = Column(Integer, default=0)
    status = Column(String(16), default="running")          # running | completed | failed

    persons = relationship("Person", back_populates="run", cascade="all, delete-orphan")
    facilities = relationship("Facility", back_populates="run", cascade="all, delete-orphan")
    conflicts = relationship("Conflict", back_populates="run", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# Layer 1 — Raw records (append-only, never mutated)
# ---------------------------------------------------------------------------

class RawRecord(Base):
    """Exactly what the row said in the file. The `data` JSON holds both
    the original values (`raw`) and the column mapping used (`column_mapping`).
    This row is never updated — if you re-ingest a file, you get new rows."""

    __tablename__ = "raw_records"

    id = Column(String(36), primary_key=True, default=_uuid)
    org_id = Column(String(64), nullable=False, index=True)
    source = Column(String(32), nullable=False)
    source_file = Column(String(255), nullable=False)
    data = Column(JSON, nullable=False)                      # {"raw": {...}, "mapped": {...}, "column_mapping": {...}}
    ingested_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    ingestion_log_id = Column(String(36), ForeignKey("ingestion_logs.id"), nullable=True)

    normalized = relationship("NormalizedRecord", back_populates="raw_record", uselist=False)

    __table_args__ = (
        Index("ix_raw_org_source", "org_id", "source"),
    )


# ---------------------------------------------------------------------------
# Layer 2 — Normalized records (one per raw, FK back to Layer 1)
# ---------------------------------------------------------------------------

class NormalizedRecord(Base):
    """Cleaned version of a raw record. Still per-source — not merged yet.
    The confidence dict records how sure the normalizer was about each field
    transformation. Keeping this separate from raw means you can re-normalize
    without re-ingesting (e.g. if the alias table changes)."""

    __tablename__ = "normalized_records"

    id = Column(String(36), primary_key=True, default=_uuid)
    org_id = Column(String(64), nullable=False, index=True)
    raw_record_id = Column(String(36), ForeignKey("raw_records.id"), nullable=False)
    source = Column(String(32), nullable=False)
    data = Column(JSON, nullable=False)                      # {"canonical_name": "...", "facility": "...", ...}
    confidence = Column(JSON, nullable=False, default=dict)  # {"name": 0.95, "facility": 1.0, ...}
    normalization_log = Column(JSON, nullable=False, default=list)

    raw_record = relationship("RawRecord", back_populates="normalized")

    __table_args__ = (
        Index("ix_norm_org_source", "org_id", "source"),
    )


# ---------------------------------------------------------------------------
# Layer 3 — Resolved entities
# ---------------------------------------------------------------------------

class Person(Base):
    """The unified 'single source of truth' record for one person.

    Linked to its source records through two join tables:
      person_source_records  — the HR/payroll/license raw records that were matched
      person_schedule_records — the schedule rows linked by name after matching

    This is the shape every downstream feature reads from. The field_statuses
    JSON says, per field, whether sources agreed — this is what makes
    conflicts a first-class fact rather than a log entry you have to search."""

    __tablename__ = "persons"

    id = Column(String(36), primary_key=True, default=_uuid)
    org_id = Column(String(64), nullable=False, index=True)
    run_id = Column(String(36), ForeignKey("reconciliation_runs.id"), nullable=False)
    canonical_name = Column(String(255), nullable=False)
    first_name = Column(String(128), default="")
    last_name = Column(String(128), default="")
    employee_id = Column(String(32), default="")
    job_title = Column(String(128), default="")
    facility = Column(String(128), default="")
    phone = Column(String(32), default="")
    license_number = Column(String(64), default="")
    license_type = Column(String(128), default="")
    license_expiration = Column(String(16), default="")
    hire_date = Column(String(16), default="")
    match_confidence = Column(Float, default=0.0)
    sources = Column(JSON, nullable=False, default=list)           # ["hr", "payroll", "licenses"]
    field_statuses = Column(JSON, nullable=False, default=dict)    # {"license_number": "MATCH", ...}
    resolution_log = Column(JSON, nullable=False, default=list)
    match_evidence = Column(JSON, nullable=False, default=list)

    run = relationship("ReconciliationRun", back_populates="persons")
    source_links = relationship("PersonSourceRecord", back_populates="person", cascade="all, delete-orphan")
    schedule_links = relationship("PersonScheduleRecord", back_populates="person", cascade="all, delete-orphan")
    conflicts = relationship("Conflict", back_populates="person", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_person_org_name", "org_id", "canonical_name"),
        Index("ix_person_org_license", "org_id", "license_number"),
        Index("ix_person_org_empid", "org_id", "employee_id"),
    )


class PersonSourceRecord(Base):
    """Join table: which raw records (HR, payroll, licenses) were matched
    into this person. This replaces the in-memory `source_records: list[str]`
    with a proper FK relationship, so you can query in either direction —
    'which records built this person' or 'which person does this record belong to.'"""

    __tablename__ = "person_source_records"

    person_id = Column(String(36), ForeignKey("persons.id", ondelete="CASCADE"), primary_key=True)
    raw_record_id = Column(String(36), ForeignKey("raw_records.id"), primary_key=True)

    person = relationship("Person", back_populates="source_links")


class PersonScheduleRecord(Base):
    """Join table: which schedule rows were linked to this person by name
    after entity resolution. Separate from source_links because schedule
    rows are linked by a different mechanism (fuzzy name, not pairwise scoring)
    and have a different meaning (work assignments, not identity records)."""

    __tablename__ = "person_schedule_records"

    person_id = Column(String(36), ForeignKey("persons.id", ondelete="CASCADE"), primary_key=True)
    raw_record_id = Column(String(36), ForeignKey("raw_records.id"), primary_key=True)

    person = relationship("Person", back_populates="schedule_links")


class Facility(Base):
    __tablename__ = "facilities"

    id = Column(String(36), primary_key=True, default=_uuid)
    org_id = Column(String(64), nullable=False, index=True)
    run_id = Column(String(36), ForeignKey("reconciliation_runs.id"), nullable=False)
    canonical_name = Column(String(255), nullable=False)
    aliases = Column(JSON, nullable=False, default=list)

    run = relationship("ReconciliationRun", back_populates="facilities")


# ---------------------------------------------------------------------------
# Layer 4 — Conflicts (derived, recomputed per run)
# ---------------------------------------------------------------------------

class Conflict(Base):
    """A disagreement, absence, or risk detected during reconciliation.

    This is a derived cache — delete + re-insert every run rather than
    trying to diff. Each conflict points to the person it's about and
    carries the raw values from each source, so the UI can show
    'HR says X, licensing says Y' without re-querying Layers 1-2."""

    __tablename__ = "conflicts"

    id = Column(String(36), primary_key=True, default=_uuid)
    org_id = Column(String(64), nullable=False, index=True)
    run_id = Column(String(36), ForeignKey("reconciliation_runs.id"), nullable=False)
    person_id = Column(String(36), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False)
    field_name = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False)             # MATCH | MISMATCH | MISSING | STALE
    severity = Column(Integer, nullable=False)               # 1=CRITICAL ... 5=INFO
    severity_label = Column(String(16), nullable=False)      # "CRITICAL", "HIGH", ...
    values_by_source = Column(JSON, nullable=False, default=dict)
    description = Column(Text, nullable=False, default="")
    resolved = Column(Boolean, default=False)
    resolution_note = Column(Text, default="")

    run = relationship("ReconciliationRun", back_populates="conflicts")
    person = relationship("Person", back_populates="conflicts")

    __table_args__ = (
        Index("ix_conflict_severity", "org_id", "severity"),
        Index("ix_conflict_person", "person_id"),
    )


# ---------------------------------------------------------------------------
# Match edges (optional, for full traceability)
# ---------------------------------------------------------------------------

class MatchEdge(Base):
    """The pairwise match score between two raw records that caused them to
    be clustered into the same person. The in-memory resolver computes these
    but discards them after clustering — storing them means you can always
    explain *why* two records were merged, and re-score without re-ingesting.

    This is the table that makes a graph DB argument unnecessary at this
    scale: a relational join table of scored edges + the person join tables
    gives you the same 'walk the connections' capability Neo4j would."""

    __tablename__ = "match_edges"

    id = Column(String(36), primary_key=True, default=_uuid)
    org_id = Column(String(64), nullable=False, index=True)
    run_id = Column(String(36), ForeignKey("reconciliation_runs.id"), nullable=False)
    record_a_id = Column(String(36), ForeignKey("raw_records.id"), nullable=False)
    record_b_id = Column(String(36), ForeignKey("raw_records.id"), nullable=False)
    score = Column(Float, nullable=False)
    license_exact = Column(Float, default=0.0)
    name_fuzzy = Column(Float, default=0.0)
    facility_role = Column(Float, default=0.0)
    signals = Column(JSON, nullable=False, default=list)     # ["same license number", "identical name"]
    merged = Column(Boolean, default=True)                   # True if score >= threshold

    __table_args__ = (
        Index("ix_edge_records", "record_a_id", "record_b_id"),
    )
