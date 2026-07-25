"""Immutable contracts for composing reusable prediction Tasks into a Chain."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from material_workbench.execution.inference_work_graph import semantic_digest
from material_workbench.contracts.blend_contracts import RevisionRef
from material_workbench.contracts.task_contracts import TaskDefinition
class ChainContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChainPort(ChainContractModel):
    path: Annotated[str, Field(min_length=1)]
    value_kind: Literal["number", "categorical", "sparse_blend"]
    quantity: Annotated[str, Field(min_length=1)]
    basis: str | None = None
    unit: Annotated[str, Field(min_length=1)] | None = None

    @model_validator(mode="after")
    def unit_matches_value_kind(self) -> "ChainPort":
        if self.value_kind in {"number", "sparse_blend"} and self.unit is None:
            raise ValueError(f"{self.value_kind} chain ports require a unit")
        if self.value_kind == "categorical" and self.unit is not None:
            raise ValueError("categorical chain ports do not have a numeric unit")
        if self.value_kind == "categorical" and self.basis is not None:
            raise ValueError("categorical chain ports do not have a physical basis")
        return self


class ChainStage(ChainContractModel):
    stage_id: Annotated[str, Field(min_length=1, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")]
    stage_kind: Literal["task", "deterministic_transform"]
    contract_id: Annotated[str, Field(min_length=1)]


class ExternalBindingSource(ChainContractModel):
    source_kind: Literal["external"]
    path: Annotated[str, Field(min_length=1)]


class StageOutputBindingSource(ChainContractModel):
    source_kind: Literal["stage_output"]
    stage_id: Annotated[str, Field(min_length=1)]
    output_key: Annotated[str, Field(min_length=1)]


BindingSource = Annotated[
    ExternalBindingSource | StageOutputBindingSource,
    Field(discriminator="source_kind"),
]


class UnitConversion(ChainContractModel):
    """An explicit affine conversion; its full payload participates in revision identity."""

    conversion_id: Annotated[str, Field(min_length=1)]
    source_unit: Annotated[str, Field(min_length=1)]
    target_unit: Annotated[str, Field(min_length=1)]
    factor: float
    offset: float = 0.0


class ChainBinding(ChainContractModel):
    target_stage_id: Annotated[str, Field(min_length=1)]
    target_input_path: Annotated[str, Field(min_length=1)]
    source: BindingSource
    conversion: UnitConversion | None = None


class ChainDefinition(ChainContractModel):
    schema_version: Literal["chain-definition/v1"] = "chain-definition/v1"
    chain_id: Annotated[str, Field(min_length=1)]
    label: Annotated[str, Field(min_length=1)]
    stages: Annotated[tuple[ChainStage, ...], Field(min_length=1)]
    external_inputs: tuple[ChainPort, ...] = ()
    bindings: Annotated[tuple[ChainBinding, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def local_shape_is_unambiguous_and_acyclic(self) -> "ChainDefinition":
        stage_ids = [stage.stage_id for stage in self.stages]
        if len(stage_ids) != len(set(stage_ids)):
            raise ValueError("chain stage ids must be unique")
        external_paths = [item.path for item in self.external_inputs]
        if len(external_paths) != len(set(external_paths)):
            raise ValueError("chain external input paths must be unique")
        target_keys = [
            (binding.target_stage_id, binding.target_input_path)
            for binding in self.bindings
        ]
        if len(target_keys) != len(set(target_keys)):
            raise ValueError("each stage input may be bound only once")
        stage_order = {stage_id: index for index, stage_id in enumerate(stage_ids)}
        for binding in self.bindings:
            if binding.target_stage_id not in stage_order:
                raise ValueError(
                    f"binding targets unknown stage: {binding.target_stage_id}"
                )
            if binding.source.source_kind == "external":
                if binding.source.path not in set(external_paths):
                    raise ValueError(
                        f"binding references unknown external input: {binding.source.path}"
                    )
            else:
                source_index = stage_order.get(binding.source.stage_id)
                if source_index is None:
                    raise ValueError(
                        f"binding references unknown source stage: {binding.source.stage_id}"
                    )
                if source_index >= stage_order[binding.target_stage_id]:
                    raise ValueError(
                        "stage outputs may only bind to a later stage"
                    )
        return self

    @property
    def digest(self) -> str:
        return semantic_digest(self.model_dump(mode="json"))


class ChainStageRevision(ChainContractModel):
    stage_id: Annotated[str, Field(min_length=1)]
    stage_kind: Literal["task", "deterministic_transform"]
    contract_id: Annotated[str, Field(min_length=1)]
    contract_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    package_manifest_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    dataset_view_revision_id: Annotated[str, Field(min_length=1)] | None = None
    dataset_profile_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")] | None = None

    @model_validator(mode="after")
    def training_identity_matches_stage_kind(self) -> "ChainStageRevision":
        has_dataset = self.dataset_view_revision_id is not None
        has_profile = self.dataset_profile_digest is not None
        if has_dataset != has_profile:
            raise ValueError("dataset view and profile revisions must be pinned together")
        if self.stage_kind == "task" and not has_dataset:
            raise ValueError("predictive Task stages must pin Dataset/Profile revisions")
        if self.stage_kind == "deterministic_transform" and has_dataset:
            raise ValueError("deterministic transform stages do not have training data")
        return self


class ChainRevision(ChainContractModel):
    schema_version: Literal["chain-revision/v1"] = "chain-revision/v1"
    chain_id: Annotated[str, Field(min_length=1)]
    revision: Annotated[int, Field(ge=1)]
    chain_definition_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    binding_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    unit_conversion_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    stages: Annotated[tuple[ChainStageRevision, ...], Field(min_length=1)]
    revision_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class SingleTaskProjectIdentity(ChainContractModel):
    identity_kind: Literal["single_task"]
    task_id: Annotated[str, Field(min_length=1)]
    dataset_view_revision_id: Annotated[str, Field(min_length=1)] | None = None
    task_contract_digest: Annotated[str, Field(min_length=1)] | None = None
    model_package_ref_id: Annotated[str, Field(min_length=1)] | None = None
    model_package_manifest_digest: Annotated[str, Field(min_length=1)] | None = None
    binding_provenance: Literal[
        "explicit", "assumed_current_at_upgrade", "unbound_legacy"
    ] = "explicit"

    @model_validator(mode="after")
    def binding_is_complete_or_explicitly_legacy(self) -> "SingleTaskProjectIdentity":
        bindings = (
            self.dataset_view_revision_id,
            self.task_contract_digest,
            self.model_package_ref_id,
            self.model_package_manifest_digest,
        )
        if self.binding_provenance == "unbound_legacy":
            if any(bindings):
                raise ValueError("unbound legacy identity cannot invent partial bindings")
        elif not all(bindings):
            raise ValueError("bound single-Task identity requires every immutable reference")
        return self


class ChainProjectIdentity(ChainContractModel):
    identity_kind: Literal["chain"]
    chain_revision_id: Annotated[str, Field(min_length=1)]
    chain_revision_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


ProjectScientificIdentity = Annotated[
    SingleTaskProjectIdentity | ChainProjectIdentity,
    Field(discriminator="identity_kind"),
]


class ChainSnapshotIdentity(ChainContractModel):
    """Stored v1 identity. Kept readable because saved snapshots are immutable.

    v1 hard-codes the sparse-blend references. New snapshots use v2, where the
    candidate adapter supplies its own references through ``domain_references``.
    """

    schema_version: Literal["chain-snapshot-identity/v1"] = "chain-snapshot-identity/v1"
    chain_revision_id: Annotated[str, Field(min_length=1)]
    chain_revision_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    design_space: RevisionRef
    candidate_id: Annotated[str, Field(min_length=1)]
    candidate_revision: Annotated[int, Field(ge=1)]
    commercial_catalog: RevisionRef


class ChainDomainReference(ChainContractModel):
    """One adapter-owned revision reference required to interpret a snapshot."""

    kind: Annotated[str, Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")]
    ref: RevisionRef


class ChainSnapshotIdentityV2(ChainContractModel):
    """Chain-Core identity plus whatever references the adapter declares."""

    schema_version: Literal["chain-snapshot-identity/v2"] = "chain-snapshot-identity/v2"
    chain_revision_id: Annotated[str, Field(min_length=1)]
    chain_revision_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    candidate_id: Annotated[str, Field(min_length=1)]
    candidate_revision: Annotated[int, Field(ge=1)]
    candidate_adapter_id: Annotated[str, Field(min_length=1)]
    domain_references: tuple[ChainDomainReference, ...] = ()

    @model_validator(mode="after")
    def domain_reference_kinds_are_unique(self) -> "ChainSnapshotIdentityV2":
        kinds = [item.kind for item in self.domain_references]
        if len(kinds) != len(set(kinds)):
            raise ValueError("snapshot domain reference kinds must be unique")
        return self


ChainSnapshotIdentityRef = Annotated[
    ChainSnapshotIdentity | ChainSnapshotIdentityV2,
    Field(discriminator="schema_version"),
]


class ChainStageLock(ChainContractModel):
    contract_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    package_manifest_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    dataset_view_revision_id: Annotated[str, Field(min_length=1)] | None = None
    dataset_profile_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")] | None = None


class StageContractSurface(ChainContractModel):
    """The canonical I/O surface used by Chain validation.

    Task and deterministic-transform loaders project their richer contracts to
    this small common surface. ChainDefinition stays ignorant of runtime code.
    """

    stage_kind: Literal["task", "deterministic_transform"]
    contract_id: Annotated[str, Field(min_length=1)]
    contract_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    input_ports: Annotated[tuple[ChainPort, ...], Field(min_length=1)]
    output_ports: Annotated[tuple[ChainPort, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def ports_are_unique(self) -> "StageContractSurface":
        for ports in (self.input_ports, self.output_ports):
            paths = [item.path for item in ports]
            if len(paths) != len(set(paths)):
                raise ValueError("stage contract ports must be unique")
        return self


def task_contract_surface(
    task: TaskDefinition,
    *,
    contract_digest: str,
) -> StageContractSurface:
    """Project a TaskDefinition to its exact reusable Chain surface."""

    def basis(unit: str | None) -> str | None:
        if unit is None:
            return None
        normalized = unit.lower().replace("_", " ")
        if "whole wire" in normalized:
            return "whole_wire"
        if "deposited metal" in normalized:
            return "deposited_metal"
        return None

    inputs: list[ChainPort] = []
    for group in task.input_groups:
        for field in group.fields:
            if not field.required:
                continue
            if field.kind == "heat_pattern":
                # No v1 welding Chain stage consumes a heat pattern. A future
                # Chain can add a dedicated canonical series type deliberately.
                raise ValueError(
                    f"Task {task.id} has a required heat-pattern input unsupported by Chain v1"
                )
            inputs.append(
                ChainPort(
                    path=field.path,
                    value_kind=field.kind,
                    quantity=field.path.rsplit(".", 1)[-1],
                    basis=basis(field.unit),
                    unit=field.unit,
                )
            )
    return StageContractSurface(
        stage_kind="task",
        contract_id=task.id,
        contract_digest=contract_digest,
        input_ports=tuple(inputs),
        output_ports=tuple(
            ChainPort(
                path=output.key,
                value_kind="number",
                quantity=output.key,
                basis=basis(output.unit),
                unit=output.unit,
            )
            for output in task.outputs
        ),
    )


def validate_chain_definition(
    definition: ChainDefinition,
    *,
    contracts: Mapping[tuple[str, str], StageContractSurface],
) -> None:
    """Validate ports and units against exact Task contracts."""

    stages = {stage.stage_id: stage for stage in definition.stages}
    external_ports = {item.path: item for item in definition.external_inputs}
    for stage in definition.stages:
        key = (stage.stage_kind, stage.contract_id)
        contract = contracts.get(key)
        if contract is None:
            raise ValueError(
                f"chain stage references unknown contract: {stage.stage_kind}/{stage.contract_id}"
            )
        if contract.stage_kind != stage.stage_kind or contract.contract_id != stage.contract_id:
            raise ValueError("chain stage contract registry key does not match its value")
    bound_targets: dict[str, set[str]] = {stage.stage_id: set() for stage in definition.stages}
    for binding in definition.bindings:
        target_stage = stages[binding.target_stage_id]
        target_contract = contracts[(target_stage.stage_kind, target_stage.contract_id)]
        target_ports = {port.path: port for port in target_contract.input_ports}
        target_port = target_ports.get(binding.target_input_path)
        if target_port is None:
            raise ValueError(
                f"binding targets unknown scalar input: "
                f"{binding.target_stage_id}.{binding.target_input_path}"
            )
        bound_targets[binding.target_stage_id].add(binding.target_input_path)
        if binding.source.source_kind == "external":
            source_port = external_ports[binding.source.path]
        else:
            source_stage = stages[binding.source.stage_id]
            source_contract = contracts[(source_stage.stage_kind, source_stage.contract_id)]
            outputs = {port.path: port for port in source_contract.output_ports}
            source_port = outputs.get(binding.source.output_key)
            if source_port is None:
                raise ValueError(
                    f"binding references unknown stage output: "
                    f"{binding.source.stage_id}.{binding.source.output_key}"
                )
        if source_port.value_kind != target_port.value_kind:
            raise ValueError(
                f"binding type mismatch: {source_port.value_kind!r} -> "
                f"{target_port.value_kind!r}"
            )
        if (
            source_port.quantity != target_port.quantity
            or source_port.basis != target_port.basis
        ):
            raise ValueError(
                "binding physical quantity or basis mismatch: "
                f"{source_port.quantity!r}/{source_port.basis!r} -> "
                f"{target_port.quantity!r}/{target_port.basis!r}"
            )
        source_unit = source_port.unit
        target_unit = target_port.unit
        if binding.conversion is None:
            if source_unit != target_unit:
                raise ValueError(
                    f"binding unit mismatch: {source_unit!r} -> {target_unit!r}"
                )
        elif (
            binding.conversion.source_unit != source_unit
            or binding.conversion.target_unit != target_unit
        ):
            raise ValueError("unit conversion does not match binding port units")

    for stage in definition.stages:
        contract = contracts[(stage.stage_kind, stage.contract_id)]
        required_ports = {port.path for port in contract.input_ports}
        missing = sorted(required_ports - bound_targets[stage.stage_id])
        if missing:
            raise ValueError(
                f"chain stage {stage.stage_id} has unbound required inputs: {missing}"
            )


def build_chain_revision(
    definition: ChainDefinition,
    *,
    revision: int,
    contracts: Mapping[tuple[str, str], StageContractSurface],
    stage_locks: Mapping[str, ChainStageLock],
) -> ChainRevision:
    validate_chain_definition(definition, contracts=contracts)
    expected_stage_ids = {stage.stage_id for stage in definition.stages}
    if set(stage_locks) != expected_stage_ids:
        raise ValueError("stage locks must match chain stages exactly")
    for stage in definition.stages:
        surface = contracts[(stage.stage_kind, stage.contract_id)]
        if stage_locks[stage.stage_id].contract_digest != surface.contract_digest:
            raise ValueError(
                f"stage lock contract digest does not match surface: {stage.stage_id}"
            )
    stage_revisions = tuple(
        ChainStageRevision(
            stage_id=stage.stage_id,
            stage_kind=stage.stage_kind,
            contract_id=stage.contract_id,
            **stage_locks[stage.stage_id].model_dump(),
        )
        for stage in definition.stages
    )
    binding_payload = [item.model_dump(mode="json") for item in definition.bindings]
    conversion_payload = [
        item.conversion.model_dump(mode="json")
        for item in definition.bindings
        if item.conversion is not None
    ]
    partial = {
        "schema_version": "chain-revision/v1",
        "chain_id": definition.chain_id,
        "revision": revision,
        "chain_definition_digest": definition.digest,
        "binding_digest": semantic_digest(binding_payload),
        "unit_conversion_digest": semantic_digest(conversion_payload),
        "stages": [item.model_dump(mode="json") for item in stage_revisions],
    }
    return ChainRevision(
        **partial,
        revision_digest=semantic_digest(partial),
    )


def validate_chain_revision(
    definition: ChainDefinition,
    revision: ChainRevision,
    *,
    contracts: Mapping[tuple[str, str], StageContractSurface],
) -> None:
    """Recompute every semantic digest before an immutable revision is stored."""

    validate_chain_definition(definition, contracts=contracts)
    if (
        revision.chain_id != definition.chain_id
        or revision.chain_definition_digest != definition.digest
    ):
        raise ValueError("Chain Revision does not reference the exact Definition")
    expected_stages = [
        (stage.stage_id, stage.stage_kind, stage.contract_id)
        for stage in definition.stages
    ]
    actual_stages = [
        (stage.stage_id, stage.stage_kind, stage.contract_id)
        for stage in revision.stages
    ]
    if actual_stages != expected_stages:
        raise ValueError("Chain Revision ordered stages do not match Definition")
    for stage, locked in zip(definition.stages, revision.stages, strict=True):
        surface = contracts[(stage.stage_kind, stage.contract_id)]
        if locked.contract_digest != surface.contract_digest:
            raise ValueError(
                f"Chain Revision contract digest does not match surface: {stage.stage_id}"
            )
    expected_binding_digest = semantic_digest(
        [item.model_dump(mode="json") for item in definition.bindings]
    )
    expected_conversion_digest = semantic_digest(
        [
            item.conversion.model_dump(mode="json")
            for item in definition.bindings
            if item.conversion is not None
        ]
    )
    if revision.binding_digest != expected_binding_digest:
        raise ValueError("Chain Revision binding digest is invalid")
    if revision.unit_conversion_digest != expected_conversion_digest:
        raise ValueError("Chain Revision unit-conversion digest is invalid")
    payload = revision.model_dump(mode="json", exclude={"revision_digest"})
    if revision.revision_digest != semantic_digest(payload):
        raise ValueError("Chain Revision digest is invalid")
