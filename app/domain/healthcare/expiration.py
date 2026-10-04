"""
Module C — Expiration Tracker
Computes days until expiration for every credential, sorts by urgency,
and cross-checks license data across sources.
"""

from __future__ import annotations
from datetime import datetime, date

from app.models.entities import PersonEntity, UrgencyTier


def _compute_urgency(expiration_str: str) -> tuple[UrgencyTier, int | None]:
    """Determine urgency tier and days remaining from an expiration date string."""
    if not expiration_str:
        return (UrgencyTier.OK, None)
    try:
        exp_date = datetime.strptime(expiration_str, "%Y-%m-%d").date()
        days_left = (exp_date - date.today()).days
        if days_left < 0:
            return (UrgencyTier.EXPIRED, days_left)
        elif days_left <= 30:
            return (UrgencyTier.EXPIRING_30, days_left)
        elif days_left <= 90:
            return (UrgencyTier.EXPIRING_90, days_left)
        else:
            return (UrgencyTier.OK, days_left)
    except ValueError:
        return (UrgencyTier.OK, None)


def build_expiration_report(persons: list[PersonEntity]) -> dict:
    """
    Build the credential expiration report.

    Returns:
    {
        "credentials": [
            {
                "name", "employee_id", "facility", "license_number",
                "license_type", "expiration_date", "days_remaining",
                "urgency", "license_source_match"
            }
        ],
        "summary": {
            "EXPIRED": N, "EXPIRING_30": N, "EXPIRING_90": N, "OK": N
        }
    }
    """
    credentials: list[dict] = []
    summary = {t.value: 0 for t in UrgencyTier}

    for person in persons:
        if not person.license_number:
            continue

        urgency, days_left = _compute_urgency(person.license_expiration)
        summary[urgency.value] += 1

        # Check if license field has a mismatch across sources
        lic_status = person.field_statuses.get("license_number")
        exp_status = person.field_statuses.get("license_expiration")

        credentials.append({
            "name": person.canonical_name,
            "employee_id": person.employee_id,
            "facility": person.facility,
            "license_number": person.license_number,
            "license_type": person.license_type,
            "expiration_date": person.license_expiration,
            "days_remaining": days_left,
            "urgency": urgency.value,
            "license_number_consistent": lic_status.value if lic_status else "SINGLE_SOURCE",
            "expiration_consistent": exp_status.value if exp_status else "SINGLE_SOURCE",
        })

    # Sort: EXPIRED first, then EXPIRING_30, then EXPIRING_90, then OK
    tier_order = {
        UrgencyTier.EXPIRED.value: 0,
        UrgencyTier.EXPIRING_30.value: 1,
        UrgencyTier.EXPIRING_90.value: 2,
        UrgencyTier.OK.value: 3,
    }
    credentials.sort(key=lambda c: (
        tier_order.get(c["urgency"], 99),
        c.get("days_remaining") if c.get("days_remaining") is not None else 9999,
    ))

    return {
        "credentials": credentials,
        "summary": summary,
    }
