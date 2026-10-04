"""
Streamlit Dashboard for Pulse Foundry — Single Source of Truth.
Run with: streamlit run frontend/dashboard.py
"""

import sys
import os

# Add project root to path so imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
import pandas as pd
from io import StringIO

from app.ingest.csv_loader import load_csv
from app.ingest.pdf_loader import load_pdf
from app.ingest.normalizer import normalize_batch
from app.reconcile.entity_resolver import resolve_persons, resolve_facilities
from app.reconcile.conflict_detector import detect_conflicts
from app.domain.healthcare import HealthcareDomainPlugin

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Pulse Foundry — Single Source of Truth",
    page_icon="🔍",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Session state initialization
# ---------------------------------------------------------------------------

if "raw_records" not in st.session_state:
    st.session_state.raw_records = []
    st.session_state.normalized_records = []
    st.session_state.persons = []
    st.session_state.facilities = []
    st.session_state.conflicts = []
    st.session_state.ingestion_log = []
    st.session_state.reconciled = False
    st.session_state.domain = HealthcareDomainPlugin()


def reset():
    st.session_state.raw_records = []
    st.session_state.normalized_records = []
    st.session_state.persons = []
    st.session_state.facilities = []
    st.session_state.conflicts = []
    st.session_state.ingestion_log = []
    st.session_state.reconciled = False


# ---------------------------------------------------------------------------
# Sidebar — file upload
# ---------------------------------------------------------------------------

st.sidebar.title("🔍 Pulse Foundry SSOT")
st.sidebar.markdown("**Upload source files**")

uploaded_files = st.sidebar.file_uploader(
    "Drop CSV or PDF files here",
    accept_multiple_files=True,
    type=["csv", "pdf"],
)

if st.sidebar.button("🔄 Reset All Data"):
    reset()
    st.rerun()

# Process uploads
if uploaded_files:
    for uf in uploaded_files:
        # Check if already ingested
        already = any(uf.name in log for log in st.session_state.ingestion_log)
        if already:
            continue

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
        st.session_state.ingestion_log.append(
            f"✅ {uf.name} → {src} ({len(raw)} records)"
        )
        st.session_state.reconciled = False  # need re-reconciliation

# Show ingestion status
if st.session_state.ingestion_log:
    st.sidebar.markdown("---")
    st.sidebar.markdown("**Ingestion Log**")
    for entry in st.session_state.ingestion_log:
        st.sidebar.text(entry)

    st.sidebar.markdown(f"**Total raw records:** {len(st.session_state.raw_records)}")
    st.sidebar.markdown(f"**Total normalized:** {len(st.session_state.normalized_records)}")

# Reconcile button
if st.session_state.normalized_records and not st.session_state.reconciled:
    if st.sidebar.button("⚡ Run Reconciliation", type="primary"):
        st.session_state.persons = resolve_persons(st.session_state.normalized_records)
        st.session_state.facilities = resolve_facilities(st.session_state.normalized_records)
        st.session_state.conflicts = detect_conflicts(
            st.session_state.persons, st.session_state.normalized_records
        )
        st.session_state.reconciled = True
        st.rerun()

# ---------------------------------------------------------------------------
# Main area
# ---------------------------------------------------------------------------

st.title("🏥 Single Source of Truth")
st.markdown("*Unified data reconciliation for Harborview Care Group*")

if not st.session_state.ingestion_log:
    st.info("👈 Upload your source files (HR roster, Payroll, Licenses, Schedule) from the sidebar to get started.")
    st.stop()

if not st.session_state.reconciled:
    st.warning("Files uploaded. Click **Run Reconciliation** in the sidebar to process.")
    st.stop()

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 Overview",
    "🏥 Capacity Dashboard",
    "📋 Compliance Report",
    "⏰ Expiration Tracker",
    "🔎 Audit Trail",
])

persons = st.session_state.persons
conflicts = st.session_state.conflicts
normalized = st.session_state.normalized_records
facilities = st.session_state.facilities
domain = st.session_state.domain

# ---- Tab 1: Overview ----
with tab1:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Employees Resolved", len(persons))
    col2.metric("Facilities", len(facilities))
    col3.metric("Conflicts Found", len(conflicts))
    critical = sum(1 for c in conflicts if c.severity.value <= 2)
    col4.metric("Critical/High", critical, delta=None)

    st.markdown("### Resolved Persons")
    person_data = []
    for p in persons:
        person_data.append({
            "Name": p.canonical_name,
            "Employee ID": p.employee_id,
            "Role": p.job_title,
            "Facility": p.facility,
            "License #": p.license_number,
            "License Exp": p.license_expiration,
            "Confidence": f"{p.match_confidence:.0%}",
            "Sources": len(p.source_records),
            "Conflicts": len(p.conflicts),
        })
    if person_data:
        st.dataframe(pd.DataFrame(person_data), use_container_width=True)

    st.markdown("### Conflict Report")
    if conflicts:
        conflict_data = []
        for c in conflicts:
            conflict_data.append({
                "Severity": c.severity.name,
                "Field": c.field_name,
                "Status": c.status.value,
                "Description": c.description,
            })
        df_conf = pd.DataFrame(conflict_data)
        st.dataframe(df_conf, use_container_width=True)
    else:
        st.success("No conflicts detected!")


