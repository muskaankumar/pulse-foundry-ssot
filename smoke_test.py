"""
End-to-end smoke test: ingest all sample files → normalize → reconcile → generate reports.
"""

import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))

from app.ingest.csv_loader import load_csv
from app.ingest.pdf_loader import load_pdf
from app.ingest.normalizer import normalize_batch
from app.reconcile.entity_resolver import resolve_persons, resolve_facilities
from app.reconcile.conflict_detector import detect_conflicts
from app.domain.healthcare import HealthcareDomainPlugin

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "sample")

def main():
    all_raw = []
    all_normalized = []

    # --- Ingest CSVs ---
    for fname in ["hr_roster.csv", "payroll.csv", "licenses.csv"]:
        path = os.path.join(DATA_DIR, fname)
        with open(path, "r") as f:
            content = f.read()
        src, recs = load_csv(content, fname)
        print(f"[INGEST] {fname} → detected as '{src}', {len(recs)} records")
        all_raw.extend(recs)
        all_normalized.extend(normalize_batch(recs))

    # --- Ingest PDF ---
    pdf_path = os.path.join(DATA_DIR, "weekly_schedule.pdf")
    pdf_recs = load_pdf(pdf_path, "weekly_schedule.pdf")
    print(f"[INGEST] weekly_schedule.pdf → {len(pdf_recs)} schedule entries")
    all_raw.extend(pdf_recs)
    all_normalized.extend(normalize_batch(pdf_recs))

    print(f"\nTotal raw records: {len(all_raw)}")
    print(f"Total normalized: {len(all_normalized)}")

    # --- Reconcile ---
    persons = resolve_persons(all_normalized)
    facilities = resolve_facilities(all_normalized)
    conflicts = detect_conflicts(persons, all_normalized)

    print(f"\n[RECONCILE] Persons resolved: {len(persons)}")
    print(f"[RECONCILE] Facilities resolved: {len(facilities)}")
    print(f"[RECONCILE] Conflicts found: {len(conflicts)}")

    for p in persons:
        print(f"  • {p.canonical_name} | {p.job_title} | {p.facility} | "
              f"lic={p.license_number} | sources={len(p.source_records)} | "
              f"conflicts={len(p.conflicts)} | conf={p.match_confidence:.0%}")

    print(f"\nTop conflicts:")
    for c in conflicts[:5]:
        print(f"  [{c.severity.name}] {c.description}")

    # --- Reports ---
    domain = HealthcareDomainPlugin()
    schedule_recs = [r for r in all_normalized if r.source == "schedule"]

    cap = domain.generate_capacity_report(persons, schedule_recs, facilities)
    print(f"\n[CAPACITY] Facilities: {list(cap['summary'].keys())}")
    for w in cap.get("license_warnings", []):
        print(f"  ⚠ {w['name']}: {w['severity']}")

    comp = domain.generate_compliance_report(persons, all_normalized, conflicts)
    discs = comp.get("hours_discrepancies", [])
    print(f"\n[COMPLIANCE] Hours discrepancies: {len(discs)}")
    for d in discs[:3]:
        print(f"  • {d['name']}: schedule={d['schedule_hours']}h, payroll={d['payroll_hours']}h ({d['direction']})")

    exp = domain.generate_expiration_report(persons)
    print(f"\n[EXPIRATIONS]")
    for tier, count in exp["summary"].items():
        print(f"  {tier}: {count}")

    print("\n✅ End-to-end smoke test passed!")

if __name__ == "__main__":
    main()
