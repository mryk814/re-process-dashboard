"""Register the bundled welding A -> B -> C Chain as immutable catalog data."""
from __future__ import annotations

from collections.abc import Iterable

from material_workbench.contracts.chain_contracts import (
    ChainBinding,
    ChainDefinition,
    ChainPort,
    ChainStage,
    ChainStageLock,
    ExternalBindingSource,
    StageContractSurface,
    StageOutputBindingSource,
    build_chain_revision,
    task_contract_surface,
)
from material_workbench.execution.inference_work_graph import semantic_digest
from material_workbench.modeling.transform_catalog import (
    DeterministicTransformCatalog,
    deterministic_transform_contract_digest,
)
from material_workbench.modeling.model_package_verification import VerifiedModelPackage
from material_workbench.persistence.store import Store
from material_workbench.persistence.workspace_catalog import WorkspaceCatalog
from material_workbench.tasks.task_registry import TaskRegistry


WELDING_CHAIN_ID = "welding-consumable-a-b-c-v1"
STAGE_A_ID = "welding-stage-a-v1"
STAGE_B_ID = "welding-consumable-stage-b-v1"
STAGE_C_ID = "welding-stage-c-properties-v1"


class WeldingChainBootstrapError(RuntimeError):
    """Bundled Task/Package/Profile records cannot form the declared Chain."""


def welding_stage_a_surface(package: VerifiedModelPackage) -> StageContractSurface:
    manifest = package.manifest
    spec = manifest.deterministic_transforms[0]
    contract_digest = deterministic_transform_contract_digest(package)
    outputs = [
        ChainPort(
            path=name,
            value_kind="number",
            quantity=name,
            basis="whole_wire",
            unit="mass% whole wire",
        )
        for name in spec.output_names
    ]
    outputs.extend(
        ChainPort(
            path=name,
            value_kind="number",
            quantity=name,
            unit="µm",
        )
        for name in spec.auxiliary_feature_names
    )
    return StageContractSurface(
        stage_kind="deterministic_transform",
        contract_id=STAGE_A_ID,
        contract_digest=contract_digest,
        input_ports=(
            ChainPort(
                path="blend",
                value_kind="sparse_blend",
                quantity="blend",
                basis="core",
                unit="sparse-blend/v1",
            ),
        ),
        output_ports=tuple(outputs),
    )


def _stage_a_surface(
    catalog: DeterministicTransformCatalog,
) -> StageContractSurface:
    return welding_stage_a_surface(catalog.entry(STAGE_A_ID).package)


def _task_surface(
    registry: TaskRegistry,
    task_id: str,
) -> StageContractSurface:
    return task_contract_surface(
        registry.contract_for(task_id).task_definition,
        contract_digest=semantic_digest(
            registry.contract_for(task_id).task_definition.model_dump(mode="json")
        ),
    )


def _external_inputs() -> tuple[ChainPort, ...]:
    return (
        ChainPort(
            path="candidate.blend",
            value_kind="sparse_blend",
            quantity="blend",
            basis="core",
            unit="sparse-blend/v1",
        ),
        ChainPort(
            path="candidate.welding_context.heat_input_kj_per_mm",
            value_kind="number",
            quantity="heat_input_kj_per_mm",
            unit="kJ/mm",
        ),
        ChainPort(
            path="candidate.welding_context.voltage_v",
            value_kind="number",
            quantity="voltage_v",
            unit="V",
        ),
        ChainPort(
            path="candidate.welding_context.gas_flow_l_per_min",
            value_kind="number",
            quantity="gas_flow_l_per_min",
            unit="L/min",
        ),
        ChainPort(
            path="candidate.welding_context.shielding_gas",
            value_kind="categorical",
            quantity="shielding_gas",
        ),
        ChainPort(
            path="candidate.welding_context.welding_position",
            value_kind="categorical",
            quantity="welding_position",
        ),
        ChainPort(
            path="candidate.welding_context.preheat_temp_c",
            value_kind="number",
            quantity="preheat_temp_c",
            unit="°C",
        ),
        ChainPort(
            path="candidate.test_context.test_temperature_c",
            value_kind="number",
            quantity="test_temperature_c",
            unit="°C",
        ),
        ChainPort(
            path="candidate.test_context.test_solution",
            value_kind="categorical",
            quantity="test_solution",
        ),
    )


def _external_binding(target_stage: str, target_path: str, source_path: str) -> ChainBinding:
    return ChainBinding(
        target_stage_id=target_stage,
        target_input_path=target_path,
        source=ExternalBindingSource(source_kind="external", path=source_path),
    )


def _stage_binding(
    target_stage: str,
    target_path: str,
    source_stage: str,
    output_key: str,
) -> ChainBinding:
    return ChainBinding(
        target_stage_id=target_stage,
        target_input_path=target_path,
        source=StageOutputBindingSource(
            source_kind="stage_output",
            stage_id=source_stage,
            output_key=output_key,
        ),
    )


