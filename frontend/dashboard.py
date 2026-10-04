"""
Pulse Foundry — Streamlit dashboard.

Run from the project root:  streamlit run frontend/dashboard.py
"""

import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "frontend")):
    if p not in sys.path:
        sys.path.insert(0, p)

import streamlit as st

from app.domain.healthcare import HealthcareDomainPlugin
from app.ingest.csv_loader import load_csv
from app.ingest.normalizer import normalize_batch
from app.ingest.pdf_loader import load_pdf
from app.reconcile.conflict_detector import detect_conflicts
from app.reconcile.entity_resolver import resolve_facilities, resolve_persons
from frontend.ui import LOGO_PATH, esc, html, inject_theme, logo_data_uri
from frontend import views

SAMPLE_DIR = ROOT / "data" / "sample"
SAMPLE_FILES = ["hr_roster.csv", "payroll.csv", "licenses.csv", "weekly_schedule.pdf"]
SOURCE_LABEL = {"hr": "HR roster", "payroll": "Payroll", "licenses": "Licensing board", "schedule": "Schedule"}

st.set_page_config(
    page_title="Pulse Foundry",
    page_icon=str(LOGO_PATH) if LOGO_PATH.exists() else "⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_theme()


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def _init_state() -> None:
    defaults = {
        "raw_records": [],
        "normalized_records": [],
        "files": {},            # filename -> {"source": str, "rows": int}
        "results": None,
        "dirty": False,
        "follow_up": set(),
        "lookup": None,
        "uploader_key": 0,
        "errors": [],
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _reset() -> None:
    key = st.session_state.get("uploader_key", 0) + 1
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    _init_state()
    st.session_state.uploader_key = key


def _ingest(name: str, content: bytes) -> None:
    if name in st.session_state.files:
        return
    try:
        if name.lower().endswith(".pdf"):
            raw = load_pdf(content, name)
            src = "schedule"
        elif name.lower().endswith(".csv"):
            src, raw = load_csv(content, name)
        else:
            st.session_state.errors.append(f"{name}: only CSV and PDF files are supported.")
            return
        if not raw:
            st.session_state.errors.append(f"{name}: no rows could be read from this file.")
            return
        if src != "schedule" or name.lower().endswith(".csv"):
            mapping = raw[0].data.get("column_mapping")
            if src not in SOURCE_LABEL or (mapping is not None and len(mapping) < 2):
                st.session_state.errors.append(
                    f"{name}: the columns weren't recognised as HR, payroll, licensing or schedule data.")
                return
        st.session_state.raw_records.extend(raw)
        st.session_state.normalized_records.extend(normalize_batch(raw))
        st.session_state.files[name] = {"source": src, "rows": len(raw)}
        st.session_state.dirty = True
    except Exception as exc:  # surface the problem instead of crashing the page
        st.session_state.errors.append(f"{name}: couldn't be read ({exc}).")


def _reconcile() -> dict:
    norm = st.session_state.normalized_records
    raw = st.session_state.raw_records
    domain = HealthcareDomainPlugin()
    today = date.today()

    persons = resolve_persons(norm)
    facilities = resolve_facilities(norm)
    conflicts = detect_conflicts(persons, norm)
    schedule = [r for r in norm if r.source == "schedule"]
    capacity = domain.generate_capacity_report(persons, schedule, facilities)
    compliance = domain.generate_compliance_report(persons, norm, conflicts)
    expiration = domain.generate_expiration_report(persons, norm)
    review = domain.generate_review(persons, norm, conflicts, capacity, compliance)

    return {
        "today": today,
        "persons": persons,
        "person_by_id": {p.id: p for p in persons},
        "facilities": facilities,
        "conflicts": conflicts,
        "capacity": capacity,
        "compliance": compliance,
        "expiration": expiration,
        "review": review,
        "raw_records": raw,
        "normalized": norm,
        "raw_index": {r.id: r for r in raw},
        "norm_index": {n.raw_record_id: n for n in norm},
        "file_names": list(st.session_state.files.keys()),
        "uploaded_sources": {v["source"] for v in st.session_state.files.values()},
    }


_init_state()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    html(f'''<div class="pf-brand">
        {f'<img src="{logo_data_uri()}" alt="">' if LOGO_PATH.exists() else ""}
        <div><div class="name">Pulse Foundry</div><div class="org">Harborview Care Group</div></div>
    </div>''')

    sources_box = st.empty()  # filled after uploads are processed so status is current

    uploads = st.file_uploader(
        "Add CSV or PDF files", accept_multiple_files=True, type=["csv", "pdf"],
        key=f"uploader_{st.session_state.uploader_key}",
        help="HR roster, payroll, licensing export and the weekly schedule. File types are detected automatically.",
    )
    for uf in uploads or []:
        _ingest(uf.name, uf.getvalue())

    loaded = {}
    for name, meta in st.session_state.files.items():
        loaded.setdefault(meta["source"], []).append((name, meta["rows"]))
    rows = []
    for src in ["hr", "payroll", "licenses", "schedule"]:
        files = loaded.get(src, [])
        meta = (", ".join(n for n, _ in files) if files else "Not uploaded")
        rows.append(f'<div class="pf-source{" on" if files else ""}" title="{esc(meta)}"><span class="dot"></span>'
                    f'<span class="lbl">{SOURCE_LABEL[src]}</span><span class="meta">{esc(meta)}</span></div>')
    with sources_box.container():
        html(f'<div class="pf-side-title">Data sources</div><div class="pf-sources">{"".join(rows)}</div>')

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Load sample data", use_container_width=True,
                     disabled=all(f in st.session_state.files for f in SAMPLE_FILES)):
            for fname in SAMPLE_FILES:
                path = SAMPLE_DIR / fname
                if path.exists():
                    _ingest(fname, path.read_bytes())
            st.rerun()
    with c2:
        if st.button("Clear all", use_container_width=True, disabled=not st.session_state.files):
            _reset()
            st.rerun()

    for err in st.session_state.errors:
        st.warning(err)

    html('<div class="pf-side-foot">Industry profile: skilled nursing. Records are matched on license number, '
         'name, facility and role. The licensing board is trusted for license data, HR for everything else.</div>')


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

today = date.today()
html(f'''<div class="pf-header">
    <div><h1>Staff records, reconciled</h1>
    <p>HR, payroll, licensing and the schedule in one place, with who needs to fix what.</p></div>
    <div class="asof">Data as of {today.strftime('%b')} {today.day}, {today.year}</div>
</div><div class="pf-pulse"></div>''')

if not st.session_state.files:
    html('''<div class="pf-welcome">
        <h2>Add your source files to get started</h2>
        <p>Drop the exports from each system into the sidebar. Columns are detected automatically,
        names and codes are cleaned up, and the same person is linked across every file.</p>
        <div class="pf-welcome-grid">
          <div><b>HR roster</b><span>Employees, roles, license numbers</span></div>
          <div><b>Payroll</b><span>Paid hours by pay period</span></div>
          <div><b>Licensing board</b><span>License status and expiry</span></div>
          <div><b>Schedule</b><span>Weekly shifts, CSV or PDF</span></div>
        </div></div>''')
    st.write("")
    if st.button("Load sample data", type="primary", key="welcome_sample"):
        for fname in SAMPLE_FILES:
            path = SAMPLE_DIR / fname
            if path.exists():
                _ingest(fname, path.read_bytes())
        st.rerun()
    st.stop()

if st.session_state.dirty or st.session_state.results is None:
    with st.spinner("Matching records across systems…"):
        st.session_state.results = _reconcile()
    st.session_state.dirty = False
    if st.session_state.lookup and st.session_state.lookup not in st.session_state.results["person_by_id"]:
        st.session_state.lookup = None

ctx = st.session_state.results


# ---------------------------------------------------------------------------
# Staff lookup
# ---------------------------------------------------------------------------

options = views.search_options(ctx)
labels = list(options.keys())
current_label = next((l for l, pid in options.items() if pid == st.session_state.lookup), None)
html('<div class="pf-search-label">Look up a staff member</div>')
picked = st.selectbox(
    "Look up a staff member", labels,
    index=labels.index(current_label) if current_label in labels else None,
    placeholder="Type a name, employee ID or license number",
    label_visibility="collapsed", key=f"lookup_box_{st.session_state.lookup or 'none'}",
)
if picked and options.get(picked) != st.session_state.lookup:
    st.session_state.lookup = options[picked]
    st.rerun()
if st.session_state.lookup:
    views.render_profile(ctx, st.session_state.lookup)


# ---------------------------------------------------------------------------
# Status strip
# ---------------------------------------------------------------------------

people = ctx["review"]["people"]
n_ok = sum(1 for v in people.values() if v["status"] == "ok")
n_look = sum(1 for v in people.values() if v["status"] == "review")
n_action = sum(1 for v in people.values() if v["status"] == "action")
n_cover = len(ctx["capacity"].get("cover_needed", []))
html(f'''<div class="pf-strip">
    <div class="pf-stat green"><div class="n">{n_ok}</div><div class="l">Up to date</div></div>
    <div class="pf-stat {"amber" if n_look else ""}"><div class="n">{n_look}</div><div class="l">Need a look</div></div>
    <div class="pf-stat {"red" if n_action else ""}"><div class="n">{n_action}</div><div class="l">Need action</div></div>
    <div class="pf-stat {"red" if n_cover else ""}"><div class="n">{n_cover}</div><div class="l">Shifts needing cover</div></div>
</div>''')


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

n_issues = len(ctx["review"]["issues"])
tab_review, tab_sched, tab_creds, tab_comp, tab_dir = st.tabs([
    f"Review queue ({n_issues})", "Schedule", "Credentials", "Compliance report", "Staff directory",
])
with tab_review:
    views.render_review(ctx)
with tab_sched:
    views.render_schedule(ctx)
with tab_creds:
    views.render_credentials(ctx)
with tab_comp:
    views.render_compliance(ctx)
with tab_dir:
    views.render_directory(ctx)
