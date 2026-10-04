"""
Module A — Capacity Dashboard
Computes staffing per facility per shift per role from reconciled schedule + HR data.
Flags staff with expired or expiring licenses.
Shows all facilities, computes role counts per shift.
"""

from __future__ import annotations
from collections import defaultdict
from datetime import datetime, date

from rapidfuzz import fuzz

from app.models.entities import PersonEntity, NormalizedRecord, FacilityEntity

DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _match_schedule_to_person(
    sched_name: str,
    persons: list[PersonEntity],
) -> PersonEntity | None:
    """Best-effort match a schedule name to a resolved person."""
    sched_lower = sched_name.lower().strip()
    best_person = None
    best_score = 0

    for p in persons:
        score = fuzz.token_sort_ratio(sched_lower, p.canonical_name.lower())
        if score > best_score and score >= 70:
            best_score = score
            best_person = p

    return best_person


def build_capacity_dashboard(
    persons: list[PersonEntity],
    schedule_records: list[NormalizedRecord],
    facilities: list[FacilityEntity],
) -> dict:
    """
    Build a staffing capacity view.
    """
    # Organize: facility -> day -> shift -> list of staff
    grid: dict[str, dict[str, dict[str, list[dict]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )

    license_warnings: list[dict] = []
    seen_warnings: set[tuple[str, str]] = set()  # (name, severity) dedup
    today = date.today()

    # Track staff per facility from HR for "expected" counts
    staff_per_facility: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for p in persons:
        if p.facility and p.job_title:
            staff_per_facility[p.facility][p.job_title] += 1

    for rec in schedule_records:
        d = rec.data
        facility = d.get("facility", "Unknown")
        day = d.get("day", "")
        shift = d.get("shift", "")
        emp_name = d.get("canonical_name", "") or d.get("employee_name", "")
        hours = d.get("hours", 0)

        if not day or not emp_name or str(shift).upper() in ("OFF", "X", "-", ""):
            continue

        person = _match_schedule_to_person(emp_name, persons)
        role = person.job_title if person else d.get("role", "Unknown")
        if not role:
            role = "Unknown"
        license_ok = True

        if person and person.license_expiration:
            try:
                exp = datetime.strptime(person.license_expiration, "%Y-%m-%d").date()
                days_left = (exp - today).days
                warn_key = (person.canonical_name, "EXPIRED" if days_left < 0 else "EXPIRING_SOON")
                if days_left < 0 and warn_key not in seen_warnings:
                    license_ok = False
                    license_warnings.append({
                        "name": person.canonical_name,
                        "facility": facility,
                        "license_number": person.license_number,
                        "expired_on": person.license_expiration,
                        "days_overdue": abs(days_left),
                        "severity": "EXPIRED",
                    })
                    seen_warnings.add(warn_key)
                elif 0 <= days_left <= 30 and warn_key not in seen_warnings:
                    license_warnings.append({
                        "name": person.canonical_name,
                        "facility": facility,
                        "license_number": person.license_number,
                        "expires_on": person.license_expiration,
                        "days_remaining": days_left,
                        "severity": "EXPIRING_SOON",
                    })
                    seen_warnings.add(warn_key)
            except ValueError:
                pass

        entry = {
            "name": emp_name,
            "role": role,
            "license_ok": license_ok,
            "hours": hours,
        }
        grid[facility][day][shift].append(entry)

    # Make sure all known facilities appear even if they have no schedule data
    for fac in facilities:
        if fac.canonical_name not in grid:
            grid[fac.canonical_name] = {}

    # Build summary per facility
    summary: dict[str, dict] = {}
    for fac_name in grid:
        total_shifts = 0
        roles_count: dict[str, int] = defaultdict(int)
        for day in grid[fac_name]:
            for shift in grid[fac_name][day]:
                for staff in grid[fac_name][day][shift]:
                    total_shifts += 1
                    roles_count[staff["role"]] += 1

        summary[fac_name] = {
            "total_staff_shifts": total_shifts,
            "roles_scheduled": dict(roles_count),
            "roles_on_roster": dict(staff_per_facility.get(fac_name, {})),
        }

    return {
        "facilities": {fac: dict(days) for fac, days in grid.items()},
        "summary": summary,
        "license_warnings": license_warnings,
    }
