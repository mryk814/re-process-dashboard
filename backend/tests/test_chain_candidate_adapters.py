"""Chain Coreとcandidate adapterの境界を固定する。

Chain Coreは候補の形状を仮定してはならない。疎配合を前提にしないChainが
成立することの受入テストは backend/scripts/experiments/spikes/spike_case_d.py が担う。
ここでは境界そのものを軽量に固定する。
"""
from __future__ import annotations

from datetime import UTC, datetime
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from decision_workbench.application.chain import (
    plan as chain_execution_plan,
    snapshot as chain_snapshot_use_case,
)
from decision_workbench.application.chain_candidate_adapters import (
    ChainCandidateAdapterError,
    ScalarChainAdapter,
    SparseBlendChainAdapter,
    candidate_adapter_for,
    candidate_adapter_shape_for,
)
from decision_workbench.application.chain.plan import (
    ChainExecutionError,
    ChainPlanningUseCase,
)
from decision_workbench.application.chains import ChainUseCases
from decision_workbench.application.payload_normalization import plain_payload
from decision_workbench.contracts.chain_contracts import (
    ChainBinding,
    ChainDefinition,
    ChainPort,
    ChainRevision,
    ChainSnapshotIdentityV2,
    ChainStage,
    ChainStageRevision,
    ExternalBindingSource,
    StageOutputBindingSource,
    UnitConversion,
)
from decision_workbench.contracts.chain_execution_contracts import (
    IntermediateActualRecord,
)
from decision_workbench.contracts.candidate_project_contracts import (
    Candidate,
    CandidateInput,
    CandidateInputs,
)


