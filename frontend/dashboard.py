import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
import pandas as pd

from app.ingest.csv_loader import load_csv
from app.ingest.pdf_loader import load_pdf
from app.ingest.normalizer import normalize_batch
from app.reconcile.entity_resolver import resolve_persons, resolve_facilities
from app.reconcile.conflict_detector import detect_conflicts
from app.domain.healthcare import HealthcareDomainPlugin

st.set_page_config(
    page_title="Pulse Foundry — Single Source of Truth",
    page_icon="🔍",
    layout="wide",
)

if "raw_records" not in st.session_state:
    st.session_state.raw_records = []
    st.session_state.normalized_records = []
    st.session_state.persons = []
    st.session_state.facilities = []
    st.session_state.conflicts = []
    st.session_state.ingestion_log = []
    st.session_state.reconciled = False
    st.session_state.domain = HealthcareDomainPlugin()
    st.session_state.aha_moments = []

def reset():
    st.session_state.raw_records = []
    st.session_state.normalized_records = []
    st.session_state.persons = []
    st.session_state.facilities = []
    st.session_state.conflicts = []
    st.session_state.ingestion_log = []
    st.session_state.reconciled = False
    st.session_state.aha_moments = []

st.sidebar.title("🔍 Pulse Foundry SSOT")

# Concretely illustrate the Domain Plugin Architecture
st.sidebar.markdown("### Active Domain")
domain_choice = st.sidebar.selectbox("Industry Schema", [
    "Healthcare — Skilled Nursing",
    "Construction (Coming Soon)",
    "Logistics (Coming Soon)"
])

st.sidebar.markdown("---")
st.sidebar.markdown("**Upload source files**")

uploaded_files = st.sidebar.file_uploader(
    "Drop CSV or PDF files here",
    accept_multiple_files=True,
    type=["csv", "pdf"],
)

if st.sidebar.button("🔄 Reset All Data"):
    reset()
    st.rerun()

if uploaded_files:
    for uf in uploaded_files:
        already = any(uf.name in log for log in st.session_state.ingestion_log)
        if already:
            continue
        try:
            content = uf.read()
            uf.seek(0)

            if uf.name.lower().endswith(".pdf"):
                raw = load_pdf(content, uf.name)
                src = "schedule"
            elif uf.name.lower().endswith(".csv"):
                src, raw = load_csv(content, uf.name)
            else:
                st.sidebar.warning(f"Skipping unsupported file format: {uf.name}")
                continue

            st.session_state.raw_records.extend(raw)
            normalized = normalize_batch(raw)
            st.session_state.normalized_records.extend(normalized)
            st.session_state.ingestion_log.append(f"✅ {uf.name} → {src} ({len(raw)} records)")
            st.session_state.reconciled = False
        except Exception as e:
            st.sidebar.error(f"Failed to ingest {uf.name}. Unrecognized format or missing headers.")

if st.session_state.normalized_records and not st.session_state.reconciled:
    if st.sidebar.button("⚡ Run Reconciliation", type="primary"):
        st.session_state.persons = resolve_persons(st.session_state.normalized_records)
        st.session_state.facilities = resolve_facilities(st.session_state.normalized_records)
        st.session_state.conflicts = detect_conflicts(st.session_state.persons, st.session_state.normalized_records)
        st.session_state.reconciled = True
        
        # Surface an automated "aha" moment for the live demo
        if len(st.session_state.persons) > 0:
            st.session_state.aha_moments.append(f"Detected '{st.session_state.persons[0].canonical_name}' across {len(st.session_state.persons[0].source_records)} different systems via fuzzy matching (Confidence: {st.session_state.persons[0].match_confidence:.1%}).")
        
        st.rerun()

st.title("🏥 Single Source of Truth")
st.markdown(f"*Unified data reconciliation for {domain_choice.split('—')[0].strip()}*")

