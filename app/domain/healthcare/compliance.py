"""
Module B — Compliance Reporter
Auto-generates staffing summary reports, cross-checks payroll vs schedule hours
matched by pay period, and produces an auditable output.
"""

from __future__ import annotations
from collections import defaultdict

from rapidfuzz import fuzz

from app.models.entities import PersonEntity, NormalizedRecord, Conflict


def _name_match(name_a: str, name_b: str) -> bool:
    return fuzz.token_sort_ratio(name_a.lower(), name_b.lower()) >= 70


def _dates_match(date_a: str, date_b: str) -> bool:
    """Check if two date strings refer to the same date.
    Handles partial dates like '09/28' matching '2026-09-28'."""
    if date_a == date_b:
        return True
    # Strip to just month/day digits for comparison
    import re
    digits_a = re.findall(r'\d+', date_a)
    digits_b = re.findall(r'\d+', date_b)
    # Compare last two components (month, day)
    if len(digits_a) >= 2 and len(digits_b) >= 2:
        return digits_a[-2:] == digits_b[-2:] or digits_a[-2:][::-1] == digits_b[-2:]
    return False


def _get_schedule_hours_by_period(
    person: PersonEntity,
    schedule_records: list[NormalizedRecord],
    payroll_records: list[NormalizedRecord],
) -> dict[str, float]:
    """
    Sum schedule hours for a person, grouped by the payroll period
    whose date range contains the schedule day.
    Falls back to total if no period alignment is possible.

    Returns {period_key: hours} where period_key is "start|end".
    """
    # Build list of pay periods from payroll
    periods: list[tuple[str, str]] = []
    for rec in payroll_records:
        ps = rec.data.get("period_start", "")
        pe = rec.data.get("period_end", "")
        if ps and pe:
            periods.append((ps, pe))
    periods = sorted(set(periods))

    # Gather schedule entries for this person
    matched_entries: list[NormalizedRecord] = []
    for rec in schedule_records:
        name = rec.data.get("canonical_name", "") or rec.data.get("employee_name", "")
        if _name_match(name, person.canonical_name):
            matched_entries.append(rec)

    if not periods:
        # No period info — just total
        total = sum(float(r.data.get("hours", 0)) for r in matched_entries)
        return {"_total": total}

    # Try to match schedule rows to periods via schedule's own period_start/end
    by_period: dict[str, float] = defaultdict(float)
    unmatched_hours = 0.0

    for rec in matched_entries:
        sched_ps = rec.data.get("period_start", "")
        sched_pe = rec.data.get("period_end", "")
        hours = float(rec.data.get("hours", 0))

        matched = False
        for ps, pe in periods:
            if sched_ps and sched_pe and _dates_match(sched_ps, ps) and _dates_match(sched_pe, pe):
                by_period[f"{ps}|{pe}"] += hours
                matched = True
                break

        if not matched:
            unmatched_hours += hours

    if not by_period and unmatched_hours > 0:
        # Couldn't align — report total against sum of payroll
        return {"_total": unmatched_hours}

    if unmatched_hours > 0:
        by_period["_unaligned"] = unmatched_hours

    return dict(by_period)


def _get_payroll_hours_by_period(
    person: PersonEntity,
    payroll_records: list[NormalizedRecord],
) -> dict[str, float]:
    """Sum payroll hours per period for a person."""
    by_period: dict[str, float] = defaultdict(float)
    for rec in payroll_records:
        name = rec.data.get("canonical_name", "")
        if not _name_match(name, person.canonical_name):
            continue
        ps = rec.data.get("period_start", "")
        pe = rec.data.get("period_end", "")
        hours = float(rec.data.get("hours", 0))
        key = f"{ps}|{pe}" if ps and pe else "_total"
        by_period[key] += hours
    return dict(by_period)


def build_compliance_report(
    persons: list[PersonEntity],
    all_records: list[NormalizedRecord],
    conflicts: list[Conflict],
) -> dict:
    """
    Build the compliance / staffing summary report.
    """
    schedule_recs = [r for r in all_records if r.source == "schedule"]
    payroll_recs = [r for r in all_records if r.source == "payroll"]

    by_role: dict[str, dict] = defaultdict(lambda: {"count": 0, "total_schedule_hours": 0.0})
    by_facility: dict[str, dict] = defaultdict(lambda: {"count": 0, "total_schedule_hours": 0.0})

    hours_discrepancies: list[dict] = []
    audit_trail: list[str] = []

    for person in persons:
        role = person.job_title or "Unknown"
        facility = person.facility or "Unknown"

        by_role[role]["count"] += 1
        by_facility[facility]["count"] += 1

        # Schedule hours by period
        sched_by_period = _get_schedule_hours_by_period(person, schedule_recs, payroll_recs)
        sched_total = sum(sched_by_period.values())
        by_role[role]["total_schedule_hours"] += sched_total
        by_facility[facility]["total_schedule_hours"] += sched_total

        # Payroll hours by period
        pay_by_period = _get_payroll_hours_by_period(person, payroll_recs)
        pay_total = sum(pay_by_period.values())

        # Cross-check per period where possible
        all_keys = set(sched_by_period.keys()) | set(pay_by_period.keys())
        for key in sorted(all_keys):
            if key.startswith("_"):
                continue  # skip totals / unaligned
            sh = sched_by_period.get(key, 0)
            ph = pay_by_period.get(key, 0)
            if abs(sh - ph) > 0.5:
                period_label = key.replace("|", " to ")
                discrepancy = {
                    "name": person.canonical_name,
                    "facility": facility,
                    "role": role,
                    "period": period_label,
                    "schedule_hours": sh,
                    "payroll_hours": ph,
                    "difference": round(sh - ph, 1),
                    "direction": "schedule > payroll" if sh > ph else "payroll > schedule",
                }
                hours_discrepancies.append(discrepancy)
                audit_trail.append(
                    f"DISCREPANCY ({period_label}): {person.canonical_name} — "
                    f"Schedule={sh}h, Payroll={ph}h (diff={discrepancy['difference']}h)"
                )

        # If no per-period alignment, compare totals
        if all(k.startswith("_") for k in sched_by_period) and sched_total > 0 and pay_total > 0:
            if abs(sched_total - pay_total) > 0.5:
                hours_discrepancies.append({
                    "name": person.canonical_name,
                    "facility": facility,
                    "role": role,
                    "period": "total (unaligned)",
                    "schedule_hours": sched_total,
                    "payroll_hours": pay_total,
                    "difference": round(sched_total - pay_total, 1),
                    "direction": "schedule > payroll" if sched_total > pay_total else "payroll > schedule",
                })

        audit_trail.append(
            f"{person.canonical_name} ({role}, {facility}): "
            f"schedule_hours={sched_total}, payroll_hours={pay_total}, "
            f"sources={person.source_records}"
        )

    # Conflict summary
    severity_counts: dict[str, int] = defaultdict(int)
    for c in conflicts:
        severity_counts[c.severity.name] += 1

    return {
        "staffing_summary": {
            "by_role": dict(by_role),
            "by_facility": dict(by_facility),
            "total_employees": len(persons),
        },
        "hours_discrepancies": hours_discrepancies,
        "conflict_summary": {
            "total_conflicts": len(conflicts),
            "by_severity": dict(severity_counts),
            "top_conflicts": [
                {"description": c.description, "severity": c.severity.name}
                for c in conflicts[:10]
            ],
        },
        "audit_trail": audit_trail,
    }
