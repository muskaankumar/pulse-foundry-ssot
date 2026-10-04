"""
Review layer — turns reconciliation output into two things people act on:

1. A status per person built from concrete checks (found in each system,
   records agree, license valid, hours match). This replaces the old match
   percentage: the status only says whether something is missing or needs a
   look, and every check says why.

2. A list of issues, each routed to the team that owns the fix:
     HR          employee records, license data, missing people
     Payroll     paid hours vs schedule, job/facility codes in payroll
     Scheduling  shifts that need someone else while a license is sorted out
"""

from __future__ import annotations

from datetime import date

from app.domain.healthcare.schedule import license_state
from app.models.entities import Conflict, FieldStatus, NormalizedRecord, PersonEntity

TEAMS = ["HR", "Payroll", "Scheduling"]
TEAM_SCOPE = {
    "HR": "Employee records, licenses and anyone missing from the roster",
    "Payroll": "Paid hours that don't match the schedule, job and facility codes",
    "Scheduling": "Shifts someone else needs to cover while a license is resolved",
}

LEVELS = {"action": 0, "review": 1, "heads_up": 2}
LEVEL_LABEL = {"action": "Action needed", "review": "Needs a look", "heads_up": "Heads-up"}
STATUS_LABEL = {"action": "Action needed", "review": "Needs a look", "ok": "Up to date"}

SOURCE_LABEL = {"hr": "HR roster", "payroll": "Payroll", "licenses": "Licensing board",
                "schedule": "Schedule", "computed": "Computed"}

FIELD_LABEL = {
    "first_name": "First name", "last_name": "Last name", "employee_id": "Employee ID",
    "job_title": "Role", "facility": "Facility", "phone": "Phone",
    "license_number": "License number", "license_type": "License type",
    "license_expiration": "License expiry", "hire_date": "Hire date",
}


def _fmt_date(iso: str) -> str:
    from datetime import datetime
    try:
        d = datetime.strptime(iso, "%Y-%m-%d").date()
        return f"{d.strftime('%b')} {d.day}, {d.year}"
    except (TypeError, ValueError):
        return iso or "—"


def _evidence(values_by_source: dict) -> list[dict]:
    return [{"source": SOURCE_LABEL.get(s, s), "value": v} for s, v in values_by_source.items()]