def welding_chain_definition(
    stage_a: StageContractSurface,
    stage_b: StageContractSurface,
    stage_c: StageContractSurface,
) -> ChainDefinition:
    bindings: list[ChainBinding] = [
        _external_binding("A", "blend", "candidate.blend"),
    ]
    bindings.extend(
        _stage_binding("B", f"composition.{port.path}", "A", port.path)
        for port in stage_a.output_ports
        if port.basis == "whole_wire"
    )
    bindings.append(
        _stage_binding(
            "B",
            "process.alloy_powder_d50_um",
            "A",
            "alloy_powder_d50_um",
        )
    )
    for path in (
        "heat_input_kj_per_mm",
        "voltage_v",
        "gas_flow_l_per_min",
    ):
        bindings.append(
            _external_binding(
                "B",
                f"process.{path}",
                f"candidate.welding_context.{path}",
            )
        )
    for path in ("shielding_gas", "welding_position"):
        bindings.append(
            _external_binding(
                "B",
                f"categorical.{path}",
                f"candidate.welding_context.{path}",
            )
        )
    bindings.extend(
        _stage_binding("C", f"composition.{port.path}", "B", port.path)
        for port in stage_b.output_ports
    )
    for path in ("heat_input_kj_per_mm", "preheat_temp_c"):
        bindings.append(
            _external_binding(
                "C",
                f"process.{path}",
                f"candidate.welding_context.{path}",
            )
        )
    bindings.extend(
        (
            _external_binding(
                "C",
                "process.test_temperature_c",
                "candidate.test_context.test_temperature_c",
            ),
            _external_binding(
                "C",
                "categorical.test_solution",
                "candidate.test_context.test_solution",
            ),
        )
    )
    return ChainDefinition(
        chain_id=WELDING_CHAIN_ID,
        label="溶接材料 配合→溶着金属→特性",
        stages=(
            ChainStage(
                stage_id="A",
                stage_kind="deterministic_transform",
                contract_id=stage_a.contract_id,
            ),
            ChainStage(
                stage_id="B",
                stage_kind="task",
                contract_id=stage_b.contract_id,
            ),
            ChainStage(
                stage_id="C",
                stage_kind="task",
                contract_id=stage_c.contract_id,
            ),
        ),
        external_inputs=_external_inputs(),
        bindings=tuple(bindings),
    )


def _matching_dataset_view(
    catalog: WorkspaceCatalog,
    *,
    source_sha256: str,
    profile_digest: str,
) -> str:
    dataset_ids: set[str] = set()
    for dataset in catalog.list_dataset_revisions():
        profile = catalog.get_profile_revision(dataset.profile_revision_id)
        asset = catalog.get_data_asset(dataset.data_asset_id)
        if (
            profile is not None
            and asset is not None
            and profile.profile_digest == profile_digest
            and asset.sha256 == source_sha256
        ):
            dataset_ids.add(dataset.id)
    if len(dataset_ids) != 1:
        raise WeldingChainBootstrapError(
            "Chain Taskの学習Dataset Revisionを一意に固定できません: "
            f"profile={profile_digest}, matches={len(dataset_ids)}"
        )
    dataset_id = next(iter(dataset_ids))
    canonical_view_id = f"single-{dataset_id}"
    views = [
        view.id
        for view in catalog.list_dataset_view_revisions()
        if view.kind == "single"
        and len(view.members) == 1
        and view.view_id == canonical_view_id
        and view.members[0].dataset_revision_id == dataset_id
    ]
    if len(views) != 1:
        raise WeldingChainBootstrapError(
            "Chain Taskの学習Dataset Viewを一意に固定できません: "
            f"profile={profile_digest}, canonical_matches={len(views)}"
        )
    return views[0]


def _task_lock(
    catalog: WorkspaceCatalog,
    registry: TaskRegistry,
    surface: StageContractSurface,
) -> ChainStageLock:
    entry = registry.entry_for(surface.contract_id)
    package = entry.model_package
    package_refs = [
        item
        for item in catalog.list_model_package_refs()
        if item.task_id == surface.contract_id
        and item.manifest_digest == package.manifest_sha256
        and item.task_contract_digest == surface.contract_digest
    ]
    if len(package_refs) != 1:
        raise WeldingChainBootstrapError(
            f"Chain TaskのModel Package参照を一意に固定できません: {surface.contract_id}"
        )
    profile_digest = package.manifest.provenance.dataset_profile_id
    if not profile_digest.startswith("sha256:"):
        raise WeldingChainBootstrapError(
            f"Chain TaskのProfile digestが不正です: {surface.contract_id}"
        )
    return ChainStageLock(
        contract_digest=surface.contract_digest,
        package_manifest_digest=f"sha256:{package.manifest_sha256}",
        dataset_view_revision_id=_matching_dataset_view(
            catalog,
            source_sha256=entry.predictor_runtime.data.source_sha256,
            profile_digest=profile_digest,
        ),
        dataset_profile_digest=profile_digest,
    )


def bootstrap_welding_chain(
    *,
    store: Store,
    workspace_catalog: WorkspaceCatalog,
    task_registry: TaskRegistry,
    transform_catalog: DeterministicTransformCatalog,
) -> str:
    """Validate and register the exact bundled welding Chain revision."""

    required_tasks: Iterable[str] = (STAGE_B_ID, STAGE_C_ID)
    for task_id in required_tasks:
        task_registry.require_available(task_id)
    stage_a = _stage_a_surface(transform_catalog)
    stage_b = _task_surface(task_registry, STAGE_B_ID)
    stage_c = _task_surface(task_registry, STAGE_C_ID)
    contracts = {
        (surface.stage_kind, surface.contract_id): surface
        for surface in (stage_a, stage_b, stage_c)
    }
    definition = welding_chain_definition(stage_a, stage_b, stage_c)
    stage_a_entry = transform_catalog.entry(STAGE_A_ID)
    revision = build_chain_revision(
        definition,
        revision=1,
        contracts=contracts,
        stage_locks={
            "A": ChainStageLock(
                contract_digest=stage_a.contract_digest,
                package_manifest_digest=(
                    f"sha256:{stage_a_entry.package.manifest_sha256}"
                ),
            ),
            "B": _task_lock(workspace_catalog, task_registry, stage_b),
            "C": _task_lock(workspace_catalog, task_registry, stage_c),
        },
    )
    store.register_chain_definition(definition)
    return store.register_chain_revision(revision, contracts=contracts)
