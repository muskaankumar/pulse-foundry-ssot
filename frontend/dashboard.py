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

# ---------------------------------------------------------------------------
# Theming and Layout (Sophisticated Dark Mode)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Pulse Foundry — Single Source of Truth",
    page_icon="💠",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for dark, sophisticated theme
st.markdown("""
<style>
    /* Main Background & Text */
    .stApp {
        background-color: #0d1117;
        color: #e6edf3;
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    
    /* Headers */
    h1, h2, h3, h4, h5, h6 {
        color: #ffffff !important;
        font-weight: 600;
        letter-spacing: -0.02em;
    }
    
    h1 {
        font-size: 3rem !important;
        margin-bottom: 0.2em !important;
    }
    
    /* Sidebar */
    [data-testid="stSidebar"] {
        background-color: #010409;
        border-right: 1px solid #30363d;
    }
    
    /* Buttons */
    .stButton>button {
        background-color: #1f6feb;
        color: white;
        border: 1px solid rgba(240, 246, 252, 0.1);
        border-radius: 6px;
        padding: 0.5rem 1rem;
        font-weight: 500;
        transition: 0.2s cubic-bezier(0.3, 0, 0.5, 1);
    }
    .stButton>button:hover {
        background-color: #388bfd;
        border-color: #8b949e;
    }
    
    /* Metrics */
    [data-testid="stMetricValue"] {
        color: #58a6ff;
        font-size: 2.5rem;
    }
    
    /* Dataframes */
    .stDataFrame {
        border-radius: 8px;
        border: 1px solid #30363d;
    }
    
    /* Tabs */
    .stTabs [data-baseweb="tab-list"] {
        gap: 2rem;
        border-bottom: 1px solid #30363d;
    }
    .stTabs [data-baseweb="tab"] {
        color: #8b949e;
        border-bottom-color: transparent !important;
    }
    .stTabs [aria-selected="true"] {
        color: #e6edf3 !important;
        border-bottom-color: #f78166 !important; /* A nice accent color */
    }
    
    /* Expanders */
    .streamlit-expanderHeader {
        background-color: #161b22;
        border-radius: 6px;
        border: 1px solid #30363d;
    }
    .streamlit-expanderContent {
        border-left: 1px solid #30363d;
        border-right: 1px solid #30363d;
        border-bottom: 1px solid #30363d;
        border-bottom-left-radius: 6px;
        border-bottom-right-radius: 6px;
    }
    
    /* Custom spacing */
    .block-container {
        padding-top: 2rem !important;
        max-width: 1200px;
    }
    
    /* Subtitle styling */
    .subtitle {
        color: #8b949e;
        font-size: 1.2rem;
        margin-bottom: 2rem;
    }
</style>
""", unsafe_allow_html=True)

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

st.sidebar.title("💠 Pulse Foundry")

st.sidebar.markdown("<div style='color: #8b949e; margin-bottom: 1rem; font-size: 0.9em; text-transform: uppercase; letter-spacing: 0.05em;'>Active Domain</div>", unsafe_allow_html=True)
domain_choice = st.sidebar.selectbox("Industry Schema", [
    "Healthcare — Skilled Nursing",
    "Construction (Coming Soon)",
    "Logistics (Coming Soon)"
], label_visibility="collapsed")

st.sidebar.markdown("---")
st.sidebar.markdown("<div style='color: #8b949e; margin-bottom: 1rem; font-size: 0.9em; text-transform: uppercase; letter-spacing: 0.05em;'>Upload Data</div>", unsafe_allow_html=True)

uploaded_files = st.sidebar.file_uploader(
    "Drop CSV or PDF files here",
    accept_multiple_files=True,
    type=["csv", "pdf"],
    label_visibility="collapsed"
)

st.sidebar.markdown("<br>", unsafe_allow_html=True)
if st.sidebar.button("Reset Platform", use_container_width=True):
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
                st.sidebar.warning(f"Skipping unsupported file: {uf.name}")
                continue

            st.session_state.raw_records.extend(raw)
            normalized = normalize_batch(raw)
            st.session_state.normalized_records.extend(normalized)
            st.session_state.ingestion_log.append(f"{uf.name} → {src} ({len(raw)} records)")
            st.session_state.reconciled = False
        except Exception as e:
            st.sidebar.error(f"Failed to ingest {uf.name}. Error: {str(e)}")

