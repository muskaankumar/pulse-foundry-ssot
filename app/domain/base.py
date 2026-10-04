"""
Base domain plugin interface.
All industry-specific logic inherits from DomainPlugin.
The core pipeline is domain-agnostic; only this layer changes.
"""

from __future__ import annotations
from abc import ABC, abstractmethod

from app.models.entities import PersonEntity, NormalizedRecord, FacilityEntity, Conflict


class DomainPlugin(ABC):
    """
    Abstract base class for domain-specific intelligence layers.
    Swap this out to adapt the system for any industry.
    """

    @abstractmethod
    def get_name(self) -> str:
        """Return a display name for this domain (e.g. 'Healthcare')."""
        ...

    @abstractmethod
    def generate_capacity_report(
        self,
        persons: list[PersonEntity],
        schedule_records: list[NormalizedRecord],
        facilities: list[FacilityEntity],
    ) -> dict:
        """Produce a capacity / staffing dashboard payload."""
        ...

    @abstractmethod
    def generate_compliance_report(
        self,
        persons: list[PersonEntity],
        all_records: list[NormalizedRecord],
        conflicts: list[Conflict],
    ) -> dict:
        """Produce a regulatory compliance report payload."""
        ...

    @abstractmethod
    def generate_expiration_report(
        self,
        persons: list[PersonEntity],
    ) -> dict:
        """Produce a credential / expiration tracker payload."""
        ...

    def generate_review(
        self,
        persons: list[PersonEntity],
        all_records: list[NormalizedRecord],
        conflicts: list[Conflict],
        capacity: dict | None = None,
        compliance: dict | None = None,
    ) -> dict:
        """Optional: per-person checks and a team-routed issue list."""
        return {"issues": [], "people": {}}
