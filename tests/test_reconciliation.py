"""
Tests for the reconciliation pipeline.
Validates ingestion, normalization, entity resolution, and conflict detection.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.ingest.csv_loader import load_csv
from app.ingest.normalizer import normalize_name, normalize_facility, normalize_job, normalize_batch
from app.reconcile.confidence import compute_match_score, name_similarity
from app.reconcile.entity_resolver import resolve_persons
from app.reconcile.conflict_detector import detect_conflicts
from app.models.entities import NormalizedRecord


# ---------------------------------------------------------------------------
# Normalizer tests
# ---------------------------------------------------------------------------

class TestNameNormalization:
    def test_last_first_format(self):
        first, last, conf = normalize_name("REYES, SOFIA")
        assert first == "Sofia"
        assert last == "Reyes"
        assert conf > 0.9

    def test_first_last_format(self):
        first, last, conf = normalize_name("sofia reyes")
        assert first == "Sofia"
        assert last == "Reyes"
        assert conf > 0.8

    def test_all_caps(self):
        first, last, conf = normalize_name("JAMES OKONKWO")
        assert first == "James"
        assert last == "Okonkwo"

    def test_single_name(self):
        first, last, conf = normalize_name("Sofia")
        assert first == "Sofia"
        assert last == ""
        assert conf < 0.8

    def test_empty(self):
        first, last, conf = normalize_name("")
        assert first == ""
        assert conf == 0.0


class TestFacilityNormalization:
    def test_abbreviation(self):
        import json
        from pathlib import Path
        aliases_path = Path(__file__).resolve().parents[1] / "config" / "aliases.json"
        with open(aliases_path) as f:
            aliases = json.load(f)

        fac, conf = normalize_facility("BYS", aliases)
        assert fac == "Bayside"
        assert conf == 1.0

    def test_full_name(self):
        import json
        from pathlib import Path
        aliases_path = Path(__file__).resolve().parents[1] / "config" / "aliases.json"
        with open(aliases_path) as f:
            aliases = json.load(f)

        fac, conf = normalize_facility("Riverdale", aliases)
        assert fac == "Riverdale"

    def test_unknown(self):
        fac, conf = normalize_facility("Springfield", {})
        assert conf == 0.5


class TestJobNormalization:
    def test_code(self):
        import json
        from pathlib import Path
        aliases_path = Path(__file__).resolve().parents[1] / "config" / "aliases.json"
        with open(aliases_path) as f:
            aliases = json.load(f)

        job, conf = normalize_job("RN", aliases)
        assert job == "Registered Nurse"
        assert conf == 1.0

    def test_full_title(self):
        import json
        from pathlib import Path
        aliases_path = Path(__file__).resolve().parents[1] / "config" / "aliases.json"
        with open(aliases_path) as f:
            aliases = json.load(f)

        job, conf = normalize_job("Registered Nurse", aliases)
        assert job == "Registered Nurse"


# ---------------------------------------------------------------------------
# Confidence scoring tests
# ---------------------------------------------------------------------------

class TestConfidence:
    def test_name_similarity_exact(self):
        a = NormalizedRecord(data={"canonical_name": "Sofia Reyes"})
        b = NormalizedRecord(data={"canonical_name": "Sofia Reyes"})
        score = name_similarity(a, b)
        assert score == 100.0

    def test_name_similarity_case_insensitive(self):
        a = NormalizedRecord(data={"canonical_name": "SOFIA REYES"})
        b = NormalizedRecord(data={"canonical_name": "Sofia Reyes"})
        score = name_similarity(a, b)
        assert score > 90

    def test_match_score_with_license(self):
        a = NormalizedRecord(data={
            "canonical_name": "Sofia Reyes",
            "license_number": "RN-551203",
            "facility": "Bayside",
            "job_title": "Registered Nurse",
        })
        b = NormalizedRecord(data={
            "canonical_name": "REYES SOFIA",
            "license_number": "RN-551203",
            "facility": "Bayside",
            "job_title": "Registered Nurse",
        })
        score, breakdown = compute_match_score(a, b)
        assert score > 0.8
        assert breakdown["license_exact"] == 1.0


# ---------------------------------------------------------------------------
# Entity resolution tests
# ---------------------------------------------------------------------------

class TestEntityResolution:
    def test_matches_same_person_across_sources(self):
        hr_rec = NormalizedRecord(
            source="hr",
            data={
                "canonical_name": "Sofia Reyes",
                "first_name": "Sofia",
                "last_name": "Reyes",
                "license_number": "RN-551203",
                "facility": "Bayside",
                "job_title": "Registered Nurse",
                "employee_id": "E201",
            },
        )
        payroll_rec = NormalizedRecord(
            source="payroll",
            data={
                "canonical_name": "Sofia Reyes",
                "first_name": "Sofia",
                "last_name": "Reyes",
                "facility": "Bayside",
                "job_title": "Registered Nurse",
            },
        )
        license_rec = NormalizedRecord(
            source="license",
            data={
                "canonical_name": "Sofia Reyes",
                "first_name": "Sofia",
                "last_name": "Reyes",
                "license_number": "RN-551203",
                "license_type": "Registered Nurse",
            },
        )

        persons = resolve_persons([hr_rec, payroll_rec, license_rec])
        # Should merge into 1 person
        assert len(persons) == 1
        assert persons[0].canonical_name == "Sofia Reyes"

    def test_keeps_different_people_separate(self):
        rec_a = NormalizedRecord(
            source="hr",
            data={
                "canonical_name": "Sofia Reyes",
                "license_number": "RN-551203",
                "facility": "Bayside",
            },
        )
        rec_b = NormalizedRecord(
            source="hr",
            data={
                "canonical_name": "James Okonkwo",
                "license_number": "CNA-884710",
                "facility": "Bayside",
            },
        )
        persons = resolve_persons([rec_a, rec_b])
        assert len(persons) == 2


# ---------------------------------------------------------------------------
# CSV ingestion integration test
# ---------------------------------------------------------------------------

class TestCSVIngestion:
    def test_hr_csv_detection(self):
        csv_content = (
            "employee_id,first_name,last_name,job_title,facility,phone,"
            "license_number,license_expiration,hire_date\n"
            "E201,Sofia,Reyes,Registered Nurse,Bayside,555-123-4567,"
            "RN-551203,03/15/2025,06/01/2019\n"
        )
        source, records = load_csv(csv_content, "hr_roster.csv")
        assert source == "hr"
        assert len(records) == 1

    def test_payroll_csv_detection(self):
        csv_content = (
            "payroll_id,employee_name,job_code,facility_code,"
            "period_start,period_end,hours_paid\n"
            "P1001,REYES SOFIA,RN,BYS,2026-09-21,2026-09-27,40\n"
        )
        source, records = load_csv(csv_content, "payroll.csv")
        assert source == "payroll"
        assert len(records) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
