"""Explicit, reproducible uncertainty propagation for an already-fresh Chain."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping
import uuid

import numpy as np

from material_workbench.application.chain_candidate_adapters import (
    ChainCandidateAdapter,
    ChainCandidateAdapterError,
)
from material_workbench.application.chain_execution_plan import (
    ChainExecutionError,
    ChainPlanningUseCase,
    set_path,
)
from material_workbench.application.chain_stage_execution import ChainStageExecutor
from material_workbench.contracts.chain_contracts import ChainBinding, ChainStageRevision
from material_workbench.contracts.chain_uncertainty_contracts import (
    ChainDistributionCapability,
    ChainDistributionProvenance,
    ChainDistributionRun,
    ChainStageUncertainty,
    ChainStageSamplingCapability,
    DistributionSummary,
    StageSamplingCapability,
)
from material_workbench.contracts.schemas import CandidateInputs
from material_workbench.persistence.store import Store, StoreDataIntegrityError
from material_workbench.task_composition.ports import StageSampleRuntime


def _method_label(method: str) -> str:
    if method == "deterministic-exact/v1":
        return "決定論的（不確かさなし）"
    if method == "independent-residual-normal-bounded-from-q05-q95/v1":
        return "独立残差正規近似（q05–q95由来・出力境界適用）"
    return method


def _summary(values: np.ndarray) -> DistributionSummary:
    q05, q50, q95 = np.quantile(values, (0.05, 0.50, 0.95))
    return DistributionSummary(
        mean=float(np.mean(values)),
        standard_deviation=float(np.std(values)),
        quantiles={"0.05": float(q05), "0.50": float(q50), "0.95": float(q95)},
        sample_count=len(values),
    )


def combine_additive_stage_samples(
    conditional_point: np.ndarray,
    intrinsic_samples: np.ndarray,
    reference_point: float,
) -> np.ndarray:
    """Compose upstream-conditioned means with this stage's residual draws."""

    conditional = np.asarray(conditional_point, dtype=float)
    intrinsic = np.asarray(intrinsic_samples, dtype=float)
    if conditional.shape != intrinsic.shape or conditional.ndim != 1:
        raise ValueError("conditional and intrinsic samples must be aligned vectors")
    if (
        not np.isfinite(conditional).all()
        or not np.isfinite(intrinsic).all()
        or not np.isfinite(reference_point)
    ):
        raise ValueError("Monte Carlo samples must be finite")
    return conditional + intrinsic - float(reference_point)


def apply_output_bounds(
    values: np.ndarray,
    bounds: tuple[float | None, float | None],
) -> np.ndarray:
    """Apply an allow-listed output support after Monte Carlo composition."""

    result = np.asarray(values, dtype=float)
    if result.ndim != 1 or not np.isfinite(result).all():
        raise ValueError("Monte Carlo samples must be a finite vector")
    lower, upper = bounds
    if lower is not None:
        result = np.maximum(result, lower)
    if upper is not None:
        result = np.minimum(result, upper)
    return result


def _point_estimates(
    stage: ChainStageRevision,
    result: Mapping[str, Any],
    adapter: ChainCandidateAdapter,
) -> dict[str, float]:
    if stage.stage_kind == "deterministic_transform":
        try:
            values: Mapping[str, Any] = adapter.deterministic_outputs(result)
        except ChainCandidateAdapterError as exc:
            raise ChainExecutionError(str(exc)) from exc
    else:
        values = {
            key: item.get("value")
            for key, item in result.get("predictions", {}).items()
            if isinstance(item, Mapping)
        }
    return {
        str(key): float(value)
        for key, value in values.items()
        if isinstance(value, (int, float))
    }


