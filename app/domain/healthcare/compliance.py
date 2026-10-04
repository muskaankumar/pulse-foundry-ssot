"""
Module B — Compliance reporter.

Produces the staffing summary used for state reporting and cross-checks paid
hours against scheduled hours. Rules that keep the numbers honest:

  * Payroll and schedule rows are tied to a person through the entity
    resolver's links, not by re-matching names.
  * Hours are only compared for a pay period the schedule fully covers.
    A pay period with no schedule on file is reported as "not compared",
    never as a discrepancy.
  * A schedule without dates is assumed to describe the most recent pay
    period, and the report says so.
  * Every figure carries the source rows it was built from (audit trail).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

from app.domain.healthcare.schedule import (
    dedupe_schedule, is_off, license_state, resolve_week, role_code,
    schedule_rows_by_person,
)
from app.models.entities import Conflict, NormalizedRecord, PersonEntity

HOURS_TOLERANCE = 0.5

STATUS_MATCH = "Matches"
STATUS_PAID_MORE = "Paid more than scheduled"
STATUS_SCHED_MORE = "Scheduled more than paid"
STATUS_NOT_COMPARED = "Not compared"


def _d(iso: str) -> date | None:
    try:
        return datetime.strptime(iso, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def period_label(start: str, end: str) -> str:
    a, b = _d(start), _d(end)
    if not a or not b:
        return f"{start} to {end}".strip()
    if a.year == b.year:
        return f"{a.strftime('%b')} {a.day} – {b.strftime('%b')} {b.day}, {b.year}"
    return f"{a.strftime('%b')} {a.day}, {a.year} – {b.strftime('%b')} {b.day}, {b.year}"


def _row_hours(rec: NormalizedRecord) -> float:
    try:
        return float(rec.data.get("hours") or 0)
    except (TypeError, ValueError):
        return 0.0


def build_compliance_report(
    persons: list[PersonEntity],
    all_records: list[NormalizedRecord],
    conflicts: list[Conflict],
    today: date | None = None,
) -> dict:
    today = today or date.today()

    schedule_recs = dedupe_schedule([r for r in all_records if r.source == "schedule"])
    payroll_recs = [r for r in all_records if r.source == "payroll"]
    payroll_by_raw = {r.raw_record_id: r for r in payroll_recs}
    week = resolve_week(schedule_recs)
    sched_rows = schedule_rows_by_person(persons, schedule_recs)

    # ------------------------------------------------------------------
    # Pay periods and which ones the schedule can be compared against
    # ------------------------------------------------------------------
    periods = sorted({
        (r.data.get("period_start", ""), r.data.get("period_end", ""))
        for r in payroll_recs if r.data.get("period_start") and r.data.get("period_end")
    })
    notes: list[str] = []
    sched_start, sched_end = _d(week["start"]), _d(week["end"])
    has_schedule = bool(schedule_recs)

    period_info: list[dict] = []
    for ps, pe in periods:
        a, b = _d(ps), _d(pe)
        compared, note = False, ""
        if not has_schedule:
            note = "No schedule uploaded"
        elif week["dated"] and sched_start and sched_end and a and b:
            if sched_start <= a and b <= sched_end:
                compared = True
            elif b < sched_start or a > sched_end:
                note = "No schedule on file for this period"
            else:
                note = "Schedule only covers part of this period"
        period_info.append({"start": ps, "end": pe, "label": period_label(ps, pe),
                            "compared": compared, "note": note, "assumed": False})

    if has_schedule and not week["dated"] and period_info:
        latest = period_info[-1]
        latest.update(compared=True, assumed=True,
                      note="Schedule file has no dates; treated as this pay period")
        for older in period_info[:-1]:
            older["note"] = "Schedule file has no dates; only the latest period was compared"
        notes.append(
            f"The schedule file has no dates, so it was compared with the most recent "
            f"pay period ({latest['label']})."
        )
    for p in period_info:
        if not p["compared"] and p["note"] and not p["note"].startswith("Schedule file has no dates"):
            notes.append(f"{p['label']}: {p['note'].lower()}, so hours were not compared.")

    compared_periods = [p for p in period_info if p["compared"]]
    report = compared_periods[-1] if compared_periods else (period_info[-1] if period_info else None)
    if report:
        report_period = {"start": report["start"], "end": report["end"], "label": report["label"],
                         "hours_compared": report["compared"]}
    elif week["dated"]:
        report_period = {"start": week["start"], "end": week["end"],
                         "label": period_label(week["start"], week["end"]), "hours_compared": False}
    else:
        report_period = {"start": "", "end": "", "label": "Current schedule week", "hours_compared": False}

    def in_period(rec: NormalizedRecord, p: dict) -> bool:
        if p.get("assumed"):
            return True
        d = _d(rec.data.get("date", ""))
        a, b = _d(p["start"]), _d(p["end"])
        return bool(d and a and b and a <= d <= b)

    # ------------------------------------------------------------------
    # Per-person hours reconciliation
    # ------------------------------------------------------------------
    reconciliation: list[dict] = []
    audit: list[dict] = []
    person_hours: dict[str, dict] = {}

    for person in persons:
        role = person.job_title or "Unknown"
        facility = person.facility or "Unknown"
        pay_rows = [payroll_by_raw[rid] for rid in person.source_records if rid in payroll_by_raw]
        worked_rows = [r for r in sched_rows.get(person.id, []) if not is_off(r.data.get("shift", ""))]

        for p in period_info:
            p_rows = [r for r in pay_rows
                      if r.data.get("period_start") == p["start"] and r.data.get("period_end") == p["end"]]
            paid = sum(_row_hours(r) for r in p_rows)
            s_rows = [r for r in worked_rows if in_period(r, p)] if p["compared"] else []
            scheduled = sum(_row_hours(r) for r in s_rows)
            if not p_rows and not s_rows:
                continue

            if not p["compared"]:
                status = STATUS_NOT_COMPARED
            elif abs(scheduled - paid) <= HOURS_TOLERANCE:
                status = STATUS_MATCH
            elif paid > scheduled:
                status = STATUS_PAID_MORE
            else:
                status = STATUS_SCHED_MORE

            row = {
                "person_id": person.id,
                "name": person.canonical_name,
                "employee_id": person.employee_id,
                "facility": facility,
                "role": role,
                "period_start": p["start"],
                "period_end": p["end"],
                "period": p["label"],
                "scheduled_hours": scheduled if p["compared"] else None,
                "paid_hours": paid,
                "difference": round(paid - scheduled, 1) if p["compared"] else None,
                "status": status,
                "note": p["note"] if not p["compared"] else ("Assumed period" if p["assumed"] else ""),
                "payroll_ids": [r.data.get("payroll_id", "") for r in p_rows],
                "shifts": [
                    f"{r.data.get('day', '')[:3]} {r.data.get('shift', '')} ({_row_hours(r):g}h)"
                    for r in s_rows
                ],
            }
            reconciliation.append(row)

            if p["compared"]:
                audit.append({
                    "person": person.canonical_name, "figure": "Scheduled hours",
                    "period": p["label"], "value": scheduled,
                    "built_from": "; ".join(row["shifts"]) or "no shifts",
                })
            audit.append({
                "person": person.canonical_name, "figure": "Paid hours",
                "period": p["label"], "value": paid,
                "built_from": ", ".join(f"payroll row {pid}" for pid in row["payroll_ids"] if pid) or "no payroll rows",
            })

        rp_rows = [r for r in reconciliation if r["person_id"] == person.id
                   and r["period_start"] == report_period["start"] and r["period_end"] == report_period["end"]]
        week_sched = sum(_row_hours(r) for r in worked_rows)
        person_hours[person.id] = {
            "scheduled": (rp_rows[0]["scheduled_hours"] or 0.0) if rp_rows and rp_rows[0]["scheduled_hours"] is not None else week_sched,
            "paid": rp_rows[0]["paid_hours"] if rp_rows else 0.0,
        }

    reconciliation.sort(key=lambda r: (r["period_start"], r["facility"], r["name"]))
    discrepancies = [r for r in reconciliation if r["status"] in (STATUS_PAID_MORE, STATUS_SCHED_MORE)]

    # ------------------------------------------------------------------
    # Staffing summary (facility x role) for the report period
    # ------------------------------------------------------------------
    on_date = _d(report_period["end"]) or today
    matrix: dict[tuple[str, str], dict] = {}
    by_role: dict[str, dict] = defaultdict(lambda: {"count": 0, "total_schedule_hours": 0.0, "total_payroll_hours": 0.0})
    by_facility: dict[str, dict] = defaultdict(lambda: {"count": 0, "total_schedule_hours": 0.0, "total_payroll_hours": 0.0})

    for person in persons:
        role = person.job_title or "Unknown"
        facility = person.facility or "Unknown"
        key = (facility, role)
        cell = matrix.setdefault(key, {
            "facility": facility, "role": role, "role_code": role_code(role),
            "headcount": 0, "licensed": 0, "license_valid": 0,
            "scheduled_hours": 0.0, "paid_hours": 0.0,
        })
        hrs = person_hours.get(person.id, {"scheduled": 0.0, "paid": 0.0})
        cell["headcount"] += 1
        if person.license_number:
            cell["licensed"] += 1
            state, _ = license_state(person, on_date, today)
            if state in ("valid", "expiring"):
                cell["license_valid"] += 1
        cell["scheduled_hours"] += hrs["scheduled"]
        cell["paid_hours"] += hrs["paid"]
        for bucket in (by_role[role], by_facility[facility]):
            bucket["count"] += 1
            bucket["total_schedule_hours"] += hrs["scheduled"]
            bucket["total_payroll_hours"] += hrs["paid"]

    staffing_matrix = sorted(matrix.values(), key=lambda c: (c["facility"], c["role"]))

    # ------------------------------------------------------------------
    # Credential snapshot
    # ------------------------------------------------------------------
    credentials: list[dict] = []
    for person in persons:
        if not person.license_number and not person.license_expiration:
            continue
        state, days_left = license_state(person, today, today)
        credentials.append({
            "name": person.canonical_name,
            "facility": person.facility,
            "role": person.job_title,
            "license_number": person.license_number,
            "license_type": person.license_type,
            "expires": person.license_expiration,
            "days_left": days_left,
            "status": {"expired": "Expired", "expiring": "Renew within 30 days",
                       "valid": "Current", "unknown": "Date unreadable"}.get(state, state),
        })
    credentials.sort(key=lambda c: (c["days_left"] if c["days_left"] is not None else 99999))

    # ------------------------------------------------------------------
    # Totals
    # ------------------------------------------------------------------
    rp_rows = [r for r in reconciliation
               if r["period_start"] == report_period["start"] and r["period_end"] == report_period["end"]]
    totals = {
        "employees": len(persons),
        "licensed_staff": sum(1 for c in credentials),
        "licenses_expired": sum(1 for c in credentials if c["status"] == "Expired"),
        "licenses_due_30": sum(1 for c in credentials if c["status"] == "Renew within 30 days"),
        "scheduled_hours": sum(r["scheduled_hours"] or 0 for r in rp_rows),
        "paid_hours": sum(r["paid_hours"] for r in rp_rows),
        "people_hours_mismatch": len({r["person_id"] for r in rp_rows
                                      if r["status"] in (STATUS_PAID_MORE, STATUS_SCHED_MORE)}),
        "people_compared": len({r["person_id"] for r in rp_rows if r["status"] != STATUS_NOT_COMPARED}),
    }

    severity_counts: dict[str, int] = defaultdict(int)
    for c in conflicts:
        severity_counts[c.severity.name] += 1

    # Legacy shape kept for the API / smoke test
    legacy_discrepancies = [{
        "name": r["name"], "facility": r["facility"], "role": r["role"], "period": r["period"],
        "schedule_hours": r["scheduled_hours"], "payroll_hours": r["paid_hours"],
        "difference": round((r["scheduled_hours"] or 0) - r["paid_hours"], 1),
        "direction": "schedule > payroll" if r["status"] == STATUS_SCHED_MORE else "payroll > schedule",
    } for r in discrepancies]

    return {
        "generated_on": today.isoformat(),
        "report_period": report_period,
        "periods": period_info,
        "schedule_week": {"start": week["start"], "end": week["end"], "dated": week["dated"]},
        "notes": notes,
        "totals": totals,
        "staffing_matrix": staffing_matrix,
        "staffing_summary": {
            "by_role": dict(by_role),
            "by_facility": dict(by_facility),
            "total_employees": len(persons),
        },
        "hours_reconciliation": reconciliation,
        "hours_discrepancies": legacy_discrepancies,
        "credentials": credentials,
        "conflict_summary": {
            "total_conflicts": len(conflicts),
            "by_severity": dict(severity_counts),
            "top_conflicts": [{"description": c.description, "severity": c.severity.name}
                              for c in conflicts[:10]],
        },
        "audit_trail": audit,
    }
