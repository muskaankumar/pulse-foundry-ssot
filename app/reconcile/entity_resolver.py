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

# Which system is the authority for each field. When sources disagree the
# unified record takes the value from the first authority that has one; the
# disagreement itself is still surfaced by the conflict detector.
SOURCE_AUTHORITY: dict[str, list[str]] = {
    "license_number":     ["licenses", "hr"],
    "license_expiration": ["licenses", "hr"],
    "license_type":       ["licenses", "hr"],
    "employee_id":        ["hr"],
    "phone":              ["hr"],
    "hire_date":          ["hr"],
    "job_title":          ["hr", "payroll", "schedule"],
    "facility":           ["hr", "payroll", "schedule"],
    "first_name":         ["hr", "licenses", "payroll", "schedule"],
    "last_name":          ["hr", "licenses", "payroll", "schedule"],
    "canonical_name":     ["hr", "licenses", "payroll", "schedule"],
}

SOURCE_LABELS = {
    "hr": "HR roster",
    "payroll": "Payroll",
    "licenses": "Licensing board",
    "schedule": "Schedule",
}


def _pick_best_value(field: str, records: list[NormalizedRecord]) -> tuple[str, dict[str, str]]:
    """Among several records, pick the authoritative value for a field."""
    values_by_source: dict[str, str] = {}
    for rec in records:
        val = rec.data.get(field, "")
        if val and rec.source not in values_by_source:
            values_by_source[rec.source] = str(val)

    if not values_by_source:
        return ("", {})

    for src in SOURCE_AUTHORITY.get(field, []):
        if src in values_by_source:
            return (values_by_source[src], values_by_source)

    # No declared authority: fall back to the most confident normalization
    best_val, best_conf = "", -1.0
    for rec in records:
        val = rec.data.get(field, "")
        conf = rec.confidence.get(field, 0.5)
        if val and conf > best_conf:
            best_conf, best_val = conf, str(val)
    return (best_val, values_by_source)


def _match_signals(breakdown: dict, rec_a: NormalizedRecord, rec_b: NormalizedRecord) -> list[str]:
    """Translate a score breakdown into the reasons a person can read."""
    signals: list[str] = []
    if breakdown.get("license_exact") == 1.0:
        signals.append(f"same license number ({rec_a.data.get('license_number', '')})")
    name_sim = breakdown.get("name_fuzzy", 0.0)
    if name_sim >= 0.999:
        signals.append("identical name after cleanup")
    elif name_sim >= 0.85:
        signals.append("name matches after cleanup")
    elif name_sim >= 0.7:
        signals.append("similar name (possible typo)")
    fac_a, fac_b = rec_a.data.get("facility", ""), rec_b.data.get("facility", "")
    if fac_a and fac_b and fac_a.lower() == fac_b.lower():
        signals.append(f"same facility ({fac_a})")
    job_a, job_b = rec_a.data.get("job_title", ""), rec_b.data.get("job_title", "")
    if job_a and job_b and job_a.lower() == job_b.lower():
        signals.append(f"same role ({job_a})")
    return signals


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

        cluster_pairs = {
            (i_idx, j_idx): v for (i_idx, j_idx), v in match_logs.items()
            if i_idx in indices and j_idx in indices
        }
        for (i_idx, j_idx), (score, breakdown) in cluster_pairs.items():
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

        # Plain-language evidence: for every record, the strongest link that
        # attached it to the rest of the cluster. The anchor is the HR record.
        anchor_pos = next((k for k, idx in enumerate(indices) if person_records[idx].source == "hr"), 0)
        anchor_idx = indices[anchor_pos]
        for idx in indices:
            if idx == anchor_idx:
                continue
            best = None
            for (i_idx, j_idx), (score, breakdown) in cluster_pairs.items():
                if idx in (i_idx, j_idx):
                    other = j_idx if i_idx == idx else i_idx
                    if best is None or score > best[0]:
                        best = (score, breakdown, other)
            if best is None:
                continue
            score, breakdown, other = best
            rec, partner = person_records[idx], person_records[other]
            person.match_evidence.append({
                "record_id": rec.raw_record_id,
                "source": rec.source,
                "source_label": SOURCE_LABELS.get(rec.source, rec.source),
                "name_as_written": rec.data.get("canonical_name", ""),
                "linked_to_source": SOURCE_LABELS.get(partner.source, partner.source),
                "linked_to_name": partner.data.get("canonical_name", ""),
                "signals": _match_signals(breakdown, rec, partner),
            })

        relevant_scores = [score for (score, _) in cluster_pairs.values()]
        person.match_confidence = max(
            relevant_scores,
            default=(0.5 if len(sources) == 1 else 0.0),
        )
        person.sources = sources
        person.resolution_log.append(f"Sources: {sources}")

        persons.append(person)

    _link_schedule_rows(persons, all_records)
    return persons


SCHEDULE_NAME_THRESHOLD = 85


def _link_schedule_rows(persons: list[PersonEntity], all_records: list[NormalizedRecord]) -> None:
    """Attach schedule rows to people by name (facility breaks ties).

    Anyone on the schedule who can't be matched becomes their own
    schedule-only person so they surface in review instead of vanishing.
    """
    from rapidfuzz import fuzz

    schedule_rows = [r for r in all_records if r.source == "schedule"]
    if not schedule_rows:
        return

    cache: dict[tuple[str, str], PersonEntity | None] = {}
    orphans: dict[tuple[str, str], list[NormalizedRecord]] = defaultdict(list)

    for rec in schedule_rows:
        name = rec.data.get("canonical_name", "")
        fac = rec.data.get("facility", "")
        if not name:
            continue
        key = (name.lower(), fac.lower())
        if key not in cache:
            best, best_score = None, 0.0
            for p in persons:
                score = fuzz.token_sort_ratio(name.lower(), p.canonical_name.lower())
                if fac and p.facility and fac.lower() == p.facility.lower():
                    score += 3  # tie-breaker only
                if score > best_score:
                    best, best_score = p, score
            cache[key] = best if best_score >= SCHEDULE_NAME_THRESHOLD else None
        person = cache[key]
        if person is None:
            orphans[key].append(rec)
            continue
        person.schedule_records.append(rec.raw_record_id)
        if "schedule" not in person.sources:
            person.sources = sorted(set(person.sources) | {"schedule"})
            person.resolution_log.append(
                f"Linked schedule rows for '{name}' ({fac or 'no facility'}) by name"
            )

    for (_, _), recs in orphans.items():
        first = recs[0]
        p = PersonEntity(
            canonical_name=first.data.get("canonical_name", ""),
            first_name=first.data.get("first_name", ""),
            last_name=first.data.get("last_name", ""),
            facility=first.data.get("facility", ""),
            job_title=first.data.get("job_title", ""),
        )
        ids = [r.raw_record_id for r in recs]
        p.source_records = ids
        p.schedule_records = list(ids)
        p.sources = ["schedule"]
        p.resolution_log.append(
            "⚠ UNMATCHED: Only found in 'schedule' — no HR, payroll or license record."
        )
        persons.append(p)


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