class ChainUncertaintyService:
    def __init__(
        self,
        store: Store,
        planning: ChainPlanningUseCase,
        stage_executor: ChainStageExecutor,
    ) -> None:
        self.store = store
        self.planning = planning
        self.stage_executor = stage_executor

    def capability(self, project_id: str) -> ChainDistributionCapability:
        project = self.store.get_project(project_id)
        if project is None or project.scientific_identity.identity_kind != "chain":
            raise ChainExecutionError("このAPIはChain Project専用です")
        identity = project.scientific_identity
        revision = self.store.get_chain_revision(identity.chain_revision_id)
        if revision is None or revision.revision_digest != identity.chain_revision_digest:
            raise ChainExecutionError("固定されたChain Revisionを解決できません")
        stages: list[ChainStageSamplingCapability] = []
        full_propagation_supported = True
        sampled_predictive_stages = 0
        for stage in revision.stages:
            if stage.stage_kind == "deterministic_transform":
                capability = StageSamplingCapability(
                    supported=True,
                    method="deterministic-exact/v1",
                    method_label=_method_label("deterministic-exact/v1"),
                    output_dependence="deterministic",
                )
            else:
                entry = self.planning.registry.entry_for(stage.contract_id)
                runtime = entry.predictor_runtime
                method = (
                    runtime.chain_sampling_method
                    if isinstance(runtime, StageSampleRuntime)
                    else ""
                )
                exact_package = entry.package_digest == stage.package_manifest_digest
                if method and exact_package:
                    sampled_predictive_stages += 1
                    capability = StageSamplingCapability(
                        supported=True,
                        method=method,
                        method_label=_method_label(method),
                        output_dependence="independent",
                    )
                else:
                    full_propagation_supported = False
                    capability = StageSamplingCapability(
                        supported=False,
                        reason=(
                            "固定Package/runtimeがStage sample protocolを宣言していません"
                            if exact_package
                            else "固定Packageを現在のruntimeで解決できません"
                        ),
                    )
            stages.append(
                ChainStageSamplingCapability(
                    stage_id=stage.stage_id,
                    package_manifest_digest=stage.package_manifest_digest,
                    capability=capability,
                )
            )
        return ChainDistributionCapability(
            chain_revision_id=identity.chain_revision_id,
            chain_revision_digest=identity.chain_revision_digest,
            explicit_run_available=sampled_predictive_stages > 0,
            full_propagation_supported=full_propagation_supported,
            stages=tuple(stages),
        )

    @staticmethod
    def _sample_binding_value(
        binding: ChainBinding,
        sample_index: int,
        external: Mapping[str, Any],
        point_outputs: Mapping[str, Mapping[str, Any]],
        sampled_outputs: Mapping[str, Mapping[str, np.ndarray]],
    ) -> Any:
        source = binding.source
        if source.source_kind == "external":
            return external[source.path]
        stage_samples = sampled_outputs.get(source.stage_id)
        if stage_samples is not None and source.output_key in stage_samples:
            return float(stage_samples[source.output_key][sample_index])
        return point_outputs[source.stage_id][source.output_key]

    def _sample_canonical_input(
        self,
        *,
        definition: Any,
        stage_id: str,
        sample_index: int,
        external: Mapping[str, Any],
        point_outputs: Mapping[str, Mapping[str, Any]],
        sampled_outputs: Mapping[str, Mapping[str, np.ndarray]],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for binding in definition.bindings:
            if binding.target_stage_id != stage_id:
                continue
            value = self._sample_binding_value(
                binding,
                sample_index,
                external,
                point_outputs,
                sampled_outputs,
            )
            if binding.conversion is not None:
                value = value * binding.conversion.factor + binding.conversion.offset
            set_path(result, binding.target_input_path, value)
        return result

    def run(
        self,
        *,
        project_id: str,
        candidate_id: str,
        candidate_revision: int,
        seed: int,
        sample_count: int,
    ) -> ChainDistributionRun:
        candidate, definition, revision, identity = self.planning.resolve(
            project_id, candidate_id, candidate_revision
        )
        point = self.store.get_chain_execution(project_id, candidate_id)
        if (
            point is None
            or point.status != "latest"
            or point.candidate_revision != candidate_revision
            or point.chain_revision_digest != identity.chain_revision_digest
            or any(stage.status != "latest" for stage in point.stages)
        ):
            raise ChainExecutionError(
                "分布を実行する前に、同じ候補revisionの点推定を最新にしてください"
            )
        point_by_stage = {stage.stage_id: stage for stage in point.stages}
        adapter = self.planning.adapter_for(revision)
        external = adapter.external_values(candidate)
        point_outputs: dict[str, dict[str, Any]] = {}
        sampled_outputs: dict[str, dict[str, np.ndarray]] = {}
        stage_results: list[ChainStageUncertainty] = []
        stage_seeds = np.random.SeedSequence(seed).spawn(len(revision.stages))
        unsupported_predictive = False
        propagation_blocked = False

        for stage, seed_sequence in zip(revision.stages, stage_seeds, strict=True):
            evidence = point_by_stage[stage.stage_id]
            assert evidence.result is not None
            point_values = _point_estimates(stage, evidence.result, adapter)
            point_outputs[stage.stage_id] = self.stage_executor._outputs_from_payload(
                stage, evidence.result, adapter
            )
            if stage.stage_kind == "deterministic_transform":
                stage_results.append(
                    ChainStageUncertainty(
                        stage_id=stage.stage_id,
                        capability=StageSamplingCapability(
                            supported=True,
                            method="deterministic-exact/v1",
                            method_label=_method_label("deterministic-exact/v1"),
                            output_dependence="deterministic",
                        ),
                        package_manifest_digest=stage.package_manifest_digest,
                        point_estimates=point_values,
                    )
                )
                continue

            self.stage_executor._assert_runtime_identity(stage, candidate, adapter)
            runtime = self.planning.registry.entry_for(
                stage.contract_id
            ).predictor_runtime
            method = (
                runtime.chain_sampling_method
                if isinstance(runtime, StageSampleRuntime)
                else ""
            )
            if not method:
                unsupported_predictive = True
                propagation_blocked = True
                stage_results.append(
                    ChainStageUncertainty(
                        stage_id=stage.stage_id,
                        capability=StageSamplingCapability(
                            supported=False,
                            reason=(
                                "固定Package/runtimeがStage sample protocolを"
                                "宣言していません"
                            ),
                        ),
                        package_manifest_digest=stage.package_manifest_digest,
                        point_estimates=point_values,
                    )
                )
                continue

            stage_seed = int(seed_sequence.generate_state(1)[0])
            fixed_input = evidence.canonical_input
            fixed_candidate = candidate.model_copy(
                deep=True,
                update={
                    "inputs": CandidateInputs.model_validate(
                        {
                            **fixed_input,
                            "heat_pattern": None,
                            "heat_time_basis": "line_speed",
                        }
                    ),
                    "blend": None,
                },
            )
            sample_result = runtime.sample_core(
                fixed_candidate,
                sample_count=sample_count,
                seed=stage_seed,
            )
            if sample_result.method != method:
                raise ChainExecutionError(
                    f"Stage {stage.stage_id}のsample方式がPackage宣言と一致しません"
                )
            if set(sample_result.outputs) != set(point_values):
                raise ChainExecutionError(
                    f"Stage {stage.stage_id}のsample出力がcanonical outputと一致しません"
                )
            bounds = runtime.chain_sample_bounds
            if set(bounds) != set(point_values):
                raise ChainExecutionError(
                    f"Stage {stage.stage_id}のsample境界がcanonical outputと一致しません"
                )
            raw_intrinsic = {
                key: np.asarray(values, dtype=float)
                for key, values in sample_result.outputs.items()
            }
            intrinsic = {
                key: apply_output_bounds(values, bounds[key])
                for key, values in raw_intrinsic.items()
            }
            if sampled_outputs and not propagation_blocked:
                conditional = {key: np.empty(sample_count) for key in intrinsic}
                for index in range(sample_count):
                    canonical_input = self._sample_canonical_input(
                        definition=definition,
                        stage_id=stage.stage_id,
                        sample_index=index,
                        external=external,
                        point_outputs=point_outputs,
                        sampled_outputs=sampled_outputs,
                    )
                    _, outputs = self.stage_executor._run_stage(
                        stage, canonical_input, candidate, adapter
                    )
                    for key in conditional:
                        conditional[key][index] = float(outputs[key])
                propagated = {
                    key: apply_output_bounds(
                        combine_additive_stage_samples(
                            conditional[key],
                            raw_intrinsic[key],
                            sample_result.reference_points[key],
                        ),
                        bounds[key],
                    )
                    for key in raw_intrinsic
                }
            elif not propagation_blocked:
                propagated = intrinsic
            else:
                propagated = {}
            if propagated:
                sampled_outputs[stage.stage_id] = propagated
            stage_results.append(
                ChainStageUncertainty(
                    stage_id=stage.stage_id,
                    capability=StageSamplingCapability(
                        supported=True,
                        method=method,
                        method_label=_method_label(method),
                        output_dependence="independent",
                    ),
                    package_manifest_digest=stage.package_manifest_digest,
                    point_estimates=point_values,
                    stage_uncertainty={
                        key: _summary(values) for key, values in intrinsic.items()
                    },
                    propagated_uncertainty={
                        key: _summary(values) for key, values in propagated.items()
                    },
                    seed=stage_seed,
                )
            )

        run = ChainDistributionRun(
            run_id=str(uuid.uuid4()),
            project_id=project_id,
            status="unsupported" if unsupported_predictive else "completed",
            provenance=ChainDistributionProvenance(
                seed=seed,
                sample_count=sample_count,
                chain_revision_id=identity.chain_revision_id,
                chain_revision_digest=identity.chain_revision_digest,
                candidate_id=candidate_id,
                candidate_revision=candidate_revision,
                point_execution_request_id=point.request_id,
            ),
            stages=tuple(stage_results),
            created_at=datetime.now(UTC),
        )
        try:
            return self.store.insert_chain_distribution_run(
                run, expected_point=point
            )
        except StoreDataIntegrityError as exc:
            raise ChainExecutionError(
                "分布実行中に候補または点推定が更新されたため、"
                "この結果は保存されませんでした"
            ) from exc
