"""
PDF Loader — extracts weekly staff schedule tables from PDF files
using pdfplumber.  Each page is assumed to be one facility's schedule.

Handles the brief's exact header format:  Staff | Role | Mon 09/14 | Tue 09/15 | ...
Also handles bare day names:              Employee | Monday | Tuesday | ...
"""

from __future__ import annotations
import json
import re
from pathlib import Path

import pdfplumber

from app.models.entities import RawRecord

ALIASES_PATH = Path(__file__).resolve().parents[2] / "config" / "aliases.json"

# Canonical day names and their abbreviations
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAY_ABBREVS = {
    "mon": "Monday", "tue": "Tuesday", "tues": "Tuesday",
    "wed": "Wednesday", "thu": "Thursday", "thur": "Thursday", "thurs": "Thursday",
    "fri": "Friday", "sat": "Saturday", "sun": "Sunday",
    "monday": "Monday", "tuesday": "Tuesday", "wednesday": "Wednesday",
    "thursday": "Thursday", "friday": "Friday", "saturday": "Saturday", "sunday": "Sunday",
}

# Patterns for headers like "Mon 09/14", "Tue 09/15", "Wed 9/16", "Mon\n09/14"
_DAY_DATE_RE = re.compile(
    r"^(mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\w*"   # day abbrev/name
    r"[\s\n.,-]*"                                           # separator
    r"(\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?)?$",          # optional date
    re.IGNORECASE,
)


def _load_aliases() -> dict:
    with open(ALIASES_PATH) as f:
        return json.load(f)


def _detect_facility_from_text(text: str, aliases: dict) -> str:
    """Try to find a facility name in the page text.
    Check longer alias strings first so 'Harborview Bayside' matches
    before a bare 'Bayside' substring would."""
    text_lower = text.lower()
    # Sort aliases longest-first for specificity
    for alias, canonical in sorted(
        aliases.get("facilities", {}).items(),
        key=lambda kv: len(kv[0]),
        reverse=True,
    ):
        if alias.lower() in text_lower:
            return canonical
    return "Unknown"


def _normalize_day(header: str) -> tuple[str | None, str | None]:
    """Map a column header to (canonical_day_name, raw_date_if_present).
    Handles: 'Monday', 'Mon', 'Mon 09/14', 'Tue 09/15', 'Wed\n9/16'.
    Returns (None, None) if the header is not a day column.
    """
    h = header.strip()
    if not h:
        return (None, None)

    m = _DAY_DATE_RE.match(h)
    if m:
        abbrev = m.group(1).lower()[:3]   # normalise to 3-char key
        # Handle 'tues'/'thur'/'thurs' by truncating
        if abbrev in ("tue", "tu"):
            abbrev = "tue"
        elif abbrev in ("thu", "th"):
            abbrev = "thu"
        day = DAY_ABBREVS.get(abbrev)
        raw_date = m.group(2) or ""
        return (day, raw_date if raw_date else None)

    # Try bare match
    h_lower = h.lower()
    day = DAY_ABBREVS.get(h_lower)
    return (day, None) if day else (None, None)


def _parse_shift_hours(shift: str, aliases: dict) -> float:
    """Given a shift string like '7a-3p', return the hours."""
    if not shift:
        return 0.0
    shift_clean = shift.strip().upper()
    if shift_clean in ("OFF", "-", "", "X", "NONE"):
        return 0.0
    shift_map = aliases.get("shift_hours", {})
    for pattern, hours in shift_map.items():
        if pattern.upper().replace(" ", "") == shift_clean.replace(" ", ""):
            return float(hours)
    return _estimate_hours(shift_clean)


def _estimate_hours(shift: str) -> float:
    """Fallback: estimate hours from a shift pattern like 7A-3P."""
    match = re.match(
        r"(\d{1,2})\s*([AP])\s*[-–]\s*(\d{1,2})\s*([AP])",
        shift, re.IGNORECASE,
    )
    if not match:
        return 0.0
    start_h, start_p, end_h, end_p = match.groups()
    start = int(start_h) + (12 if start_p.upper() == "P" and int(start_h) != 12 else 0)
    end = int(end_h) + (12 if end_p.upper() == "P" and int(end_h) != 12 else 0)
    if end <= start:
        end += 24
    return float(end - start)


def _detect_schedule_dates(page_text: str) -> tuple[str, str]:
    """Try to extract the week range from page text (e.g. 'Sep 14 – Sep 20')."""
    # Common patterns: "09/14 - 09/20", "September 14 – September 20, 2026", etc.
    m = re.search(
        r"(\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?)\s*[-–]\s*(\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?)",
        page_text,
    )
    if m:
        return m.group(1), m.group(2)
    return ("", "")


def load_pdf(file_path: str | bytes, filename: str = "schedule.pdf") -> list[RawRecord]:
    """
    Extract schedule data from a PDF.  Each page = one facility.
    Returns a list of RawRecords with source='schedule'.
    """
    aliases = _load_aliases()
    records: list[RawRecord] = []

    if isinstance(file_path, bytes):
        import io
        pdf_input = io.BytesIO(file_path)
    else:
        pdf_input = file_path

    with pdfplumber.open(pdf_input) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            facility = _detect_facility_from_text(page_text, aliases)
            period_start, period_end = _detect_schedule_dates(page_text)

            tables = page.extract_tables()
            for table in tables:
                if not table or len(table) < 2:
                    continue

                # First row = headers
                headers = [str(h).strip() if h else "" for h in table[0]]

                # Identify columns
                day_columns: dict[int, tuple[str, str | None]] = {}  # idx -> (day, raw_date)
                name_col: int | None = None
                role_col: int | None = None

                for i, h in enumerate(headers):
                    day, raw_date = _normalize_day(h)
                    if day:
                        day_columns[i] = (day, raw_date)
                    elif h.lower() in ("employee", "name", "staff", "staff name", "employee name"):
                        name_col = i
                    elif h.lower() in ("role", "title", "position", "job", "job_title"):
                        role_col = i

                # If we found no day columns, skip this table
                if not day_columns:
                    continue

                # Default name_col to 0 if not explicitly found
                if name_col is None:
                    name_col = 0

                # Parse data rows
                for row in table[1:]:
                    if not row:
                        continue
                    raw_name = str(row[name_col]).strip() if row[name_col] else ""
                    if not raw_name:
                        continue
                    # Skip footer / key rows
                    if any(kw in raw_name.lower() for kw in ("shift", "key", "total", "note")):
                        continue

                    # Extract role if available
                    role = ""
                    if role_col is not None and role_col < len(row) and row[role_col]:
                        role = str(row[role_col]).strip()

                    for col_idx, (day_name, raw_date) in day_columns.items():
                        cell = str(row[col_idx]).strip() if col_idx < len(row) and row[col_idx] else ""
                        shift = cell if cell else "OFF"
                        hours = _parse_shift_hours(shift, aliases)

                        record = RawRecord(
                            source="schedule",
                            source_file=filename,
                            data={
                                "raw": {
                                    "employee_name": raw_name,
                                    "role": role,
                                    "facility": facility,
                                    "day": day_name,
                                    "date": raw_date or "",
                                    "shift": shift,
                                },
                                "mapped": {
                                    "employee_name": raw_name,
                                    "role": role,
                                    "facility": facility,
                                    "day": day_name,
                                    "date": raw_date or "",
                                    "shift": shift,
                                    "hours": hours,
                                    "period_start": period_start,
                                    "period_end": period_end,
                                },
                            },
                        )
                        records.append(record)

    return records
