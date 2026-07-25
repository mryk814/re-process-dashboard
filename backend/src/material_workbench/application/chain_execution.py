"""Explicit A -> B -> C execution, partial recomputation, and immutable evidence."""
from __future__ import annotations

from datetime import UTC, datetime
import threading
import time
from typing import Any, Mapping
import uuid

from pydantic import BaseModel

from material_workbench.contracts.blend_contracts import (
    BlendStructuralError,
    ResolvedBlendContracts,
    SparseBlend,
    validate_sparse_blend,
)
from material_workbench.contracts.chain_contracts import (
    ChainBinding,
    ChainDefinition,
    ChainProjectIdentity,
    ChainRevision,
    ChainSnapshotIdentity,
    ChainStageRevision,
)
from material_workbench.contracts.chain_execution_contracts import (
    ActualConditionedVariant,
    ActualConditionedVariantIdentity,
    ChainExecution,
    ChainSnapshot,
    ChainStageExecution,
    IntermediateActualRecord,
)
from material_workbench.contracts.schemas import Candidate, CandidateInput, CandidateInputs
from material_workbench.execution.inference_work_graph import semantic_digest
from material_workbench.modeling.transform_catalog import DeterministicTransformCatalog
from material_workbench.persistence.store import CandidateRevisionConflictError, Store
from material_workbench.tasks.task_registry import TaskRegistry


class ChainExecutionError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _set_path(target: dict[str, Any], path: str, value: Any) -> None:
    current = target
    parts = path.split(".")
    for part in parts[:-1]:
        child = current.setdefault(part, {})
        if not isinstance(child, dict):
            raise ChainExecutionError(f"binding target path conflicts at {path}")
        current = child
    current[parts[-1]] = value


def _external_values(candidate: Candidate | CandidateInput) -> dict[str, Any]:
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


