"""
Shared schedule helpers used by the capacity, compliance and review modules.

Everything that needs to reason about "who works when" goes through here so
the calendar, the payroll cross-check and the cover suggestions all agree on
how a shift string like '11p-7a' turns into hours and clock times.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from app.models.entities import NormalizedRecord, PersonEntity

ALIASES_PATH = Path(__file__).resolve().parents[3] / "config" / "aliases.json"

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
OFF_VALUES = {"OFF", "X", "-", "", "NONE", "N/A", "PTO", "LOA"}

# License types that count as a licensed nurse on the floor.
NURSE_LICENSES = {"Registered Nurse", "Licensed Practical Nurse"}

# Minimum rest between two shifts before someone can be offered a cover shift.
MIN_REST_HOURS = 8
# Weekly hours above which a cover shift would push someone into overtime.
OVERTIME_HOURS = 40


def _load_aliases() -> dict:
    try:
        with open(ALIASES_PATH) as f:
            return json.load(f)
    except OSError:
        return {}


_ALIASES = _load_aliases()
_TITLE_TO_CODE = {title: code for code, title in _ALIASES.get("job_codes", {}).items()}


def role_code(title: str) -> str:
    """'Registered Nurse' -> 'RN'. Unknown titles get their initials."""
    if not title:
        return "?"
    if title in _TITLE_TO_CODE:
        return _TITLE_TO_CODE[title]
    return "".join(w[0] for w in title.split() if w)[:3].upper()


# ---------------------------------------------------------------------------
# Shift parsing
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ShiftWindow:
    code: str          # canonical text, e.g. '7a-3p'
    start: int         # hour of day the shift starts (0-23)
    end: int           # hour it ends, > start; can exceed 24 for overnight
    label: str         # human slot name: Day, Evening, Night, Day (12h)...
    tone: str          # css tone key used by the calendar

    @property
    def hours(self) -> float:
        return float(self.end - self.start)

    @property
    def pretty(self) -> str:
        return f"{_fmt_hour(self.start)}–{_fmt_hour(self.end % 24)}"


def _fmt_hour(h: int) -> str:
    h = h % 24
    suffix = "a" if h < 12 else "p"
    h12 = h % 12 or 12
    return f"{h12}{suffix}"


_SHIFT_RE = re.compile(
    r"^\s*(\d{1,2})(?::(\d{2}))?\s*([ap])m?\s*[-–—to ]+\s*(\d{1,2})(?::(\d{2}))?\s*([ap])m?\s*$",
    re.IGNORECASE,
)


def is_off(shift: str) -> bool:
    return str(shift or "").strip().upper() in OFF_VALUES


def parse_shift(shift: str) -> ShiftWindow | None:
    """Parse '7a-3p', '7A - 7P', '11pm-7am' into a ShiftWindow."""
    if is_off(shift):
        return None
    m = _SHIFT_RE.match(str(shift))
    if not m:
        return None
    sh, _, sp, eh, _, ep = m.groups()
    start = int(sh) % 12 + (12 if sp.lower() == "p" else 0)
    end = int(eh) % 12 + (12 if ep.lower() == "p" else 0)
    if end <= start:
        end += 24
    length = end - start
    if length >= 12:
        label, tone = ("Day, 12h", "long") if start < 12 else ("Night, 12h", "night")
    elif start < 11:
        label, tone = "Day", "day"
    elif start < 19:
        label, tone = "Evening", "evening"
    else:
        label, tone = "Night", "night"
    code = f"{_fmt_hour(start)}-{_fmt_hour(end % 24)}"
    return ShiftWindow(code=code, start=start, end=end, label=label, tone=tone)


def slot_sort_key(win: ShiftWindow) -> tuple[int, int]:
    return (win.start if win.start >= 5 else win.start + 24, win.end - win.start)


# ---------------------------------------------------------------------------
# Week resolution
# ---------------------------------------------------------------------------

def _iso(d: str) -> date | None:
    try:
        return datetime.strptime(d, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def resolve_week(schedule_records: list[NormalizedRecord]) -> dict:
    """Work out which dates the schedule covers.

    Returns {"dated": bool, "days": [{"day", "date", "short", "sub"}],
    "start": iso|'', "end": iso|''}. If the file has no dates the seven
    weekdays are returned without dates.
    """
    by_day: dict[str, date] = {}
    for rec in schedule_records:
        d = _iso(rec.data.get("date", ""))
        day = rec.data.get("day", "")
        if d and day and day not in by_day:
            by_day[day] = d

    if by_day:
        # Anchor on any known date and lay out the Monday-start week around it
        any_day, any_date = next(iter(by_day.items()))
        monday = any_date - timedelta(days=WEEKDAYS.index(any_day)) if any_day in WEEKDAYS else any_date
        days = []
        for i, name in enumerate(WEEKDAYS):
            d = by_day.get(name, monday + timedelta(days=i))
            days.append({
                "day": name, "date": d.isoformat(),
                "short": name[:3], "sub": f"{d.strftime('%b')} {d.day}",
            })
        return {"dated": True, "days": days, "start": days[0]["date"], "end": days[-1]["date"]}

    days = [{"day": n, "date": "", "short": n[:3], "sub": ""} for n in WEEKDAYS]
    return {"dated": False, "days": days, "start": "", "end": ""}


# ---------------------------------------------------------------------------
# De-duplication
# ---------------------------------------------------------------------------

def dedupe_schedule(schedule_records: list[NormalizedRecord]) -> list[NormalizedRecord]:
    """If the same week was uploaded twice (e.g. CSV and PDF), keep one row
    per person per day, preferring the version that carries a real date."""
    best: dict[tuple[str, str, str], NormalizedRecord] = {}
    order: list[tuple[str, str, str]] = []
    for rec in schedule_records:
        name = (rec.data.get("canonical_name") or "").lower().strip()
        fac = (rec.data.get("facility") or "").lower().strip()
        day = rec.data.get("day", "")
        if not name or not day:
            continue
        key = (name, fac, day)
        if key not in best:
            best[key] = rec
            order.append(key)
        elif not best[key].data.get("date") and rec.data.get("date"):
            best[key] = rec
    return [best[k] for k in order]


# ---------------------------------------------------------------------------
# Person <-> schedule rows
# ---------------------------------------------------------------------------

def schedule_rows_by_person(
    persons: list[PersonEntity],
    schedule_records: list[NormalizedRecord],
) -> dict[str, list[NormalizedRecord]]:
    """Map person.id -> their (deduplicated) schedule rows using the links the
    entity resolver created."""
    rows_by_raw = {r.raw_record_id: r for r in schedule_records}
    out: dict[str, list[NormalizedRecord]] = {}
    for p in persons:
        rows = [rows_by_raw[rid] for rid in p.schedule_records if rid in rows_by_raw]
        out[p.id] = rows
    return out


def day_index(day: str, iso_date: str, week: dict) -> int:
    """Position of a day in the resolved week (0 = first day)."""
    if iso_date and week.get("dated"):
        for i, d in enumerate(week["days"]):
            if d["date"] == iso_date:
                return i
    return WEEKDAYS.index(day) if day in WEEKDAYS else 0


def absolute_interval(win: ShiftWindow, idx: int) -> tuple[int, int]:
    """Shift as absolute hours from the start of the week."""
    return (idx * 24 + win.start, idx * 24 + win.end)


def rest_ok(interval: tuple[int, int], busy: list[tuple[int, int]], min_rest: int = MIN_REST_HOURS) -> bool:
    """True if `interval` doesn't overlap any busy interval and leaves at least
    `min_rest` hours either side."""
    a, b = interval
    for x, y in busy:
        if not (y + min_rest <= a or b + min_rest <= x):
            return False
    return True


def license_state(person: PersonEntity, on: date, today: date) -> tuple[str, int | None]:
    """'valid' | 'expiring' (valid on the date, renews within 30 days of today)
    | 'expired' (not valid on the date) | 'none' (no license on file)."""
    exp = _iso(person.license_expiration)
    if not person.license_number and not exp:
        return ("none", None)
    if exp is None:
        return ("unknown", None)
    days_left = (exp - today).days
    if exp < on:
        return ("expired", days_left)
    if days_left <= 30:
        return ("expiring", days_left)
    return ("valid", days_left)