DIGEST = "sha256:" + "0" * 64
CORE_MODULES = (
    "backend/src/decision_workbench/application/chain/plan.py",
    "backend/src/decision_workbench/application/chain/execution.py",
    "backend/src/decision_workbench/application/chain/snapshot.py",
    "backend/src/decision_workbench/application/chain/stage_execution.py",
    "backend/src/decision_workbench/application/chain_uncertainty.py",
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


def test_candidate_capability_reads_revision_shape_without_transform_catalog() -> None:
    revision = _revision("deterministic_transform", "task", "task")
    shape = candidate_adapter_shape_for(revision)

    assert shape.adapter_id == "sparse_blend/v1"
    assert shape.sparse_blend is True

    class ReadOnlyStore:
        def get_project(self, project_id: str):
            assert project_id == "project"
            return SimpleNamespace(
                scientific_identity=SimpleNamespace(
                    identity_kind="chain",
                    chain_revision_id="chain:r1",
                    chain_revision_digest=revision.revision_digest,
                )
            )

        def get_chain_revision(self, revision_id: str):
            assert revision_id == "chain:r1"
            return revision

        def get_chain_definition(self, chain_id: str, definition_digest: str):
            assert (chain_id, definition_digest) == (
                "chain", revision.chain_definition_digest
            )
            return ChainDefinition(
                chain_id="chain",
                label="read-only capability fixture",
                stages=tuple(
                    ChainStage(
                        stage_id=stage.stage_id,
                        stage_kind=stage.stage_kind,
                        contract_id=stage.contract_id,
                    )
                    for stage in revision.stages
                ),
                external_inputs=(
                    ChainPort(
                        path="candidate.blend",
                        value_kind="sparse_blend",
                        quantity="blend",
                        unit="sparse-blend/v1",
                    ),
                ),
                bindings=(
                    ChainBinding(
                        target_stage_id=revision.stages[0].stage_id,
                        target_input_path="blend",
                        source=ExternalBindingSource(
                            source_kind="external",
                            path="candidate.blend",
                        ),
                    ),
                ),
            )

    planning = ChainPlanningUseCase(
        ReadOnlyStore(), registry=None, transform_catalog=None,  # type: ignore[arg-type]
    )
    use_cases = object.__new__(ChainUseCases)
    use_cases._planning_use_case = planning
    use_cases.subsystem_registry = SimpleNamespace(
        require=lambda _subsystem_id: pytest.fail(
            "read-only capability must not require availability"
        ),
    )

    capability = use_cases.candidate_capability("project")

    assert capability.adapter_id == "sparse_blend/v1"
    assert capability.sparse_blend is True
    assert capability.external_input_paths == ("candidate.blend",)
    with pytest.raises(ChainExecutionError, match="決定論的Transform"):
        planning.adapter_for(revision)


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


def test_scalar_adapter_applies_complete_intermediate_actuals_to_process_input() -> None:
    adapter = ScalarChainAdapter()
    definition = ChainDefinition(
        chain_id="molding-chain",
        label="成形から平面度",
        stages=(
            ChainStage(stage_id="X", stage_kind="task", contract_id="molding"),
            ChainStage(stage_id="Y", stage_kind="task", contract_id="flatness"),
        ),
        bindings=(
            ChainBinding(
                target_stage_id="Y",
                target_input_path="process.shrinkage_pct",
                source=StageOutputBindingSource(
                    source_kind="stage_output",
                    stage_id="X",
                    output_key="shrinkage_pct",
                ),
            ),
        ),
    )
    target_stage = _revision("task", "task").stages[1].model_copy(
        update={"stage_id": "Y"}
    )
    base = {
        "composition": {},
        "process": {"shrinkage_pct": 0.8, "anneal_temperature_c": 120.0},
        "categorical": {},
    }

    result = adapter.apply_actual_measurements(
        definition,
        target_stage,
        base,
        (
            IntermediateActualRecord(
                actual_id="MOLD-001",
                values={"shrinkage_pct": 0.91},
            ),
        ),
    )

    assert result.coverage == ("shrinkage_pct",)
    assert result.measured_values == {"shrinkage_pct": 0.91}
    assert result.canonical_input == {
        "composition": {},
        "process": {"shrinkage_pct": 0.91, "anneal_temperature_c": 120.0},
        "categorical": {},
    }
    assert base["process"]["shrinkage_pct"] == 0.8

    converted_definition = definition.model_copy(
        update={
            "bindings": (
                definition.bindings[0].model_copy(
                    update={
                        "target_input_path": "process.temperature_k",
                        "source": StageOutputBindingSource(
                            source_kind="stage_output",
                            stage_id="X",
                            output_key="temperature_c",
                        ),
                        "conversion": UnitConversion(
                            conversion_id="celsius-to-kelvin",
                            source_unit="°C",
                            target_unit="K",
                            factor=1.0,
                            offset=273.15,
                        ),
                    }
                ),
            )
        }
    )
    converted = adapter.apply_actual_measurements(
        converted_definition,
        target_stage,
        {
            "composition": {},
            "process": {"temperature_k": 300.0},
            "categorical": {},
        },
        (
            IntermediateActualRecord(
                actual_id="MOLD-TEMP-001",
                values={"temperature_c": 25.0},
            ),
        ),
    )
    assert converted.measured_values == {"temperature_c": 25.0}
    assert converted.canonical_input["process"]["temperature_k"] == pytest.approx(
        298.15
    )

    with pytest.raises(ChainCandidateAdapterError, match="予測値では補完しません"):
        adapter.apply_actual_measurements(
            definition,
            target_stage,
            base,
            (
                IntermediateActualRecord(
                    actual_id="MOLD-002",
                    values={"unrelated": 1.0},
                ),
            ),
        )
    with pytest.raises(ChainCandidateAdapterError, match="複数の実測ID"):
        adapter.apply_actual_measurements(
            definition,
            target_stage,
            base,
            (
                IntermediateActualRecord(
                    actual_id="MOLD-003",
                    values={"shrinkage_pct": 0.89},
                ),
                IntermediateActualRecord(
                    actual_id="MOLD-004",
                    values={"shrinkage_pct": 0.90},
                ),
            ),
        )
    unsupported_definition = definition.model_copy(
        update={
            "bindings": (
                definition.bindings[0].model_copy(
                    update={"target_input_path": "categorical.shrinkage_class"}
                ),
            )
        }
    )
    with pytest.raises(ChainCandidateAdapterError, match="適用できない"):
        adapter.apply_actual_measurements(
            unsupported_definition,
            target_stage,
            base,
            (
                IntermediateActualRecord(
                    actual_id="MOLD-005",
                    values={"shrinkage_pct": 0.90},
                ),
            ),
        )


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


def test_candidate_adapter_does_not_import_chain_use_cases() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (
        root
        / "backend/src/decision_workbench/application/chain_candidate_adapters.py"
    ).read_text(encoding="utf-8")

    assert "decision_workbench.application.chain_execution_" not in source


def test_plain_payload_normalizes_models_and_nested_containers() -> None:
    payload = plain_payload(
        {
            "identity": ChainSnapshotIdentityV2(
                chain_revision_id="chain:r1",
                chain_revision_digest=DIGEST,
                candidate_id="candidate",
                candidate_revision=1,
                candidate_adapter_id="scalar/v1",
            ),
            "values": ({"value": 1.0}, 2.0),
        }
    )

    assert payload == {
        "identity": {
            "schema_version": "chain-snapshot-identity/v2",
            "chain_revision_id": "chain:r1",
            "chain_revision_digest": DIGEST,
            "candidate_id": "candidate",
            "candidate_revision": 1,
            "candidate_adapter_id": "scalar/v1",
            "domain_references": [],
        },
        "values": [{"value": 1.0}, 2.0],
    }


def test_core_prepare_candidate_delegates_shape_validation_to_the_adapter() -> None:
    source = inspect.getsource(
        chain_execution_plan.ChainPlanningUseCase.prepare_candidate
    )

    assert "adapter.prepare_candidate" in source
    assert "blend" not in source


def test_core_actual_conditioning_delegates_measurement_semantics_to_the_adapter() -> None:
    source = inspect.getsource(
        chain_snapshot_use_case.ChainSnapshotUseCase.actual_conditioned_variant
    )

    assert "adapter.apply_actual_measurements" in source
    assert "composition." not in source
    assert "実測成分" not in source


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
