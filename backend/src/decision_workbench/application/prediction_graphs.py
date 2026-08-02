"""Application facade for the dedicated Prediction Graph runtime."""
from __future__ import annotations

import re
import uuid
from typing import Any

from pydantic import ValidationError

from decision_workbench.application.chain.graph_execution import (
    PredictionGraphExecutionUseCase,
)
from decision_workbench.application.chain.graph_plan import (
    PredictionGraphPlanningUseCase,
)
from decision_workbench.application.chain.graph_snapshot import (
    PredictionGraphSnapshotUseCase,
)
from decision_workbench.application.chain.plan import ChainExecutionError
from decision_workbench.application.chains import (
    ChainCandidateRevisionError,
    ChainConflictError,
    ChainNotFoundError,
    ChainValidationError,
    resolve_task_stage_lock,
    resolve_task_stage_surface,
    scalar_chain_catalog,
)
from decision_workbench.application.chain_candidate_adapters import (
    ChainCandidateAdapterError,
    candidate_adapter_shape_for,
    candidate_path_for_revision,
)
from decision_workbench.contracts.candidate_project_contracts import (
    Candidate,
    CandidateInput,
    CandidateUpdate,
    Project,
    ProjectCreateInput,
)
from decision_workbench.contracts.chain_api_contracts import (
    ChainExecutionRequest,
    PredictionGraphCatalogResponse,
    PredictionGraphDraftValidation,
    PredictionGraphDraftValidationRequest,
    PredictionGraphPublishRequest,
    PredictionGraphPublishResponse,
    PredictionGraphProjectCreateRequest,
    PredictionGraphStageCatalogItem,
    PredictionGraphValidationFinding,
    PredictionGraphValidationTarget,
)
from decision_workbench.contracts.chain_contracts import (
    ChainStageLock,
    PredictionGraphDefinition,
    PredictionGraphProjectBinding,
    PredictionGraphProjectIdentity,
    PredictionGraphRevision,
    StageContractSurface,
    build_prediction_graph_revision,
)
from decision_workbench.contracts.chain_execution_contracts import (
    PredictionGraphExecution,
    PredictionGraphSnapshot,
)
from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.modeling.transform_catalog import (
    DeterministicTransformCatalog,
)
from decision_workbench.persistence.store import (
    CandidateRevisionConflictError,
    ChainCatalogConflictError,
    Store,
)
from decision_workbench.persistence.workspace_catalog import WorkspaceCatalog
from decision_workbench.tasks.task_registry import TaskRegistry, TaskRegistryError


def _finding_code(message: str) -> str:
    lowered = message.lower()
    if "unbound required inputs" in lowered:
        return "unbound_required_input"
    if "mismatch" in lowered or "conversion does not match" in lowered:
        return "port_mismatch"
    if "unknown" in lowered or "見つかりません" in message:
        return "unknown_stage_contract"
    if "利用できません" in message or "利用不能" in message:
        return "unavailable_stage_contract"
    if "candidate adapter" in lowered or "候補入力" in message:
        return "unsupported_candidate_adapter"
    if "acyclic" in lowered or "cycle" in lowered:
        return "cycle"
    if "decision output" in lowered or "terminal" in lowered:
        return "terminal_output_missing"
    if "fixed parameter" in lowered or "fixed value" in lowered:
        return "fixed_parameter_missing"
    return "invalid_graph"


def _suggested_action(code: str) -> str:
    return {
        "unbound_required_input": "必須portへ互換sourceを接続してください。",
        "port_mismatch": "型・quantity・unit・basisが一致するsourceを選んでください。",
        "unknown_stage_contract": "catalogにあるTaskまたはTransformへ置き換えてください。",
        "unavailable_stage_contract": "利用不能理由を確認し、利用可能な契約を選んでください。",
        "unsupported_candidate_adapter": "scalarまたは疎配合adapterが扱える入力構成へ変更してください。",
        "cycle": "循環edgeを削除し、依存関係を一方向にしてください。",
        "terminal_output_missing": "利用するstage outputをDecision Outputへ接続してください。",
        "fixed_parameter_missing": "fixed parameterの値またはProject bindingを指定してください。",
        "invalid_graph": "対象のGraph設定を修正してください。",
    }[code]


