"""Immutable contracts for composing reusable prediction Tasks into a Chain."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.contracts.blend_contracts import RevisionRef
from decision_workbench.contracts.task_contracts import TaskDefinition
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


class CandidateGraphInputSource(ChainContractModel):
    source_kind: Literal["candidate"]
    candidate_path: Annotated[str, Field(min_length=1)]


class ProjectGraphInputSource(ChainContractModel):
    source_kind: Literal["project_binding"]
    binding_key: Annotated[str, Field(min_length=1)]


class FixedGraphInputSource(ChainContractModel):
    source_kind: Literal["fixed_value"]
    value: float | str


GraphInputSource = Annotated[
    CandidateGraphInputSource | ProjectGraphInputSource | FixedGraphInputSource,
    Field(discriminator="source_kind"),
]


class GraphInput(ChainContractModel):
    input_id: Annotated[str, Field(min_length=1)]
    label: Annotated[str, Field(min_length=1)]
    port: ChainPort
    role: Literal["design_variable", "scenario_context", "fixed_parameter"]
    value_source: GraphInputSource
    required: bool = True
    default_presentation_group: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def identity_and_source_match_role(self) -> "GraphInput":
        if self.port.path != self.input_id:
            raise ValueError("Graph input id must match its typed port path")
        if (
            self.role == "design_variable"
            and self.value_source.source_kind != "candidate"
        ):
            raise ValueError("design variables must use a Candidate source")
        if (
            self.role == "fixed_parameter"
            and self.value_source.source_kind == "candidate"
        ):
            raise ValueError("fixed parameters cannot use a Candidate source")
        if self.value_source.source_kind == "fixed_value":
            value = self.value_source.value
            if self.port.value_kind == "number" and not isinstance(value, float):
                raise ValueError("numeric fixed Graph inputs require a numeric value")
            if self.port.value_kind == "categorical" and not isinstance(value, str):
                raise ValueError("categorical fixed Graph inputs require a string value")
            if self.port.value_kind == "sparse_blend":
                raise ValueError("sparse blend Graph inputs cannot embed a fixed value")
        return self


class DecisionOutputEvidence(ChainContractModel):
    """Reader-facing evidence boundary that is also part of Graph identity."""

    evidence_kind: Literal["measured", "synthetic_demonstration"]
    unit_or_scale: Annotated[str, Field(min_length=1)]
    goal_direction: Literal["at_least", "at_most", "target", "none"]
    source_variables: Annotated[tuple[str, ...], Field(min_length=1)]
    causal_claim: Literal["none"] = "none"
    production_use: Literal["allowed", "prohibited"]
    limitation: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def synthetic_evidence_is_never_production_ready(self) -> "DecisionOutputEvidence":
        if (
            self.evidence_kind == "synthetic_demonstration"
            and self.production_use != "prohibited"
        ):
            raise ValueError("synthetic demonstration evidence prohibits production use")
        if len(self.source_variables) != len(set(self.source_variables)):
            raise ValueError("Decision Output source variables must be unique")
        return self


class DecisionOutput(ChainContractModel):
    output_id: Annotated[str, Field(min_length=1)]
    source_stage_id: Annotated[str, Field(min_length=1)]
    source_output_key: Annotated[str, Field(min_length=1)]
    label: Annotated[str, Field(min_length=1)]
    group: Annotated[str, Field(min_length=1)]
    role: Literal[
        "primary_objective",
        "hard_constraint",
        "secondary_outcome",
        "diagnostic",
    ]
    required_for_complete_result: bool
    evidence: DecisionOutputEvidence | None = None


class PredictionGraphTopology(ChainContractModel):
    direct_dependencies: dict[str, tuple[str, ...]]
    ancestors: dict[str, tuple[str, ...]]
    descendants: dict[str, tuple[str, ...]]
    topological_layers: Annotated[
        tuple[tuple[Annotated[str, Field(min_length=1)], ...], ...],
        Field(min_length=1),
    ]
    affected_nodes_by_input: dict[str, tuple[str, ...]]


class ProjectedGraphInput(ChainContractModel):
    input_id: Annotated[str, Field(min_length=1)]
    label: Annotated[str, Field(min_length=1)]
    port: ChainPort
    role: Literal[
        "design_variable",
        "scenario_context",
        "fixed_parameter",
        "legacy_unspecified",
    ]
    required: bool
    affected_node_ids: tuple[str, ...]
    default_presentation_group: Annotated[str, Field(min_length=1)]


class PredictionGraphProjection(ChainContractModel):
    schema_version: Literal["prediction-graph-projection/v1"] = (
        "prediction-graph-projection/v1"
    )
    source_schema_version: Literal[
        "chain-definition/v1",
        "prediction-graph-definition/v1",
    ]
    graph_id: Annotated[str, Field(min_length=1)]
    topology: PredictionGraphTopology
    inputs: tuple[ProjectedGraphInput, ...]
    decision_outputs: tuple[DecisionOutput, ...]
    limitations: tuple[str, ...] = ()


def _binding_sort_key(binding: ChainBinding) -> tuple[str, ...]:
    source = binding.source
    return (
        binding.target_stage_id,
        binding.target_input_path,
        source.source_kind,
        source.path if source.source_kind == "external" else source.stage_id,
        "" if source.source_kind == "external" else source.output_key,
    )


def _prediction_graph_scientific_payload(
    definition: "PredictionGraphDefinition",
) -> dict[str, object]:
    """Canonical scientific identity; tuple order is presentation, never dependency."""

    return {
        "schema_version": definition.schema_version,
        "graph_id": definition.graph_id,
        "stages": [
            {
                "stage_id": item.stage_id,
                "stage_kind": item.stage_kind,
                "contract_id": item.contract_id,
            }
            for item in sorted(definition.stages, key=lambda item: item.stage_id)
        ],
        "inputs": [
            {
                "input_id": item.input_id,
                "port": item.port.model_dump(mode="json"),
                "role": item.role,
                "value_source": item.value_source.model_dump(mode="json"),
                "required": item.required,
            }
            for item in sorted(definition.inputs, key=lambda item: item.input_id)
        ],
        "bindings": [
            item.model_dump(mode="json")
            for item in sorted(definition.bindings, key=_binding_sort_key)
        ],
        "decision_outputs": [
            {
                "output_id": item.output_id,
                "source_stage_id": item.source_stage_id,
                "source_output_key": item.source_output_key,
                "role": item.role,
                "required_for_complete_result": item.required_for_complete_result,
                **(
                    {"evidence": item.evidence.model_dump(mode="json")}
                    if item.evidence is not None
                    else {}
                ),
            }
            for item in sorted(
                definition.decision_outputs,
                key=lambda item: item.output_id,
            )
        ],
    }


class PredictionGraphDefinition(ChainContractModel):
    schema_version: Literal["prediction-graph-definition/v1"] = (
        "prediction-graph-definition/v1"
    )
    graph_id: Annotated[str, Field(min_length=1)]
    label: Annotated[str, Field(min_length=1)]
    stages: Annotated[tuple[ChainStage, ...], Field(min_length=1)]
    inputs: tuple[GraphInput, ...] = ()
    bindings: Annotated[tuple[ChainBinding, ...], Field(min_length=1)]
    decision_outputs: Annotated[tuple[DecisionOutput, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def local_graph_shape_is_unambiguous(self) -> "PredictionGraphDefinition":
        stage_ids = [item.stage_id for item in self.stages]
        input_ids = [item.input_id for item in self.inputs]
        output_ids = [item.output_id for item in self.decision_outputs]
        if len(stage_ids) != len(set(stage_ids)):
            raise ValueError("Prediction Graph stage ids must be unique")
        if len(input_ids) != len(set(input_ids)):
            raise ValueError("Prediction Graph input ids must be unique")
        if len(output_ids) != len(set(output_ids)):
            raise ValueError("Prediction Graph decision output ids must be unique")
        if not any(
            item.required_for_complete_result
            for item in self.decision_outputs
        ):
            raise ValueError(
                "Prediction Graph requires at least one required decision output"
            )
        target_keys = [
            (binding.target_stage_id, binding.target_input_path)
            for binding in self.bindings
        ]
        if len(target_keys) != len(set(target_keys)):
            raise ValueError("each Prediction Graph stage input may be bound only once")
        known_stages = set(stage_ids)
        known_inputs = set(input_ids)
        for binding in self.bindings:
            if binding.target_stage_id not in known_stages:
                raise ValueError(
                    f"binding targets unknown stage: {binding.target_stage_id}"
                )
            if binding.source.source_kind == "external":
                if binding.source.path not in known_inputs:
                    raise ValueError(
                        "binding references unknown Graph input: "
                        f"{binding.source.path}"
                    )
            elif binding.source.stage_id not in known_stages:
                raise ValueError(
                    "binding references unknown source stage: "
                    f"{binding.source.stage_id}"
                )
        source_keys = [
            (item.source_stage_id, item.source_output_key)
            for item in self.decision_outputs
        ]
        if len(source_keys) != len(set(source_keys)):
            raise ValueError(
                "each Prediction Graph stage output may be a decision output only once"
            )
        unknown_terminal_stages = sorted(
            {
                item.source_stage_id
                for item in self.decision_outputs
                if item.source_stage_id not in known_stages
            }
        )
        if unknown_terminal_stages:
            raise ValueError(
                "decision outputs reference unknown stages: "
                f"{unknown_terminal_stages}"
            )
        topology = derive_prediction_graph_topology(self)
        terminal_stages = {item.source_stage_id for item in self.decision_outputs}
        used_stages = set(terminal_stages)
        for stage_id in terminal_stages:
            used_stages.update(topology.ancestors[stage_id])
        unused = sorted(known_stages - used_stages)
        if unused:
            raise ValueError(
                "Prediction Graph stages must feed a decision output: "
                f"{unused}"
            )
        bound_inputs = {
            binding.source.path
            for binding in self.bindings
            if binding.source.source_kind == "external"
        }
        unbound_required = sorted(
            item.input_id
            for item in self.inputs
            if item.required and item.input_id not in bound_inputs
        )
        if unbound_required:
            raise ValueError(
                "required Prediction Graph inputs must affect a stage: "
                f"{unbound_required}"
            )
        return self

    @property
    def chain_id(self) -> str:
        """Compatibility name for the existing catalog table namespace."""

        return self.graph_id

    @property
    def digest(self) -> str:
        return semantic_digest(_prediction_graph_scientific_payload(self))

    @property
    def topology(self) -> PredictionGraphTopology:
        return derive_prediction_graph_topology(self)


GraphDefinitionRef = Annotated[
    ChainDefinition | PredictionGraphDefinition,
    Field(discriminator="schema_version"),
]
_GRAPH_DEFINITION_ADAPTER = TypeAdapter(GraphDefinitionRef)


def parse_graph_definition_json(payload: str | bytes) -> GraphDefinitionRef:
    return _GRAPH_DEFINITION_ADAPTER.validate_json(payload)


def derive_prediction_graph_topology(
    definition: ChainDefinition | PredictionGraphDefinition,
) -> PredictionGraphTopology:
    stage_ids = {item.stage_id for item in definition.stages}
    input_ids = (
        {item.path for item in definition.external_inputs}
        if isinstance(definition, ChainDefinition)
        else {item.input_id for item in definition.inputs}
    )
    dependencies: dict[str, set[str]] = {
        stage_id: set() for stage_id in stage_ids
    }
    direct_input_targets: dict[str, set[str]] = {
        input_id: set() for input_id in input_ids
    }
    for binding in definition.bindings:
        if binding.target_stage_id not in stage_ids:
            raise ValueError(
                f"binding targets unknown stage: {binding.target_stage_id}"
            )
        if binding.source.source_kind == "stage_output":
            if binding.source.stage_id not in stage_ids:
                raise ValueError(
                    "binding references unknown source stage: "
                    f"{binding.source.stage_id}"
                )
            dependencies[binding.target_stage_id].add(binding.source.stage_id)
        else:
            if binding.source.path not in input_ids:
                raise ValueError(
                    f"binding references unknown Graph input: {binding.source.path}"
                )
            direct_input_targets[binding.source.path].add(
                binding.target_stage_id
            )

    remaining = {stage_id: set(items) for stage_id, items in dependencies.items()}
    layers: list[tuple[str, ...]] = []
    resolved: set[str] = set()
    while len(resolved) < len(stage_ids):
        layer = tuple(
            sorted(
                stage_id
                for stage_id, items in remaining.items()
                if stage_id not in resolved and items <= resolved
            )
        )
        if not layer:
            cyclic = sorted(stage_ids - resolved)
            raise ValueError(
                f"Prediction Graph must be acyclic; cycle involves: {cyclic}"
            )
        layers.append(layer)
        resolved.update(layer)

    ancestors: dict[str, set[str]] = {}
    for layer in layers:
        for stage_id in layer:
            values = set(dependencies[stage_id])
            for dependency in dependencies[stage_id]:
                values.update(ancestors[dependency])
            ancestors[stage_id] = values
    descendants: dict[str, set[str]] = {stage_id: set() for stage_id in stage_ids}
    for stage_id, values in ancestors.items():
        for ancestor in values:
            descendants[ancestor].add(stage_id)
    affected = {
        input_id: {
            node
            for target in targets
            for node in {target, *descendants[target]}
        }
        for input_id, targets in direct_input_targets.items()
    }
    return PredictionGraphTopology(
        direct_dependencies={
            stage_id: tuple(sorted(dependencies[stage_id]))
            for stage_id in sorted(stage_ids)
        },
        ancestors={
            stage_id: tuple(sorted(ancestors[stage_id]))
            for stage_id in sorted(stage_ids)
        },
        descendants={
            stage_id: tuple(sorted(descendants[stage_id]))
            for stage_id in sorted(stage_ids)
        },
        topological_layers=tuple(layers),
        affected_nodes_by_input={
            input_id: tuple(sorted(affected[input_id]))
            for input_id in sorted(input_ids)
        },
    )


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


class PredictionGraphRevision(ChainContractModel):
    schema_version: Literal["prediction-graph-revision/v1"] = (
        "prediction-graph-revision/v1"
    )
    graph_id: Annotated[str, Field(min_length=1)]
    revision: Annotated[int, Field(ge=1)]
    graph_definition_digest: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    binding_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    unit_conversion_digest: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    topology_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    stages: Annotated[tuple[ChainStageRevision, ...], Field(min_length=1)]
    revision_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]

    @property
    def chain_id(self) -> str:
        """Compatibility name for the existing catalog table namespace."""

        return self.graph_id

    @property
    def chain_definition_digest(self) -> str:
        """Compatibility name for the existing catalog relation."""

        return self.graph_definition_digest


GraphRevisionRef = Annotated[
    ChainRevision | PredictionGraphRevision,
    Field(discriminator="schema_version"),
]
_GRAPH_REVISION_ADAPTER = TypeAdapter(GraphRevisionRef)


def parse_graph_revision_json(payload: str | bytes) -> GraphRevisionRef:
    return _GRAPH_REVISION_ADAPTER.validate_json(payload)


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


class PredictionGraphProjectBinding(ChainContractModel):
    schema_version: Literal["prediction-graph-project-binding/v1"] = (
        "prediction-graph-project-binding/v1"
    )
    revision: Annotated[int, Field(ge=1)] = 1
    values: dict[
        Annotated[str, Field(min_length=1)],
        Annotated[float, Field(allow_inf_nan=False)] | str,
    ] = Field(default_factory=dict)
    digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def digest_matches_values(self) -> "PredictionGraphProjectBinding":
        expected = semantic_digest(
            {
                "schema_version": self.schema_version,
                "revision": self.revision,
                "values": self.values,
            }
        )
        if self.digest != expected:
            raise ValueError("Prediction Graph Project binding digestが一致しません")
        return self


class PredictionGraphProjectIdentity(ChainContractModel):
    identity_kind: Literal["prediction_graph"]
    graph_revision_id: Annotated[str, Field(min_length=1)]
    graph_revision_digest: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    project_binding: PredictionGraphProjectBinding


ProjectScientificIdentity = Annotated[
    SingleTaskProjectIdentity
    | ChainProjectIdentity
    | PredictionGraphProjectIdentity,
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


def validate_prediction_graph_definition(
    definition: PredictionGraphDefinition,
    *,
    contracts: Mapping[tuple[str, str], StageContractSurface],
) -> None:
    """Validate Graph ports and terminals against exact pinned stage contracts."""

    stages = {stage.stage_id: stage for stage in definition.stages}
    external_ports = {item.input_id: item.port for item in definition.inputs}
    for stage in definition.stages:
        key = (stage.stage_kind, stage.contract_id)
        contract = contracts.get(key)
        if contract is None:
            raise ValueError(
                "Prediction Graph stage references unknown contract: "
                f"{stage.stage_kind}/{stage.contract_id}"
            )
        if (
            contract.stage_kind != stage.stage_kind
            or contract.contract_id != stage.contract_id
        ):
            raise ValueError(
                "Prediction Graph contract registry key does not match its value"
            )

    bound_targets: dict[str, set[str]] = {
        stage.stage_id: set() for stage in definition.stages
    }
    for binding in definition.bindings:
        target_stage = stages[binding.target_stage_id]
        target_contract = contracts[
            (target_stage.stage_kind, target_stage.contract_id)
        ]
        target_ports = {port.path: port for port in target_contract.input_ports}
        target_port = target_ports.get(binding.target_input_path)
        if target_port is None:
            raise ValueError(
                "binding targets unknown Graph stage input: "
                f"{binding.target_stage_id}.{binding.target_input_path}"
            )
        bound_targets[binding.target_stage_id].add(binding.target_input_path)
        if binding.source.source_kind == "external":
            source_port = external_ports[binding.source.path]
        else:
            source_stage = stages[binding.source.stage_id]
            source_contract = contracts[
                (source_stage.stage_kind, source_stage.contract_id)
            ]
            source_port = next(
                (
                    port
                    for port in source_contract.output_ports
                    if port.path == binding.source.output_key
                ),
                None,
            )
            if source_port is None:
                raise ValueError(
                    "binding references unknown Graph stage output: "
                    f"{binding.source.stage_id}.{binding.source.output_key}"
                )
        source_label = (
            binding.source.path
            if binding.source.source_kind == "external"
            else f"{binding.source.stage_id}.{binding.source.output_key}"
        )
        target_label = f"{binding.target_stage_id}.{binding.target_input_path}"
        if source_port.value_kind != target_port.value_kind:
            raise ValueError(
                f"binding {source_label} -> {target_label} type mismatch: "
                f"{source_port.value_kind!r} -> "
                f"{target_port.value_kind!r}"
            )
        if (
            source_port.quantity != target_port.quantity
            or source_port.basis != target_port.basis
        ):
            raise ValueError(
                f"binding {source_label} -> {target_label} physical quantity "
                "or basis mismatch: "
                f"{source_port.quantity!r}/{source_port.basis!r} -> "
                f"{target_port.quantity!r}/{target_port.basis!r}"
            )
        if binding.conversion is None:
            if source_port.unit != target_port.unit:
                raise ValueError(
                    f"binding {source_label} -> {target_label} unit mismatch: "
                    f"{source_port.unit!r} -> "
                    f"{target_port.unit!r}"
                )
        elif (
            binding.conversion.source_unit != source_port.unit
            or binding.conversion.target_unit != target_port.unit
        ):
            raise ValueError(
                f"binding {source_label} -> {target_label} conversion does not "
                "match binding port units"
            )

    for stage in definition.stages:
        contract = contracts[(stage.stage_kind, stage.contract_id)]
        required_ports = {port.path for port in contract.input_ports}
        missing = sorted(required_ports - bound_targets[stage.stage_id])
        if missing:
            raise ValueError(
                f"Prediction Graph stage {stage.stage_id} has unbound required "
                f"inputs: {missing}"
            )

    for terminal in definition.decision_outputs:
        stage = stages[terminal.source_stage_id]
        contract = contracts[(stage.stage_kind, stage.contract_id)]
        if terminal.source_output_key not in {
            port.path for port in contract.output_ports
        }:
            raise ValueError(
                "decision output references unknown Graph stage output: "
                f"{terminal.source_stage_id}.{terminal.source_output_key}"
            )


def project_prediction_graph(
    definition: GraphDefinitionRef,
    *,
    contracts: Mapping[tuple[str, str], StageContractSurface],
) -> PredictionGraphProjection:
    """Return a read-only Graph view without inventing missing v1 semantics."""

    topology = derive_prediction_graph_topology(definition)
    if isinstance(definition, PredictionGraphDefinition):
        return PredictionGraphProjection(
            source_schema_version=definition.schema_version,
            graph_id=definition.graph_id,
            topology=topology,
            inputs=tuple(
                ProjectedGraphInput(
                    input_id=item.input_id,
                    label=item.label,
                    port=item.port,
                    role=item.role,
                    required=item.required,
                    affected_node_ids=topology.affected_nodes_by_input[
                        item.input_id
                    ],
                    default_presentation_group=item.default_presentation_group,
                )
                for item in definition.inputs
            ),
            decision_outputs=definition.decision_outputs,
        )

    task_stages = [
        stage for stage in definition.stages if stage.stage_kind == "task"
    ]
    terminal_stage = task_stages[-1] if task_stages else definition.stages[-1]
    terminal_surface = contracts.get(
        (terminal_stage.stage_kind, terminal_stage.contract_id)
    )
    decision_outputs = (
        tuple(
            DecisionOutput(
                output_id=(
                    f"legacy.{terminal_stage.stage_id}.{port.path}"
                ),
                source_stage_id=terminal_stage.stage_id,
                source_output_key=port.path,
                label=port.path,
                group="legacy_terminal",
                role="secondary_outcome",
                required_for_complete_result=True,
            )
            for port in terminal_surface.output_ports
        )
        if terminal_surface is not None
        else ()
    )
    limitations = [
        "v1 external input roles are unspecified and are not inferred"
    ]
    if terminal_surface is None:
        limitations.append(
            "v1 terminal outputs are unavailable because the pinned surface is missing"
        )
    return PredictionGraphProjection(
        source_schema_version=definition.schema_version,
        graph_id=definition.chain_id,
        topology=topology,
        inputs=tuple(
            ProjectedGraphInput(
                input_id=item.path,
                label=item.path,
                port=item,
                role="legacy_unspecified",
                required=True,
                affected_node_ids=topology.affected_nodes_by_input[item.path],
                default_presentation_group="legacy_inputs",
            )
            for item in definition.external_inputs
        ),
        decision_outputs=decision_outputs,
        limitations=tuple(limitations),
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


def build_prediction_graph_revision(
    definition: PredictionGraphDefinition,
    *,
    revision: int,
    contracts: Mapping[tuple[str, str], StageContractSurface],
    stage_locks: Mapping[str, ChainStageLock],
) -> PredictionGraphRevision:
    validate_prediction_graph_definition(definition, contracts=contracts)
    expected_stage_ids = {stage.stage_id for stage in definition.stages}
    if set(stage_locks) != expected_stage_ids:
        raise ValueError("stage locks must match Prediction Graph stages exactly")
    for stage in definition.stages:
        surface = contracts[(stage.stage_kind, stage.contract_id)]
        if stage_locks[stage.stage_id].contract_digest != surface.contract_digest:
            raise ValueError(
                f"stage lock contract digest does not match surface: {stage.stage_id}"
            )
    topology = definition.topology
    stage_by_id = {stage.stage_id: stage for stage in definition.stages}
    topological_stage_ids = tuple(
        stage_id
        for layer in topology.topological_layers
        for stage_id in layer
    )
    stage_revisions = tuple(
        ChainStageRevision(
            stage_id=stage_id,
            stage_kind=stage_by_id[stage_id].stage_kind,
            contract_id=stage_by_id[stage_id].contract_id,
            **stage_locks[stage_id].model_dump(),
        )
        for stage_id in topological_stage_ids
    )
    binding_payload = [
        item.model_dump(mode="json")
        for item in sorted(definition.bindings, key=_binding_sort_key)
    ]
    conversion_payload = [
        item.conversion.model_dump(mode="json")
        for item in sorted(definition.bindings, key=_binding_sort_key)
        if item.conversion is not None
    ]
    partial = {
        "schema_version": "prediction-graph-revision/v1",
        "graph_id": definition.graph_id,
        "revision": revision,
        "graph_definition_digest": definition.digest,
        "binding_digest": semantic_digest(binding_payload),
        "unit_conversion_digest": semantic_digest(conversion_payload),
        "topology_digest": semantic_digest(topology.model_dump(mode="json")),
        "stages": [item.model_dump(mode="json") for item in stage_revisions],
    }
    return PredictionGraphRevision(
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


def validate_prediction_graph_revision(
    definition: PredictionGraphDefinition,
    revision: PredictionGraphRevision,
    *,
    contracts: Mapping[tuple[str, str], StageContractSurface],
) -> None:
    """Recompute Graph topology and every scientific digest before storage."""

    validate_prediction_graph_definition(definition, contracts=contracts)
    if (
        revision.graph_id != definition.graph_id
        or revision.graph_definition_digest != definition.digest
    ):
        raise ValueError(
            "Prediction Graph Revision does not reference the exact Definition"
        )
    topology = definition.topology
    stage_by_id = {stage.stage_id: stage for stage in definition.stages}
    expected_stage_ids = [
        stage_id
        for layer in topology.topological_layers
        for stage_id in layer
    ]
    actual_stage_ids = [stage.stage_id for stage in revision.stages]
    if actual_stage_ids != expected_stage_ids:
        raise ValueError(
            "Prediction Graph Revision stages do not match derived topology"
        )
    for locked in revision.stages:
        stage = stage_by_id[locked.stage_id]
        if (
            locked.stage_kind != stage.stage_kind
            or locked.contract_id != stage.contract_id
        ):
            raise ValueError(
                "Prediction Graph Revision stage identity does not match Definition"
            )
        surface = contracts[(stage.stage_kind, stage.contract_id)]
        if locked.contract_digest != surface.contract_digest:
            raise ValueError(
                "Prediction Graph Revision contract digest does not match surface: "
                f"{stage.stage_id}"
            )
    expected_binding_digest = semantic_digest(
        [
            item.model_dump(mode="json")
            for item in sorted(definition.bindings, key=_binding_sort_key)
        ]
    )
    expected_conversion_digest = semantic_digest(
        [
            item.conversion.model_dump(mode="json")
            for item in sorted(definition.bindings, key=_binding_sort_key)
            if item.conversion is not None
        ]
    )
    if revision.binding_digest != expected_binding_digest:
        raise ValueError("Prediction Graph Revision binding digest is invalid")
    if revision.unit_conversion_digest != expected_conversion_digest:
        raise ValueError(
            "Prediction Graph Revision unit-conversion digest is invalid"
        )
    if revision.topology_digest != semantic_digest(
        topology.model_dump(mode="json")
    ):
        raise ValueError("Prediction Graph Revision topology digest is invalid")
    payload = revision.model_dump(mode="json", exclude={"revision_digest"})
    if revision.revision_digest != semantic_digest(payload):
        raise ValueError("Prediction Graph Revision digest is invalid")
