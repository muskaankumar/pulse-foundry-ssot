from __future__ import annotations
import csv
import io
import json
import re
from pathlib import Path
from rapidfuzz import fuzz

from app.models.entities import RawRecord

SCHEMA_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "config" / "schema_registry.json"
ALIASES_PATH = Path(__file__).resolve().parents[2] / "config" / "aliases.json"

# ---------- Day detection (self-contained, no pdf_loader import) ----------

_DAY_ABBREVS = {
    "mon": "Monday", "tue": "Tuesday", "tues": "Tuesday",
    "wed": "Wednesday", "thu": "Thursday", "thur": "Thursday", "thurs": "Thursday",
    "fri": "Friday", "sat": "Saturday", "sun": "Sunday",
    "monday": "Monday", "tuesday": "Tuesday", "wednesday": "Wednesday",
    "thursday": "Thursday", "friday": "Friday", "saturday": "Saturday", "sunday": "Sunday",
}

_DAY_DATE_RE = re.compile(
    r"^(mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\w*"
    r"[\s\n.,-]*"
    r"(\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?)?$",
    re.IGNORECASE,
)

_SHIFT_HOURS = {
    "7A-3P": 8, "3P-11P": 8, "11P-7A": 8, "7A-7P": 12, "7P-7A": 12,
}


def _detect_day(header: str) -> tuple[str | None, str | None]:
    """Return (canonical_day, raw_date_or_None) if header is a day column."""
    h = header.strip()
    if not h:
        return (None, None)
    m = _DAY_DATE_RE.match(h)
    if m:
        abbrev = m.group(1).lower()[:3]
        day = _DAY_ABBREVS.get(abbrev)
        return (day, m.group(2) or None)
    day = _DAY_ABBREVS.get(h.lower())
    return (day, None) if day else (None, None)


def _shift_to_hours(shift: str) -> float:
    """Convert a shift string like '7a-3p' to hours."""
    clean = shift.strip().upper()
    if clean in ("OFF", "-", "", "X", "NONE"):
        return 0.0
    if clean in _SHIFT_HOURS:
        return float(_SHIFT_HOURS[clean])
    match = re.match(r"(\d{1,2})\s*([AP])\s*[-–]\s*(\d{1,2})\s*([AP])", clean, re.IGNORECASE)
    if match:
        sh, sp, eh, ep = match.groups()
        start = int(sh) + (12 if sp.upper() == "P" and int(sh) != 12 else 0)
        end = int(eh) + (12 if ep.upper() == "P" and int(eh) != 12 else 0)
        if end <= start:
            end += 24
        return float(end - start)
    return 0.0


# ---------- Schema registry ----------

def _load_schema_registry() -> dict:
    with open(SCHEMA_REGISTRY_PATH) as f:
        return json.load(f)


def _load_aliases() -> dict:
    with open(ALIASES_PATH) as f:
        return json.load(f)


def _best_source_match(headers: list[str], registry: dict) -> tuple[str, dict[str, str]]:
    best_source = ""
    best_score = 0
    best_mapping: dict[str, str] = {}

    for source, schema in registry.items():
        mapping: dict[str, str] = {}
        total_score = 0
        for canonical, aliases in schema["canonical_fields"].items():
            top_match = ""
            top_ratio = 0
            for header in headers:
                for alias in aliases:
                    ratio = fuzz.ratio(header.lower().strip(), alias.lower())
                    if ratio > top_ratio:
                        top_ratio = ratio
                        top_match = header
            if top_ratio >= 60:
                mapping[canonical] = top_match
                total_score += top_ratio

        avg = total_score / max(len(schema["canonical_fields"]), 1)
        if avg > best_score:
            best_score = avg
            best_source = source
            best_mapping = mapping

    return best_source, best_mapping


# ---------- Main loader ----------

def load_csv(file_content: str | bytes, filename: str = "unknown.csv") -> tuple[str, list[RawRecord]]:
    if isinstance(file_content, bytes):
        file_content = file_content.decode("utf-8", errors="replace")

    reader = csv.DictReader(io.StringIO(file_content))
    headers = reader.fieldnames or []

    # 1. Detect wide schedule format by looking for day columns
    day_columns: dict[str, tuple[str, str | None]] = {}
    for h in headers:
        day, raw_date = _detect_day(h)
        if day:
            day_columns[h] = (day, raw_date)

    is_wide_schedule = len(day_columns) >= 3

    # 2. Wide schedule: unpivot each row into one record per person per day
    if is_wide_schedule:
        records: list[RawRecord] = []

        # Find name, facility, role columns (case-insensitive)
        def _find_col(candidates: list[str]) -> str | None:
            for h in headers:
                if h.lower().strip() in candidates:
                    return h
            return None

        name_col = _find_col(["staff", "employee", "name", "employee_name", "staff name", "employee name"])
        fac_col = _find_col(["facility", "location", "site"])
        role_col = _find_col(["role", "title", "job", "position", "job_title"])

        # If no name column found, assume first non-day, non-facility, non-role column
        if name_col is None:
            for h in headers:
                if h not in day_columns and h != fac_col and h != role_col:
                    name_col = h
                    break

        for row in reader:
            raw_name = row.get(name_col, "").strip() if name_col else ""
            if not raw_name:
                continue
            # Skip footer rows
            if any(kw in raw_name.lower() for kw in ("shift", "key", "total", "note")):
                continue

            facility = row.get(fac_col, "").strip() if fac_col else ""
            role = row.get(role_col, "").strip() if role_col else ""

            for header_text, (day_name, raw_date) in day_columns.items():
                shift = row.get(header_text, "").strip() or "OFF"
                hours = _shift_to_hours(shift)

                record = RawRecord(
                    source="schedule",
                    source_file=filename,
                    data={
                        "raw": dict(row),
                        "mapped": {
                            "employee_name": raw_name,
                            "role": role,
                            "facility": facility,
                            "day": day_name,
                            "date": raw_date or "",
                            "shift": shift,
                            "hours": hours,
                        },
                    },
                )
                records.append(record)

        return "schedule", records

    # 3. Standard flat CSV: HR, Payroll, Licenses
    registry = _load_schema_registry()
    source_type, column_map = _best_source_match(headers, registry)

    records: list[RawRecord] = []
    for row in reader:
        raw_data = dict(row)
        mapped_data = {}
        for canonical, actual_col in column_map.items():
            mapped_data[canonical] = row.get(actual_col, "").strip()

        record = RawRecord(
            source=source_type,
            source_file=filename,
            data={
                "raw": raw_data,
                "mapped": mapped_data,
                "column_mapping": column_map,
            },
        )
        records.append(record)

    return source_type, records