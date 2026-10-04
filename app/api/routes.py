"""
FastAPI routes for the Single Source of Truth system.
Handles file upload, pipeline execution, and report generation.
"""

from __future__ import annotations
import json
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse

from app.ingest.csv_loader import load_csv
from app.ingest.pdf_loader import load_pdf
from app.ingest.normalizer import normalize_batch
from app.reconcile.entity_resolver import resolve_persons, resolve_facilities
from app.reconcile.conflict_detector import detect_conflicts
from app.domain.healthcare import HealthcareDomainPlugin
from app.models.entities import (
    RawRecord, NormalizedRecord, PersonEntity, FacilityEntity, Conflict,
    FieldStatus, ConflictSeverity, UrgencyTier,
)

router = APIRouter()

# ---------------------------------------------------------------------------
# In-memory state (fine for demo / hackathon)
# ---------------------------------------------------------------------------

class PipelineState:
    """Holds all ingested and reconciled data in memory."""
    def __init__(self):
        self.raw_records: list[RawRecord] = []
        self.normalized_records: list[NormalizedRecord] = []
        self.persons: list[PersonEntity] = []
        self.facilities: list[FacilityEntity] = []
        self.conflicts: list[Conflict] = []
        self.ingestion_log: list[str] = []
        self.domain_plugin = HealthcareDomainPlugin()

    def clear(self):
        self.__init__()


state = PipelineState()


def _serialize(obj: Any) -> Any:
    """Make dataclass / enum objects JSON-serializable."""
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
# Upload endpoints
# ---------------------------------------------------------------------------

@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """
    Upload a CSV or PDF file. The system auto-detects the source type
    and runs ingestion + normalization.
    """
    content = await file.read()
    filename = file.filename or "unknown"

    if filename.lower().endswith(".pdf"):
        raw_records = load_pdf(content, filename)
        source_type = "schedule"
        state.ingestion_log.append(
            f"Ingested PDF '{filename}': {len(raw_records)} schedule entries"
        )
    elif filename.lower().endswith(".csv"):
        source_type, raw_records = load_csv(content, filename)
        state.ingestion_log.append(
            f"Ingested CSV '{filename}' as '{source_type}': {len(raw_records)} records"
        )
    else:
        raise HTTPException(400, f"Unsupported file type: {filename}")

    state.raw_records.extend(raw_records)
    normalized = normalize_batch(raw_records)
    state.normalized_records.extend(normalized)

    return {
        "status": "ok",
        "filename": filename,
        "detected_source": source_type,
        "records_ingested": len(raw_records),
        "sample_normalized": _serialize(normalized[:3]) if normalized else [],
    }


@router.post("/reset")
async def reset_pipeline():
    """Clear all ingested data and start fresh."""
    state.clear()
    return {"status": "reset"}


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

@router.post("/reconcile")
async def run_reconciliation():
    """Run entity resolution and conflict detection on all ingested data."""
    if not state.normalized_records:
        raise HTTPException(400, "No data ingested yet. Upload files first.")

    state.persons = resolve_persons(state.normalized_records)
    state.facilities = resolve_facilities(state.normalized_records)
    state.conflicts = detect_conflicts(state.persons, state.normalized_records)

    return {
        "status": "ok",
        "persons_resolved": len(state.persons),
        "facilities_resolved": len(state.facilities),
        "conflicts_found": len(state.conflicts),
        "persons": _serialize(state.persons),
        "conflicts": _serialize(state.conflicts),
    }


# ---------------------------------------------------------------------------
# Report endpoints
# ---------------------------------------------------------------------------

@router.get("/reports/capacity")
async def capacity_report():
    """Module A — Capacity Dashboard."""
    if not state.persons:
        raise HTTPException(400, "Run /reconcile first.")
    schedule_recs = [r for r in state.normalized_records if r.source == "schedule"]
    report = state.domain_plugin.generate_capacity_report(
        state.persons, schedule_recs, state.facilities
    )
    return _serialize(report)


@router.get("/reports/compliance")
async def compliance_report():
    """Module B — Compliance Reporter."""
    if not state.persons:
        raise HTTPException(400, "Run /reconcile first.")
    report = state.domain_plugin.generate_compliance_report(
        state.persons, state.normalized_records, state.conflicts
    )
    return _serialize(report)


@router.get("/reports/expirations")
async def expiration_report():
    """Module C — Expiration Tracker."""
    if not state.persons:
        raise HTTPException(400, "Run /reconcile first.")
    report = state.domain_plugin.generate_expiration_report(state.persons)
    return _serialize(report)


# ---------------------------------------------------------------------------
# Debug / audit endpoints
# ---------------------------------------------------------------------------

@router.get("/status")
async def status():
    """Quick status of ingested data."""
    return {
        "raw_records": len(state.raw_records),
        "normalized_records": len(state.normalized_records),
        "persons_resolved": len(state.persons),
        "facilities_resolved": len(state.facilities),
        "conflicts": len(state.conflicts),
        "ingestion_log": state.ingestion_log,
        "domain": state.domain_plugin.get_name(),
    }


@router.get("/audit/persons")
async def audit_persons():
    """Full audit view of all resolved persons."""
    return _serialize(state.persons)


@router.get("/audit/raw")
async def audit_raw():
    """View raw records for audit trail."""
    return _serialize(state.raw_records[:50])  # limit for readability