def _finding_for_message(
    message: str,
    definition: PredictionGraphDefinition,
) -> PredictionGraphValidationFinding:
    code = _finding_code(message)
    target = PredictionGraphValidationTarget(
        target_kind="graph",
        target_id=definition.graph_id,
    )
    unbound = re.search(
        r"stage ([A-Za-z][A-Za-z0-9_-]*) has unbound required inputs: "
        r"\['([^']+)'",
        message,
    )
    binding = re.search(
        r"binding \S+ -> ([A-Za-z][A-Za-z0-9_-]*)\.(\S+) "
        r"(?:type|physical|unit|conversion)",
        message,
    )
    if unbound:
        target = PredictionGraphValidationTarget(
            target_kind="stage",
            target_id=unbound.group(1),
            port_path=unbound.group(2),
        )
    elif binding:
        target = PredictionGraphValidationTarget(
            target_kind="binding",
            target_id=binding.group(1),
            port_path=binding.group(2),
        )
    else:
        referenced = next(
            (
                stage
                for stage in definition.stages
                if stage.contract_id in message or stage.stage_id in message
            ),
            None,
        )
        if referenced is not None:
            target = PredictionGraphValidationTarget(
                target_kind="stage",
                target_id=referenced.stage_id,
            )
    return PredictionGraphValidationFinding(
        code=code,
        message=message,
        target=target,
        suggested_action=_suggested_action(code),
    )


def _pydantic_finding(
    error: dict[str, Any],
    raw_definition: dict[str, Any],
) -> PredictionGraphValidationFinding:
    message = str(error["msg"]).removeprefix("Value error, ")
    code = _finding_code(message)
    target_kind = "graph"
    target_id = str(raw_definition.get("graph_id", "draft"))
    port_path = None
    location = tuple(error.get("loc", ()))
    for collection, kind, id_key in (
        ("stages", "stage", "stage_id"),
        ("inputs", "input", "input_id"),
        ("bindings", "binding", "target_stage_id"),
        ("decision_outputs", "decision_output", "output_id"),
    ):
        if collection not in location:
            continue
        index_position = location.index(collection) + 1
        if index_position >= len(location) or not isinstance(
            location[index_position], int
        ):
            continue
        items = raw_definition.get(collection)
        index = location[index_position]
        if isinstance(items, list) and index < len(items):
            item = items[index]
            if isinstance(item, dict):
                target_kind = kind
                target_id = str(item.get(id_key, f"{collection}.{index}"))
                port_path = (
                    str(item["target_input_path"])
                    if collection == "bindings"
                    and "target_input_path" in item
                    else None
                )
        break
    return PredictionGraphValidationFinding(
        code=code,
        message=message,
        target=PredictionGraphValidationTarget(
            target_kind=target_kind,
            target_id=target_id,
            port_path=port_path,
        ),
        suggested_action=_suggested_action(code),
    )


