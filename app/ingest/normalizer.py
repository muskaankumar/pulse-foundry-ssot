"""
Normalizer — takes RawRecords and produces NormalizedRecords.
Handles name casing, LAST-FIRST inversion, facility alias resolution,
job code expansion, and date normalization.

Key design rule: fields that mean the same thing across sources are
written to ONE canonical key so the reconciler can compare them.
  - HR  license_expiration  }
  - Lic expiration_date     } → both become "license_expiration"
  - Schedule role / HR job_title / Payroll job_code → all become "job_title"
"""

from __future__ import annotations
import json
import re
from datetime import datetime
from pathlib import Path

from app.models.entities import RawRecord, NormalizedRecord

ALIASES_PATH = Path(__file__).resolve().parents[2] / "config" / "aliases.json"


def _load_aliases() -> dict:
    with open(ALIASES_PATH) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------

def normalize_name(raw_name: str) -> tuple[str, str, float]:
    """
    Convert any name format to (first_name, last_name) in title case.
    Handles: "REYES, SOFIA", "sofia reyes", "Reyes Sofia", "SOFIA REYES".
    Returns (first, last, confidence).
    """
    if not raw_name or not raw_name.strip():
        return ("", "", 0.0)

    name = raw_name.strip()

    # LAST, FIRST format
    if "," in name:
        parts = [p.strip() for p in name.split(",", 1)]
        last = parts[0].strip().title()
        first = parts[1].strip().title() if len(parts) > 1 else ""
        return (first, last, 0.95)

    # Simple space-separated — assume FIRST LAST
    parts = name.split()
    if len(parts) == 1:
        return (parts[0].title(), "", 0.7)
    elif len(parts) == 2:
        return (parts[0].title(), parts[1].title(), 0.9)
    else:
        first = parts[0].title()
        last = " ".join(parts[1:]).title()
        return (first, last, 0.8)


def normalize_facility(raw_facility: str, aliases: dict) -> tuple[str, float]:
    """Resolve a facility name/code to its canonical form."""
    if not raw_facility:
        return ("", 0.0)
    clean = raw_facility.strip()
    facility_map = aliases.get("facilities", {})

    # Exact match (case-insensitive) — check longest aliases first
    for alias, canonical in sorted(facility_map.items(), key=lambda kv: len(kv[0]), reverse=True):
        if clean.upper() == alias.upper():
            return (canonical, 1.0)

    # Substring match — longest first
    for alias, canonical in sorted(facility_map.items(), key=lambda kv: len(kv[0]), reverse=True):
        if alias.upper() in clean.upper() or clean.upper() in alias.upper():
            return (canonical, 0.8)

    return (clean.title(), 0.5)


def normalize_job(raw_job: str, aliases: dict) -> tuple[str, float]:
    """Resolve a job code/title to its canonical form."""
    if not raw_job:
        return ("", 0.0)
    clean = raw_job.strip().upper()
    job_map = aliases.get("job_codes", {})

    # Exact code match
    if clean in job_map:
        return (job_map[clean], 1.0)

    # Check if raw value IS the full title
    for code, title in job_map.items():
        if clean == title.upper():
            return (title, 1.0)

    # Fuzzy: check if code is contained
    for code, title in job_map.items():
        if code in clean:
            return (title, 0.85)

    return (raw_job.strip().title(), 0.5)