st.title("Single Source of Truth.")
st.markdown(f"<div class='subtitle'>Unified data reconciliation for {domain_choice.split('—')[0].strip()} — linking disparate systems into one accountable model.</div>", unsafe_allow_html=True)

if not st.session_state.ingestion_log:
    st.info("Start where the friction is. Upload your source files (HR roster, Payroll, Licenses, Schedule) from the sidebar to establish a baseline.")
    st.stop()

if st.session_state.normalized_records and not st.session_state.reconciled:
    col1, col2 = st.columns([1, 2])
    with col1:
        if st.button("Synthesize Data", type="primary"):
            st.session_state.persons = resolve_persons(st.session_state.normalized_records)
            st.session_state.facilities = resolve_facilities(st.session_state.normalized_records)
            st.session_state.conflicts = detect_conflicts(st.session_state.persons, st.session_state.normalized_records)
            st.session_state.reconciled = True
            
            if len(st.session_state.persons) > 0:
                p = st.session_state.persons[0]
                st.session_state.aha_moments.append(f"Linked '{p.canonical_name}' across {len(p.source_records)} systems via semantic mapping. Trust score: {p.match_confidence:.0%}")
            
            st.rerun()
    with col2:
        st.markdown(f"<div style='color: #8b949e; padding-top: 0.5rem;'>Ready to process {len(st.session_state.raw_records)} records across {len(st.session_state.ingestion_log)} files.</div>", unsafe_allow_html=True)
    st.stop()

# Post-Reconciliation Dashboard
persons = st.session_state.persons
conflicts = st.session_state.conflicts
normalized = st.session_state.normalized_records
facilities = st.session_state.facilities
domain = st.session_state.domain

# Summary KPIs
col1, col2, col3, col4 = st.columns(4)
col1.metric("Unified Entities", len(persons))
col2.metric("Facilities Mapped", len(facilities))
critical = sum(1 for c in conflicts if c.severity.value <= 2)
col3.metric("Critical Discrepancies", critical)
match_avg = sum(p.match_confidence for p in persons) / len(persons) if persons else 0
col4.metric("Avg Trust Score", f"{match_avg:.0%}")

if st.session_state.aha_moments:
    st.markdown(f"<div style='background-color: rgba(88, 166, 255, 0.1); border-left: 3px solid #58a6ff; padding: 1rem; margin-top: 1rem; margin-bottom: 2rem; border-radius: 4px; color: #e6edf3;'>Insight: {st.session_state.aha_moments[0]}</div>", unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "Conflict Resolution",
    "Capacity Intelligence",
    "Compliance Ledger",
    "Credential Authority",
    "Data Lineage",
])

with tab1:
    st.markdown("### Discrepancy Queue")
    st.markdown("<div style='color: #8b949e; margin-bottom: 1.5rem;'>Review and authorize merges where system data conflicts.</div>", unsafe_allow_html=True)
    
    if not conflicts:
        st.success("All integrated data sources are aligned.")
    else:
        person_conflicts = {}
        for c in conflicts:
            person_conflicts.setdefault(c.person_id, []).append(c)
            
        for pid, confs in person_conflicts.items():
            person = next((p for p in persons if p.id == pid), None)
            if not person: continue
            
            trust_score = person.match_confidence
            indicator = "🟢" if trust_score > 0.9 else ("🟡" if trust_score > 0.7 else "🔴")
            
            with st.expander(f"{indicator} {person.canonical_name} — {len(confs)} Issue(s) Pending", expanded=True):
                for c in confs:
                    st.markdown(f"<span style='color: #f78166; font-weight: bold; font-size: 0.9em; text-transform: uppercase;'>{c.severity.name} Priority</span>", unsafe_allow_html=True)
                    st.markdown(f"**Field mismatch:** `{c.field_name}`")
                    st.markdown(f"<div style='color: #8b949e; margin-bottom: 1rem;'>{c.description}</div>", unsafe_allow_html=True)
                    
                    c1, c2 = st.columns([3, 1])
                    with c1:
                        st.selectbox("Authorize canonical value:", ["System A (HR)", "System B (Payroll)", "Override"], key=f"sel_{c.person_id}_{c.field_name}", label_visibility="collapsed")
                    with c2:
                        st.button("Commit Resolution", key=f"btn_{c.person_id}_{c.field_name}", use_container_width=True)
                st.markdown("<hr style='border-color: #30363d;'>", unsafe_allow_html=True)

