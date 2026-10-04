"""
FastAPI routes backed by the database.

Drop-in replacement for routes.py. Switch by changing the import in main.py:
    from app.api.routes_db import router     # ← database-backed
    # from app.api.routes import router      # ← original in-memory

The pipeline code (csv_loader, normalizer, entity_resolver, conflict_detector)
is untouched — it still produces lists of dataclasses. This module calls
the repository to persist them after the pipeline runs, and loads them back
when a report endpoint is hit.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db import repository as repo
from app.ingest.csv_loader import load_csv
from app.ingest.pdf_loader import load_pdf
from app.ingest.normalizer import normalize_batch
from app.reconcile.entity_resolver import resolve_persons, resolve_facilities
from app.reconcile.conflict_detector import detect_conflicts
from app.domain.healthcare import HealthcareDomainPlugin
from app.models.entities import FieldStatus, ConflictSeverity, UrgencyTier

router = APIRouter()

# Default org for the hackathon demo. In production this comes from auth.
DEFAULT_ORG = "harborview"

domain_plugin = HealthcareDomainPlugin()


def _serialize(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(i) for i in obj]
    if isinstance(obj, (FieldStatus, ConflictSeverity, UrgencyTier)):
        return obj.value
    if hasattr(obj, "__dataclass_fields__"):
        return _serialize(asdict(obj))
    return obj


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

@router.post("/upload")
async def upload_file(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    filename = file.filename or "unknown"
    org_id = DEFAULT_ORG

    # Deduplicate by file hash
    if repo.file_already_ingested(db, org_id, content):
        return {"status": "skipped", "reason": "This exact file was already uploaded."}

    # Ingest
    if filename.lower().endswith(".pdf"):
        raw_records = load_pdf(content, filename)
        source_type = "schedule"
    elif filename.lower().endswith(".csv"):
        source_type, raw_records = load_csv(content, filename)
    else:
        raise HTTPException(400, f"Unsupported file type: {filename}")

    if not raw_records:
        raise HTTPException(400, f"No records could be read from {filename}.")

    # Persist Layers 1-2
    log_id = repo.write_ingestion(db, org_id, filename, source_type, len(raw_records), content)
    repo.write_raw_records(db, org_id, raw_records, log_id)
    normalized = normalize_batch(raw_records)
    repo.write_normalized_records(db, org_id, normalized)

    return {
        "status": "ok",
        "filename": filename,
        "detected_source": source_type,
        "records_ingested": len(raw_records),
        "sample_normalized": _serialize(normalized[:3]) if normalized else [],
    }


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

@router.post("/reconcile")
async def run_reconciliation(db: Session = Depends(get_db)):
    org_id = DEFAULT_ORG

    # Load Layers 1-2 from the database
    all_raw = repo.load_raw_records(db, org_id)
    all_normalized = repo.load_normalized_records(db, org_id)

    if not all_normalized:
        raise HTTPException(400, "No data ingested yet. Upload files first.")

    # Run the pipeline (pure functions, no DB awareness)
    persons = resolve_persons(all_normalized)
    facilities = resolve_facilities(all_normalized)
    conflicts = detect_conflicts(persons, all_normalized)

    # Persist Layers 3-4
    run_id = repo.write_reconciliation(db, org_id, persons, facilities, conflicts)

    return {
        "status": "ok",
        "run_id": run_id,
        "persons_resolved": len(persons),
        "facilities_resolved": len(facilities),
        "conflicts_found": len(conflicts),
        "persons": _serialize(persons),
        "conflicts": _serialize(conflicts),
    }


# ---------------------------------------------------------------------------
# Reports (read-only against the latest reconciliation run)
# ---------------------------------------------------------------------------

def _load_context(db: Session, org_id: str = DEFAULT_ORG):
    """Load the full resolved context from the latest run."""
    persons = repo.load_persons(db, org_id)
    if not persons:
        raise HTTPException(400, "Run /reconcile first.")
    facilities = repo.load_facilities(db, org_id)
    conflicts = repo.load_conflicts(db, org_id)
    normalized = repo.load_normalized_records(db, org_id)
    return persons, facilities, conflicts, normalized


@router.get("/reports/capacity")
async def capacity_report(db: Session = Depends(get_db)):
    persons, facilities, conflicts, normalized = _load_context(db)
    schedule_recs = [r for r in normalized if r.source == "schedule"]
    return _serialize(domain_plugin.generate_capacity_report(persons, schedule_recs, facilities))


@router.get("/reports/compliance")
async def compliance_report(db: Session = Depends(get_db)):
    persons, facilities, conflicts, normalized = _load_context(db)
    return _serialize(domain_plugin.generate_compliance_report(persons, normalized, conflicts))


@router.get("/reports/expirations")
async def expiration_report(db: Session = Depends(get_db)):
    persons, _, _, normalized = _load_context(db)
    return _serialize(domain_plugin.generate_expiration_report(persons, normalized))


# ---------------------------------------------------------------------------
# Audit / debug
# ---------------------------------------------------------------------------

@router.get("/status")
async def status(db: Session = Depends(get_db)):
    org_id = DEFAULT_ORG
    run_id = repo.load_latest_run(db, org_id)
    ingestion_log = repo.load_ingestion_log(db, org_id)
    persons = repo.load_persons(db, org_id, run_id) if run_id else []
    conflicts = repo.load_conflicts(db, org_id, run_id) if run_id else []
    return {
        "org_id": org_id,
        "latest_run_id": run_id,
        "persons_resolved": len(persons),
        "conflicts": len(conflicts),
        "ingestion_log": ingestion_log,
        "domain": domain_plugin.get_name(),
    }


@router.get("/audit/persons")
async def audit_persons(db: Session = Depends(get_db)):
    persons = repo.load_persons(db, DEFAULT_ORG)
    return _serialize(persons)


@router.post("/reset")
async def reset_pipeline(db: Session = Depends(get_db)):
    repo.reset_org(db, DEFAULT_ORG)
    return {"status": "reset"}