class PredictionGraphUseCases:
    """Real application entry points; legacy Chain v1 remains a separate facade."""

    def __init__(
        self,
        *,
        store: Store,
        planning: PredictionGraphPlanningUseCase,
        execution: PredictionGraphExecutionUseCase,
        snapshots: PredictionGraphSnapshotUseCase,
        workspace_catalog: WorkspaceCatalog,
        task_registry: TaskRegistry,
        transform_catalog: DeterministicTransformCatalog | None,
    ) -> None:
        self.store = store
        self.planning = planning
        self.execution = execution
        self.snapshots = snapshots
        self.workspace_catalog = workspace_catalog
        self.task_registry = task_registry
        self.transform_catalog = transform_catalog

    def catalog(self) -> PredictionGraphCatalogResponse:
        task_items = scalar_chain_catalog(
            self.task_registry,
            self.workspace_catalog,
        ).stages
        stages = [
            PredictionGraphStageCatalogItem(
                stage_kind="task",
                contract_id=item.contract_id,
                label=item.label,
                status=item.status,
                reason=item.reason,
                surface=item.surface,
                stage_lock=item.stage_lock,
            )
            for item in task_items
        ]
        if self.transform_catalog is not None:
            for transform_id in self.transform_catalog.transform_ids:
                try:
                    surface, lock = self.transform_catalog.authoring_contract(
                        transform_id
                    )
                    stages.append(PredictionGraphStageCatalogItem(
                        stage_kind="deterministic_transform",
                        contract_id=transform_id,
                        label=transform_id,
                        status="available",
                        surface=surface,
                        stage_lock=lock,
                    ))
                except (KeyError, ValueError) as exc:
                    stages.append(PredictionGraphStageCatalogItem(
                        stage_kind="deterministic_transform",
                        contract_id=transform_id,
                        label=transform_id,
                        status="unavailable",
                        reason=str(exc),
                    ))
        adapter_ids = ["scalar/v1"]
        if self.transform_catalog is not None:
            adapter_ids.append("sparse_blend/v1")
        return PredictionGraphCatalogResponse(
            candidate_adapter_ids=tuple(adapter_ids),
            stages=tuple(stages),
        )

    def validate(
        self,
        payload: PredictionGraphDraftValidationRequest,
    ) -> PredictionGraphDraftValidation:
        definition_digest = semantic_digest(payload.definition)
        try:
            definition = PredictionGraphDefinition.model_validate(
                payload.definition
            )
        except ValidationError as exc:
            return PredictionGraphDraftValidation(
                valid=False,
                definition_digest=definition_digest,
                findings=tuple(
                    _pydantic_finding(error, payload.definition)
                    for error in exc.errors(include_url=False)
                ),
            )
        try:
            _, _, revision = self._prepare_authoring(definition)
        except (
            ChainCandidateAdapterError,
            ChainValidationError,
            TaskRegistryError,
            KeyError,
            ValueError,
        ) as exc:
            return PredictionGraphDraftValidation(
                valid=False,
                definition_digest=definition_digest,
                findings=(_finding_for_message(str(exc), definition),),
            )
        return PredictionGraphDraftValidation(
            valid=True,
            definition_digest=definition_digest,
            candidate_adapter_id=candidate_adapter_shape_for(
                revision
            ).adapter_id,
        )

    def _prepare_authoring(
        self,
        definition: PredictionGraphDefinition,
        *,
        revision_number: int = 1,
    ) -> tuple[
        dict[tuple[str, str], StageContractSurface],
        dict[str, ChainStageLock],
        PredictionGraphRevision,
    ]:
        contracts: dict[tuple[str, str], StageContractSurface] = {}
        locks: dict[str, ChainStageLock] = {}
        for stage in definition.stages:
            if stage.stage_kind == "task":
                self.task_registry.require_available(stage.contract_id)
                surface = resolve_task_stage_surface(
                    self.task_registry,
                    stage.contract_id,
                )
                lock = resolve_task_stage_lock(
                    self.workspace_catalog,
                    self.task_registry,
                    surface,
                )
            else:
                if self.transform_catalog is None:
                    raise ChainValidationError(
                        "deterministic Transform catalogを利用できません: "
                        f"{stage.contract_id}"
                    )
                surface, lock = self.transform_catalog.authoring_contract(
                    stage.contract_id
                )
            contracts[(stage.stage_kind, stage.contract_id)] = surface
            locks[stage.stage_id] = lock
        revision = build_prediction_graph_revision(
            definition,
            revision=revision_number,
            contracts=contracts,
            stage_locks=locks,
        )
        candidate_adapter_shape_for(revision)
        for graph_input in definition.inputs:
            source = graph_input.value_source
            if source.source_kind != "candidate":
                continue
            resolved = candidate_path_for_revision(
                revision,
                f"candidate.{source.candidate_path}",
                graph_input.port.value_kind,
                graph_input.port.quantity,
            )
            if resolved != source.candidate_path:
                raise ChainValidationError(
                    "Prediction Graph candidate sourceをcanonical pathへ"
                    f"解決できません: {source.candidate_path}"
                )
        return contracts, locks, revision

    def publish(
        self,
        payload: PredictionGraphPublishRequest,
    ) -> PredictionGraphPublishResponse:
        definition = payload.definition
        try:
            contracts, locks, _ = self._prepare_authoring(definition)

            def build_revision(revision_number: int):
                return build_prediction_graph_revision(
                    definition,
                    revision=revision_number,
                    contracts=contracts,
                    stage_locks=locks,
                )

            _, revision = self.store.publish_prediction_graph(
                definition,
                contracts=contracts,
                revision_factory=build_revision,
            )
        except ChainValidationError:
            raise
        except (
            ChainCandidateAdapterError,
            ChainCatalogConflictError,
            TaskRegistryError,
            KeyError,
            ValueError,
        ) as exc:
            raise ChainValidationError(str(exc)) from exc
        return PredictionGraphPublishResponse(
            definition=definition,
            graph_revision_id=(
                f"{revision.graph_id}:r{revision.revision}"
            ),
            revision=revision,
        )

    def create_project(
        self,
        payload: PredictionGraphProjectCreateRequest,
    ) -> Project:
        if (
            payload.project.scientific_identity is not None
            or payload.project.initial_candidate is not None
        ):
            raise ChainValidationError(
                "Prediction Graph Projectのidentityと候補は専用項目/APIで指定してください"
            )
        binding_payload = {
            "schema_version": "prediction-graph-project-binding/v1",
            "revision": payload.project_binding_revision,
            "values": payload.project_binding_values,
        }
        identity = PredictionGraphProjectIdentity(
            identity_kind="prediction_graph",
            graph_revision_id=payload.graph_revision_id,
            graph_revision_digest=payload.graph_revision_digest,
            project_binding=PredictionGraphProjectBinding(
                **binding_payload,
                digest=semantic_digest(binding_payload),
            ),
        )
        project_payload = ProjectCreateInput.model_validate(
            {
                **payload.project.model_dump(
                    exclude={"scientific_identity", "initial_candidate"}
                ),
                "task_id": "",
                "scientific_identity": identity,
            }
        )
        try:
            return self.store.create_prediction_graph_project(
                project_payload,
                identity,
            )
        except ChainCatalogConflictError as exc:
            raise ChainConflictError(str(exc)) from exc

    def create_candidate(
        self,
        project_id: str,
        payload: CandidateInput,
    ) -> Candidate:
        try:
            prepared = self.planning.prepare_candidate(project_id, payload)
        except ChainExecutionError as exc:
            raise ChainValidationError(str(exc)) from exc
        return self.store.create_candidate(prepared, project_id)

    def update_candidate(
        self,
        project_id: str,
        candidate_id: str,
        payload: CandidateUpdate,
    ) -> Candidate:
        try:
            prepared = self.planning.prepare_candidate(
                project_id,
                CandidateInput.model_validate(
                    payload.model_dump(exclude={"expected_revision"})
                ),
            )
            request_id = f"candidate-revision:{uuid.uuid4()}"
            updated, generation = self.store.update_chain_candidate(
                candidate_id,
                project_id,
                prepared,
                payload.expected_revision,
                request_id,
            )
        except ChainExecutionError as exc:
            raise ChainValidationError(str(exc)) from exc
        except CandidateRevisionConflictError as exc:
            raise ChainCandidateRevisionError(str(exc), exc.current) from exc
        if updated is None:
            raise ChainNotFoundError("Prediction Graph candidateが見つかりません")
        self.execution.coordinator.begin(
            self.store.chain_execution_scope(project_id, candidate_id),
            request_id,
        )
        self.execution.mark_candidate_changed(
            project_id=project_id,
            candidate_id=candidate_id,
            candidate_revision=updated.revision,
            request_id=request_id,
            generation=generation,
        )
        return updated

    def execute(
        self,
        project_id: str,
        candidate_id: str,
        payload: ChainExecutionRequest,
    ) -> PredictionGraphExecution:
        try:
            return self.execution.execute(
                project_id=project_id,
                candidate_id=candidate_id,
                candidate_revision=payload.candidate_revision,
                request_id=payload.request_id,
                debounce_ms=payload.debounce_ms,
            )
        except ChainExecutionError as exc:
            raise ChainConflictError(str(exc)) from exc

    def latest_execution(
        self,
        project_id: str,
        candidate_id: str,
    ) -> PredictionGraphExecution:
        result = self.store.get_prediction_graph_execution(
            project_id,
            candidate_id,
        )
        if result is None:
            raise ChainNotFoundError("Prediction Graph executionが見つかりません")
        return result

    def create_snapshot(
        self,
        project_id: str,
        candidate_id: str,
        payload: ChainExecutionRequest,
    ) -> PredictionGraphSnapshot:
        try:
            return self.snapshots.snapshot(
                project_id=project_id,
                candidate_id=candidate_id,
                candidate_revision=payload.candidate_revision,
            )
        except ChainExecutionError as exc:
            raise ChainConflictError(str(exc)) from exc

    def list_snapshots(
        self,
        project_id: str,
        candidate_id: str,
    ) -> list[PredictionGraphSnapshot]:
        return self.store.list_prediction_graph_snapshots(
            project_id,
            candidate_id,
        )

    def snapshot(
        self,
        project_id: str,
        snapshot_id: str,
    ) -> PredictionGraphSnapshot:
        result = self.store.get_prediction_graph_snapshot(
            snapshot_id,
            project_id=project_id,
        )
        if result is None:
            raise ChainNotFoundError("Prediction Graph snapshotが見つかりません")
        return result
