"""
Conflict Detector — scans PersonEntities for mismatches, missing data,
stale values, and expired credentials.  Produces a prioritized conflict report.

Key improvement: only flags MISSING when the field is natural to a linked source,
and distinguishes STALE (same field, different values in sources that should
agree) from general MISMATCH.
"""

from __future__ import annotations
from datetime import datetime, date

from app.models.entities import (
    PersonEntity, NormalizedRecord, Conflict,
    FieldStatus, ConflictSeverity,
)


# Map fields to severity when they mismatch or are missing
SEVERITY_MAP_MISMATCH = {
    "license_number": ConflictSeverity.CRITICAL,
    "license_expiration": ConflictSeverity.CRITICAL,
    "license_type": ConflictSeverity.HIGH,
    "job_title": ConflictSeverity.HIGH,
    "facility": ConflictSeverity.HIGH,
    "first_name": ConflictSeverity.MEDIUM,
    "last_name": ConflictSeverity.MEDIUM,
    "employee_id": ConflictSeverity.MEDIUM,
    "phone": ConflictSeverity.LOW,
    "hire_date": ConflictSeverity.LOW,
}

SEVERITY_MAP_MISSING = {
    "license_number": ConflictSeverity.HIGH,
    "license_expiration": ConflictSeverity.HIGH,
    "employee_id": ConflictSeverity.MEDIUM,
    "phone": ConflictSeverity.LOW,
    "hire_date": ConflictSeverity.LOW,
}


def _get_values_by_source(
    field: str,
    linked_recs: list[NormalizedRecord],
) -> dict[str, str]:
    """Collect the value each source has for a field (one per source)."""
    vals: dict[str, str] = {}
    for rec in linked_recs:
        val = rec.data.get(field, "")
        if val and rec.source not in vals:
            vals[rec.source] = str(val)
    return vals


def detect_conflicts(
    persons: list[PersonEntity],
    all_normalized: list[NormalizedRecord],
) -> list[Conflict]:
    """
    Scan all PersonEntities for field-level conflicts and produce
    a prioritized list sorted by severity.
    """
    from app.ingest.normalizer import SOURCE_EXPECTED_FIELDS

    rec_index: dict[str, NormalizedRecord] = {r.raw_record_id: r for r in all_normalized}

    all_conflicts: list[Conflict] = []

    for person in persons:
        linked_recs = [rec_index[rid] for rid in person.source_records if rid in rec_index]
        linked_sources = set(r.source for r in linked_recs)

        # --- Unmatched / single-source person ---
        if len(linked_sources) == 1:
            src = list(linked_sources)[0]
            conflict = Conflict(
                person_id=person.id,
                field_name="_entity",
                status=FieldStatus.MISSING,
                severity=ConflictSeverity.HIGH,
                values_by_source={src: person.canonical_name},
                description=(
                    f"{person.canonical_name}: only appears in '{src}' — "
                    f"no corroborating record in other systems. Needs human review."
                ),
            )
            all_conflicts.append(conflict)
            person.conflicts.append(conflict)

        # --- Field-level conflicts ---
        for field, status in person.field_statuses.items():
            if status == FieldStatus.MATCH:
                continue

            vals_by_src = _get_values_by_source(field, linked_recs)

            if status == FieldStatus.MISMATCH:
                severity = SEVERITY_MAP_MISMATCH.get(field, ConflictSeverity.MEDIUM)

                # Upgrade to STALE for license_expiration mismatch (HR vs license authority)
                if field == "license_expiration" and len(set(vals_by_src.values())) > 1:
                    status = FieldStatus.STALE

                desc = (
                    f"{person.canonical_name}: '{field}' disagrees across sources — "
                    + ", ".join(f"{src}='{val}'" for src, val in vals_by_src.items())
                )

            elif status == FieldStatus.MISSING:
                severity = SEVERITY_MAP_MISSING.get(field, ConflictSeverity.LOW)
                present = [s for s in vals_by_src]
                expected_but_missing = [
                    s for s in linked_sources
                    if field in SOURCE_EXPECTED_FIELDS.get(s, set())
                    and s not in vals_by_src
                ]
                if not expected_but_missing:
                    continue  # not actually missing from any source that should have it
                desc = (
                    f"{person.canonical_name}: '{field}' present in {present} "
                    f"but missing from {expected_but_missing}"
                )
            else:
                severity = ConflictSeverity.MEDIUM
                desc = f"{person.canonical_name}: '{field}' status={status.value}"

            conflict = Conflict(
                person_id=person.id,
                field_name=field,
                status=status,
                severity=severity,
                values_by_source=vals_by_src,
                description=desc,
            )
            all_conflicts.append(conflict)
            person.conflicts.append(conflict)

        # --- Check for expired / expiring licenses ---
        exp_str = person.license_expiration
        if exp_str:
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                today = date.today()
                days_left = (exp_date - today).days

                if days_left < 0:
                    conflict = Conflict(
                        person_id=person.id,
                        field_name="license_expiration",
                        status=FieldStatus.STALE,
                        severity=ConflictSeverity.CRITICAL,
                        values_by_source={"computed": exp_str},
                        description=(
                            f"EXPIRED: {person.canonical_name}'s license "
                            f"({person.license_number}) expired on {exp_str} "
                            f"({abs(days_left)} days ago)"
                        ),
                    )
                    all_conflicts.append(conflict)
                    person.conflicts.append(conflict)
                elif days_left <= 30:
                    conflict = Conflict(
                        person_id=person.id,
                        field_name="license_expiration",
                        status=FieldStatus.STALE,
                        severity=ConflictSeverity.CRITICAL,
                        values_by_source={"computed": exp_str},
                        description=(
                            f"EXPIRING SOON: {person.canonical_name}'s license "
                            f"({person.license_number}) expires on {exp_str} "
                            f"({days_left} days remaining)"
                        ),
                    )
                    all_conflicts.append(conflict)
                    person.conflicts.append(conflict)
                elif days_left <= 90:
                    conflict = Conflict(
                        person_id=person.id,
                        field_name="license_expiration",
                        status=FieldStatus.STALE,
                        severity=ConflictSeverity.HIGH,
                        values_by_source={"computed": exp_str},
                        description=(
                            f"EXPIRING <90 DAYS: {person.canonical_name}'s license "
                            f"({person.license_number}) expires on {exp_str} "
                            f"({days_left} days remaining)"
                        ),
                    )
                    all_conflicts.append(conflict)
                    person.conflicts.append(conflict)
            except ValueError:
                pass

    # Sort by severity (CRITICAL first)
    all_conflicts.sort(key=lambda c: c.severity.value)

    return all_conflicts