if not st.session_state.ingestion_log:
    st.info("👈 Upload your source files (HR roster, Payroll, Licenses, Schedule) from the sidebar to get started.")
    st.stop()

if not st.session_state.reconciled:
    st.warning("Files uploaded. Click **Run Reconciliation** in the sidebar to process.")
    st.success(f"📦 **Ingestion Summary**: {len(st.session_state.ingestion_log)} files uploaded. {len(st.session_state.raw_records)} total raw records parsed.")
    st.stop()

persons = st.session_state.persons
conflicts = st.session_state.conflicts
normalized = st.session_state.normalized_records
facilities = st.session_state.facilities
domain = st.session_state.domain

# Top Summary KPIs
col1, col2, col3, col4 = st.columns(4)
col1.metric("👥 Unified Employees", len(persons))
col2.metric("🏥 Facilities Identified", len(facilities))
critical = sum(1 for c in conflicts if c.severity.value <= 2)
col3.metric("🚨 Critical Conflicts", critical, delta="- Action Required", delta_color="inverse")
match_avg = sum(p.match_confidence for p in persons) / len(persons) if persons else 0
col4.metric("✨ Avg Entity Trust Score", f"{match_avg:.0%}")

if st.session_state.aha_moments:
    st.info("💡 **System Insight:** " + st.session_state.aha_moments[0])

st.markdown("---")

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🚨 Conflict Resolution",
    "🏥 Capacity Dashboard",
    "📋 Compliance Report",
    "⏰ Expiration Tracker",
    "🔎 Audit Trail",
])

with tab1:
    st.markdown("### Resolve Entity Conflicts")
    if not conflicts:
        st.success("🎉 No conflicts detected! All data sources are perfectly aligned.")
    else:
        # Group conflicts by person for actionable review workflow
        person_conflicts = {}
        for c in conflicts:
            person_conflicts.setdefault(c.person_id, []).append(c)
            
        for pid, confs in person_conflicts.items():
            person = next((p for p in persons if p.id == pid), None)
            if not person: continue
            
            trust_score = person.match_confidence
            color = "🟢" if trust_score > 0.9 else ("🟡" if trust_score > 0.7 else "🔴")
            
            with st.expander(f"{color} {person.canonical_name} ({len(confs)} issues) - Trust Score: {trust_score:.0%}", expanded=True):
                for c in confs:
                    st.markdown(f"**Field:** `{c.field_name}` | **Severity:** {c.severity.name}")
                    st.markdown(f"> {c.description}")
                    col_a, col_b, col_c = st.columns([2, 1, 1])
                    with col_a:
                        st.selectbox("Select True Value:", ["Use HR Record", "Use Payroll Record", "Manual Override"], key=f"sel_{c.person_id}_{c.field_name}")
                    with col_b:
                        st.button("✅ Mark Resolved", key=f"btn_{c.person_id}_{c.field_name}")
                st.markdown("---")

with tab2:
    schedule_recs = [r for r in normalized if r.source == "schedule"]
    if not schedule_recs:
        st.warning("No schedule data uploaded. Upload a schedule PDF or CSV.")
    else:
        capacity = domain.generate_capacity_report(persons, schedule_recs, facilities)
        
        st.markdown("### Shift Decision Support")
        filt_col1, filt_col2 = st.columns(2)
        sel_fac = filt_col1.selectbox("Filter by Facility", ["All"] + list(facilities))
        sel_shift = filt_col2.selectbox("Filter by Shift", ["All", "7a-3p", "3p-11p", "11p-7a", "7a-7p"])
        
        # Cross-module linkage: identifying expired staff on the live schedule
        exp_report = domain.generate_expiration_report(persons)
        expired_creds = {c["employee_id"]: c for c in exp_report.get("credentials", []) if c["urgency"] == "EXPIRED"}

        fac_data = capacity.get("facilities", {})
        for fac_name, days in fac_data.items():
            if sel_fac != "All" and fac_name != sel_fac: continue
            st.markdown(f"#### 🏥 {fac_name}")
            
            for day_name in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
                shifts = days.get(day_name, {})
                if not shifts: continue
                
                with st.expander(f"📅 {day_name}", expanded=(day_name=="Monday")):
                    for shift_name, staff_list in shifts.items():
                        if sel_shift != "All" and shift_name != sel_shift: continue
                        
                        st.markdown(f"**{shift_name}** ({len(staff_list)} staff)")
                        for s in staff_list:
                            pid = s["person_id"]
                            is_expired = pid in expired_creds
                            alert = "🚨 **EXPIRED LICENSE**" if is_expired else "✅ Valid"
                            color = "red" if is_expired else "green"
                            st.markdown(f"- {s['name']} ({s['role']}) - :{color}[{alert}]")

