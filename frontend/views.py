"""
Screens for the dashboard. Each render_* function draws one area and reads
everything it needs from the shared context built in dashboard.py.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime

import streamlit as st

from app.domain.healthcare.compliance_export import build_compliance_workbook, build_hours_csv
from app.domain.healthcare.schedule import role_code
from frontend.ui import (
    check_icon, empty, esc, html, note, pill, section, segmented, status_pill, table, team_chip,
)

SOURCE_LABEL = {"hr": "HR roster", "payroll": "Payroll", "licenses": "Licensing board", "schedule": "Schedule"}
SOURCE_ORDER = ["hr", "payroll", "licenses", "schedule"]
LEVEL_ORDER = [("action", "Action needed"), ("review", "Needs a look"), ("heads_up", "Heads-up")]


def fmt_date(iso: str, with_year: bool = True) -> str:
    try:
        d = datetime.strptime(iso, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return iso or "—"
    return f"{d.strftime('%b')} {d.day}, {d.year}" if with_year else f"{d.strftime('%b')} {d.day}"


def hrs(v) -> str:
    if v is None:
        return "—"
    return f"{v:g}h"


# ---------------------------------------------------------------------------
# Staff lookup
# ---------------------------------------------------------------------------

# canonical field -> (label, {source: mapped raw key}, normalized key)
PARSE_FIELDS = [
    ("Name", {"hr": ("first_name", "last_name"), "payroll": "employee_name",
              "licenses": "name_on_license", "schedule": "employee_name"}, "canonical_name"),
    ("Employee ID", {"hr": "employee_id"}, "employee_id"),
    ("Role", {"hr": "job_title", "payroll": "job_code", "schedule": "role"}, "job_title"),
    ("Facility", {"hr": "facility", "payroll": "facility_code", "schedule": "facility"}, "facility"),
    ("Phone", {"hr": "phone"}, "phone"),
    ("License number", {"hr": "license_number", "licenses": "license_number"}, "license_number"),
    ("License type", {"licenses": "license_type"}, "license_type"),
    ("License expiry", {"hr": "license_expiration", "licenses": "expiration_date"}, "license_expiration"),
    ("Hire date", {"hr": "hire_date"}, "hire_date"),
]


def search_options(ctx) -> dict[str, str]:
    """label -> person id. Labels carry ID and license so typing either finds the person."""
    opts = {}
    for p in sorted(ctx["persons"], key=lambda x: x.canonical_name):
        bits = [p.canonical_name]
        extra = ", ".join(b for b in (p.job_title, p.facility) if b)
        if extra:
            bits.append(f"({extra})")
        ids = " ".join(b for b in (p.employee_id, p.license_number) if b)
        if ids:
            bits.append(f"· {ids}")
        opts[" ".join(bits)] = p.id
    return opts


def render_profile(ctx, pid: str) -> None:
    p = ctx["person_by_id"][pid]
    info = ctx["review"]["people"].get(pid, {})
    issues = [i for i in ctx["review"]["issues"] if i["person_id"] == pid]
    raw_index, norm_index = ctx["raw_index"], ctx["norm_index"]

    with st.container(border=True):
        top_l, top_r = st.columns([5, 1])
        with top_l:
            meta = ", ".join(b for b in (p.job_title, p.facility) if b) or "No role on file"
            html(f'''
                <div class="pf-prof-head">
                  <h2>{esc(p.canonical_name)}</h2>{status_pill(info.get("status", "ok"))}
                  <span class="meta">{esc(meta)}</span>
                </div>
                <div class="pf-prof-reason">
                  {esc(info.get("checks_passed", 0))} of {esc(info.get("checks_total", 0))} checks pass.
                  {"" if info.get("status") == "ok" else esc(info.get("reason", ""))}
                </div>''')
        with top_r:
            if st.button("Close", key="close_profile", use_container_width=True):
                st.session_state.lookup = None
                st.rerun()

        left, right = st.columns([2, 3], gap="large")

        # ------------------------------ checks + issues
        with left:
            html('<div class="pf-section" style="margin-top:6px"><h3>Checks</h3></div>')
            rows = []
            for c in info.get("checks", []):
                det = f'<span class="det">{esc(c["detail"])}</span>' if c.get("detail") else ""
                rows.append(f'<div class="pf-check">{check_icon(c["state"])}'
                            f'<div><span class="lbl">{esc(c["label"])}</span>{det}</div></div>')
            html(f'<div class="pf-checks">{"".join(rows)}</div>')

            if issues:
                html('<div class="pf-section"><h3>What needs doing</h3></div>')
                for i in issues:
                    html(f'''<div class="pf-issue {i["level"]}" style="margin-bottom:10px">
                        <div class="t">{esc(i["title"])}</div>
                        <div class="who">{"".join(team_chip(t) for t in i["teams"])}</div>
                        <div class="nx"><em>Next</em>{esc(i["next_step"])}</div></div>''')

        # ------------------------------ parsing view
        with right:
            linked = [raw_index[r] for r in p.source_records if r in raw_index]
            sched = [raw_index[r] for r in p.schedule_records if r in raw_index]
            first_by_source = {}
            for raw in linked + sched:
                first_by_source.setdefault(raw.source, raw)
            present = [s for s in SOURCE_ORDER if s in first_by_source]

            html('<div class="pf-section" style="margin-top:6px"><h3>How each system had it</h3>'
                 '<p>Original value as written in each file. Grey text is the cleaned value when it changed.</p></div>')
            headers = ["Field"] + [SOURCE_LABEL[s] for s in present] + ["Unified record"]
            trows, classes = [], []
            for label, keys, norm_key in PARSE_FIELDS:
                cells, cleaned_vals = [], set()
                any_val = False
                for src in present:
                    raw = first_by_source[src]
                    mapped = raw.data.get("mapped", {})
                    key = keys.get(src)
                    if key is None:
                        cells.append('<span class="muted">—</span>')
                        continue
                    if isinstance(key, tuple):
                        original = " ".join(str(mapped.get(k, "")).strip() for k in key).strip()
                    else:
                        original = str(mapped.get(key, "") or "").strip()
                    norm = norm_index.get(raw.id)
                    cleaned = str(norm.data.get(norm_key, "")) if norm else ""
                    if not original:
                        cells.append('<span class="muted">missing</span>')
                        continue
                    any_val = True
                    if cleaned:
                        cleaned_vals.add(cleaned.lower())
                    sub = (f'<span class="sub">{esc(cleaned)}</span>'
                           if cleaned and cleaned != original else "")
                    cells.append(f"{esc(original)}{sub}")
                if not any_val:
                    continue
                unified = getattr(p, norm_key, "") if norm_key != "canonical_name" else p.canonical_name
                trows.append([f"<b>{esc(label)}</b>"] + cells + [f"<b>{esc(unified) or '—'}</b>"])
                classes.append("hl" if len(cleaned_vals) > 1 else "")
            html(table(headers, trows, row_classes=classes, compact=True))
            if any(c == "hl" for c in classes):
                html('<div class="pf-steps">Highlighted rows disagree between systems. '
                     'The unified record uses the licensing board for license data and HR for everything else.</div>')

            # How records were linked
            html('<div class="pf-section"><h3>How the records were linked</h3></div>')
            lines = []
            hr_raw = first_by_source.get("hr")
            if hr_raw:
                lines.append(f'<div class="pf-link"><b>HR roster</b> row from {esc(hr_raw.source_file)} is the anchor record.</div>')
            for ev in p.match_evidence:
                raw = raw_index.get(ev["record_id"])
                written = ""
                if raw:
                    m = raw.data.get("mapped", {})
                    written = m.get("employee_name") or m.get("name_on_license") or ""
                why = ", ".join(ev["signals"]) or "combined score above threshold"
                where = f' in {esc(raw.source_file)}' if raw else ""
                lines.append(
                    f'<div class="pf-link"><b>{esc(ev["source_label"])}</b>{where}: '
                    f'“{esc(written or ev["name_as_written"])}” linked to {esc(ev["linked_to_source"])} by {esc(why)}.</div>')
            if sched:
                first = sched[0]
                written = first.data.get("mapped", {}).get("employee_name", "")
                lines.append(
                    f'<div class="pf-link"><b>Schedule</b> in {esc(first.source_file)}: “{esc(written)}” '
                    f'matched by name, {len(sched)} day rows.</div>')
            if not lines:
                lines.append('<div class="pf-link">Only found in one system, so there was nothing to link.</div>')
            html("".join(lines))

            # Column detection + cleanup steps
            with st.expander("Parsing details: column mapping and cleanup steps"):
                for src in present:
                    raw = first_by_source[src]
                    colmap = raw.data.get("column_mapping") or {}
                    norm = norm_index.get(raw.id)
                    steps = (norm.normalization_log if norm else [])[:8]
                    cm = ", ".join(f"{esc(v)} → {esc(k)}" for k, v in colmap.items()) if colmap else (
                        "Table read from PDF page" if raw.source_file.lower().endswith(".pdf") else "Wide schedule columns unpivoted by day")
                    html(f'''<div style="margin:4px 0 12px"><b>{SOURCE_LABEL[src]}</b>
                        <span class="pf-tag">{esc(raw.source_file)}</span>
                        <div class="pf-steps"><div><b>Columns:</b> {cm}</div>
                        {"".join(f"<div>{esc(s)}</div>" for s in steps)}</div></div>''')

            # This week
            shifts = [s for s in ctx["capacity"].get("shifts", []) if s["person_id"] == pid]
            if ctx["capacity"].get("week") and (shifts or sched):
                week = ctx["capacity"]["week"]
                by_idx = {s["day_index"]: s for s in shifts}
                cells = []
                for i, d in enumerate(week["days"]):
                    s = by_idx.get(i)
                    if s:
                        cls = "pf-shift alert" if s["needs_cover"] else f"pf-shift tone-{s['tone']}"
                        inner = f'<div class="{cls}">{esc(s["shift"])}</div>'
                    else:
                        inner = '<span class="muted" style="color:var(--pf-line-2)">off</span>'
                    cells.append(f'<div class="d"><div class="n">{d["short"]} {esc(d["sub"])}</div><div class="s">{inner}</div></div>')
                total = sum(s["hours"] for s in shifts)
                html(f'<div class="pf-section"><h3>This week</h3><p>{total:g} scheduled hours</p></div>'
                     f'<div class="pf-week">{"".join(cells)}</div>')


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------

def _issue_csv(issues: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["level", "team", "name", "facility", "role", "issue", "details", "next_step"])
    for i in issues:
        w.writerow([i["level_label"], ", ".join(i["teams"]), i["name"], i["facility"], i["role"],
                    i["title"], i["detail"], i["next_step"]])
    return buf.getvalue().encode("utf-8")


def render_review(ctx) -> None:
    review = ctx["review"]
    issues = review["issues"]
    counts = review["counts"]["by_team"]
    follow = st.session_state.follow_up

    def count_for(team):
        return len(issues) if team == "All" else sum(counts.get(team, {}).values())

    options = ["All"] + review["teams"]
    team = segmented(
        "Show issues for", options, key="team_view", default="All",
        format_func=lambda t: f"{'Everything' if t == 'All' else t} ({count_for(t)})",
    )
    scope = ("Every open issue across all teams." if team == "All"
             else review["team_scope"].get(team, ""))
    html(f'<div class="pf-scope">{esc(scope)}</div>')

    shown = issues if team == "All" else [i for i in issues if team in i["teams"]]
    flagged = [i for i in shown if i["id"] in follow]

    if flagged:
        c1, c2 = st.columns([3, 1])
        with c1:
            html(f'<div class="pf-follow"><b>{len(flagged)}</b> on the follow-up list'
                 f'{"" if team == "All" else " for " + esc(team)}. Download it to share with the team.</div>')
        with c2:
            st.download_button(
                "Download follow-up list", _issue_csv(flagged),
                file_name=f"follow_up_{team.lower()}_{ctx['today'].isoformat()}.csv",
                mime="text/csv", use_container_width=True, key=f"dl_follow_{team}",
            )

    if not shown:
        empty("Nothing for this team right now.")
        return

    for level, label in LEVEL_ORDER:
        group = [i for i in shown if i["level"] == level]
        if not group:
            continue
        hint = {"action": "Blocking. Someone should act today.",
                "review": "Something is missing or doesn't line up.",
                "heads_up": "Not urgent. Coming up soon."}[level]
        if level == "heads_up":
            with st.expander(f"{label} ({len(group)}). {hint}", expanded=False):
                for i in group:
                    _issue_card(i, follow, team)
            continue
        html(f'<div class="pf-level-head"><h4>{label}</h4><span>{len(group)}, {esc(hint.lower())}</span></div>')
        for i in group:
            _issue_card(i, follow, team)


def _issue_card(i: dict, follow: set, team: str) -> None:
    with st.container(border=True):
        c1, c2 = st.columns([5, 1.25], vertical_alignment="center") if _supports_valign() else st.columns([5, 1.25])
        with c1:
            ev = "".join(f'<span>{esc(e["source"])}: <b>{esc(e["value"])}</b></span>' for e in i["evidence"][:6])
            html(f'''<div class="pf-issue {i["level"]}">
                <div class="t">{esc(i["title"])}</div>
                <div class="who"><b>{esc(i["name"])}</b>, {esc(i["role"])}, {esc(i["facility"])}
                  &nbsp;{"".join(team_chip(t) for t in i["teams"])}</div>
                <div class="d">{esc(i["detail"])}</div>
                {f'<div class="ev">{ev}</div>' if ev else ""}
                <div class="nx"><em>Next</em>{esc(i["next_step"])}</div>
            </div>''')
        with c2:
            on = i["id"] in follow
            if st.button("On follow-up list ✓" if on else "Add to follow-up",
                         key=f"fu_{team}_{i['id']}", use_container_width=True,
                         type="primary" if on else "secondary"):
                if on:
                    follow.discard(i["id"])
                else:
                    follow.add(i["id"])
                st.rerun()


def _supports_valign() -> bool:
    import inspect
    try:
        return "vertical_alignment" in inspect.signature(st.columns).parameters
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------

def render_schedule(ctx) -> None:
    cap = ctx["capacity"]
    if not cap.get("shifts"):
        empty("Upload a schedule (CSV or PDF) to see the week here.")
        return

    week = cap["week"]
    facilities = list(cap["calendar"].keys())
    today_iso = ctx["today"].isoformat()

    c1, c2, c3 = st.columns([2.2, 1.6, 2.2])
    with c1:
        fac = segmented("Facility", facilities, key="sched_fac", default=facilities[0])
    with c2:
        view = segmented("View", ["Calendar", "By person"], key="sched_view", default="Calendar")
    with c3:
        roles = sorted({s["role_code"] for s in cap["shifts"] if s["facility"] == fac})
        picked = st.multiselect("Roles", roles, default=[], key=f"sched_roles_{fac}",
                                placeholder="All roles", label_visibility="collapsed")
    only_attention = st.toggle("Focus on shifts that need cover", key="sched_focus", value=False)

    title = (f"Week of {fmt_date(week['start'], False)} – {fmt_date(week['end'])}"
             if week["dated"] else "Weekly schedule (no dates in the file)")
    fac_cover = [c for c in cap["cover_needed"] if c["facility"] == fac]
    sub = (f"{len(fac_cover)} shift{'s' if len(fac_cover) != 1 else ''} at {fac} need someone else."
           if fac_cover else f"Every shift at {fac} can be worked as scheduled.")
    section(title, sub)

    html('''<div class="pf-legend">
        <span><i class="tone-day"></i>Day</span><span><i class="tone-evening"></i>Evening</span>
        <span><i class="tone-night"></i>Night</span><span><i class="tone-long"></i>12-hour day</span>
        <span><i style="background:var(--pf-red-bg);box-shadow:inset 0 0 0 1px var(--pf-red-line)"></i>License invalid, needs cover</span>
        <span><i style="background:#fff;box-shadow:inset 0 0 0 1px var(--pf-amber-line)"></i>License renews within 30 days</span>
    </div>''')

    def visible(s):
        return not picked or s["role_code"] in picked

    if view == "Calendar":
        _calendar(cap, fac, week, today_iso, visible, only_attention)
    else:
        _by_person(cap, fac, week, today_iso, visible, only_attention)

    _cover_panel(ctx, cap, fac)


def _chip(s: dict, dim: bool) -> str:
    cls = ["pf-chip", f"tone-{s['tone']}"]
    flag = ""
    if s["needs_cover"]:
        cls = ["pf-chip", "alert"]
        flag = '<span class="flag">Needs cover</span>'
    elif s["license_state"] == "expiring":
        cls.append("warn")
    if dim:
        cls.append("dim")
    title = f'{s["name"]}, {s["role"]}, {s["shift_pretty"]}'
    if s["license_state"] == "expiring":
        title += f', license renews {s["license_expires"]}'
    return (f'<div class="{" ".join(cls)}" title="{esc(title)}"><span class="rc">{esc(s["role_code"])}</span>'
            f'<span class="nm">{esc(s["name"])}</span>{flag}</div>')


def _calendar(cap, fac, week, today_iso, visible, only_attention) -> None:
    cal = cap["calendar"].get(fac, {"slots": [], "cells": {}})
    parts = ['<div class="pf-cal-h"></div>']
    for i, d in enumerate(week["days"]):
        day_shifts = [s for code in cal["cells"] for s in cal["cells"][code].get(i, []) if visible(s)]
        n_alert = sum(1 for s in day_shifts if s["needs_cover"])
        cnt = (f'<span class="cnt alert">{n_alert} need cover</span>' if n_alert
               else f'<span class="cnt">{len(day_shifts)} on shift</span>')
        today = " today" if d["date"] == today_iso else ""
        parts.append(f'<div class="pf-cal-h{today}"><span class="dow">{d["short"]}</span>'
                     f'<span class="dt">{"Today" if today else esc(d["sub"])}</span>{cnt}</div>')
    for slot in cal["slots"]:
        parts.append(f'<div class="pf-slot tone-{slot["tone"]}"><b>{esc(slot["label"])}</b>'
                     f'<span>{esc(slot["pretty"])}</span></div>')
        for i in range(7):
            entries = [s for s in cal["cells"].get(slot["code"], {}).get(i, []) if visible(s)]
            alert = any(s["needs_cover"] for s in entries)
            if entries:
                chips = "".join(_chip(s, only_attention and not alert) for s in entries)
            else:
                chips = '<span class="none">—</span>'
            parts.append(f'<div class="pf-cell{" alert" if alert else ""}">{chips}</div>')
    html(f'<div class="pf-cal-wrap"><div class="pf-cal">{"".join(parts)}</div></div>')


def _by_person(cap, fac, week, today_iso, visible, only_attention) -> None:
    rows = [r for r in cap["staff_rows"].get(fac, []) if visible(r)]
    if only_attention:
        rows = [r for r in rows if any(s["needs_cover"] for s in r["by_day"].values())]
    if not rows:
        empty("No one matches these filters.")
        return
    otl = cap["rules"]["overtime_hours"]
    parts = ['<div class="h">Staff</div>']
    for d in week["days"]:
        today = " today" if d["date"] == today_iso else ""
        parts.append(f'<div class="h{today}">{d["short"]}<span>{"Today" if today else esc(d["sub"])}</span></div>')
    parts.append('<div class="h" style="align-items:flex-end">Hours</div>')
    for r in rows:
        alert_row = any(s["needs_cover"] for s in r["by_day"].values())
        rc = " row-alert" if alert_row else ""
        lic = ""
        if r["license_state"] == "expired":
            lic = '<span class="pf-tag red">License expired</span>'
        elif r["license_state"] == "expiring":
            lic = f'<span class="pf-tag amber">Renews in {r["days_left"]}d</span>'
        parts.append(f'<div class="who{rc}"><b>{esc(r["name"])}</b><span>{esc(r["role_code"])}, '
                     f'{esc(r["role"])} {lic}</span></div>')
        for i in range(7):
            s = r["by_day"].get(i)
            if s:
                cls = "pf-shift alert" if s["needs_cover"] else f"pf-shift tone-{s['tone']}"
                parts.append(f'<div class="{rc.strip()}"><div class="{cls}" title="{esc(s["shift_pretty"])}">{esc(s["shift"])}</div></div>')
            else:
                parts.append(f'<div class="{rc.strip()}"><span class="off">off</span></div>')
        ot = f'<span class="pf-tag amber">OT</span>' if r["hours"] > otl else ""
        parts.append(f'<div class="hrs{rc}">{r["hours"]:g}h {ot}</div>')
    html(f'<div class="pf-cal-wrap"><div class="pf-pgrid">{"".join(parts)}</div></div>')
    html(f'<div class="pf-steps" style="margin-top:6px">OT marks anyone scheduled over {otl} hours this week.</div>')


def _cover_panel(ctx, cap, fac) -> None:
    needs = [c for c in cap["cover_needed"] if c["facility"] == fac]
    if not needs:
        return
    rules = cap["rules"]
    section("Shifts that need someone else",
            "These people can't work licensed shifts until their license is renewed. "
            "Suggestions are staff with the same license, not already working, ranked by same facility and fewest hours.")
    by_person: dict[str, list[dict]] = {}
    for c in needs:
        by_person.setdefault(c["person_id"], []).append(c)

    for pid, items in by_person.items():
        first = items[0]
        alone = sum(1 for c in items if c["only_nurse_on_shift"])
        with st.container(border=True):
            html(f'''<div class="pf-cover-head"><b>{esc(first["name"])}</b>
                {pill(first["reason"], "red")}
                <span>{esc(first["role"])}, {esc(first["facility"])}. {len(items)} shift{"s" if len(items) != 1 else ""} to reassign
                {f", only licensed nurse on {alone}" if alone else ""}.</span></div>''')
            rows = []
            for c in items:
                when = f'<b>{esc(c["day"][:3])} {esc(fmt_date(c["date"], False)) if c["date"] else ""}</b>'
                floor = (pill("Only nurse on the floor", "red") if c["only_nurse_on_shift"]
                         else '<span class="muted">Another nurse on shift</span>')
                if c["candidates"]:
                    cands = []
                    for k in c["candidates"]:
                        tags = ""
                        if not k["same_facility"]:
                            tags += f'<span class="pf-tag">from {esc(k["facility"])}</span>'
                        if k["short_rest"]:
                            tags += f'<span class="pf-tag amber">back-to-back, under {rules["min_rest_hours"]}h rest</span>'
                        if k["overtime"]:
                            tags += f'<span class="pf-tag amber">overtime</span>'
                        if k["license_expiring_soon"]:
                            tags += '<span class="pf-tag amber">license renews soon</span>'
                        cands.append(f'<span class="pf-cand"><b>{esc(k["name"])}</b> '
                                     f'<span class="muted">{k["hours_before"]:g}h → {k["hours_after"]:g}h</span>{tags}</span>')
                    cand_html = "".join(cands)
                else:
                    cand_html = '<span class="muted">No one on the roster is free. Consider agency or PRN staff.</span>'
                rows.append([when, f'{esc(c["shift_pretty"])}<span class="sub">{c["hours"]:g}h</span>', floor, cand_html])
            html(table(["Day", "Shift", "On the floor", "Suggested cover"], rows, compact=True))


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def render_credentials(ctx) -> None:
    creds = ctx["expiration"].get("credentials", [])
    if not creds:
        empty("Upload the HR roster or the licensing file to track credentials.")
        return
    shifts_by_person: dict[str, int] = {}
    for s in ctx["capacity"].get("shifts", []):
        shifts_by_person[s["person_id"]] = shifts_by_person.get(s["person_id"], 0) + 1

    groups = [
        ("EXPIRED", "Expired", "Not valid. Remove from licensed shifts until renewed.", "red"),
        ("EXPIRING_30", "Renew within 30 days", "Valid now, but needs renewal soon.", "amber"),
        ("EXPIRING_90", "Renew within 90 days", "Plan ahead.", "neutral"),
        ("OK", "Current", "Valid for more than 90 days.", "green"),
    ]
    summary = ctx["expiration"].get("summary", {})
    html('<div class="pf-strip">' + "".join(
        f'<div class="pf-stat {"red" if k == "EXPIRED" and summary.get(k) else "amber" if k == "EXPIRING_30" and summary.get(k) else ""}">'
        f'<div class="n">{summary.get(k, 0)}</div><div class="l">{esc(lbl)}</div></div>'
        for k, lbl, _, _ in groups) + "</div>")

    for key, label, hint, tone in groups:
        items = [c for c in creds if c["urgency"] == key]
        if not items:
            continue
        section(label, hint)
        rows = []
        for c in items:
            board = c.get("board_expiration") or ""
            hr_val = c.get("hr_expiration") or ""
            if board and hr_val and board != hr_val:
                hr_cell = f'<span class="pf-tag amber">HR says {esc(fmt_date(hr_val))}</span>'
            elif board and hr_val:
                hr_cell = '<span class="pf-yes">Matches</span>'
            elif not c.get("on_board_record", True):
                hr_cell = '<span class="pf-tag amber">Not in board file</span>'
            else:
                hr_cell = '<span class="muted">One source</span>'
            days = c["days_remaining"]
            days_txt = "—" if days is None else (f"{abs(days)} days ago" if days < 0 else f"{days} days")
            n_shifts = shifts_by_person.get(c.get("person_id"), 0)
            shift_txt = (f'<span class="pf-tag red">{n_shifts} shifts</span>' if key == "EXPIRED" and n_shifts
                         else (f"{n_shifts}" if n_shifts else '<span class="muted">0</span>'))
            rows.append([
                f'<b>{esc(c["name"])}</b><span class="sub">{esc(c.get("job_title") or "")}, {esc(c["facility"])}</span>',
                f'{esc(c["license_number"])}<span class="sub">{esc(c["license_type"])}</span>',
                esc(fmt_date(c["expiration_date"])),
                days_txt, hr_cell, shift_txt,
            ])
        html(table(["Name", "License", "Expires (board)", "Time left", "HR record", "Shifts this week"],
                   rows, numeric={3, 5}))


# ---------------------------------------------------------------------------
# Compliance report
# ---------------------------------------------------------------------------

def render_compliance(ctx) -> None:
    comp = ctx["compliance"]
    rp = comp["report_period"]
    totals = comp["totals"]

    c1, c2, c3 = st.columns([3, 1.3, 1])
    with c1:
        section(f"Staffing and compliance report, {rp['label']}",
                "Hours, headcount and license status for the pay period the schedule covers.")
    workbook = build_compliance_workbook(
        comp, ctx["review"]["issues"], ctx["persons"], ctx["raw_records"], ctx["normalized"],
        ctx["file_names"],
    )
    with c2:
        st.download_button("Download report (Excel)", workbook,
                           file_name=f"harborview_compliance_{rp.get('start') or ctx['today'].isoformat()}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           type="primary", use_container_width=True, key="dl_xlsx")
    with c3:
        st.download_button("Hours (CSV)", build_hours_csv(comp),
                           file_name=f"hours_reconciliation_{rp.get('start') or ctx['today'].isoformat()}.csv",
                           mime="text/csv", use_container_width=True, key="dl_csv")

    notes = ["The licensing board is the source of truth for license numbers and expiry dates."] + comp.get("notes", [])
    note("<br>".join(esc(n) for n in notes))

    diff = totals["paid_hours"] - totals["scheduled_hours"]
    html(f'''<div class="pf-strip">
        <div class="pf-stat"><div class="n">{totals["employees"]}</div><div class="l">Staff on file</div></div>
        <div class="pf-stat {"red" if totals["licenses_expired"] else ""}"><div class="n">{totals["licenses_expired"]}</div><div class="l">Licenses expired</div></div>
        <div class="pf-stat"><div class="n">{totals["scheduled_hours"]:g} / {totals["paid_hours"]:g}</div><div class="l">Hours scheduled / paid ({diff:+g}h)</div></div>
        <div class="pf-stat {"amber" if totals["people_hours_mismatch"] else ""}"><div class="n">{totals["people_hours_mismatch"]} of {totals["people_compared"]}</div><div class="l">People whose hours don't match</div></div>
    </div>''')

    # Staffing matrix
    section("Staffing by facility and role")
    rows, classes = [], []
    for fac in sorted({m["facility"] for m in comp["staffing_matrix"]}):
        for m in [x for x in comp["staffing_matrix"] if x["facility"] == fac]:
            valid = f'{m["license_valid"]} of {m["licensed"]}' if m["licensed"] else "—"
            if m["licensed"] and m["license_valid"] < m["licensed"]:
                valid = f'<span class="pf-tag red">{valid}</span>'
            rows.append([esc(fac), esc(m["role"]), str(m["headcount"]), valid,
                         f'{m["scheduled_hours"]:g}', f'{m["paid_hours"]:g}',
                         f'{m["paid_hours"] - m["scheduled_hours"]:+g}'])
            classes.append("")
    tot = comp["staffing_matrix"]
    rows.append(["Total", "", str(sum(m["headcount"] for m in tot)),
                 f'{sum(m["license_valid"] for m in tot)} of {sum(m["licensed"] for m in tot)}',
                 f'{sum(m["scheduled_hours"] for m in tot):g}', f'{sum(m["paid_hours"] for m in tot):g}',
                 f'{sum(m["paid_hours"] - m["scheduled_hours"] for m in tot):+g}'])
    classes.append("total")
    html(table(["Facility", "Role", "Headcount", "Valid licenses", "Scheduled h", "Paid h", "Paid minus scheduled"],
               rows, numeric={2, 3, 4, 5, 6}, row_classes=classes))

    # Hours reconciliation
    section("Paid vs scheduled hours",
            "Differences over 0.5h are flagged for Payroll. Periods without a schedule are listed but not compared.")
    periods = [p["label"] for p in comp["periods"]]
    if not periods:
        empty("Upload payroll to compare paid hours with the schedule.")
        return
    default = rp["label"] if rp["label"] in periods else periods[-1]
    sel = segmented("Pay period", periods, key="comp_period", default=default)
    rows, classes = [], []
    for r in [x for x in comp["hours_reconciliation"] if x["period"] == sel]:
        st_tone = {"Matches": "green", "Not compared": "neutral"}.get(r["status"], "amber")
        rows.append([
            f'<b>{esc(r["name"])}</b><span class="sub">{esc(r["role"])}, {esc(r["facility"])}</span>',
            hrs(r["scheduled_hours"]), hrs(r["paid_hours"]),
            "—" if r["difference"] is None else f'{r["difference"]:+g}h',
            pill(r["status"], st_tone),
            esc(", ".join(p for p in r["payroll_ids"] if p)) or '<span class="muted">—</span>',
        ])
        classes.append("hl" if st_tone == "amber" else "")
    period_meta = next((p for p in comp["periods"] if p["label"] == sel), {})
    if period_meta and not period_meta.get("compared"):
        note(esc(period_meta.get("note") or "Not compared") + ". Paid hours are shown for reference only.", "neutral")
    html(table(["Name", "Scheduled", "Paid", "Paid minus scheduled", "Status", "Payroll rows"], rows,
               numeric={1, 2, 3}, row_classes=classes))


# ---------------------------------------------------------------------------
# Staff directory
# ---------------------------------------------------------------------------

def render_directory(ctx) -> None:
    people = ctx["review"]["people"]
    uploaded = ctx["uploaded_sources"]
    order = {"action": 0, "review": 1, "ok": 2}
    persons = sorted(ctx["persons"], key=lambda p: (order.get(people.get(p.id, {}).get("status"), 3), p.canonical_name))
    section("Everyone on file", "Use the search above to see how any person's records were parsed and linked.")
    rows = []
    for p in persons:
        info = people.get(p.id, {})
        marks = []
        for s in SOURCE_ORDER:
            if s not in uploaded:
                marks.append('<span class="muted">·</span>')
            elif s in p.sources:
                marks.append('<span class="pf-yes">✓</span>')
            else:
                marks.append('<span class="pf-tag red">missing</span>' if s != "schedule" else '<span class="muted">off</span>')
        reason = "" if info.get("status") == "ok" else f'<span class="sub">{esc(info.get("reason", ""))}</span>'
        rows.append([
            f'<b>{esc(p.canonical_name)}</b><span class="sub">{esc(p.employee_id or "No employee ID")}</span>',
            f'{esc(role_code(p.job_title))}<span class="sub">{esc(p.facility or "—")}</span>',
            f'{status_pill(info.get("status", "ok"))}{reason}',
            *marks,
            f'{info.get("checks_passed", 0)} of {info.get("checks_total", 0)}',
        ])
    html(table(["Name", "Role", "Status", "HR", "Payroll", "License", "Schedule", "Checks passed"], rows,
               numeric={7}))