with tab2:
    st.markdown("### Shift Decision Matrix")
    st.markdown("<div style='color: #8b949e; margin-bottom: 1.5rem;'>Real-time alignment of scheduled capacity against verified credential status.</div>", unsafe_allow_html=True)
    
    schedule_recs = [r for r in normalized if r.source == "schedule"]
    if not schedule_recs:
        st.info("Schedule data not integrated. Upload a schedule payload to activate capacity intelligence.")
    else:
        capacity = domain.generate_capacity_report(persons, schedule_recs, facilities)
        
        filt_col1, filt_col2 = st.columns(2)
        sel_fac = filt_col1.selectbox("Scope by Facility", ["Global View"] + [f.canonical_name for f in facilities])
        sel_shift = filt_col2.selectbox("Scope by Shift", ["All Active Shifts", "7a-3p", "3p-11p", "11p-7a", "7a-7p"])
        
        exp_report = domain.generate_expiration_report(persons)
        expired_creds = {c["employee_id"]: c for c in exp_report.get("credentials", []) if c["urgency"] == "EXPIRED"}

        fac_data = capacity.get("facilities", {})
        
        for fac_name, days in fac_data.items():
            if sel_fac != "Global View" and fac_name != sel_fac: continue
            
            st.markdown(f"#### {fac_name}")
            
            # Using columns for days to make it a clean, dashboard-like grid
            day_cols = st.columns(5) # M-F layout
            display_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
            
            for i, day_name in enumerate(display_days):
                with day_cols[i]:
                    st.markdown(f"<div style='border-bottom: 1px solid #30363d; padding-bottom: 0.5rem; margin-bottom: 1rem; font-weight: 600;'>{day_name}</div>", unsafe_allow_html=True)
                    shifts = days.get(day_name, {})
                    if not shifts: 
                        st.markdown("<div style='color: #484f58; font-size: 0.9em;'>No coverage mapped</div>", unsafe_allow_html=True)
                        continue
                    
                    for shift_name, staff_list in shifts.items():
                        if sel_shift != "All Active Shifts" and shift_name != sel_shift: continue
                        
                        st.markdown(f"<div style='color: #8b949e; font-size: 0.85em; margin-top: 1rem;'>{shift_name} ({len(staff_list)})</div>", unsafe_allow_html=True)
                        for s in staff_list:
                            # Verify if the scheduled person has an expired license
                            matched_pid = None
                            for p in persons:
                                if p.canonical_name == s["name"]:
                                    matched_pid = p.employee_id
                                    break
                            
                            is_expired = matched_pid in expired_creds
                            
                            # Clean, colored dots indicating readiness state
                            if is_expired:
                                st.markdown(f"<div style='padding: 0.3rem 0; font-size: 0.9em;'><span style='color: #f78166;'>●</span> {s['name']} <span style='color: #8b949e;'>{s['role']}</span></div>", unsafe_allow_html=True)
                            else:
                                st.markdown(f"<div style='padding: 0.3rem 0; font-size: 0.9em;'><span style='color: #3fb950;'>●</span> {s['name']} <span style='color: #8b949e;'>{s['role']}</span></div>", unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)

with tab3:
    st.markdown("### Auditor Reconciliation")
    st.markdown("<div style='color: #8b949e; margin-bottom: 1.5rem;'>Automated cross-referencing of scheduled allocations vs. materialized payroll execution.</div>", unsafe_allow_html=True)
    
    compliance = domain.generate_compliance_report(persons, normalized, conflicts)
    
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("<div style='font-size: 0.9em; text-transform: uppercase; letter-spacing: 0.05em; color: #8b949e; margin-bottom: 0.5rem;'>Allocation by Role</div>", unsafe_allow_html=True)
        role_df = pd.DataFrame([
            {"Classification": r, "Headcount": i["count"], "Projected Hrs": i["total_schedule_hours"]}
            for r, i in compliance.get("staffing_summary", {}).get("by_role", {}).items()
        ])
        st.dataframe(role_df, use_container_width=True, hide_index=True)
        
    with c2:
        st.markdown("<div style='font-size: 0.9em; text-transform: uppercase; letter-spacing: 0.05em; color: #8b949e; margin-bottom: 0.5rem;'>Allocation by Node</div>", unsafe_allow_html=True)
        fac_df = pd.DataFrame([
            {"Node": f, "Headcount": i["count"], "Projected Hrs": i["total_schedule_hours"]}
            for f, i in compliance.get("staffing_summary", {}).get("by_facility", {}).items()
        ])
        st.dataframe(fac_df, use_container_width=True, hide_index=True)

    discreps = compliance.get("hours_discrepancies", [])
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("<div style='font-size: 0.9em; text-transform: uppercase; letter-spacing: 0.05em; color: #8b949e; margin-bottom: 0.5rem;'>Execution Variance (Payroll vs Schedule)</div>", unsafe_allow_html=True)
    
    if discreps:
        st.markdown(f"<div style='background-color: rgba(247, 129, 102, 0.1); border-left: 3px solid #f78166; padding: 1rem; margin-bottom: 1rem; border-radius: 4px; color: #e6edf3;'>Identified {len(discreps)} variance flags requiring audit.</div>", unsafe_allow_html=True)
        st.dataframe(pd.DataFrame(discreps), use_container_width=True, hide_index=True)
    else:
        st.markdown("<div style='background-color: rgba(63, 185, 80, 0.1); border-left: 3px solid #3fb950; padding: 1rem; margin-bottom: 1rem; border-radius: 4px; color: #e6edf3;'>Execution aligned. No variances detected.</div>", unsafe_allow_html=True)

    audit = compliance.get("audit_trail", [])
    if audit:
        st.download_button(
            "Export Auditor Ledger (CSV)",
            data="\n".join(audit),
            file_name="ledger_export.csv",
            mime="text/csv",
        )