def build_review(
    persons: list[PersonEntity],
    all_records: list[NormalizedRecord],
    conflicts: list[Conflict],
    capacity: dict | None = None,
    compliance: dict | None = None,
    today: date | None = None,
) -> dict:
    today = today or date.today()
    capacity = capacity or {}
    compliance = compliance or {}

    uploaded = {r.source for r in all_records}
    conflicts_by_person: dict[str, list[Conflict]] = {}
    for c in conflicts:
        conflicts_by_person.setdefault(c.person_id, []).append(c)

    cover_by_person: dict[str, list[dict]] = {}
    for c in capacity.get("cover_needed", []):
        cover_by_person.setdefault(c["person_id"], []).append(c)

    hours_by_person: dict[str, list[dict]] = {}
    for r in compliance.get("hours_reconciliation", []):
        hours_by_person.setdefault(r["person_id"], []).append(r)

    issues: list[dict] = []
    people: dict[str, dict] = {}

    for p in persons:
        own: list[dict] = []
        checks: list[dict] = []
        sources = set(p.sources) or set()

        def add(key, level, category, teams, title, detail, next_step, evidence=None):
            own.append({
                "id": f"{p.id}:{key}",
                "person_id": p.id, "name": p.canonical_name,
                "facility": p.facility or "—", "role": p.job_title or "—",
                "level": level, "level_label": LEVEL_LABEL[level],
                "category": category, "teams": teams,
                "title": title, "detail": detail, "next_step": next_step,
                "evidence": evidence or [],
            })

        # ---------------- presence in each system ----------------
        in_hr = "hr" in sources
        checks.append({"label": "In HR roster", "state": "pass" if in_hr else "fail",
                       "detail": "" if in_hr else "No employee record"})
        if not in_hr:
            if "payroll" in sources:
                add("missing_hr", "action", "Missing record", ["HR", "Payroll"],
                    "Paid, but not in the HR roster",
                    f"{p.canonical_name} has payroll entries but no HR record.",
                    "Confirm they're employed and add them to HR, or hold the payment.")
            elif sources == {"schedule"}:
                add("missing_hr", "action", "Missing record", ["HR", "Scheduling"],
                    "On the schedule, but not in HR or payroll",
                    f"{p.canonical_name} is scheduled this week but no other system knows them.",
                    "Check the name on the schedule; if they're new, add them to HR before they work.")
            else:
                add("missing_hr", "review", "Missing record", ["HR"],
                    "License on file, but no employee record",
                    f"The licensing board lists {p.canonical_name}, but HR has no record.",
                    "Confirm whether they're on staff and add or remove accordingly.")

        if "payroll" in uploaded:
            in_pay = "payroll" in sources
            checks.append({"label": "In payroll", "state": "pass" if in_pay else "warn",
                           "detail": "" if in_pay else "No payroll entries"})
            if in_hr and not in_pay:
                add("missing_payroll", "review", "Missing record", ["Payroll"],
                    "In HR, but no payroll entries",
                    f"{p.canonical_name} is on the HR roster but wasn't paid in any uploaded period.",
                    "Check whether they're on leave, newly hired, or missing from payroll.")
        else:
            checks.append({"label": "In payroll", "state": "na", "detail": "Payroll not uploaded"})

        needs_license = bool(p.license_number or p.license_expiration)
        if "licenses" in uploaded and needs_license:
            on_board = "licenses" in sources
            checks.append({"label": "License found with the board", "state": "pass" if on_board else "warn",
                           "detail": "" if on_board else "Not in the licensing file"})
            if not on_board and in_hr:
                add("missing_board", "review", "Credential", ["HR"],
                    "License can't be verified",
                    f"HR lists license {p.license_number or '(no number)'} but the licensing file has no match.",
                    "Look the license up with the state board and fix the number in HR if it's wrong.")
        elif needs_license:
            checks.append({"label": "License found with the board", "state": "na",
                           "detail": "Licensing file not uploaded"})

        if "schedule" in uploaded:
            on_sched = "schedule" in sources
            checks.append({"label": "On this week's schedule", "state": "pass" if on_sched else "na",
                           "detail": "" if on_sched else "Not scheduled this week"})

        # ---------------- field agreement ----------------
        mismatched: list[str] = []
        for c in conflicts_by_person.get(p.id, []):
            if c.field_name == "_entity" or "computed" in c.values_by_source:
                continue
            label = FIELD_LABEL.get(c.field_name, c.field_name)
            vals = c.values_by_source
            ev = _evidence(vals)

            if c.status == FieldStatus.MISSING:
                if c.field_name in ("license_number", "license_expiration"):
                    add(f"missing_{c.field_name}", "review", "Missing record", ["HR"],
                        f"{label} missing from a system",
                        c.description.split(": ", 1)[-1],
                        f"Fill in the {label.lower()} so every system has it.", ev)
                else:
                    add(f"missing_{c.field_name}", "heads_up", "Missing record", ["HR"],
                        f"{label} missing", c.description.split(": ", 1)[-1],
                        f"Add the {label.lower()} to the record.", ev)
                mismatched.append(label)
                continue

            mismatched.append(label)
            if c.field_name == "license_expiration":
                board = vals.get("licenses", "")
                add("lic_exp_mismatch", "review", "Record mismatch", ["HR"],
                    "HR has a different license expiry than the board",
                    f"HR says {_fmt_date(vals.get('hr', ''))}, the licensing board says {_fmt_date(board)}.",
                    f"Update HR to the board's date ({_fmt_date(board)}). The board is the source of truth.", ev)
            elif c.field_name == "license_number":
                add("lic_no_mismatch", "action", "Record mismatch", ["HR"],
                    "License numbers don't match",
                    "The systems list different license numbers for the same person.",
                    "Verify the number with the state board and correct whichever system is wrong.", ev)
            elif c.field_name in ("job_title", "facility"):
                teams = ["HR", "Payroll"] if "payroll" in vals else ["HR"]
                add(f"{c.field_name}_mismatch", "review", "Record mismatch", teams,
                    f"{label} differs between systems",
                    ", ".join(f"{e['source']}: {e['value']}" for e in ev),
                    f"Confirm the correct {label.lower()} and update the system that's out of date.", ev)
            elif c.field_name == "license_type":
                add("lic_type_mismatch", "review", "Record mismatch", ["HR"],
                    "License type differs between systems",
                    ", ".join(f"{e['source']}: {e['value']}" for e in ev),
                    "Check the license type with the board and update HR.", ev)
            else:
                add(f"{c.field_name}_mismatch", "heads_up", "Record mismatch", ["HR"],
                    f"{label} differs between systems",
                    ", ".join(f"{e['source']}: {e['value']}" for e in ev),
                    f"Correct the {label.lower()} in HR.", ev)

        checks.append({
            "label": "Records agree across systems",
            "state": "warn" if mismatched else ("pass" if len(sources - {"schedule"}) > 1 else "na"),
            "detail": ", ".join(mismatched) if mismatched else "",
        })

        # ---------------- license validity ----------------
        if needs_license:
            state, days_left = license_state(p, today, today)
            exp = _fmt_date(p.license_expiration)
            if state == "expired":
                checks.append({"label": "License valid", "state": "fail", "detail": f"Expired {exp}"})
                add("lic_expired", "action", "Credential", ["HR"],
                    f"License expired {exp}",
                    f"{p.license_type or 'License'} {p.license_number} expired "
                    f"{abs(days_left or 0)} days ago.",
                    "Contact them about renewal and confirm the new expiry with the board. "
                    "They can't work licensed shifts until it's renewed.")
            elif state == "expiring":
                checks.append({"label": "License valid", "state": "warn",
                               "detail": f"Renews in {days_left} days ({exp})"})
                add("lic_due_30", "review", "Credential", ["HR"],
                    f"License renews in {days_left} days",
                    f"{p.license_type or 'License'} {p.license_number} expires {exp}.",
                    "Remind them to renew now so their shifts aren't affected.")
            elif state == "valid" and days_left is not None and days_left <= 90:
                checks.append({"label": "License valid", "state": "pass",
                               "detail": f"Renews in {days_left} days"})
                add("lic_due_90", "heads_up", "Credential", ["HR"],
                    f"License renews in {days_left} days",
                    f"{p.license_type or 'License'} {p.license_number} expires {exp}.",
                    "Add a renewal reminder.")
            elif state == "unknown":
                checks.append({"label": "License valid", "state": "warn", "detail": "Expiry date unreadable"})
                add("lic_unknown", "review", "Credential", ["HR"],
                    "License expiry can't be read",
                    f"The expiry value '{p.license_expiration}' isn't a date.",
                    "Correct the expiry date in HR and the licensing file.")
            else:
                checks.append({"label": "License valid", "state": "pass",
                               "detail": f"Until {exp}" if p.license_expiration else ""})

        # ---------------- schedule cover ----------------
        covers = cover_by_person.get(p.id, [])
        if covers:
            alone = sum(1 for c in covers if c["only_nurse_on_shift"])
            with_option = sum(1 for c in covers if c["candidates"])
            detail = f"{len(covers)} shift{'s' if len(covers) != 1 else ''} this week while the license is invalid."
            if alone:
                detail += f" On {alone} of them they're the only licensed nurse on the floor."
            add("needs_cover", "action", "Coverage", ["Scheduling"],
                f"{len(covers)} shift{'s' if len(covers) != 1 else ''} need someone else",
                detail,
                f"Reassign these shifts. Suggested cover is available for {with_option} of {len(covers)}; "
                f"see the Schedule tab.",
                [{"source": "Schedule", "value": f"{c['day'][:3]} {c['shift']}"} for c in covers])
            checks.append({"label": "Shifts can be worked", "state": "fail",
                           "detail": f"{len(covers)} need cover"})
        elif "schedule" in sources:
            checks.append({"label": "Shifts can be worked", "state": "pass", "detail": ""})

        # ---------------- hours vs payroll ----------------
        rows = hours_by_person.get(p.id, [])
        compared = [r for r in rows if r["status"] != "Not compared"]
        bad = [r for r in compared if r["status"] in ("Paid more than scheduled", "Scheduled more than paid")]
        if bad and not in_hr:
            # Already flagged as a missing person; the hours follow from that.
            checks.append({"label": "Paid hours match the schedule", "state": "warn",
                           "detail": "Can't be checked until they're in HR"})
        elif bad:
            for r in bad:
                diff = abs(r["difference"])
                more = "paid" if r["status"] == "Paid more than scheduled" else "scheduled"
                add(f"hours:{r['period_start']}", "review", "Hours", ["Payroll"],
                    f"Paid {r['paid_hours']:g}h, scheduled {r['scheduled_hours']:g}h",
                    f"{diff:g} more hours {more} for {r['period']}.",
                    "Check timesheets for this period and correct payroll or the schedule.",
                    [{"source": "Payroll", "value": f"{r['paid_hours']:g}h"},
                     {"source": "Schedule", "value": f"{r['scheduled_hours']:g}h"}])
            checks.append({"label": "Paid hours match the schedule", "state": "warn",
                           "detail": "; ".join(f"{r['period']}: {r['difference']:+g}h" for r in bad)})
        elif compared:
            checks.append({"label": "Paid hours match the schedule", "state": "pass", "detail": ""})
        elif rows:
            checks.append({"label": "Paid hours match the schedule", "state": "na",
                           "detail": "No pay period overlaps the schedule"})

        # ---------------- person status ----------------
        own.sort(key=lambda i: LEVELS[i["level"]])
        worst = own[0]["level"] if own else None
        status = worst if worst in ("action", "review") else "ok"
        people[p.id] = {
            "status": status,
            "status_label": STATUS_LABEL[status],
            "reason": own[0]["title"] if own and status != "ok" else (
                own[0]["title"] if own else "All checks passed"),
            "checks": checks,
            "issue_ids": [i["id"] for i in own],
            "checks_passed": sum(1 for c in checks if c["state"] == "pass"),
            "checks_total": sum(1 for c in checks if c["state"] != "na"),
        }
        issues.extend(own)

    issues.sort(key=lambda i: (LEVELS[i["level"]], i["name"], i["title"]))

    by_team = {t: {lv: 0 for lv in LEVELS} for t in TEAMS}
    for i in issues:
        for t in i["teams"]:
            by_team.setdefault(t, {lv: 0 for lv in LEVELS})[i["level"]] += 1
    by_level = {lv: sum(1 for i in issues if i["level"] == lv) for lv in LEVELS}

    return {
        "issues": issues,
        "people": people,
        "teams": TEAMS,
        "team_scope": TEAM_SCOPE,
        "counts": {"by_team": by_team, "by_level": by_level},
    }
