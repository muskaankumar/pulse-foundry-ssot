"""
Initialize the database.

    python -m app.db.init_db              # create tables only
    python -m app.db.init_db --sample     # create tables + load sample data
    python -m app.db.init_db --reset      # drop and recreate tables

Run from the project root.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description="Initialize the Pulse Foundry database")
    parser.add_argument("--sample", action="store_true", help="Load sample data after creating tables")
    parser.add_argument("--reset", action="store_true", help="Drop all tables and recreate them")
    args = parser.parse_args()

    from app.db.session import create_tables, drop_tables, SessionLocal, DATABASE_URL
    print(f"Database: {DATABASE_URL}")

    if args.reset:
        print("Dropping all tables...")
        drop_tables()

    print("Creating tables...")
    create_tables()
    print("Tables ready.")

    if args.sample:
        _load_sample()


def _load_sample():
    """Ingest the four sample files and run reconciliation."""
    from app.db.session import SessionLocal
    from app.db import repository as repo
    from app.ingest.csv_loader import load_csv
    from app.ingest.pdf_loader import load_pdf
    from app.ingest.normalizer import normalize_batch
    from app.reconcile.entity_resolver import resolve_persons, resolve_facilities
    from app.reconcile.conflict_detector import detect_conflicts

    SAMPLE_DIR = ROOT / "data" / "sample"
    ORG = "harborview"
    files = {
        "hr_roster.csv": "csv",
        "payroll.csv": "csv",
        "licenses.csv": "csv",
        "weekly_schedule.pdf": "pdf",
    }

    db = SessionLocal()
    all_raw, all_norm = [], []

    try:
        for filename, kind in files.items():
            path = SAMPLE_DIR / filename
            if not path.exists():
                print(f"  skip {filename} (not found)")
                continue

            content = path.read_bytes()
            if repo.file_already_ingested(db, ORG, content):
                print(f"  skip {filename} (already loaded)")
                continue

            if kind == "pdf":
                raw = load_pdf(content, filename)
                source = "schedule"
            else:
                source, raw = load_csv(content, filename)

            log_id = repo.write_ingestion(db, ORG, filename, source, len(raw), content)
            repo.write_raw_records(db, ORG, raw, log_id)
            normalized = normalize_batch(raw)
            repo.write_normalized_records(db, ORG, normalized)

            all_raw.extend(raw)
            all_norm.extend(normalized)
            print(f"  {filename} → {source}, {len(raw)} records")

        if not all_norm:
            # Load from DB if files were already there
            all_raw = repo.load_raw_records(db, ORG)
            all_norm = repo.load_normalized_records(db, ORG)

        if all_norm:
            persons = resolve_persons(all_norm)
            facilities = resolve_facilities(all_norm)
            conflicts = detect_conflicts(persons, all_norm)
            run_id = repo.write_reconciliation(db, ORG, persons, facilities, conflicts)
            print(f"\nReconciliation complete: {len(persons)} persons, "
                  f"{len(facilities)} facilities, {len(conflicts)} conflicts")
            print(f"Run ID: {run_id}")

        db.commit()
        print("Done.")

    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