def normalize_date(raw_date: str) -> tuple[str, float]:
    """Try multiple date formats and return ISO format YYYY-MM-DD."""
    if not raw_date or not raw_date.strip():
        return ("", 0.0)
    clean = raw_date.strip()

    formats = [
        "%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%d/%m/%Y",
        "%m/%d/%y", "%m-%d-%y", "%Y/%m/%d",
        "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(clean, fmt)
            return (dt.strftime("%Y-%m-%d"), 0.95)
        except ValueError:
            continue
    return (clean, 0.3)


def normalize_phone(raw_phone: str) -> tuple[str, float]:
    """Strip a phone to digits and format as (XXX) XXX-XXXX."""
    if not raw_phone:
        return ("", 0.0)
    digits = re.sub(r"\D", "", raw_phone.strip())
    if len(digits) == 10:
        formatted = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
        return (formatted, 1.0)
    elif len(digits) == 11 and digits[0] == "1":
        formatted = f"({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
        return (formatted, 0.95)
    return (raw_phone.strip(), 0.5)


# ---------------------------------------------------------------------------
# Which fields each source is expected to have
# ---------------------------------------------------------------------------

# Fields that are NATURAL to each source.  A "missing" flag is only raised
# when a field is natural to a source but absent in the actual data.
# This prevents noise like "payroll doesn't have a phone number".
SOURCE_EXPECTED_FIELDS: dict[str, set[str]] = {
    "hr":       {"first_name", "last_name", "employee_id", "job_title", "facility",
                 "phone", "license_number", "license_expiration", "hire_date"},
    "payroll":  {"first_name", "last_name", "job_title", "facility", "hours",
                 "period_start", "period_end"},
    "licenses": {"first_name", "last_name", "license_number", "license_type",
                 "license_expiration", "last_verified"},
    "schedule": {"first_name", "last_name", "facility", "job_title"},
}


# ---------------------------------------------------------------------------
# Main normalization entry point
# ---------------------------------------------------------------------------

def normalize_record(raw: RawRecord) -> NormalizedRecord:
    """
    Take a RawRecord and produce a NormalizedRecord with confidence scores
    for each normalization step.
    """
    aliases = _load_aliases()
    mapped = raw.data.get("mapped", raw.data.get("raw", {}))
    normalized: dict = {}
    confidence: dict[str, float] = {}
    log: list[str] = []

    source = raw.source

    # --- Name normalization ---
    if source in ("hr",):
        first = mapped.get("first_name", "")
        last = mapped.get("last_name", "")
        if first or last:
            f_clean = first.strip().title()
            l_clean = last.strip().title()
            normalized["first_name"] = f_clean
            normalized["last_name"] = l_clean
            normalized["canonical_name"] = f"{f_clean} {l_clean}".strip()
            confidence["name"] = 0.95
            log.append(f"Name: '{first}' '{last}' → '{f_clean} {l_clean}'")
    elif "employee_name" in mapped or "name_on_license" in mapped:
        raw_name = mapped.get("employee_name") or mapped.get("name_on_license", "")
        first, last, conf = normalize_name(raw_name)
        normalized["first_name"] = first
        normalized["last_name"] = last
        normalized["canonical_name"] = f"{first} {last}".strip()
        confidence["name"] = conf
        log.append(f"Name: '{raw_name}' → '{first} {last}' (conf={conf:.2f})")

    # --- Facility normalization ---
    raw_fac = mapped.get("facility") or mapped.get("facility_code", "")
    if raw_fac:
        fac, fac_conf = normalize_facility(raw_fac, aliases)
        normalized["facility"] = fac
        confidence["facility"] = fac_conf
        log.append(f"Facility: '{raw_fac}' → '{fac}' (conf={fac_conf:.2f})")

    # --- Job normalization ---
    # Unify: job_title (HR), job_code (payroll), role (schedule) → "job_title"
    raw_job = mapped.get("job_title") or mapped.get("job_code") or mapped.get("role", "")
    if raw_job:
        job, job_conf = normalize_job(raw_job, aliases)
        normalized["job_title"] = job
        confidence["job_title"] = job_conf
        log.append(f"Job: '{raw_job}' → '{job}' (conf={job_conf:.2f})")

    # --- License number (pass through, just clean) ---
    lic = mapped.get("license_number", "").strip()
    if lic:
        normalized["license_number"] = lic.upper()
        confidence["license_number"] = 1.0

    # --- Dates: unify expiration_date → license_expiration ---
    raw_lic_exp = mapped.get("license_expiration") or mapped.get("expiration_date", "")
    if raw_lic_exp:
        norm_date, date_conf = normalize_date(raw_lic_exp)
        normalized["license_expiration"] = norm_date
        confidence["license_expiration"] = date_conf
        log.append(f"License expiration: '{raw_lic_exp}' → '{norm_date}'")

    for date_field in ("hire_date", "last_verified", "period_start", "period_end"):
        raw_date = mapped.get(date_field, "")
        if raw_date:
            norm_date, date_conf = normalize_date(raw_date)
            normalized[date_field] = norm_date
            confidence[date_field] = date_conf
            log.append(f"Date '{date_field}': '{raw_date}' → '{norm_date}'")

    # --- Phone ---
    raw_phone = mapped.get("phone", "")
    if raw_phone:
        phone, phone_conf = normalize_phone(raw_phone)
        normalized["phone"] = phone
        confidence["phone"] = phone_conf

    # --- License type: unify to canonical job title if it's a code ---
    lic_type = mapped.get("license_type", "").strip()
    if lic_type:
        resolved, _ = normalize_job(lic_type, aliases)
        normalized["license_type"] = resolved
        confidence["license_type"] = 0.9

    # --- Employee ID ---
    emp_id = mapped.get("employee_id", "").strip()
    if emp_id:
        normalized["employee_id"] = emp_id
        confidence["employee_id"] = 1.0

    # --- Hours (payroll / schedule) ---
    hours = mapped.get("hours_paid") or mapped.get("hours")
    if hours is not None:
        try:
            normalized["hours"] = float(hours)
            confidence["hours"] = 1.0
        except (ValueError, TypeError):
            normalized["hours"] = 0.0
            confidence["hours"] = 0.3

    # --- Payroll ID ---
    pay_id = mapped.get("payroll_id", "").strip()
    if pay_id:
        normalized["payroll_id"] = pay_id

    # --- Schedule-specific ---
    if source == "schedule":
        normalized["day"] = mapped.get("day", "")
        normalized["shift"] = mapped.get("shift", "")
        normalized["date"] = mapped.get("date", "")
        normalized["period_start"] = mapped.get("period_start", "")
        normalized["period_end"] = mapped.get("period_end", "")
        hours_val = mapped.get("hours", 0)
        try:
            normalized["hours"] = float(hours_val)
        except (ValueError, TypeError):
            normalized["hours"] = 0.0

    return NormalizedRecord(
        raw_record_id=raw.id,
        source=raw.source,
        data=normalized,
        confidence=confidence,
        normalization_log=log,
    )


def normalize_batch(records: list[RawRecord]) -> list[NormalizedRecord]:
    """Normalize a batch of RawRecords."""
    return [normalize_record(r) for r in records]
