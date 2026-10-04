"""
Core domain entities for the Single Source of Truth system.
All entities are domain-agnostic at the base level.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any
import uuid


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ConflictSeverity(Enum):
    CRITICAL = 1    # e.g. expired license
    HIGH = 2        # e.g. value mismatch across sources
    MEDIUM = 3      # e.g. missing value in one source
    LOW = 4         # e.g. formatting difference
    INFO = 5        # e.g. extra data in one source


class FieldStatus(Enum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    MISSING = "MISSING"
    STALE = "STALE"


class UrgencyTier(Enum):
    EXPIRED = "EXPIRED"
    EXPIRING_30 = "EXPIRING_30"
    EXPIRING_90 = "EXPIRING_90"
    OK = "OK"


# ---------------------------------------------------------------------------
# Raw record — preserves the original data exactly as ingested
# ---------------------------------------------------------------------------

@dataclass
class RawRecord:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source: str = ""          # "hr", "payroll", "license", "schedule"
    source_file: str = ""     # original filename
    data: dict[str, Any] = field(default_factory=dict)
    ingested_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


# ---------------------------------------------------------------------------
# Normalized record — cleaned version with confidence scores
# ---------------------------------------------------------------------------

@dataclass
class NormalizedRecord:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    raw_record_id: str = ""
    source: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    confidence: dict[str, float] = field(default_factory=dict)  # field -> score
    normalization_log: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Unified Person entity — the reconciled "single source of truth"
# ---------------------------------------------------------------------------

@dataclass
class PersonEntity:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    canonical_name: str = ""
    first_name: str = ""
    last_name: str = ""
    employee_id: str = ""
    job_title: str = ""
    facility: str = ""
    phone: str = ""
    license_number: str = ""
    license_type: str = ""
    license_expiration: str = ""
    hire_date: str = ""
    source_records: list[str] = field(default_factory=list)       # raw record ids (hr/payroll/licenses)
    schedule_records: list[str] = field(default_factory=list)     # raw record ids of schedule rows
    sources: list[str] = field(default_factory=list)              # systems this person was found in
    match_evidence: list[dict] = field(default_factory=list)      # plain-language "why these records were linked"
    field_statuses: dict[str, FieldStatus] = field(default_factory=dict)
    conflicts: list[Conflict] = field(default_factory=list)
    resolution_log: list[str] = field(default_factory=list)
    # Kept for API compatibility. The UI no longer shows this number; it shows
    # concrete checks instead (see app/domain/healthcare/review.py).
    match_confidence: float = 0.0


# ---------------------------------------------------------------------------
# Facility entity
# ---------------------------------------------------------------------------

@dataclass
class FacilityEntity:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    canonical_name: str = ""
    aliases: list[str] = field(default_factory=list)
    source_records: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Conflict record
# ---------------------------------------------------------------------------

@dataclass
class Conflict:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    person_id: str = ""
    field_name: str = ""
    status: FieldStatus = FieldStatus.MISMATCH
    severity: ConflictSeverity = ConflictSeverity.MEDIUM
    values_by_source: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    resolved: bool = False
    resolution_note: str = ""


# ---------------------------------------------------------------------------
# Schedule entry (per person per day)
# ---------------------------------------------------------------------------

@dataclass
class ScheduleEntry:
    employee_name: str = ""
    facility: str = ""
    day: str = ""
    shift: str = ""     # e.g. "7a-3p", "OFF"
    hours: float = 0.0


# ---------------------------------------------------------------------------
# License / credential record
# ---------------------------------------------------------------------------

@dataclass
class LicenseRecord:
    person_id: str = ""
    license_number: str = ""
    license_type: str = ""
    name_on_license: str = ""
    expiration_date: str = ""
    last_verified: str = ""
    urgency: UrgencyTier = UrgencyTier.OK
    days_until_expiry: int | None = None
