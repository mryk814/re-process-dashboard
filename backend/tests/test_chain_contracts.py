from __future__ import annotations

import pytest
from pydantic import ValidationError

from material_workbench.contracts.chain_contracts import (
    ChainBinding,
    ChainDefinition,
    ChainPort,
    ChainProjectIdentity,
    ChainSnapshotIdentity,
    ChainStage,
    ChainStageLock,
    ExternalBindingSource,
    StageContractSurface,
    StageOutputBindingSource,
    SingleTaskProjectIdentity,
    UnitConversion,
    build_chain_revision,
    validate_chain_definition,
)


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
DIGEST_D = "sha256:" + "d" * 64


def _surface(
    kind: str,
    contract_id: str,
    inputs: tuple[tuple[str, str], ...],
    outputs: tuple[tuple[str, str], ...],
    digest: str,
) -> StageContractSurface:
    return StageContractSurface(
        stage_kind=kind,
        contract_id=contract_id,
        contract_digest=digest,
        input_ports=tuple(
            ChainPort(
                path=path,
                value_kind="sparse_blend" if unit == "sparse-blend/v1" else "number",
                unit=unit,
            )
            for path, unit in inputs
        ),
        output_ports=tuple(
            ChainPort(path=path, value_kind="number", unit=unit)
            for path, unit in outputs
        ),
    )


def _contracts() -> dict[tuple[str, str], StageContractSurface]:
    result = (
        _surface(
            "deterministic_transform",
            "stage-a",
            (("blend", "sparse-blend/v1"),),
            (("C", "mass% whole wire"),),
            DIGEST_A,
        ),
        _surface(
            "task",
            "stage-b",
            (("composition.C", "mass% whole wire"), ("process.heat", "kJ/mm")),
            (("C", "mass% deposited metal"),),
            DIGEST_B,
        ),
        _surface(
            "task",
            "stage-c",
            (("composition.C", "mass% deposited metal"), ("process.heat", "kJ/mm")),
            (("TS", "MPa"),),
            DIGEST_C,
        ),
    )
    return {(item.stage_kind, item.contract_id): item for item in result}


def _definition() -> ChainDefinition:
    return ChainDefinition(
        chain_id="welding-abc",
        label="溶接材料 A→B→C",
        stages=(
            ChainStage(
                stage_id="A",
                stage_kind="deterministic_transform",
                contract_id="stage-a",
            ),
            ChainStage(stage_id="B", stage_kind="task", contract_id="stage-b"),
            ChainStage(stage_id="C", stage_kind="task", contract_id="stage-c"),
        ),
        external_inputs=(
            ChainPort(
                path="candidate.blend",
                value_kind="sparse_blend",
                unit="sparse-blend/v1",
            ),
            ChainPort(path="candidate.heat", value_kind="number", unit="kJ/mm"),
        ),
        bindings=(
            ChainBinding(
                target_stage_id="A",
                target_input_path="blend",
                source=ExternalBindingSource(
                    source_kind="external",
                    path="candidate.blend",
                ),
            ),
            ChainBinding(
                target_stage_id="B",
                target_input_path="composition.C",
                source=StageOutputBindingSource(
                    source_kind="stage_output",
                    stage_id="A",
                    output_key="C",
                ),
            ),
            ChainBinding(
                target_stage_id="B",
                target_input_path="process.heat",
                source=ExternalBindingSource(
                    source_kind="external",
                    path="candidate.heat",
                ),
            ),
            ChainBinding(
                target_stage_id="C",
                target_input_path="composition.C",
                source=StageOutputBindingSource(
                    source_kind="stage_output",
                    stage_id="B",
                    output_key="C",
                ),
            ),
            ChainBinding(
                target_stage_id="C",
                target_input_path="process.heat",
                source=ExternalBindingSource(
                    source_kind="external",
                    path="candidate.heat",
                ),
            ),
        ),
    )


def test_chain_revision_pins_ordered_contract_package_dataset_and_binding_identity() -> None:
    definition = _definition()
    contracts = _contracts()
    revision = build_chain_revision(
        definition,
        revision=1,
        contracts=contracts,
        stage_locks={
            "A": ChainStageLock(
                contract_digest=DIGEST_A,
                package_manifest_digest=DIGEST_D,
            ),
            "B": ChainStageLock(
                contract_digest=DIGEST_B,
                package_manifest_digest=DIGEST_C,
                dataset_view_revision_id="stage-b-view-r1",
                dataset_profile_digest=DIGEST_D,
            ),
            "C": ChainStageLock(
                contract_digest=DIGEST_C,
                package_manifest_digest=DIGEST_B,
                dataset_view_revision_id="stage-c-view-r1",
                dataset_profile_digest=DIGEST_A,
            ),
        },
    )

    assert [item.stage_id for item in revision.stages] == ["A", "B", "C"]
    assert revision.chain_definition_digest == definition.digest
    assert revision.revision_digest.startswith("sha256:")
    assert revision.stages[0].dataset_view_revision_id is None
    assert revision.stages[1].dataset_view_revision_id == "stage-b-view-r1"


