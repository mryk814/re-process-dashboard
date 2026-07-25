"""Explicit A -> B -> C execution, partial recomputation, and immutable evidence."""
from __future__ import annotations

from datetime import UTC, datetime
import threading
import time
from typing import Any, Mapping
import uuid

from pydantic import BaseModel

from material_workbench.contracts.blend_contracts import (
    CommercialMaterialCatalog,
    ResolvedBlendContracts,
    ScientificHoop,
    ScientificMaterial,
    ScientificMaterialMaster,
    SparseBlend,
    SparseBlendDesignSpace,
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
    ChainExecution,
    ChainSnapshot,
    ChainStageExecution,
)
from material_workbench.contracts.schemas import Candidate, CandidateInput, CandidateInputs
from material_workbench.execution.inference_work_graph import semantic_digest
from material_workbench.modeling.transform_catalog import DeterministicTransformCatalog
from material_workbench.persistence.store import Store
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
        entry = self.transform_catalog.entry(deterministic[0].contract_id)
        artifact = entry.transform.artifact.scientific_master
        scientific = ScientificMaterialMaster(
            schema_version="scientific-material-master/v1",
            resource_id=artifact.resource_id,
            revision=artifact.revision,
            materials=tuple(
                ScientificMaterial(
                    material_id=item.material_id,
                    name=item.material_id,
                    material_type=item.group,
                    group=item.group,
                    d50_um=item.d50_um,
                )
                for item in artifact.materials
            ),
            hoops=tuple(
                ScientificHoop(
                    hoop_id=item.hoop_id,
                    name=item.hoop_id,
                )
                for item in artifact.hoops
            ),
        )
        commercial: CommercialMaterialCatalog = entry.commercial_catalog
        design_space = SparseBlendDesignSpace(
            schema_version="sparse-blend-design-space/v1",
            resource_id="welding-stage-a-design-space",
            revision=1,
            scientific_master=artifact.ref,
            commercial_catalog=commercial.ref,
            allowed_material_ids=tuple(
                material.material_id for material in scientific.materials
            ),
            fixed_hoop_id="HP-01",
            fixed_fill_ratio=19.06,
            balance_material_id="RM-0013",
            total=100.0,
            tolerance=1e-6,
            material_bounds=(),
            group_totals=(),
            group_cardinalities=(),
            selection_count={"minimum": 1, "maximum": 20},
        )
        return ResolvedBlendContracts(scientific, commercial, design_space)

    def prepare_candidate(
        self, project_id: str, payload: CandidateInput
    ) -> CandidateInput:
        if payload.blend is None:
            raise ChainExecutionError("Chain候補には疎な配合明細が必要です")
        contracts = self.candidate_contracts(project_id)
        blend = payload.blend
        expected_scientific = contracts.design_space.scientific_master
        expected_commercial = contracts.design_space.commercial_catalog
        expected_design_space = contracts.design_space.ref
        if blend.scientific_master != expected_scientific:
            raise ChainExecutionError(
                "候補の科学変換master revisionがChainと一致しません"
            )
        if blend.commercial_catalog != expected_commercial:
            raise ChainExecutionError(
                "候補の商用catalog revisionがChainと一致しません"
            )
        if blend.design_space != expected_design_space:
            raise ChainExecutionError(
                "候補のDesign Space revisionがChainと一致しません"
            )
        try:
            validation = validate_sparse_blend(blend, contracts)
        except ValueError as exc:
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

    def _assert_runtime_identity(self, stage: ChainStageRevision) -> None:
        if stage.stage_kind == "deterministic_transform":
            entry = self.transform_catalog.entry(stage.contract_id)
            actual = f"sha256:{entry.package.manifest_sha256}"
        else:
            actual = self.registry.entry_for(stage.contract_id).package_digest
        if actual != stage.package_manifest_digest:
            raise ChainExecutionError(
                f"Stage {stage.stage_id}のPackage digestがChain Revisionと一致しません"
            )

    def _run_stage(
        self,
        stage: ChainStageRevision,
        canonical_input: dict[str, Any],
        candidate: Candidate,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        self._assert_runtime_identity(stage)
        if stage.stage_kind == "deterministic_transform":
            if candidate.blend is None:
                raise ChainExecutionError("Stage Aに配合明細がありません")
            entry = self.transform_catalog.entry(stage.contract_id)
            scientific = entry.transform.transform(candidate.blend)
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
        self.coordinator.begin(scope_id, request_id)
        if debounce_ms:
            time.sleep(debounce_ms / 1000)
        previous_execution = self.store.get_chain_execution(project_id, candidate_id)
        previous_by_stage = {
            item.stage_id: item for item in previous_execution.stages
        } if previous_execution is not None else {}
        created_at = _now()
        if not self.coordinator.is_latest(scope_id, request_id):
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
                                previous_by_stage[pending.stage_id].requested_input_digest
                                if pending.stage_id in previous_by_stage
                                else semantic_digest({"pending_stage": pending.stage_id})
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
                if self.coordinator.is_latest(scope_id, request_id):
                    self.store.save_chain_execution(execution)
                return execution
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
            self.store.save_chain_execution(
                ChainExecution(
                    request_id=request_id,
                    project_id=project_id,
                    candidate_id=candidate.id,
                    candidate_revision=candidate.revision,
                    chain_revision_id=identity.chain_revision_id,
                    chain_revision_digest=identity.chain_revision_digest,
                    status="running",
                    stages=tuple(stages + [
                        self._retained(
                            previous_by_stage.get(pending.stage_id),
                            stage=pending,
                            status="stale",
                            requested_input_digest=(
                                previous_by_stage[pending.stage_id].requested_input_digest
                                if pending.stage_id in previous_by_stage
                                else semantic_digest({"pending_stage": pending.stage_id})
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
            )
            memo_key = self._memo_key(stage, input_digest)
            memo = self.store.get_chain_stage_memo(memo_key)
            try:
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
                                previous_by_stage[pending.stage_id].requested_input_digest
                                if pending.stage_id in previous_by_stage
                                else semantic_digest({"pending_stage": pending.stage_id})
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
                if self.coordinator.is_latest(scope_id, request_id):
                    self.store.save_chain_execution(execution)
                return execution

            if not self.coordinator.is_latest(scope_id, request_id):
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
        self.store.save_chain_execution(execution)
        return execution

    def mark_candidate_changed(
        self, *, project_id: str, candidate_id: str, candidate_revision: int
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
                    requested = semantic_digest(
                        {
                            "candidate_revision": candidate_revision,
                            "blocked_stage": stage.stage_id,
                        }
                    )
                else:
                    canonical_input = previous.canonical_input
                    requested = previous.requested_input_digest
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
            request_id=previous_execution.request_id,
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
        self.store.save_chain_execution(execution)
        return execution

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
        return self.store.insert_chain_snapshot(project_id, snapshot)
