"""
Confidence scoring for entity resolution.
Uses a weighted scoring system combining license match, fuzzy name match,
and facility + role match.

The key fix: name-only matches (no license overlap) can still link records
because the name weight alone can cross threshold when the name is strong.
Weights are re-balanced so that:
  - license_exact (0.40) + name (0.35) + fac/role (0.25) = 1.0
  - A strong name + matching facility/role = 0.35*1.0 + 0.25*1.0 = 0.60 → passes 0.55
  - A weak name alone = 0.35*0.7 = 0.245 → fails (good, prevents false merges)
"""

from __future__ import annotations

from rapidfuzz import fuzz

from app.models.entities import NormalizedRecord


# Weights for the matching dimensions
WEIGHTS = {
    "license_exact": 0.40,   # Exact license number match — strongest signal
    "name_fuzzy": 0.35,      # Fuzzy name similarity
    "facility_role": 0.25,   # Facility + role agreement
}

# Thresholds
NAME_MATCH_THRESHOLD = 75       # minimum fuzzy ratio to consider a name match
OVERALL_MATCH_THRESHOLD = 0.55  # minimum combined score to link two records


def name_similarity(rec_a: NormalizedRecord, rec_b: NormalizedRecord) -> float:
    """Compute fuzzy name similarity between two records (0-100 scale)."""
    name_a = rec_a.data.get("canonical_name", "").lower().strip()
    name_b = rec_b.data.get("canonical_name", "").lower().strip()
    if not name_a or not name_b:
        return 0.0

    # Token-sort handles word order ("Sofia Reyes" vs "Reyes Sofia")
    ratio = fuzz.token_sort_ratio(name_a, name_b)
    return float(ratio)


def license_match(rec_a: NormalizedRecord, rec_b: NormalizedRecord) -> float:
    """Returns 1.0 for exact license number match, else 0.0."""
    lic_a = rec_a.data.get("license_number", "").strip().upper()
    lic_b = rec_b.data.get("license_number", "").strip().upper()
    if lic_a and lic_b and lic_a == lic_b:
        return 1.0
    return 0.0


def facility_role_match(rec_a: NormalizedRecord, rec_b: NormalizedRecord) -> float:
    """Score based on facility and job title agreement."""
    score = 0.0
    fac_a = rec_a.data.get("facility", "").lower()
    fac_b = rec_b.data.get("facility", "").lower()
    if fac_a and fac_b and fac_a == fac_b:
        score += 0.5

    job_a = rec_a.data.get("job_title", "").lower()
    job_b = rec_b.data.get("job_title", "").lower()
    if job_a and job_b:
        if job_a == job_b:
            score += 0.5
        elif fuzz.ratio(job_a, job_b) > 70:
            score += 0.3

    return score


def compute_match_score(
    rec_a: NormalizedRecord,
    rec_b: NormalizedRecord,
) -> tuple[float, dict[str, float]]:
    """
    Compute a weighted match score between two normalized records.
    Returns (overall_score, breakdown_dict).
    """
    lic = license_match(rec_a, rec_b)
    name_sim = name_similarity(rec_a, rec_b) / 100.0  # normalize to 0-1
    fac_role = facility_role_match(rec_a, rec_b)

    overall = (
        WEIGHTS["license_exact"] * lic
        + WEIGHTS["name_fuzzy"] * name_sim
        + WEIGHTS["facility_role"] * fac_role
    )

    breakdown = {
        "license_exact": lic,
        "name_fuzzy": name_sim,
        "facility_role": fac_role,
        "overall": overall,
    }
    return overall, breakdown
