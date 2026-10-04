"""
Entity Resolver — matches normalized records across sources into
unified Person and Facility entities using weighted scoring.

Key behaviors:
  - Schedule per-day rows are excluded from pairwise resolution (too many rows),
    but are linked to persons by name after clusters are built.
  - Persons resolved from a single source are flagged as "unmatched" so they
    surface in the review queue.
"""

from __future__ import annotations
from collections import defaultdict

from app.models.entities import (
    NormalizedRecord, PersonEntity, FacilityEntity, FieldStatus,
)
from app.reconcile.confidence import (
    compute_match_score, OVERALL_MATCH_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pick_best_value(field: str, records: list[NormalizedRecord]) -> tuple[str, dict[str, str]]:
    """Among several records, pick the best value for a field."""
    values_by_source: dict[str, str] = {}
    for rec in records:
        val = rec.data.get(field, "")
        if val:
            key = rec.source
            # If there are multiple records from the same source (e.g. 2 payroll periods),
            # keep the first non-empty value.
            if key not in values_by_source:
                values_by_source[key] = str(val)

    if not values_by_source:
        return ("", {})

    # Prefer the source with highest confidence for this field
    best_val = ""
    best_conf = -1.0
    for rec in records:
        val = rec.data.get(field, "")
        conf = rec.confidence.get(field, 0.5)
        if val and conf > best_conf:
            best_conf = conf
            best_val = str(val)

    return (best_val, values_by_source)


def _determine_field_status(
    field: str,
    records: list[NormalizedRecord],
    expected_fields: dict[str, set[str]],
) -> FieldStatus | None:
    """
    Determine if a field is MATCH, MISMATCH, or MISSING across sources.
    Only considers sources where the field is EXPECTED — this eliminates
    noise like 'payroll has no phone number'.
    Returns None if the field is not expected in any linked source.
    """
    relevant_sources = [r for r in records if field in expected_fields.get(r.source, set())]

    if not relevant_sources:
        return None  # field not relevant to these sources

    values: dict[str, str] = {}  # source -> value (deduplicated by source)
    for rec in relevant_sources:
        val = rec.data.get(field, "")
        if val and rec.source not in values:
            values[rec.source] = str(val).strip().lower()

    sources_with = len(values)
    sources_expected = len(set(r.source for r in relevant_sources))

    if sources_with == 0:
        return FieldStatus.MISSING

    if sources_with < sources_expected:
        return FieldStatus.MISSING

    unique_vals = set(values.values())
    if len(unique_vals) == 1:
        return FieldStatus.MATCH

    return FieldStatus.MISMATCH


# ---------------------------------------------------------------------------
# Person resolution
# ---------------------------------------------------------------------------

def resolve_persons(
    all_records: list[NormalizedRecord],
) -> list[PersonEntity]:
    """
    Group normalized records into unified PersonEntity objects.
    """
    from app.ingest.normalizer import SOURCE_EXPECTED_FIELDS

    # Separate schedule per-day rows from the records used for pairwise matching
    person_records = [r for r in all_records if r.source != "schedule"]

    # --- Union-find clustering ---
    n = len(person_records)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    match_logs: dict[tuple[int, int], tuple[float, dict]] = {}

    for i in range(n):
        for j in range(i + 1, n):
            score, breakdown = compute_match_score(person_records[i], person_records[j])
            if score >= OVERALL_MATCH_THRESHOLD:
                union(i, j)
                match_logs[(i, j)] = (score, breakdown)

    # Group by cluster root
    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        clusters[find(i)].append(i)

    # --- Build PersonEntity per cluster ---
    persons: list[PersonEntity] = []

    for indices in clusters.values():
        recs = [person_records[i] for i in indices]
        person = PersonEntity()
        person.source_records = [r.raw_record_id for r in recs]

        # Pick best values
        fields_to_resolve = [
            "canonical_name", "first_name", "last_name", "employee_id",
            "job_title", "facility", "phone", "license_number",
            "license_type", "license_expiration", "hire_date",
        ]

        for field in fields_to_resolve:
            best_val, vals_by_source = _pick_best_value(field, recs)
            if hasattr(person, field):
                setattr(person, field, best_val)

            if field == "canonical_name":
                continue

            status = _determine_field_status(field, recs, SOURCE_EXPECTED_FIELDS)
            if status is not None:
                person.field_statuses[field] = status

        # Build canonical_name if empty
        if not person.canonical_name:
            person.canonical_name = f"{person.first_name} {person.last_name}".strip()

        # Resolution log
        sources = sorted(set(r.source for r in recs))
        person.resolution_log.append(
            f"Merged {len(recs)} records from sources: {', '.join(sources)}"
        )

        # Flag single-source persons as unmatched
        if len(sources) == 1:
            person.resolution_log.append(
                f"⚠ UNMATCHED: Only found in '{sources[0]}' — no corroborating source. "
                f"Needs human review."
            )

        for (i_idx, j_idx), (score, breakdown) in match_logs.items():
            if i_idx in indices or j_idx in indices:
                rec_a = person_records[i_idx]
                rec_b = person_records[j_idx]
                name_a = rec_a.data.get("canonical_name", "?")
                name_b = rec_b.data.get("canonical_name", "?")
                lic_a = rec_a.data.get("license_number", "")
                person.resolution_log.append(
                    f"Matched {rec_a.source} '{name_a}' to {rec_b.source} '{name_b}' "
                    f"via score={score:.2f} "
                    f"(license={breakdown['license_exact']:.0f}, "
                    f"name={breakdown['name_fuzzy']:.2f}, "
                    f"fac_role={breakdown['facility_role']:.2f})"
                    + (f", license_number={lic_a}" if lic_a else "")
                )

        relevant_scores = [
            score
            for (i_idx, j_idx), (score, _) in match_logs.items()
            if i_idx in indices or j_idx in indices
        ]
        person.match_confidence = max(
            relevant_scores,
            default=(0.5 if len(sources) == 1 else 0.0),
        )
        # Store the source set for later use
        person.resolution_log.append(f"Sources: {sources}")

        persons.append(person)

    return persons


def _flatten_pair(pair: tuple[int, int]) -> list[int]:
    return [pair[0], pair[1]]


# ---------------------------------------------------------------------------
# Facility resolution
# ---------------------------------------------------------------------------

def resolve_facilities(all_records: list[NormalizedRecord]) -> list[FacilityEntity]:
    """Build unified facility entities from all records."""
    facility_names: dict[str, set[str]] = defaultdict(set)

    for rec in all_records:
        fac = rec.data.get("facility", "")
        if fac:
            facility_names[fac].add(fac)

    facilities = []
    for canonical, aliases in facility_names.items():
        fac = FacilityEntity(
            canonical_name=canonical,
            aliases=sorted(aliases),
        )
        facilities.append(fac)

    return facilities