# ---- Tab 2: Capacity Dashboard ----
with tab2:
    schedule_recs = [r for r in normalized if r.source == "schedule"]
    if not schedule_recs:
        st.warning("No schedule data uploaded. Upload a schedule PDF or CSV.")
    else:
        capacity = domain.generate_capacity_report(persons, schedule_recs, facilities)

        # License warnings
        warnings = capacity.get("license_warnings", [])
        if warnings:
            st.error(f"⚠️ {len(warnings)} license warning(s) affecting scheduled staff!")
            for w in warnings:
                severity = w.get("severity", "")
                if severity == "EXPIRED":
                    st.markdown(
                        f"🔴 **{w['name']}** — license `{w['license_number']}` "
                        f"**EXPIRED** on {w['expired_on']} ({w['days_overdue']} days ago)"
                    )
                else:
                    st.markdown(
                        f"🟠 **{w['name']}** — license `{w['license_number']}` "
                        f"expires on {w['expires_on']} ({w['days_remaining']} days)"
                    )

        # Summary
        st.markdown("### Staffing Summary")
        summary = capacity.get("summary", {})
        for fac, info in summary.items():
            st.markdown(f"**{fac}**: {info['total_staff_shifts']} staff-shifts scheduled")
            if info.get("roles"):
                role_str = ", ".join(f"{r}: {n}" for r, n in info["roles"].items())
                st.markdown(f"  Roles: {role_str}")

        # Per-facility per-day grid
        st.markdown("### Shift Grid")
        fac_data = capacity.get("facilities", {})
        for fac_name, days in fac_data.items():
            st.markdown(f"#### {fac_name}")
            for day_name in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
                shifts = days.get(day_name, {})
                if shifts:
                    with st.expander(f"{day_name}"):
                        for shift_name, staff_list in shifts.items():
                            names = [f"{s['name']} ({s['role']})" + (" ⚠️" if not s["license_ok"] else "") for s in staff_list]
                            st.markdown(f"**{shift_name}**: {', '.join(names)}")


# ---- Tab 3: Compliance Report ----
with tab3:
    compliance = domain.generate_compliance_report(persons, normalized, conflicts)

    st.markdown("### Staffing Summary")
    summary = compliance.get("staffing_summary", {})
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**By Role**")
        by_role = summary.get("by_role", {})
        if by_role:
            role_df = pd.DataFrame([
                {"Role": role, "Count": info["count"], "Schedule Hours": info["total_schedule_hours"]}
                for role, info in by_role.items()
            ])
            st.dataframe(role_df, use_container_width=True)

    with col2:
        st.markdown("**By Facility**")
        by_fac = summary.get("by_facility", {})
        if by_fac:
            fac_df = pd.DataFrame([
                {"Facility": fac, "Count": info["count"], "Schedule Hours": info["total_schedule_hours"]}
                for fac, info in by_fac.items()
            ])
            st.dataframe(fac_df, use_container_width=True)

    # Hours discrepancies
    discreps = compliance.get("hours_discrepancies", [])
    st.markdown("### Hours Discrepancies (Schedule vs Payroll)")
    if discreps:
        st.warning(f"{len(discreps)} discrepancy(ies) found!")
        disc_df = pd.DataFrame(discreps)
        st.dataframe(disc_df, use_container_width=True)
    else:
        st.success("Schedule and payroll hours are consistent!")

    # Conflict summary
    conf_summary = compliance.get("conflict_summary", {})
    st.markdown("### Conflict Summary")
    st.json(conf_summary)

    # Export
    st.markdown("### Export Report")
    audit = compliance.get("audit_trail", [])
    if audit:
        audit_text = "\n".join(audit)
        st.download_button(
            "📥 Download Audit Trail (TXT)",
            data=audit_text,
            file_name="compliance_audit_trail.txt",
            mime="text/plain",
        )


# ---- Tab 4: Expiration Tracker ----
with tab4:
    exp_report = domain.generate_expiration_report(persons)

    summary = exp_report.get("summary", {})
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("🔴 Expired", summary.get("EXPIRED", 0))
    col2.metric("🟠 < 30 Days", summary.get("EXPIRING_30", 0))
    col3.metric("🟡 < 90 Days", summary.get("EXPIRING_90", 0))
    col4.metric("🟢 OK", summary.get("OK", 0))

    creds = exp_report.get("credentials", [])
    if creds:
        cred_df = pd.DataFrame(creds)

        # Color-code urgency
        def color_urgency(val):
            colors = {
                "EXPIRED": "background-color: #e74c3c; color: white",
                "EXPIRING_30": "background-color: #e67e22; color: white",
                "EXPIRING_90": "background-color: #f39c12; color: white",
                "OK": "background-color: #27ae60; color: white",
            }
            return colors.get(val, "")

        styled = cred_df.style.map(color_urgency, subset=["urgency"])
        st.dataframe(styled, use_container_width=True)
    else:
        st.info("No license data found.")


# ---- Tab 5: Audit Trail ----
with tab5:
    st.markdown("### Entity Resolution Log")
    for p in persons:
        with st.expander(f"{p.canonical_name} (ID: {p.employee_id})"):
            for log in p.resolution_log:
                st.text(log)
            st.markdown("**Field Statuses:**")
            for field, status in p.field_statuses.items():
                emoji = {"MATCH": "✅", "MISMATCH": "❌", "MISSING": "⚠️", "STALE": "🔄"}.get(status.value, "❓")
                st.text(f"  {emoji} {field}: {status.value}")

    st.markdown("### Raw Records Sample")
    raw_sample = st.session_state.raw_records[:20]
    for rec in raw_sample:
        with st.expander(f"[{rec.source}] {rec.source_file} — {rec.id[:8]}"):
            st.json(rec.data)
