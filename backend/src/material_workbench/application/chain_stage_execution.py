"""Execute individual stages for an immutable Chain plan."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

from material_workbench.application.chain_candidate_adapters import (
    ChainCandidateAdapter,
    ChainCandidateAdapterError,
)
from material_workbench.application.chain_execution_plan import ChainExecutionError, set_path
from material_workbench.application.payload_normalization import plain_payload
from material_workbench.contracts.chain_contracts import ChainBinding, ChainStageRevision
from material_workbench.contracts.chain_execution_contracts import (
    ChainStageExecution,
    ChainStageOutputDefinition,
)
from material_workbench.contracts.schemas import Candidate, CandidateInputs
from material_workbench.execution.inference_work_graph import semantic_digest
from material_workbench.modeling.transform_catalog import DeterministicTransformCatalog
from material_workbench.tasks.task_registry import TaskRegistry


def _now() -> datetime:
    return datetime.now(UTC)


class ChainStageExecutor:
    def __init__(
        self,
        registry: TaskRegistry,
        transform_catalog: DeterministicTransformCatalog | None,
    ) -> None:
        self.registry = registry
        self.transform_catalog = transform_catalog

    def _canonical_input(
        self,
        definition: ChainDefinition,
        stage_id: str,
        external: Mapping[str, Any],
        upstream_outputs: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        bindings = [
            item for item in definition.bindings if item.target_stage_id == stage_id
        ]
        for binding in bindings:
            value = self._binding_value(
                binding, external, upstream_outputs
            )
            if binding.conversion is not None:
                value = value * binding.conversion.factor + binding.conversion.offset
            set_path(result, binding.target_input_path, value)
        return result

    def _binding_value(
        self,
        binding: ChainBinding,
        external: Mapping[str, Any],
        upstream_outputs: Mapping[str, Mapping[str, Any]],
    ) -> Any:
        source = binding.source
        if source.source_kind == "external":
            try:
                return external[source.path]
            except KeyError as exc:
                raise ChainExecutionError(
                    f"候補入力が不足しています: {source.path}"
                ) from exc
        try:
            return upstream_outputs[source.stage_id][source.output_key]
        except KeyError as exc:
            raise ChainExecutionError(
                f"上流出力が不足しています: {source.stage_id}.{source.output_key}"
            ) from exc

    def _assert_runtime_identity(
        self,
        stage: ChainStageRevision,
        candidate: Candidate,
        adapter: ChainCandidateAdapter,
    ) -> None:
        if stage.stage_kind == "deterministic_transform":
            try:
                actual = adapter.assert_deterministic_identity(stage, candidate)
            except ChainCandidateAdapterError as exc:
                raise ChainExecutionError(str(exc)) from exc
        else:
            actual_contract = semantic_digest(
                self.registry.contract_for(
                    stage.contract_id
                ).task_definition.model_dump(mode="json")
            )
            if actual_contract != stage.contract_digest:
                raise ChainExecutionError(
                    f"Stage {stage.stage_id}のcontract digestがChain Revisionと一致しません"
                )
            actual = self.registry.entry_for(stage.contract_id).package_digest
        if actual != stage.package_manifest_digest:
            raise ChainExecutionError(
                f"Stage {stage.stage_id}のPackage digestがChain Revisionと一致しません"
            )

    def _output_definitions(
        self,
        stage: ChainStageRevision,
    ) -> tuple[ChainStageOutputDefinition, ...]:
        if stage.stage_kind == "task":
            task = self.registry.contract_for(stage.contract_id).task_definition
            return tuple(
                ChainStageOutputDefinition(
                    key=output.key,
                    label=output.label,
                    unit=output.unit,
                    display_decimals=task.display_decimals[f"output.{output.key}"],

                    goal_direction=output.goal_direction,
                )
                for output in task.outputs
            )
        return tuple(
            ChainStageOutputDefinition(
                key=presentation.key,
                label=presentation.label,
                unit=presentation.unit,
                display_decimals=presentation.display_decimals,
            )
            for presentation in self.transform_catalog.output_presentations_for_revision(
                stage.contract_id,
                stage.package_manifest_digest,
                stage.contract_digest,
            )
        )

    def _run_stage(
        self,
        stage: ChainStageRevision,
        canonical_input: dict[str, Any],
        candidate: Candidate,
        adapter: ChainCandidateAdapter,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        self._assert_runtime_identity(stage, candidate, adapter)
        if stage.stage_kind == "deterministic_transform":
            try:
                return adapter.run_deterministic_stage(stage, candidate)
            except ChainCandidateAdapterError as exc:
                raise ChainExecutionError(str(exc)) from exc
        stage_candidate = candidate.model_copy(
            deep=True,
            update={
                "inputs": CandidateInputs.model_validate(
                    {
                        # A stage may bind only some input groups; Core must not
                        # assume a composition group exists.
                        "composition": {},
                        "process": {},
                        "categorical": {},
                        **canonical_input,
                        "heat_pattern": None,
                        "heat_time_basis": "line_speed",
                    }
                ),
                "blend": None,
            },
        )
        runtime = self.registry.entry_for(stage.contract_id).predictor_runtime
        payload = plain_payload(runtime.predict_core(stage_candidate, detailed=False))
        predictions = payload.get("predictions")
        if not isinstance(predictions, dict):
            raise ChainExecutionError(
                f"Stage {stage.stage_id}がcanonical predictionsを返しませんでした"
            )
        outputs = {
            key: value["value"]
            for key, value in predictions.items()
            if isinstance(value, dict) and "value" in value
        }
        return payload, outputs

    def _outputs_from_payload(
        self,
        stage: ChainStageRevision,
        payload: Mapping[str, Any],
        adapter: ChainCandidateAdapter,
    ) -> dict[str, Any]:
        if stage.stage_kind == "deterministic_transform":
            try:
                return adapter.deterministic_outputs(payload)
            except ChainCandidateAdapterError as exc:
                raise ChainExecutionError(
                    f"Stage {stage.stage_id}の保存結果をbindingへ戻せません: {exc}"
                ) from exc
        predictions = payload.get("predictions")
        if not isinstance(predictions, dict):
            raise ChainExecutionError(
                f"Stage {stage.stage_id}の保存結果をbindingへ戻せません"

            )
        return {
            key: value["value"]
            for key, value in predictions.items()
            if isinstance(value, dict) and "value" in value
        }

    def _memo_key(
        self,
        stage: ChainStageRevision, input_digest: str
    ) -> str:
        return semantic_digest(
            {
                "stage_id": stage.stage_id,
                "contract_digest": stage.contract_digest,
                "package_manifest_digest": stage.package_manifest_digest,
                "canonical_input_digest": input_digest,
            }
        )

    def _retained(
        self,
        previous: ChainStageExecution | None,
        *,
        stage: ChainStageRevision,
        status: str,
        requested_input_digest: str,
        canonical_input: dict[str, Any],
        error: str | None = None,
        started_at: datetime | None = None,
    ) -> ChainStageExecution:
        return ChainStageExecution(
            stage_id=stage.stage_id,
            output_definitions=self._output_definitions(stage),
            status=status,  # type: ignore[arg-type]
            requested_input_digest=requested_input_digest,
            result_input_digest=(
                previous.result_input_digest if previous is not None else None
            ),
            contract_digest=stage.contract_digest,
            package_manifest_digest=stage.package_manifest_digest,
            canonical_input=canonical_input,
            result=previous.result if previous is not None else None,
            cache_hit=False,
            error=error,
            started_at=started_at,
            completed_at=_now() if status == "failed" else None,
        )

    def _pending_digest(
        self,
        stage_id: str,
        candidate_revision: int,
        blocked_by_stage: str,
    ) -> str:
        return semantic_digest(
            {
                "state": "pending_upstream_result",
                "stage_id": stage_id,
                "candidate_revision": candidate_revision,
                "blocked_by_stage": blocked_by_stage,
            }
        )
