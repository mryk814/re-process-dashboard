from __future__ import annotations

from datetime import UTC, datetime

import pytest

from material_workbench.contracts.candidate_project_contracts import Candidate
from material_workbench.task_composition.candidate_family_adapters import (
    CANONICAL_CANDIDATE_ADAPTER,
    CandidateFamilyError,
    candidate_family_adapter,
)
from material_workbench.tasks.task_registry import load_task_contracts


def _candidate(task_id: str) -> Candidate:
    fixture = load_task_contracts()[task_id]
    canonical = fixture.canonical_candidate
    now = datetime.now(UTC)
    return Candidate.model_validate(
        {
            "id": f"{task_id}-candidate",
            "project_id": f"{task_id}-project",
            "revision": 3,
            "created_at": now,
            "updated_at": now,
            "name": task_id,
            "inputs": {
                "composition": canonical.composition,
                "process": canonical.process,
                "categorical": canonical.categorical,
                "heat_pattern": (
                    None
                    if canonical.heat_pattern is None
                    else [
                        point.model_dump(mode="json")
                        for point in canonical.heat_pattern
                    ]
                ),
            },
            "provenance": canonical.provenance,
        }
    )


@pytest.mark.parametrize(
    ("task_id", "path"),
    (
        ("annealed-properties-v1", "composition.C"),
        ("flank-wear-v1", "process.cutting_speed_mpm"),
    ),
)
def test_adapter_updates_material_and_tool_wear_candidates_without_identity_drift(
    task_id: str,
    path: str,
) -> None:
    definition = load_task_contracts()[task_id].task_definition
    candidate = _candidate(task_id)
    before_identity = (
        candidate.id,
        candidate.project_id,
        candidate.revision,
        candidate.created_at,
        candidate.updated_at,
        candidate.provenance,
    )
    current = CANONICAL_CANDIDATE_ADAPTER.numeric_value(candidate, path)

    updated = CANONICAL_CANDIDATE_ADAPTER.update(
        candidate,
        {path: current * 1.01},
        definition,
        balance=True,
    )

    assert CANONICAL_CANDIDATE_ADAPTER.numeric_value(updated, path) == pytest.approx(
        current * 1.01
    )
    assert (
        updated.id,
        updated.project_id,
        updated.revision,
        updated.created_at,
        updated.updated_at,
        updated.provenance,
    ) == before_identity


def test_candidate_family_allow_list_and_paths_fail_closed() -> None:
    candidate = _candidate("flank-wear-v1")

    assert (
        candidate_family_adapter("canonical-candidate/v1")
        is CANONICAL_CANDIDATE_ADAPTER
    )
    with pytest.raises(CandidateFamilyError, match="allow-list"):
        candidate_family_adapter("arbitrary-json/v1")
    with pytest.raises(CandidateFamilyError, match="入力パス"):
        CANONICAL_CANDIDATE_ADAPTER.value(candidate, "payload.anything")


def test_heat_stage_axis_contract_is_owned_by_the_family_adapter() -> None:
    CANONICAL_CANDIDATE_ADAPTER.validate_response_axis(
        "heat.stage_temperature_c",
        stage_name="加熱1",
        stage_position_m=12.5,
    )

    with pytest.raises(CandidateFamilyError, match="セット"):
        CANONICAL_CANDIDATE_ADAPTER.validate_response_axis(
            "heat.stage_temperature_c",
            stage_name=None,
            stage_position_m=None,
        )
    with pytest.raises(CandidateFamilyError, match="セット"):
        CANONICAL_CANDIDATE_ADAPTER.validate_response_axis(
            "process.cutting_speed_mpm",
            stage_name="加熱1",
            stage_position_m=12.5,
        )
