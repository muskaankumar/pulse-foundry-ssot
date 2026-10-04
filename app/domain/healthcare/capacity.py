"""
Module A — Capacity / schedule view.

Builds a calendar of the week (facility -> shift slot -> day -> staff), checks
every scheduled shift against the person's license on that date, and for any
shift worked on an invalid license suggests who on the roster could take it
over while the license is sorted out.

Cover rules (deliberately simple so a scheduler can verify them by eye):
  * same license type (an RN shift needs an RN license; unlicensed roles need
    the same job title)
  * the cover's own license is valid on that date
  * not already working, and at least MIN_REST_HOURS off before and after
    (if nobody qualifies, back-to-back options are listed and marked as such)
  * ranked: same facility first, then whoever stays under 40h, then fewest hours
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

from app.domain.healthcare.schedule import (
    MIN_REST_HOURS, NURSE_LICENSES, OVERTIME_HOURS,
    absolute_interval, day_index, dedupe_schedule, is_off, license_state,
    parse_shift, resolve_week, rest_ok, role_code, schedule_rows_by_person,
    slot_sort_key,
)
from app.models.entities import FacilityEntity, NormalizedRecord, PersonEntity


def _as_date(iso: str, fallback: date) -> date:
    try:
        return datetime.strptime(iso, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return fallback


def build_capacity_dashboard(
    persons: list[PersonEntity],
    schedule_records: list[NormalizedRecord],
    facilities: list[FacilityEntity],
    today: date | None = None,
) -> dict:
    today = today or date.today()
    schedule_records = dedupe_schedule([r for r in schedule_records if r.source == "schedule"])
    week = resolve_week(schedule_records)
    rows_by_person = schedule_rows_by_person(persons, schedule_records)

    # ------------------------------------------------------------------
    # 1. One entry per worked shift
    # ------------------------------------------------------------------
    shifts: list[dict] = []
    busy: dict[str, list[tuple[int, int]]] = defaultdict(list)
    hours_by_person: dict[str, float] = defaultdict(float)
    unparsed: list[dict] = []

    for p in persons:
        for rec in rows_by_person.get(p.id, []):
            raw_shift = rec.data.get("shift", "")
            if is_off(raw_shift):
                continue
            win = parse_shift(raw_shift)
            day = rec.data.get("day", "")
            if win is None:
                unparsed.append({"name": p.canonical_name, "day": day, "value": raw_shift})
                continue
            idx = day_index(day, rec.data.get("date", ""), week)
            shift_date = week["days"][idx]["date"] if week["dated"] else ""
            on = _as_date(shift_date, today)
            state, days_left = license_state(p, on, today)
            interval = absolute_interval(win, idx)
            busy[p.id].append(interval)
            hours = float(rec.data.get("hours") or win.hours)
            hours_by_person[p.id] += hours
            facility = rec.data.get("facility") or p.facility or "Unknown"
            title = p.job_title or rec.data.get("job_title", "")
            shifts.append({
                "person_id": p.id,
                "name": p.canonical_name,
                "role": title or "Unknown",
                "role_code": role_code(title),
                "license_type": p.license_type,
                "facility": facility,
                "day": day,
                "day_index": idx,
                "date": shift_date,
                "shift": win.code,
                "shift_pretty": win.pretty,
                "slot_label": win.label,
                "tone": win.tone,
                "hours": hours,
                "interval": interval,
                "license_state": state,
                "license_expires": p.license_expiration,
                "days_left": days_left,
                "needs_cover": state == "expired",
                "source_record": rec.raw_record_id,
            })

    # ------------------------------------------------------------------
    # 2. Cover suggestions for shifts that can't legally be worked
    # ------------------------------------------------------------------
    person_by_id = {p.id: p for p in persons}
    blocked_ids = {s["person_id"] for s in shifts if s["needs_cover"]}
    cover_needed: list[dict] = []

    for s in shifts:
        if not s["needs_cover"]:
            continue
        flagged = person_by_id[s["person_id"]]
        on = _as_date(s["date"], today)

        # Anyone else with a valid nursing license on the floor at the same time?
        only_nurse = False
        if flagged.license_type in NURSE_LICENSES:
            a, b = s["interval"]
            others = [
                o for o in shifts
                if o["facility"] == s["facility"] and o["person_id"] != s["person_id"]
                and o["license_type"] in NURSE_LICENSES and not o["needs_cover"]
                and o["interval"][0] < b and a < o["interval"][1]
            ]
            only_nurse = not others

        candidates = []
        for c in persons:
            if c.id == flagged.id or c.id in blocked_ids:
                continue
            if flagged.license_type:
                if c.license_type != flagged.license_type:
                    continue
            elif not flagged.job_title or c.job_title != flagged.job_title:
                continue
            c_state, _ = license_state(c, on, today)
            if c_state in ("expired", "unknown"):
                continue
            c_busy = busy.get(c.id, [])
            if not rest_ok(s["interval"], c_busy, 0):
                continue  # already working at that time
            short_rest = not rest_ok(s["interval"], c_busy)
            before = hours_by_person.get(c.id, 0.0)
            after = before + s["hours"]
            candidates.append({
                "person_id": c.id,
                "name": c.canonical_name,
                "role": c.job_title,
                "role_code": role_code(c.job_title),
                "facility": c.facility,
                "same_facility": (c.facility or "") == s["facility"],
                "hours_before": before,
                "hours_after": after,
                "overtime": after > OVERTIME_HOURS,
                "license_expiring_soon": c_state == "expiring",
                "short_rest": short_rest,
            })
        # Proper rest first; back-to-back options are only shown when nobody
        # with a full rest break is available.
        rested = [c for c in candidates if not c["short_rest"]]
        candidates = rested or candidates
        candidates.sort(key=lambda x: (not x["same_facility"], x["overtime"], x["hours_before"], x["name"]))

        cover_needed.append({
            **{k: s[k] for k in ("person_id", "name", "role", "role_code", "facility", "day",
                                 "day_index", "date", "shift", "shift_pretty", "hours",
                                 "license_expires")},
            "reason": (
                f"License expired {s['license_expires']}" if s["license_expires"]
                else "No valid license on file"
            ),
            "only_nurse_on_shift": only_nurse,
            "candidates": candidates[:3],
        })

    cover_needed.sort(key=lambda c: (c["name"], c["day_index"], c["shift"]))

    # ------------------------------------------------------------------
    # 3. Calendar grid: facility -> slots (rows) x days (columns)
    # ------------------------------------------------------------------
    calendar: dict[str, dict] = {}
    fac_names = sorted(
        {s["facility"] for s in shifts}
        | {f.canonical_name for f in facilities if f.canonical_name}
    )
    for fac in fac_names:
        fac_shifts = [s for s in shifts if s["facility"] == fac]
        windows = {}
        for s in fac_shifts:
            windows.setdefault(s["shift"], parse_shift(s["shift"]))
        slots = sorted(windows.values(), key=slot_sort_key)
        cells: dict[str, dict[int, list[dict]]] = {w.code: defaultdict(list) for w in slots}
        for s in fac_shifts:
            cells[s["shift"]][s["day_index"]].append(s)
        for code in cells:
            for idx in cells[code]:
                cells[code][idx].sort(key=lambda e: (not e["needs_cover"], e["role_code"], e["name"]))
        calendar[fac] = {
            "slots": [{"code": w.code, "label": w.label, "pretty": w.pretty, "tone": w.tone,
                       "hours": w.hours} for w in slots],
            "cells": {code: dict(v) for code, v in cells.items()},
        }

    # ------------------------------------------------------------------
    # 4. Staff rows (person x day) for the "by person" view
    # ------------------------------------------------------------------
    staff_rows: dict[str, list[dict]] = defaultdict(list)
    shifts_by_person: dict[str, list[dict]] = defaultdict(list)
    for s in shifts:
        shifts_by_person[s["person_id"]].append(s)
    for p in persons:
        own = shifts_by_person.get(p.id, [])
        if not own and not rows_by_person.get(p.id):
            continue
        fac = own[0]["facility"] if own else (p.facility or "Unknown")
        lic_state, days_left = license_state(p, today, today)
        staff_rows[fac].append({
            "person_id": p.id,
            "name": p.canonical_name,
            "role": p.job_title,
            "role_code": role_code(p.job_title),
            "hours": hours_by_person.get(p.id, 0.0),
            "overtime": hours_by_person.get(p.id, 0.0) > OVERTIME_HOURS,
            "license_state": lic_state,
            "days_left": days_left,
            "by_day": {s["day_index"]: s for s in own},
        })
    for fac in staff_rows:
        staff_rows[fac].sort(key=lambda r: (r["role_code"], r["name"]))

    # ------------------------------------------------------------------
    # 5. Legacy outputs (API / smoke test compatibility)
    # ------------------------------------------------------------------
    legacy_grid: dict = {}
    for s in shifts:
        legacy_grid.setdefault(s["facility"], {}).setdefault(s["day"], {}).setdefault(s["shift"], []).append({
            "name": s["name"], "role": s["role"], "license_ok": not s["needs_cover"], "hours": s["hours"],
        })
    for fac in fac_names:
        legacy_grid.setdefault(fac, {})

    license_warnings: list[dict] = []
    seen: set[str] = set()
    for s in shifts:
        if s["person_id"] in seen or s["license_state"] not in ("expired", "expiring"):
            continue
        seen.add(s["person_id"])
        lic_no = person_by_id[s["person_id"]].license_number
        if s["license_state"] == "expired":
            license_warnings.append({
                "name": s["name"], "facility": s["facility"], "license_number": lic_no,
                "expired_on": s["license_expires"], "days_overdue": abs(s["days_left"] or 0),
                "severity": "EXPIRED",
            })
        else:
            license_warnings.append({
                "name": s["name"], "facility": s["facility"], "license_number": lic_no,
                "expires_on": s["license_expires"], "days_remaining": s["days_left"],
                "severity": "EXPIRING_SOON",
            })

    summary: dict[str, dict] = {}
    for fac in fac_names:
        fac_shifts = [s for s in shifts if s["facility"] == fac]
        roles_sched: dict[str, int] = defaultdict(int)
        for s in fac_shifts:
            roles_sched[s["role"]] += 1
        roster: dict[str, int] = defaultdict(int)
        for p in persons:
            if p.facility == fac and p.job_title:
                roster[p.job_title] += 1
        summary[fac] = {
            "total_staff_shifts": len(fac_shifts),
            "shifts_needing_cover": sum(1 for s in fac_shifts if s["needs_cover"]),
            "scheduled_hours": sum(s["hours"] for s in fac_shifts),
            "roles_scheduled": dict(roles_sched),
            "roles_on_roster": dict(roster),
        }

    for s in shifts:
        s["interval"] = list(s["interval"])

    return {
        "week": week,
        "calendar": calendar,
        "staff_rows": dict(staff_rows),
        "shifts": shifts,
        "cover_needed": cover_needed,
        "unparsed_shifts": unparsed,
        "rules": {"min_rest_hours": MIN_REST_HOURS, "overtime_hours": OVERTIME_HOURS},
        "facilities": legacy_grid,
        "summary": summary,
        "license_warnings": license_warnings,
    }