with tab4:
    st.markdown("### Credential Authority")
    st.markdown("<div style='color: #8b949e; margin-bottom: 1.5rem;'>Proactive risk management for operational certifications. High-priority exposures are elevated.</div>", unsafe_allow_html=True)
    
    exp_report = domain.generate_expiration_report(persons)
    creds = exp_report.get("credentials", [])
    
    if creds:
        # Sophisticated dataframe styling
        def style_urgency(row):
            urgency = row.get("urgency", "")
            # Apply subtle red/yellow backgrounds for at-risk items, mute compliant ones
            if urgency == "EXPIRED":
                return ['background-color: rgba(248, 81, 73, 0.15); color: #ff7b72'] * len(row)
            elif urgency == "EXPIRING_30":
                return ['background-color: rgba(210, 153, 34, 0.15); color: #e3b341'] * len(row)
            elif urgency == "EXPIRING_90":
                return ['background-color: transparent; color: #e3b341'] * len(row)
            else:
                return ['background-color: transparent; color: #8b949e'] * len(row)
                
        df_creds = pd.DataFrame(creds)
        # Reorder and rename columns for a cleaner, polished look
        display_df = df_creds[["name", "facility", "license_type", "license_number", "expiration_date", "days_remaining", "urgency"]]
        display_df.columns = ["Entity", "Node", "Classification", "Identifier", "Valid Until", "T-Minus (Days)", "urgency"]
        
        st.dataframe(
            display_df.style.apply(style_urgency, axis=1), 
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("No operational certifications registered in the current schema.")

with tab5:
    st.markdown("### Provenance & Data Lineage")
    st.markdown("<div style='color: #8b949e; margin-bottom: 1.5rem;'>Cryptographic-style traceability. See exactly how discrete system records merged into unified entities.</div>", unsafe_allow_html=True)
    
    for p in persons:
        with st.expander(f"Entity Key: {p.id[:12]}...  |  {p.canonical_name}", expanded=False):
            st.markdown(f"<div style='font-family: monospace; color: #8b949e; margin-bottom: 1rem;'>MATCH CONFIDENCE: {p.match_confidence:.4f}</div>", unsafe_allow_html=True)
            
            # THE CRASH FIX: Map raw records to get their original source strings rather than calling .source on the string ID
            source_map = {r.id: r.source for r in st.session_state.raw_records}
            
            for src_id in p.source_records:
                src_name = source_map.get(src_id, "unknown")
                st.markdown(f"<div style='border-left: 2px solid #30363d; padding-left: 1rem; margin-bottom: 0.5rem;'>System: <span style='color: #58a6ff;'>{src_name.upper()}</span><br><span style='color: #8b949e; font-size: 0.9em;'>Node ID: {src_id}</span></div>", unsafe_allow_html=True)
            
            st.markdown("<div style='margin-top: 1rem; padding: 1rem; background-color: #161b22; border-radius: 6px;'>", unsafe_allow_html=True)
            st.markdown("**Synthesis Ledger:**")
            for log in p.resolution_log:
                st.markdown(f"<div style='font-family: monospace; font-size: 0.85em; color: #8b949e;'>↳ {log}</div>", unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)