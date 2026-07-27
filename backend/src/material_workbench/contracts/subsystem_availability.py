"""Typed availability for optional application subsystems.

Database, schema, and local-access failures remain fatal.  These records cover
code-free resources whose failure must be isolated to one Transform, Chain, or
evaluation surface.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SubsystemKind = Literal[
    "deterministic_transform",
    "chain",
    "chain_evaluation",
]

WELDING_TRANSFORM_RESOURCE_ID = "welding-stage-a-v1"
WELDING_CHAIN_RESOURCE_ID = "welding-consumable-a-b-c-v1"
WELDING_CHAIN_EVALUATION_RESOURCE_ID = "welding-consumable-a-b-c-nested-oof-v1"

WELDING_TRANSFORM_SUBSYSTEM_ID = (
    f"deterministic_transform:{WELDING_TRANSFORM_RESOURCE_ID}"
)
WELDING_CHAIN_SUBSYSTEM_ID = f"chain:{WELDING_CHAIN_RESOURCE_ID}"
WELDING_CHAIN_EVALUATION_SUBSYSTEM_ID = (
    f"chain_evaluation:{WELDING_CHAIN_RESOURCE_ID}"
)


class SubsystemAvailability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subsystem_id: str = Field(min_length=1)
    kind: SubsystemKind
    resource_id: str = Field(min_length=1)
    owner_kind: Literal["chain", "transform"] | None = None
    owner_resource_id: str | None = None
    status: Literal["available", "unavailable"] = "available"
    stage: str = Field(min_length=1)
    cause: str | None = None
    message: str = ""
    impact: str = ""
    recovery_hint: str = ""


class SubsystemUnavailableError(RuntimeError):
    def __init__(self, availability: SubsystemAvailability) -> None:
        super().__init__(availability.message)
        self.availability = availability


class SubsystemAvailabilityRegistry:
    def __init__(self) -> None:
        self._items: dict[str, SubsystemAvailability] = {}

    def record_available(
        self,
        *,
        subsystem_id: str,
        kind: SubsystemKind,
        resource_id: str,
        stage: str,
        owner_kind: Literal["chain", "transform"] | None = None,
        owner_resource_id: str | None = None,
    ) -> SubsystemAvailability:
        item = SubsystemAvailability(
            subsystem_id=subsystem_id,
            kind=kind,
            resource_id=resource_id,
            owner_kind=owner_kind,
            owner_resource_id=owner_resource_id,
            stage=stage,
        )
        self._items[subsystem_id] = item
        return item

    def record_unavailable(
        self,
        *,
        subsystem_id: str,
        kind: SubsystemKind,
        resource_id: str,
        stage: str,
        owner_kind: Literal["chain", "transform"] | None = None,
        owner_resource_id: str | None = None,
        cause: str,
        message: str,
        impact: str,
        recovery_hint: str,
    ) -> SubsystemAvailability:
        item = SubsystemAvailability(
            subsystem_id=subsystem_id,
            kind=kind,
            resource_id=resource_id,
            owner_kind=owner_kind,
            owner_resource_id=owner_resource_id,
            status="unavailable",
            stage=stage,
            cause=cause,
            message=message,
            impact=impact,
            recovery_hint=recovery_hint,
        )
        self._items[subsystem_id] = item
        return item

    def get(self, subsystem_id: str) -> SubsystemAvailability | None:
        return self._items.get(subsystem_id)

    def require(self, subsystem_id: str) -> SubsystemAvailability:
        item = self._items.get(subsystem_id)
        if item is None:
            raise KeyError(f"subsystem availability is not registered: {subsystem_id}")
        if item.status == "unavailable":
            raise SubsystemUnavailableError(item)
        return item

    def list(self) -> tuple[SubsystemAvailability, ...]:
        return tuple(self._items[key] for key in sorted(self._items))