class ChainExecutionCoordinator:
    """Tracks the newest request per candidate; superseded work is never committed."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: dict[str, str] = {}

    def begin(self, scope_id: str, request_id: str) -> None:
        with self._lock:
            self._latest[scope_id] = request_id

    def is_latest(self, scope_id: str, request_id: str) -> bool:
        with self._lock:
            return self._latest.get(scope_id) == request_id


class ChainExecutionService:
    def __init__(
        self,
        store: Store,
        registry: TaskRegistry,
        transform_catalog: DeterministicTransformCatalog,
        coordinator: ChainExecutionCoordinator,
    ) -> None:
        self.store = store
        self.registry = registry
        self.transform_catalog = transform_catalog
        self.coordinator = coordinator

    def candidate_contracts(
        self, project_id: str
    ) -> ResolvedBlendContracts:
        stage = self._deterministic_stage(project_id)
        transform_id = stage.contract_id
        try:
            blend = self.transform_catalog.initial_blend_for_package(
                transform_id,
                stage.package_manifest_digest,
                stage.contract_digest,
            )
            return self.transform_catalog.resolve_execution(
                transform_id,
                blend,
                stage.package_manifest_digest,
                stage.contract_digest,
            ).contracts
        except (KeyError, BlendStructuralError) as exc:
            raise ChainExecutionError(
                "Chain Revisionに固定されたStage A契約を解決できません"
            ) from exc

    def candidate_transform_id(self, project_id: str) -> str:
        return self._deterministic_stage(project_id).contract_id

    def starter_candidate(self, project_id: str) -> CandidateInput:
        """Build a usable first candidate from the exact pinned Chain contracts."""

        stage_a = self._deterministic_stage(project_id)
        try:
            blend = self.transform_catalog.initial_blend_for_package(
                stage_a.contract_id,
                stage_a.package_manifest_digest,
                stage_a.contract_digest,
            )
        except BlendStructuralError as exc:
            raise ChainExecutionError(
                "Chain Revisionに固定された初期配合を解決できません"
            ) from exc
        project = self.store.get_project(project_id)
        assert project is not None and project.scientific_identity.identity_kind == "chain"
        revision = self.store.get_chain_revision(
            project.scientific_identity.chain_revision_id
        )
        assert revision is not None
        definition = self.store.get_chain_definition(
            revision.chain_id, revision.chain_definition_digest
        )
        assert definition is not None
        stage_contracts = {
            stage.stage_id: self.registry.contract_for(stage.contract_id).task_definition
            for stage in revision.stages
            if stage.stage_kind == "task"
        }
        process: dict[str, float] = {}
        categorical: dict[str, str] = {}
        for port in definition.external_inputs:
            if port.path == "candidate.blend":
                continue
            fields = []
            for binding in definition.bindings:
                if (
                    binding.source.source_kind != "external"
                    or binding.source.path != port.path
                ):
                    continue
                task = stage_contracts.get(binding.target_stage_id)
                if task is None:
                    continue
                field = next(
                    (
                        field
                        for group in task.input_groups
                        for field in group.fields
                        if field.path == binding.target_input_path
                    ),
                    None,
                )
                if field is not None:
                    fields.append(field)
            if not fields:
                raise ChainExecutionError(
                    f"初期候補の入力契約を解決できません: {port.path}"
                )
            key = port.path.rsplit(".", 1)[-1]
            if all(field.kind == "number" for field in fields):
                ranges = [field.default_range for field in fields]
                if any(item is None for item in ranges):
                    raise ChainExecutionError(
                        f"初期候補の数値範囲がありません: {port.path}"
                    )
                lower = max(item.min for item in ranges if item is not None)
                upper = min(item.max for item in ranges if item is not None)
                if lower > upper:
                    raise ChainExecutionError(
                        f"初期候補の数値範囲がStage間で重なりません: {port.path}"
                    )
                process[key] = (lower + upper) / 2
            elif all(field.kind == "categorical" for field in fields):
                allowed = set(fields[0].choices)
                for field in fields[1:]:
                    allowed &= set(field.choices)
                if not allowed:
                    raise ChainExecutionError(
                        f"初期候補の選択肢がStage間で重なりません: {port.path}"
                    )
                categorical[key] = next(
                    choice for choice in fields[0].choices if choice in allowed
                )
            else:
                raise ChainExecutionError(
                    f"初期候補の入力型がStage間で一致しません: {port.path}"
                )
        return self.prepare_candidate(
            project_id,
            CandidateInput(
                name="基準配合",
                inputs=CandidateInputs(
                    composition={},
                    process=process,
                    categorical=categorical,
                    heat_pattern=None,
                    heat_time_basis="line_speed",
                ),
                blend=blend,
            ),
        )

    def _deterministic_stage(self, project_id: str) -> ChainStageRevision:
        project = self.store.get_project(project_id)
        if project is None:
            raise ChainExecutionError("Chain Projectが見つかりません")
        identity = project.scientific_identity
        if identity.identity_kind != "chain":
            raise ChainExecutionError("このAPIはChain Project専用です")
        revision = self.store.get_chain_revision(identity.chain_revision_id)
        if revision is None or revision.revision_digest != identity.chain_revision_digest:
            raise ChainExecutionError("固定されたChain Revisionを解決できません")
        deterministic = [
            stage for stage in revision.stages
            if stage.stage_kind == "deterministic_transform"
        ]
        if len(deterministic) != 1:
            raise ChainExecutionError(
                "v1 Chain candidateは決定論的Stageを1段だけ必要とします"
            )
        return deterministic[0]

    def prepare_candidate(
        self, project_id: str, payload: CandidateInput
    ) -> CandidateInput:
        if payload.blend is None:
            raise ChainExecutionError("Chain候補には疎な配合明細が必要です")
        blend = payload.blend
        stage = self._deterministic_stage(project_id)
        try:
            resolution = self.transform_catalog.resolve_execution(
                stage.contract_id,
                blend,
                stage.package_manifest_digest,
                stage.contract_digest,
            )
            if (
                f"sha256:{resolution.package.manifest_sha256}"
                != stage.package_manifest_digest
                or resolution.contract_digest != stage.contract_digest
            ):
                raise ChainExecutionError(
                    "候補のStage A Package/contract revisionがChain Revisionと一致しません"
                )
            contracts = resolution.contracts
            validation = validate_sparse_blend(blend, contracts)
        except (BlendStructuralError, ValueError) as exc:
            raise ChainExecutionError(str(exc)) from exc
        project = self.store.get_project(project_id)
        assert project is not None and project.scientific_identity.identity_kind == "chain"
        revision = self.store.get_chain_revision(
            project.scientific_identity.chain_revision_id
        )
        assert revision is not None
        definition = self.store.get_chain_definition(
            revision.chain_id, revision.chain_definition_digest
        )
        assert definition is not None
        external = _external_values(payload)
        missing = sorted(
            port.path for port in definition.external_inputs if port.path not in external
        )
        if missing:
            raise ChainExecutionError(
                "Chain候補の外部contextが不足しています: " + ", ".join(missing)
            )
        return payload.model_copy(update={"blend_validation": validation})

    def _resolve(
        self, project_id: str, candidate_id: str, candidate_revision: int
    ) -> tuple[Candidate, ChainDefinition, ChainRevision, ChainProjectIdentity]:
        project = self.store.get_project(project_id)
        if project is None:
            raise ChainExecutionError("Chain Projectが見つかりません")
        identity = project.scientific_identity
        if identity.identity_kind != "chain":
            raise ChainExecutionError("このAPIはChain Project専用です")
        revision = self.store.get_chain_revision(identity.chain_revision_id)
        if revision is None or revision.revision_digest != identity.chain_revision_digest:
            raise ChainExecutionError("固定されたChain Revisionを解決できません")
        definition = self.store.get_chain_definition(
            revision.chain_id, revision.chain_definition_digest
        )
        if definition is None:
            raise ChainExecutionError("固定されたChain Definitionを解決できません")
        candidate = self.store.get_candidate_revision(
            candidate_id, candidate_revision, project_id
        )
        if candidate is None:
            raise ChainExecutionError("指定したcandidate revisionが見つかりません")
        if candidate.blend is None:
            raise ChainExecutionError("Chain候補には疎な配合明細が必要です")
        return candidate, definition, revision, identity

    @staticmethod
    def _canonical_input(
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
            value = ChainExecutionService._binding_value(
                binding, external, upstream_outputs
            )
            if binding.conversion is not None:
                value = value * binding.conversion.factor + binding.conversion.offset
            _set_path(result, binding.target_input_path, value)
        return result

    @staticmethod
    def _binding_value(
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
        self, stage: ChainStageRevision, candidate: Candidate
    ) -> Any | None:
        if stage.stage_kind == "deterministic_transform":
            assert candidate.blend is not None
            resolution = self.transform_catalog.resolve_execution(
                stage.contract_id,
                candidate.blend,
                stage.package_manifest_digest,
                stage.contract_digest,
            )
            actual = f"sha256:{resolution.package.manifest_sha256}"
            if resolution.contract_digest != stage.contract_digest:
                raise ChainExecutionError(
                    f"Stage {stage.stage_id}のcontract digestがChain Revisionと一致しません"
                )
        else:
            resolution = None
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
        return resolution

    def _run_stage(
        self,
        stage: ChainStageRevision,
        canonical_input: dict[str, Any],
        candidate: Candidate,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        resolution = self._assert_runtime_identity(stage, candidate)
        if stage.stage_kind == "deterministic_transform":
            if candidate.blend is None:
                raise ChainExecutionError("Stage Aに配合明細がありません")
            assert resolution is not None
            scientific = resolution.transform.transform(candidate.blend)
            payload = _plain(scientific)
            outputs = {
                **payload["material_composition"],
                **payload["auxiliary_features"],
            }
            return payload, outputs
        stage_candidate = candidate.model_copy(
            deep=True,
            update={
                "inputs": CandidateInputs.model_validate(
                    {
                        **canonical_input,
                        "heat_pattern": None,
                        "heat_time_basis": "line_speed",
                    }
                ),
                "blend": None,
            },
        )
        runtime = self.registry.entry_for(stage.contract_id).predictor_runtime
        payload = _plain(runtime.predict_core(stage_candidate, detailed=False))
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

    @staticmethod
    def _outputs_from_payload(
        stage: ChainStageRevision, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        if stage.stage_kind == "deterministic_transform":
            composition = payload.get("material_composition", {})
            auxiliary = payload.get("auxiliary_features", {})
            if not isinstance(composition, dict) or not isinstance(auxiliary, dict):
                raise ChainExecutionError(
                    f"Stage {stage.stage_id}の保存結果をbindingへ戻せません"
                )
            return {**composition, **auxiliary}
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

    @staticmethod
    def _memo_key(
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

    @staticmethod
    def _retained(
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

    @staticmethod
    def _pending_digest(
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

    def execute(
        self,
        *,
        project_id: str,
        candidate_id: str,
        candidate_revision: int,
        request_id: str | None = None,
        debounce_ms: int = 250,
    ) -> ChainExecution:
        candidate, definition, revision, identity = self._resolve(
            project_id, candidate_id, candidate_revision
        )
        if candidate.blend_validation.status == "invalid":
            reasons = " / ".join(
                issue.message for issue in candidate.blend_validation.issues
            )
            raise ChainExecutionError(
                f"配合がDesign Spaceを満たしていないためChainを実行できません: {reasons}"
            )
        request_id = request_id or str(uuid.uuid4())
        scope_id = self.store.chain_execution_scope(project_id, candidate_id)
        generation = self.store.claim_chain_execution(
            project_id, candidate_id, candidate.revision, request_id
        )
        if generation is None:
            return self._superseded(
                project_id,
                candidate,
                identity,
                revision,
                request_id,
                self.store.get_chain_execution(project_id, candidate_id),
            )
        self.coordinator.begin(scope_id, request_id)
        if debounce_ms:
            time.sleep(debounce_ms / 1000)
        previous_execution = self.store.get_chain_execution(project_id, candidate_id)
        previous_by_stage = {
            item.stage_id: item for item in previous_execution.stages
        } if previous_execution is not None else {}
        created_at = _now()
        if (
            self.store.chain_execution_generation(
                project_id, candidate_id, request_id
            )
            != generation
        ):
            return self._superseded(
                project_id, candidate, identity, revision, request_id, previous_execution
            )

        external = _external_values(candidate)
        upstream_outputs: dict[str, dict[str, Any]] = {}
        stages: list[ChainStageExecution] = []
        for stage in revision.stages:
            try:
                canonical_input = self._canonical_input(
                    definition, stage.stage_id, external, upstream_outputs
                )
            except ChainExecutionError as exc:
                canonical_input = {}
                input_digest = semantic_digest(
                    {
                        "stage_id": stage.stage_id,
                        "binding_error": str(exc),
                        "available_upstream": sorted(upstream_outputs),
                    }
                )
                stages.append(
                    self._retained(
                        previous_by_stage.get(stage.stage_id),
                        stage=stage,
                        status="failed",
                        requested_input_digest=input_digest,
                        canonical_input=canonical_input,
                        error=str(exc),
                        started_at=_now(),
                    )
                )
                execution = ChainExecution(
                    request_id=request_id,
                    project_id=project_id,
                    candidate_id=candidate.id,
                    candidate_revision=candidate.revision,
                    chain_revision_id=identity.chain_revision_id,
                    chain_revision_digest=identity.chain_revision_digest,
                    status="failed",
                    stages=tuple(stages + [
                        self._retained(
                            previous_by_stage.get(pending.stage_id),
                            stage=pending,
                            status="stale",
                            requested_input_digest=(
                                self._pending_digest(
                                    pending.stage_id,
                                    candidate.revision,
                                    stage.stage_id,
                                )
                            ),
                            canonical_input=(
                                previous_by_stage[pending.stage_id].canonical_input
                                if pending.stage_id in previous_by_stage
                                else {}
                            ),
                        )
                        for pending in revision.stages[len(stages):]
                    ]),
                    created_at=created_at,
                    updated_at=_now(),
                )
                if self.store.save_chain_execution_if_current(execution, generation):
                    return execution
                return self._superseded(
                    project_id,
                    candidate,
                    identity,
                    revision,
                    request_id,
                    self.store.get_chain_execution(project_id, candidate_id),
                )
            input_digest = semantic_digest(canonical_input)
            previous = previous_by_stage.get(stage.stage_id)
            running = self._retained(
                previous,
                stage=stage,
                status="running",
                requested_input_digest=input_digest,
                canonical_input=canonical_input,
                started_at=_now(),
            )
            stages.append(running)
            running_execution = ChainExecution(
                request_id=request_id,
                project_id=project_id,
                candidate_id=candidate.id,
                candidate_revision=candidate.revision,
                chain_revision_id=identity.chain_revision_id,
                chain_revision_digest=identity.chain_revision_digest,
                status="running",
                stages=tuple(
                    stages
                    + [
                        self._retained(
                            previous_by_stage.get(pending.stage_id),
                            stage=pending,
                            status="stale",
                            requested_input_digest=(
                                self._pending_digest(
                                    pending.stage_id,
                                    candidate.revision,
                                    stage.stage_id,
                                )
                            ),
                            canonical_input=(
                                previous_by_stage[pending.stage_id].canonical_input
                                if pending.stage_id in previous_by_stage
                                else {}
                            ),
                        )
                        for pending in revision.stages[len(stages):]
                    ]
                ),
                created_at=created_at,
                updated_at=_now(),
            )
            if not self.store.save_chain_execution_if_current(
                running_execution, generation
            ):
                return self._superseded(
                    project_id,
                    candidate,
                    identity,
                    revision,
                    request_id,
                    self.store.get_chain_execution(project_id, candidate_id),
                )
            memo_key = self._memo_key(stage, input_digest)
            memo = self.store.get_chain_stage_memo(memo_key)
            try:
                self._assert_runtime_identity(stage, candidate)
                if memo is None:
                    payload, outputs = self._run_stage(
                        stage, canonical_input, candidate
                    )
                    memo_result = {"payload": payload, "outputs": outputs}
                    self.store.put_chain_stage_memo(
                        memo_key=memo_key,
                        stage_id=stage.stage_id,
                        input_digest=input_digest,
                        contract_digest=stage.contract_digest,
                        package_manifest_digest=stage.package_manifest_digest,
                        canonical_input=canonical_input,
                        result=memo_result,
                    )
                    cache_hit = False
                else:
                    memo_result = memo["result"]
                    payload = memo_result["payload"]
                    outputs = memo_result["outputs"]
                    cache_hit = True
            except Exception as exc:
                failed = self._retained(
                    previous,
                    stage=stage,
                    status="failed",
                    requested_input_digest=input_digest,
                    canonical_input=canonical_input,
                    error=str(exc),
                    started_at=running.started_at,
                )
                stages[-1] = failed
                execution = ChainExecution(
                    request_id=request_id,
                    project_id=project_id,
                    candidate_id=candidate.id,
                    candidate_revision=candidate.revision,
                    chain_revision_id=identity.chain_revision_id,
                    chain_revision_digest=identity.chain_revision_digest,
                    status="failed",
                    stages=tuple(stages + [
                        self._retained(
                            previous_by_stage.get(pending.stage_id),
                            stage=pending,
                            status="stale",
                            requested_input_digest=(
                                self._pending_digest(
                                    pending.stage_id,
                                    candidate.revision,
                                    stage.stage_id,
                                )
                            ),
                            canonical_input=(
                                previous_by_stage[pending.stage_id].canonical_input
                                if pending.stage_id in previous_by_stage
                                else {}
                            ),
                        )
                        for pending in revision.stages[len(stages):]
                    ]),
                    created_at=created_at,
                    updated_at=_now(),
                )
                if self.store.save_chain_execution_if_current(execution, generation):
                    return execution
                return self._superseded(
                    project_id,
                    candidate,
                    identity,
                    revision,
                    request_id,
                    self.store.get_chain_execution(project_id, candidate_id),
                )

            if (
                self.store.chain_execution_generation(
                    project_id, candidate_id, request_id
                )
                != generation
            ):
                return self._superseded(
                    project_id, candidate, identity, revision, request_id,
                    self.store.get_chain_execution(project_id, candidate_id),
                )
            stages[-1] = ChainStageExecution(
                stage_id=stage.stage_id,
                status="latest",
                requested_input_digest=input_digest,
                result_input_digest=input_digest,
                contract_digest=stage.contract_digest,
                package_manifest_digest=stage.package_manifest_digest,
                canonical_input=canonical_input,
                result=payload,
                cache_hit=cache_hit,
                started_at=running.started_at,
                completed_at=_now(),
            )
            upstream_outputs[stage.stage_id] = dict(outputs)

        execution = ChainExecution(
            request_id=request_id,
            project_id=project_id,
            candidate_id=candidate.id,
            candidate_revision=candidate.revision,
            chain_revision_id=identity.chain_revision_id,
            chain_revision_digest=identity.chain_revision_digest,
            status="latest",
            stages=tuple(stages),
            created_at=created_at,
            updated_at=_now(),
        )
        if self.store.save_chain_execution_if_current(execution, generation):
            return execution
        return self._superseded(
            project_id,
            candidate,
            identity,
            revision,
            request_id,
            self.store.get_chain_execution(project_id, candidate_id),
        )

    def mark_candidate_changed(
        self,
        *,
        project_id: str,
        candidate_id: str,
        candidate_revision: int,
        request_id: str,
        generation: int,
    ) -> ChainExecution | None:
        """Reclassify retained results immediately after a candidate revision changes."""

        previous_execution = self.store.get_chain_execution(project_id, candidate_id)
        if previous_execution is None:
            return None
        candidate, definition, revision, identity = self._resolve(
            project_id, candidate_id, candidate_revision
        )
        external = _external_values(candidate)
        previous_by_stage = {
            item.stage_id: item for item in previous_execution.stages
        }
        upstream_outputs: dict[str, dict[str, Any]] = {}
        stages: list[ChainStageExecution] = []
        upstream_stale = False
        for stage in revision.stages:
            previous = previous_by_stage.get(stage.stage_id)
            if upstream_stale:
                if previous is None:
                    canonical_input: dict[str, Any] = {}
                    requested = self._pending_digest(
                        stage.stage_id,
                        candidate_revision,
                        stages[-1].stage_id if stages else "candidate",
                    )
                else:
                    canonical_input = previous.canonical_input
                    requested = self._pending_digest(
                        stage.stage_id,
                        candidate_revision,
                        stages[-1].stage_id if stages else "candidate",
                    )
                stages.append(
                    self._retained(
                        previous,
                        stage=stage,
                        status="stale",
                        requested_input_digest=requested,
                        canonical_input=canonical_input,
                    )
                )
                continue
            canonical_input = self._canonical_input(
                definition, stage.stage_id, external, upstream_outputs
            )
            input_digest = semantic_digest(canonical_input)
            if (
                previous is not None
                and previous.result is not None
                and previous.result_input_digest == input_digest
            ):
                stages.append(
                    ChainStageExecution(
                        stage_id=stage.stage_id,
                        status="latest",
                        requested_input_digest=input_digest,
                        result_input_digest=input_digest,
                        contract_digest=stage.contract_digest,
                        package_manifest_digest=stage.package_manifest_digest,
                        canonical_input=canonical_input,
                        result=previous.result,
                        cache_hit=False,
                        started_at=previous.started_at,
                        completed_at=previous.completed_at,
                    )
                )
                upstream_outputs[stage.stage_id] = self._outputs_from_payload(
                    stage, previous.result
                )
            else:
                stages.append(
                    self._retained(
                        previous,
                        stage=stage,
                        status="stale",
                        requested_input_digest=input_digest,
                        canonical_input=canonical_input,
                    )
                )
                upstream_stale = True
        now = _now()
        execution = ChainExecution(
            request_id=request_id,
            project_id=project_id,
            candidate_id=candidate_id,
            candidate_revision=candidate_revision,
            chain_revision_id=identity.chain_revision_id,
            chain_revision_digest=identity.chain_revision_digest,
            status=(
                "latest"
                if all(stage.status == "latest" for stage in stages)
                else "stale"
            ),
            stages=tuple(stages),
            created_at=previous_execution.created_at,
            updated_at=now,
        )
        return (
            execution
            if self.store.save_chain_execution_if_current(execution, generation)
            else self.store.get_chain_execution(project_id, candidate_id)
        )

    @staticmethod
    def _superseded(
        project_id: str,
        candidate: Candidate,
        identity: ChainProjectIdentity,
        revision: ChainRevision,
        request_id: str,
        previous: ChainExecution | None,
    ) -> ChainExecution:
        if previous is None:
            stages = tuple(
                ChainStageExecution(
                    stage_id=stage.stage_id,
                    status="stale",
                    requested_input_digest=semantic_digest(
                        {"superseded_stage": stage.stage_id}
                    ),
                    contract_digest=stage.contract_digest,
                    package_manifest_digest=stage.package_manifest_digest,
                    canonical_input={},
                )
                for stage in revision.stages
            )
        else:
            stages = previous.stages
        now = _now()
        return ChainExecution(
            request_id=request_id,
            project_id=project_id,
            candidate_id=candidate.id,
            candidate_revision=candidate.revision,
            chain_revision_id=identity.chain_revision_id,
            chain_revision_digest=identity.chain_revision_digest,
            status="superseded",
            stages=stages,
            created_at=now,
            updated_at=now,
        )

    def snapshot(
        self,
        *,
        project_id: str,
        candidate_id: str,
        candidate_revision: int,
    ) -> ChainSnapshot:
        candidate, _definition, _revision, identity = self._resolve(
            project_id, candidate_id, candidate_revision
        )
        execution = self.store.get_chain_execution(project_id, candidate_id)
        if (
            execution is None
            or execution.status != "latest"
            or execution.candidate_revision != candidate_revision
        ):
            raise ChainExecutionError(
                "全Stageが最新のChain結果を先に実行してください"
            )
        blend: SparseBlend = candidate.blend  # type: ignore[assignment]
        snapshot = ChainSnapshot(
            snapshot_id=str(uuid.uuid4()),
            identity=ChainSnapshotIdentity(
                chain_revision_id=identity.chain_revision_id,
                chain_revision_digest=identity.chain_revision_digest,
                design_space=blend.design_space,
                candidate_id=candidate.id,
                candidate_revision=candidate.revision,
                commercial_catalog=blend.commercial_catalog,
            ),
            request_id=execution.request_id,
            external_input=_external_values(candidate),
            stages=execution.stages,
            created_at=_now(),
        )
        try:
            return self.store.insert_chain_snapshot(project_id, snapshot)
        except CandidateRevisionConflictError as exc:
            raise ChainExecutionError(
                "候補は更新済みのため、過去revisionからChain snapshotを作成できません"
            ) from exc

    def actual_conditioned_variant(
        self,
        *,
        project_id: str,
        candidate_id: str,
        candidate_revision: int,
        comparison_snapshot_id: str,
        actual_records: tuple[IntermediateActualRecord, ...],
    ) -> ActualConditionedVariant:
        """Run Stage C with complete measured B outputs without mutating normal Chain state."""

        candidate, definition, revision, identity = self._resolve(
            project_id, candidate_id, candidate_revision
        )
        snapshot = self.store.get_chain_snapshot(comparison_snapshot_id)
        if snapshot is None:
            raise ChainExecutionError("比較元Chain snapshotが見つかりません")
        if (
            snapshot.identity.chain_revision_id != identity.chain_revision_id
            or snapshot.identity.chain_revision_digest != identity.chain_revision_digest
            or snapshot.identity.candidate_id != candidate_id
            or snapshot.identity.candidate_revision != candidate_revision
        ):
            raise ChainExecutionError(
                "比較元snapshotはこのChain candidate revisionの結果ではありません"
            )
        if not actual_records:
            raise ChainExecutionError("Stage B実測を1件以上指定してください")
        actual_ids = [record.actual_id for record in actual_records]
        if len(actual_ids) != len(set(actual_ids)):
            raise ChainExecutionError("実測IDは重複できません")

        stage_c = revision.stages[-1]
        if stage_c.stage_kind != "task":
            raise ChainExecutionError("actual-conditioned variantの終端はTask Stageが必要です")
        stage_c_snapshot = next(
            (stage for stage in snapshot.stages if stage.stage_id == stage_c.stage_id),
            None,
        )
        if (
            stage_c_snapshot is None
            or stage_c_snapshot.package_manifest_digest
            != stage_c.package_manifest_digest
        ):
            raise ChainExecutionError("比較元snapshotのStage C Packageが一致しません")

        required_components = tuple(
            sorted(
                binding.target_input_path.removeprefix("composition.")
                for binding in definition.bindings
                if binding.target_stage_id == stage_c.stage_id
                and binding.target_input_path.startswith("composition.")
                and binding.source.source_kind == "stage_output"
            )
        )
        measured: dict[str, float] = {}
        for record in actual_records:
            for component, value in record.values.items():
                if component in measured:
                    raise ChainExecutionError(
                        f"成分 {component} が複数の実測IDに重複しています"
                    )
                measured[component] = value
        missing = sorted(set(required_components) - set(measured))
        if missing:
            raise ChainExecutionError(
                "Stage Cに必要な実測成分が不足しています（予測値では補完しません）: "
                + ", ".join(missing)
            )
        canonical_input = {
            **stage_c_snapshot.canonical_input,
            "composition": {
                component: measured[component] for component in required_components
            },
        }
        payload, _outputs = self._run_stage(stage_c, canonical_input, candidate)
        measurement_payload = [
            {
                "actual_id": record.actual_id,
                "values": {
                    key: record.values[key] for key in sorted(record.values)
                },
            }
            for record in sorted(actual_records, key=lambda item: item.actual_id)
        ]
        variant = ActualConditionedVariant(
            variant_id=str(uuid.uuid4()),
            project_id=project_id,
            identity=ActualConditionedVariantIdentity(
                base_chain_revision_id=identity.chain_revision_id,
                base_chain_revision_digest=identity.chain_revision_digest,
                base_candidate_id=candidate_id,
                base_candidate_revision=candidate_revision,
                comparison_snapshot_id=comparison_snapshot_id,
                actual_ids=tuple(sorted(actual_ids)),
                measurement_digest=semantic_digest(measurement_payload),
                coverage=required_components,
                stage_c_package_manifest_digest=stage_c.package_manifest_digest,
            ),
            measured_stage_b={
                component: measured[component] for component in required_components
            },
            stage_c_input=canonical_input,
            stage_c_result=payload,
            created_at=_now(),
        )
        return self.store.insert_chain_analysis_variant(variant)
