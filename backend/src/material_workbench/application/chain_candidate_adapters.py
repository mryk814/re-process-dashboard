"""Candidate-shape adapters for Chain execution.

Chain Core owns stage order, binding resolution, unit conversion, partial
recomputation, freshness, stale-response rejection, memoization and provenance.
Everything that depends on *what a candidate is made of* lives here:

- the external-input namespace a candidate exposes
- candidate validation and the initial candidate's domain payload
- the deterministic-transform stage's output shape
- the extra revision references a stored snapshot needs

Adapters are allow-listed and selected from the Chain Revision's declared stage
shape, not from a task id.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from material_workbench.application.payload_normalization import plain_payload
from material_workbench.contracts.blend_contracts import (
    BlendStructuralError,
    ResolvedBlendContracts,
    SparseBlend,
    validate_sparse_blend,
)
from material_workbench.contracts.chain_contracts import (
    ChainDefinition,
    ChainDomainReference,
    ChainRevision,
    ChainStageRevision,
    UnitConversion,
)
from material_workbench.contracts.chain_execution_contracts import (
    IntermediateActualRecord,
)
from material_workbench.contracts.schemas import Candidate, CandidateInput
from material_workbench.modeling.transform_catalog import DeterministicTransformCatalog


class ChainCandidateAdapterError(ValueError):
    """The candidate does not satisfy the shape this Chain Revision requires."""


@dataclass(frozen=True)
class ActualConditioningResult:
    """Typed adapter result for one immutable actual-conditioned variant."""

    coverage: tuple[str, ...]
    measured_values: dict[str, float]
    canonical_input: dict[str, Any]


@dataclass(frozen=True)
class _ActualMeasurementBinding:
    measurement_key: str
    target_input_path: str
    conversion: UnitConversion | None


def _actual_measurement_bindings(
    definition: ChainDefinition,
    target_stage: ChainStageRevision,
) -> tuple[_ActualMeasurementBinding, ...]:
    bindings = tuple(
        _ActualMeasurementBinding(
            measurement_key=binding.source.output_key,
            target_input_path=binding.target_input_path,
            conversion=binding.conversion,
        )
        for binding in definition.bindings
        if binding.target_stage_id == target_stage.stage_id
        and binding.source.source_kind == "stage_output"
    )
    if not bindings:
        raise ChainCandidateAdapterError(
            "終端Stageへ適用できる中間実測項目がありません"
        )
    return bindings


def _collect_actual_values(
    bindings: tuple[_ActualMeasurementBinding, ...],
    actual_records: tuple[IntermediateActualRecord, ...],
    *,
    item_label: str,
    missing_message: str,
) -> tuple[tuple[str, ...], dict[str, float]]:
    required = tuple(sorted({binding.measurement_key for binding in bindings}))
    measured: dict[str, float] = {}
    for record in actual_records:
        for key, value in record.values.items():
            if key in measured:
                raise ChainCandidateAdapterError(
                    f"{item_label} {key} が複数の実測IDに重複しています"
                )
            measured[key] = value
    missing = sorted(set(required) - set(measured))
    if missing:
        raise ChainCandidateAdapterError(
            f"{missing_message}（予測値では補完しません）: " + ", ".join(missing)
        )
    return required, measured


def _set_nested_value(target: dict[str, Any], path: str, value: float) -> None:
    parts = path.split(".")
    if len(parts) != 2 or parts[0] not in {"composition", "process"}:
        raise ChainCandidateAdapterError(
            f"中間実測を適用できないcanonical input pathです: {path}"
        )
    group = target.get(parts[0])
    if not isinstance(group, dict):
        raise ChainCandidateAdapterError(
            f"中間実測の適用先groupがcanonical inputにありません: {parts[0]}"
        )
    group[parts[1]] = value


def _scalar_candidate_path(
    external_path: str,
    value_kind: str,
    quantity: str,
) -> str:
    if value_kind == "sparse_blend":
        raise ChainCandidateAdapterError(
            "scalar Chainは疎な配合入力を公開できません"
        )
    candidate_path = external_path.removeprefix("candidate.")
    group = candidate_path.split(".", 1)[0]
    valid_groups = (
        {"composition", "process"}
        if value_kind == "number"
        else {"categorical"}
    )
    if (
        candidate_path == external_path
        or group not in valid_groups
        or candidate_path.rsplit(".", 1)[-1] != quantity
    ):
        raise ChainCandidateAdapterError(
            f"scalar Chainの外部入力pathを候補へ対応付けられません: {external_path}"
        )
    return candidate_path


def _sparse_blend_candidate_path(
    external_path: str,
    value_kind: str,
    quantity: str,
) -> str:
    if value_kind == "sparse_blend":
        if external_path != "candidate.blend":
            raise ChainCandidateAdapterError(
                f"疎な配合入力pathを候補へ対応付けられません: {external_path}"
            )
        return "blend"
    if value_kind not in {"number", "categorical"}:
        raise ChainCandidateAdapterError(
            f"未対応のChain候補入力型です: {value_kind}"
        )
    group = "process" if value_kind == "number" else "categorical"
    return f"{group}.{quantity}"


def candidate_path_for_revision(
    revision: ChainRevision,
    external_path: str,
    value_kind: str,
    quantity: str,
) -> str:
    """Resolve candidate storage without loading a deterministic Transform."""

    deterministic = [
        stage
        for stage in revision.stages
        if stage.stage_kind == "deterministic_transform"
    ]
    if not deterministic:
        return _scalar_candidate_path(external_path, value_kind, quantity)
    if len(deterministic) == 1:
        return _sparse_blend_candidate_path(external_path, value_kind, quantity)
    raise ChainCandidateAdapterError(
        "決定論的Stageを2段以上持つChainに対応するcandidate adapterがありません"
    )


class ChainCandidateAdapter(Protocol):
    adapter_id: str
    sparse_blend: bool

    def candidate_path(
        self,
        external_path: str,
        value_kind: str,
        quantity: str,
    ) -> str:
        """Address the canonical Candidate field backing one external port."""

    def external_values(self, candidate: Candidate | CandidateInput) -> dict[str, Any]:
        """Candidate values addressed by the Chain's external input paths."""

    def prepare_candidate(self, payload: CandidateInput) -> CandidateInput:
        """Validate and annotate a candidate before Core checks binding coverage."""

    def initial_domain_payload(self) -> dict[str, Any]:
        """Extra ``CandidateInput`` fields the initial candidate needs."""

    def snapshot_domain_references(
        self, candidate: Candidate
    ) -> tuple[ChainDomainReference, ...]:
        """Adapter-owned revision references stored with an immutable snapshot."""

    def assert_deterministic_identity(
        self, stage: ChainStageRevision, candidate: Candidate
    ) -> str:
        """Return the deterministic stage's package digest, raising on mismatch."""

    def run_deterministic_stage(
        self, stage: ChainStageRevision, candidate: Candidate
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Execute the deterministic stage, returning its payload and outputs."""

    def deterministic_outputs(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Recover binding-visible outputs from a stored deterministic payload."""

    def apply_actual_measurements(
        self,
        definition: ChainDefinition,
        target_stage: ChainStageRevision,
        base_canonical_input: Mapping[str, Any],
        actual_records: tuple[IntermediateActualRecord, ...],
    ) -> ActualConditioningResult:
        """Validate measured upstream outputs and apply them to the target input."""


class ScalarChainAdapter:
    """A Chain whose candidate is only scalar and categorical process inputs."""

    adapter_id = "scalar/v1"
    sparse_blend = False

    def candidate_path(
        self,
        external_path: str,
        value_kind: str,
        quantity: str,
    ) -> str:
        return _scalar_candidate_path(external_path, value_kind, quantity)

    def external_values(self, candidate: Candidate | CandidateInput) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for key, value in candidate.inputs.process.items():
            values[f"candidate.process.{key}"] = value
        for key, value in candidate.inputs.categorical.items():
            values[f"candidate.categorical.{key}"] = value
        for key, value in candidate.inputs.composition.items():
            values[f"candidate.composition.{key}"] = value
        return values

    def prepare_candidate(self, payload: CandidateInput) -> CandidateInput:
        if payload.blend is not None:
            raise ChainCandidateAdapterError(
                "このChainは疎な配合明細を受け取りません"
            )
        return payload

    def initial_domain_payload(self) -> dict[str, Any]:
        return {}

    def snapshot_domain_references(
        self, candidate: Candidate
    ) -> tuple[ChainDomainReference, ...]:
        return ()

    def assert_deterministic_identity(
        self, stage: ChainStageRevision, candidate: Candidate
    ) -> str:
        raise ChainCandidateAdapterError(
            "このChainには決定論的Stageがありません"
        )

    def run_deterministic_stage(
        self, stage: ChainStageRevision, candidate: Candidate
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        raise ChainCandidateAdapterError(
            "このChainには決定論的Stageがありません"
        )

    def deterministic_outputs(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        raise ChainCandidateAdapterError(
            "このChainには決定論的Stageがありません"
        )

    def apply_actual_measurements(
        self,
        definition: ChainDefinition,
        target_stage: ChainStageRevision,
        base_canonical_input: Mapping[str, Any],
        actual_records: tuple[IntermediateActualRecord, ...],
    ) -> ActualConditioningResult:
        bindings = _actual_measurement_bindings(definition, target_stage)
        coverage, measured = _collect_actual_values(
            bindings,
            actual_records,
            item_label="実測項目",
            missing_message="終端Stageに必要な実測項目が不足しています",
        )
        canonical_input = deepcopy(dict(base_canonical_input))
        for binding in bindings:
            value = measured[binding.measurement_key]
            if binding.conversion is not None:
                value = (
                    value * binding.conversion.factor
                    + binding.conversion.offset
                )
            _set_nested_value(
                canonical_input,
                binding.target_input_path,
                value,
            )
        return ActualConditioningResult(
            coverage=coverage,
            measured_values={key: measured[key] for key in coverage},
            canonical_input=canonical_input,
        )


@dataclass(frozen=True)
class SparseBlendChainAdapter:
    """The welding Chain: a sparse blend plus welding and test context scalars."""

    transform_catalog: DeterministicTransformCatalog
    stage: ChainStageRevision

    adapter_id = "sparse_blend/v1"
    sparse_blend = True

    def candidate_path(
        self,
        external_path: str,
        value_kind: str,
        quantity: str,
    ) -> str:
        return _sparse_blend_candidate_path(
            external_path,
            value_kind,
            quantity,
        )

    def external_values(self, candidate: Candidate | CandidateInput) -> dict[str, Any]:
        values: dict[str, Any] = {}
        if candidate.blend is not None:
            values["candidate.blend"] = candidate.blend.model_input_payload()
        for key, value in candidate.inputs.process.items():
            values[f"candidate.welding_context.{key}"] = value
            values[f"candidate.test_context.{key}"] = value
        for key, value in candidate.inputs.categorical.items():
            values[f"candidate.welding_context.{key}"] = value
            values[f"candidate.test_context.{key}"] = value
        return values

    def prepare_candidate(self, payload: CandidateInput) -> CandidateInput:
        if payload.blend is None:
            raise ChainCandidateAdapterError("Chain候補には疎な配合明細が必要です")
        try:
            resolution = self.transform_catalog.resolve_execution(
                self.stage.contract_id,
                payload.blend,
                self.stage.package_manifest_digest,
                self.stage.contract_digest,
            )
            if (
                f"sha256:{resolution.package.manifest_sha256}"
                != self.stage.package_manifest_digest
                or resolution.contract_digest != self.stage.contract_digest
            ):
                raise ChainCandidateAdapterError(
                    "候補のStage A Package/contract revisionがChain Revisionと一致しません"
                )
            validation = validate_sparse_blend(payload.blend, resolution.contracts)
        except (BlendStructuralError, ValueError) as exc:
            raise ChainCandidateAdapterError(str(exc)) from exc
        return payload.model_copy(update={"blend_validation": validation})

    @property
    def transform_id(self) -> str:
        return self.stage.contract_id

    def _initial_blend(self) -> SparseBlend:
        try:
            return self.transform_catalog.initial_blend_for_package(
                self.stage.contract_id,
                self.stage.package_manifest_digest,
                self.stage.contract_digest,
            )
        except (KeyError, BlendStructuralError) as exc:
            raise ChainCandidateAdapterError(
                "Chain Revisionに固定された初期配合を解決できません"
            ) from exc

    def resolved_contracts(self) -> ResolvedBlendContracts:
        """The Design Space and catalog pinned by this Chain Revision's Stage A."""

        try:
            return self.transform_catalog.resolve_execution(
                self.stage.contract_id,
                self._initial_blend(),
                self.stage.package_manifest_digest,
                self.stage.contract_digest,
            ).contracts
        except (KeyError, BlendStructuralError) as exc:
            raise ChainCandidateAdapterError(
                "Chain Revisionに固定されたStage A契約を解決できません"
            ) from exc

    def initial_domain_payload(self) -> dict[str, Any]:
        return {"blend": self._initial_blend()}

    def snapshot_domain_references(
        self, candidate: Candidate
    ) -> tuple[ChainDomainReference, ...]:
        blend: SparseBlend | None = candidate.blend
        if blend is None:
            raise ChainCandidateAdapterError("Chain候補には疎な配合明細が必要です")
        return (
            ChainDomainReference(kind="design_space", ref=blend.design_space),
            ChainDomainReference(kind="commercial_catalog", ref=blend.commercial_catalog),
        )

    def _resolution(self, stage: ChainStageRevision, candidate: Candidate) -> Any:
        if candidate.blend is None:
            raise ChainCandidateAdapterError("Stage Aに配合明細がありません")
        resolution = self.transform_catalog.resolve_execution(
            stage.contract_id,
            candidate.blend,
            stage.package_manifest_digest,
            stage.contract_digest,
        )
        if resolution.contract_digest != stage.contract_digest:
            raise ChainCandidateAdapterError(
                f"Stage {stage.stage_id}のcontract digestがChain Revisionと一致しません"
            )
        return resolution

    def assert_deterministic_identity(
        self, stage: ChainStageRevision, candidate: Candidate
    ) -> str:
        return f"sha256:{self._resolution(stage, candidate).package.manifest_sha256}"

    def run_deterministic_stage(
        self, stage: ChainStageRevision, candidate: Candidate
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        resolution = self._resolution(stage, candidate)
        assert candidate.blend is not None
        payload = plain_payload(resolution.transform.transform(candidate.blend))
        return payload, self.deterministic_outputs(payload)

    def deterministic_outputs(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        composition = payload.get("material_composition", {})
        auxiliary = payload.get("auxiliary_features", {})
        if not isinstance(composition, dict) or not isinstance(auxiliary, dict):
            raise ChainCandidateAdapterError(
                "決定論的Stageの結果をbindingへ戻せません"
            )
        return {**composition, **auxiliary}

    def apply_actual_measurements(
        self,
        definition: ChainDefinition,
        target_stage: ChainStageRevision,
        base_canonical_input: Mapping[str, Any],
        actual_records: tuple[IntermediateActualRecord, ...],
    ) -> ActualConditioningResult:
        upstream_bindings = _actual_measurement_bindings(definition, target_stage)
        unsupported = sorted(
            binding.target_input_path
            for binding in upstream_bindings
            if not binding.target_input_path.startswith("composition.")
        )
        if unsupported:
            raise ChainCandidateAdapterError(
                "疎配合Chainの中間実測を適用できないcanonical input pathです: "
                + ", ".join(unsupported)
            )
        bindings = tuple(
            _ActualMeasurementBinding(
                measurement_key=binding.target_input_path.removeprefix(
                    "composition."
                ),
                target_input_path=binding.target_input_path,
                conversion=None,
            )
            for binding in upstream_bindings
        )
        coverage, measured = _collect_actual_values(
            bindings,
            actual_records,
            item_label="成分",
            missing_message="Stage Cに必要な実測成分が不足しています",
        )
        canonical_input = deepcopy(dict(base_canonical_input))
        canonical_input["composition"] = {
            binding.target_input_path.removeprefix("composition."): measured[
                binding.measurement_key
            ]
            for binding in bindings
        }
        return ActualConditioningResult(
            coverage=coverage,
            measured_values={key: measured[key] for key in coverage},
            canonical_input=canonical_input,
        )


def candidate_adapter_for(
    revision: ChainRevision,
    transform_catalog: DeterministicTransformCatalog,
) -> ChainCandidateAdapter:
    """Select an allow-listed adapter from the Chain Revision's stage shape."""

    deterministic = [
        stage for stage in revision.stages
        if stage.stage_kind == "deterministic_transform"
    ]
    if not deterministic:
        return ScalarChainAdapter()
    if len(deterministic) == 1:
        return SparseBlendChainAdapter(transform_catalog, deterministic[0])
    raise ChainCandidateAdapterError(
        "決定論的Stageを2段以上持つChainに対応するcandidate adapterがありません"
    )
