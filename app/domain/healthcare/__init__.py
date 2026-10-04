"""
Healthcare Domain Plugin — implements the domain-specific intelligence
layer for skilled nursing facilities.
"""

from __future__ import annotations

from app.domain.base import DomainPlugin
from app.domain.healthcare.capacity import build_capacity_dashboard
from app.domain.healthcare.compliance import build_compliance_report
from app.domain.healthcare.expiration import build_expiration_report
from app.domain.healthcare.review import build_review
from app.models.entities import PersonEntity, NormalizedRecord, FacilityEntity, Conflict


class HealthcareDomainPlugin(DomainPlugin):

    def get_name(self) -> str:
        return "Healthcare — Skilled Nursing"

    def generate_capacity_report(
        self,
        persons: list[PersonEntity],
        schedule_records: list[NormalizedRecord],
        facilities: list[FacilityEntity],
    ) -> dict:
        return build_capacity_dashboard(persons, schedule_records, facilities)

    def generate_compliance_report(
        self,
        persons: list[PersonEntity],
        all_records: list[NormalizedRecord],
        conflicts: list[Conflict],
    ) -> dict:
        return build_compliance_report(persons, all_records, conflicts)

    def generate_expiration_report(
        self,
        persons: list[PersonEntity],
        normalized_records: list[NormalizedRecord] | None = None,
    ) -> dict:
        return build_expiration_report(persons, normalized_records)

    def generate_review(
        self,
        persons: list[PersonEntity],
        all_records: list[NormalizedRecord],
        conflicts: list[Conflict],
        capacity: dict | None = None,
        compliance: dict | None = None,
    ) -> dict:
        return build_review(persons, all_records, conflicts, capacity, compliance)