with tab3:
    compliance = domain.generate_compliance_report(persons, normalized, conflicts)
    st.markdown("### State Auditor Dashboard")
    st.caption("Cross-reference scheduled hours against actual paid hours.")
    
    colA, colB = st.columns([1, 1])
    with colA:
        st.markdown("**By Role**")
        st.dataframe(pd.DataFrame([
            {"Role": r, "Staff": i["count"], "Sched. Hrs": i["total_schedule_hours"]}
            for r, i in compliance.get("staffing_summary", {}).get("by_role", {}).items()
        ]), use_container_width=True)
    with colB:
        st.markdown("**By Facility**")
        st.dataframe(pd.DataFrame([
            {"Facility": f, "Staff": i["count"], "Sched. Hrs": i["total_schedule_hours"]}
            for f, i in compliance.get("staffing_summary", {}).get("by_facility", {}).items()
        ]), use_container_width=True)

    discreps = compliance.get("hours_discrepancies", [])
    st.markdown("### Hours Discrepancies (Payroll vs Schedule)")
    if discreps:
        st.error(f"Found {len(discreps)} hours discrepancy flag(s).")
        st.dataframe(pd.DataFrame(discreps), use_container_width=True)
    else:
        st.success("No discrepancies found. Scheduled hours match payroll.")

    audit = compliance.get("audit_trail", [])
    if audit:
        st.download_button(
            "📥 Export Auditor CSV Report",
            data="\\n".join(audit),
            file_name="compliance_audit_trail.csv",
            mime="text/csv",
            type="primary"
        )

with tab4:
    st.markdown("### License Expiration Tracker")
    exp_report = domain.generate_expiration_report(persons)
    creds = exp_report.get("credentials", [])
    if creds:
        def color_urgency(val):
            colors = {"EXPIRED": "#ff4b4b", "EXPIRING_30": "#ff9f43", "EXPIRING_90": "#feca57", "OK": "#1dd1a1"}
            return f"background-color: {colors.get(val, '')}; color: black; font-weight: bold;"
        
        df_creds = pd.DataFrame(creds)
        st.dataframe(df_creds.style.map(color_urgency, subset=["urgency"]), use_container_width=True)
    else:
        st.info("No credentials tracked.")

with tab5:
    st.markdown("### Data Lineage & Audit Trail")
    st.caption("Trace exactly how disparate records merged into a unified entity.")
    
    for p in persons:
        with st.expander(f"Entity Flow: {p.canonical_name}", expanded=False):
            st.markdown(f"**Unified ID:** `{p.id}`")
            
            # Lineage trace visualization
            flow_steps = []
            for src in p.source_records:
                flow_steps.append(f"[{src.source.upper()}] `{src.id[:6]}` matched on Name/ID (Confidence: {p.match_confidence:.1%})")
            
            st.markdown(" 👇 ".join(flow_steps) + " 👇 **UNIFIED ENTITY CREATED**")
            
            st.markdown("#### Merge Log")
            for log in p.resolution_log:
                st.text(f"→ {log}")