def test_chain_rejects_cycle_or_forward_reference_before_registry_validation() -> None:
    payload = _definition().model_dump(mode="json")
    payload["bindings"][1]["source"]["stage_id"] = "C"
    with pytest.raises(ValidationError, match="later stage"):
        ChainDefinition.model_validate(payload)


def test_chain_rejects_duplicate_target_binding() -> None:
    payload = _definition().model_dump(mode="json")
    payload["bindings"].append(payload["bindings"][0])
    with pytest.raises(ValidationError, match="only once"):
        ChainDefinition.model_validate(payload)


def test_chain_rejects_unknown_output_and_unbound_required_input() -> None:
    payload = _definition().model_copy(deep=True)
    bindings = list(payload.bindings)
    bindings[1] = bindings[1].model_copy(
        update={
            "source": StageOutputBindingSource(
                source_kind="stage_output",
                stage_id="A",
                output_key="missing",
            )
        }
    )
    invalid = payload.model_copy(update={"bindings": tuple(bindings)})
    with pytest.raises(ValueError, match="unknown stage output"):
        validate_chain_definition(invalid, contracts=_contracts())

    missing = payload.model_copy(update={"bindings": tuple(payload.bindings[:-1])})
    with pytest.raises(ValueError, match="unbound required inputs"):
        validate_chain_definition(missing, contracts=_contracts())


def test_basis_mismatch_requires_explicit_matching_conversion() -> None:
    payload = _definition().model_copy(deep=True)
    contracts = _contracts()
    bad_b = contracts[("task", "stage-b")].model_copy(
        update={
            "input_ports": (
                ChainPort(
                    path="composition.C",
                    value_kind="number",
                    unit="mass% deposited metal",
                ),
                ChainPort(path="process.heat", value_kind="number", unit="kJ/mm"),
            )
        }
    )
    mismatched = {**contracts, ("task", "stage-b"): bad_b}
    with pytest.raises(ValueError, match="unit mismatch"):
        validate_chain_definition(payload, contracts=mismatched)

    bindings = list(payload.bindings)
    bindings[1] = bindings[1].model_copy(
        update={
            "conversion": UnitConversion(
                conversion_id="basis-conversion-v1",
                source_unit="mass% whole wire",
                target_unit="mass% deposited metal",
                factor=1.0,
            )
        }
    )
    explicit = payload.model_copy(update={"bindings": tuple(bindings)})
    validate_chain_definition(explicit, contracts=mismatched)


def test_task_and_transform_training_identity_cannot_be_confused() -> None:
    with pytest.raises(ValidationError, match="must pin Dataset"):
        build_chain_revision(
            _definition(),
            revision=1,
            contracts=_contracts(),
            stage_locks={
                "A": ChainStageLock(
                    contract_digest=DIGEST_A,
                    package_manifest_digest=DIGEST_D,
                ),
                "B": ChainStageLock(
                    contract_digest=DIGEST_B,
                    package_manifest_digest=DIGEST_C,
                ),
                "C": ChainStageLock(
                    contract_digest=DIGEST_C,
                    package_manifest_digest=DIGEST_B,
                    dataset_view_revision_id="stage-c-view-r1",
                    dataset_profile_digest=DIGEST_A,
                ),
            },
        )


def test_project_identity_is_an_explicit_disjoint_union() -> None:
    single = SingleTaskProjectIdentity(
        identity_kind="single_task",
        task_id="stage-c",
        dataset_view_revision_id="view-r1",
        task_contract_digest=DIGEST_A,
        model_package_ref_id="package-ref",
        model_package_manifest_digest=DIGEST_B,
    )
    chain = ChainProjectIdentity(
        identity_kind="chain",
        chain_revision_id="welding-abc:r1",
        chain_revision_digest=DIGEST_C,
    )
    assert single.identity_kind == "single_task"
    assert chain.identity_kind == "chain"
    legacy = SingleTaskProjectIdentity(
        identity_kind="single_task",
        task_id="legacy-task",
        binding_provenance="unbound_legacy",
    )
    assert legacy.model_package_ref_id is None


def test_chain_snapshot_identity_pins_candidate_design_space_and_commercial_revision() -> None:
    snapshot = ChainSnapshotIdentity(
        chain_revision_id="welding-abc:r1",
        chain_revision_digest=DIGEST_A,
        design_space={
            "resource_id": "welding-design-space",
            "revision": 2,
            "digest": DIGEST_B,
        },
        candidate_id="candidate-1",
        candidate_revision=4,
        commercial_catalog={
            "resource_id": "welding-commercial-materials",
            "revision": 3,
            "digest": DIGEST_C,
        },
    )
    assert snapshot.candidate_revision == 4
    assert snapshot.design_space.revision == 2
    assert snapshot.commercial_catalog.revision == 3
