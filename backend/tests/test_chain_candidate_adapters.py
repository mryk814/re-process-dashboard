"""Chain Coreとcandidate adapterの境界を固定する。

Chain Coreは候補の形状を仮定してはならない。疎配合を前提にしないChainが
成立することの受入テストは backend/scripts/spikes/spike_case_d.py が担う。
ここでは境界そのものを軽量に固定する。
"""
from __future__ import annotations

from datetime import UTC, datetime
import inspect
from pathlib import Path

import pytest

from material_workbench.application import chain_execution
from material_workbench.application.chain_candidate_adapters import (
    ChainCandidateAdapterError,
    ScalarChainAdapter,
    SparseBlendChainAdapter,
    candidate_adapter_for,
)
from material_workbench.contracts.chain_contracts import (
    ChainRevision,
    ChainSnapshotIdentityV2,
    ChainStageRevision,
)
from material_workbench.contracts.schemas import Candidate, CandidateInput, CandidateInputs


DIGEST = "sha256:" + "0" * 64
CORE_MODULES = (
    "backend/src/material_workbench/application/chain_execution.py",
    "backend/src/material_workbench/application/chain_uncertainty.py",
)
# 溶接／疎配合固有の語彙。Chain Coreに現れてはならない。
DOMAIN_SYMBOLS = (
    "welding_context",
    "test_context",
    "material_composition",
    "auxiliary_features",
    "validate_sparse_blend",
    "initial_blend_for_package",
)


def _revision(*stage_kinds: str) -> ChainRevision:
    stages = tuple(
        ChainStageRevision(
            stage_id=f"S{index}",
            stage_kind=kind,  # type: ignore[arg-type]
            contract_id=f"contract-{index}",
            contract_digest=DIGEST,
            package_manifest_digest=DIGEST,
            dataset_view_revision_id=None if kind != "task" else f"view-{index}",
            dataset_profile_digest=None if kind != "task" else DIGEST,
        )
        for index, kind in enumerate(stage_kinds)
    )
    return ChainRevision(
        chain_id="chain",
        revision=1,
        chain_definition_digest=DIGEST,
        binding_digest=DIGEST,
        unit_conversion_digest=DIGEST,
        stages=stages,
        revision_digest=DIGEST,
    )


def _scalar_candidate() -> Candidate:
    now = datetime.now(UTC)
    return Candidate.model_validate({
        "id": "candidate",
        "project_id": "project",
        "revision": 1,
        "created_at": now,
        "updated_at": now,
        "name": "スカラー候補",
        "inputs": {
            "composition": {},
            "process": {"barrel_temperature_c": 240.0},
            "categorical": {"resin_grade": "grade_a"},
            "heat_pattern": None,
        },
    })


def test_adapter_is_selected_from_the_declared_stage_shape() -> None:
    scalar = candidate_adapter_for(_revision("task", "task"), transform_catalog=None)  # type: ignore[arg-type]
    welding = candidate_adapter_for(
        _revision("deterministic_transform", "task", "task"),
        transform_catalog=None,  # type: ignore[arg-type]
    )

    assert isinstance(scalar, ScalarChainAdapter)
    assert scalar.adapter_id == "scalar/v1" and scalar.sparse_blend is False
    assert isinstance(welding, SparseBlendChainAdapter)
    assert welding.adapter_id == "sparse_blend/v1" and welding.sparse_blend is True

    with pytest.raises(ChainCandidateAdapterError, match="2段以上"):
        candidate_adapter_for(
            _revision("deterministic_transform", "deterministic_transform", "task"),
            transform_catalog=None,  # type: ignore[arg-type]
        )


def test_scalar_adapter_exposes_candidate_inputs_in_their_own_namespace() -> None:
    adapter = ScalarChainAdapter()

    values = adapter.external_values(_scalar_candidate())

    assert values == {
        "candidate.process.barrel_temperature_c": 240.0,
        "candidate.categorical.resin_grade": "grade_a",
    }
    assert not any("welding_context" in key for key in values)


def test_scalar_adapter_rejects_a_sparse_blend_and_declares_no_domain_reference() -> None:
    adapter = ScalarChainAdapter()
    candidate = _scalar_candidate()

    assert adapter.snapshot_domain_references(candidate) == ()
    assert adapter.initial_domain_payload() == {}
    with pytest.raises(ChainCandidateAdapterError):
        adapter.run_deterministic_stage(_revision("task").stages[0], candidate)


def test_snapshot_identity_v2_needs_no_sparse_blend_reference() -> None:
    identity = ChainSnapshotIdentityV2(
        chain_revision_id="chain:r1",
        chain_revision_digest=DIGEST,
        candidate_id="candidate",
        candidate_revision=1,
        candidate_adapter_id="scalar/v1",
    )

    assert identity.domain_references == ()


def test_core_stage_input_does_not_require_a_composition_group() -> None:
    """binding済みのgroupだけを持つcanonical inputでStage候補を作れる。"""

    payload = CandidateInputs.model_validate({
        "composition": {},
        "process": {},
        "categorical": {},
        **{"process": {"shrinkage_pct": 0.8}},
        "heat_pattern": None,
        "heat_time_basis": "line_speed",
    })

    assert payload.composition == {}
    assert payload.process == {"shrinkage_pct": 0.8}


def test_chain_core_modules_do_not_name_domain_specific_symbols() -> None:
    root = Path(__file__).resolve().parents[2]

    offenders: dict[str, list[str]] = {}
    for relative in CORE_MODULES:
        source = (root / relative).read_text(encoding="utf-8")
        named = [symbol for symbol in DOMAIN_SYMBOLS if symbol in source]
        if named:
            offenders[relative] = named

    assert offenders == {}, f"Chain Coreにdomain固有symbolが残っています: {offenders}"


def test_core_prepare_candidate_delegates_shape_validation_to_the_adapter() -> None:
    source = inspect.getsource(chain_execution.ChainExecutionService.prepare_candidate)

    assert "adapter.prepare_candidate" in source
    assert "blend" not in source


def test_scalar_candidate_input_survives_the_adapter_round_trip() -> None:
    adapter = ScalarChainAdapter()
    payload = CandidateInput(
        name="スカラー候補",
        inputs=CandidateInputs(
            composition={}, process={"anneal_temperature_c": 120.0}, categorical={}
        ),
    )

    prepared = adapter.prepare_candidate(payload)

    assert prepared is payload
    assert adapter.external_values(prepared) == {
        "candidate.process.anneal_temperature_c": 120.0
    }
