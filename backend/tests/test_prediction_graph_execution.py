from __future__ import annotations

from pathlib import Path
import inspect
from types import SimpleNamespace
from typing import Any

import pytest

from decision_workbench.application.chain.execution import (
    ChainExecutionCoordinator,
)
from decision_workbench.application.chain.graph_execution import (
    PredictionGraphExecutionUseCase,
)
from decision_workbench.application.chain.graph_snapshot import (
    PredictionGraphSnapshotUseCase,
)
from decision_workbench.application.chain.graph_plan import (
    PredictionGraphPlanningUseCase,
)
from decision_workbench.application.chain.plan import ChainExecutionError, set_path
from decision_workbench.contracts.candidate_project_contracts import (
    CandidateInput,
    CandidateInputs,
    ProjectCreateInput,
)
from decision_workbench.contracts.chain_contracts import (
    CandidateGraphInputSource,
    ChainBinding,
    ChainPort,
    ChainProjectIdentity,
    ChainStage,
    ChainStageLock,
    DecisionOutput,
    ExternalBindingSource,
    GraphInput,
    PredictionGraphDefinition,
    PredictionGraphProjectBinding,
    PredictionGraphProjectIdentity,
    ProjectGraphInputSource,
    StageContractSurface,
    StageOutputBindingSource,
    build_prediction_graph_revision,
)
from decision_workbench.contracts.chain_execution_contracts import (
    ChainExecution,
    PredictionGraphExecution,
    PredictionGraphSnapshot,
    parse_execution_json,
    parse_snapshot_json,
)
from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.persistence.store import ChainCatalogConflictError, Store


def _port(path: str, quantity: str) -> ChainPort:
    return ChainPort(
        path=path,
        value_kind="number",
        quantity=quantity,
        unit="unit",
    )


def _graph_contracts() -> dict[tuple[str, str], StageContractSurface]:
    surfaces = (
        StageContractSurface(
            stage_kind="deterministic_transform",
            contract_id="runtime-a",
            contract_digest="sha256:" + "a" * 64,
            input_ports=(_port("in", "x"),),
            output_ports=(_port("out", "a"),),
        ),
        StageContractSurface(
            stage_kind="deterministic_transform",
            contract_id="runtime-b",
            contract_digest="sha256:" + "b" * 64,
            input_ports=(_port("in", "y"),),
            output_ports=(_port("out", "b"),),
        ),
        StageContractSurface(
            stage_kind="deterministic_transform",
            contract_id="runtime-c",
            contract_digest="sha256:" + "c" * 64,
            input_ports=(_port("in", "a"),),
            output_ports=(_port("result", "result"),),
        ),
    )
    return {(item.stage_kind, item.contract_id): item for item in surfaces}


def _graph() -> PredictionGraphDefinition:
    return PredictionGraphDefinition(
        graph_id="dependency-runtime",
        label="Dependency runtime",
        # Deliberately not topological: tuple order is not dependency authority.
        stages=tuple(
            ChainStage(
                stage_id=stage_id,
                stage_kind="deterministic_transform",
                contract_id=f"runtime-{stage_id.lower()}",
            )
            for stage_id in ("C", "B", "A")
        ),
        inputs=(
            GraphInput(
                input_id="graph.x",
                label="X",
                port=_port("graph.x", "x"),
                role="design_variable",
                value_source=CandidateGraphInputSource(
                    source_kind="candidate",
                    candidate_path="process.x",
                ),
                default_presentation_group="design",
            ),
            GraphInput(
                input_id="graph.y",
                label="Y",
                port=_port("graph.y", "y"),
                role="scenario_context",
                value_source=CandidateGraphInputSource(
                    source_kind="candidate",
                    candidate_path="process.y",
                ),
                default_presentation_group="scenario",
            ),
        ),
        bindings=(
            ChainBinding(
                target_stage_id="C",
                target_input_path="in",
                source=StageOutputBindingSource(
                    source_kind="stage_output",
                    stage_id="A",
                    output_key="out",
                ),
            ),
            ChainBinding(
                target_stage_id="B",
                target_input_path="in",
                source=ExternalBindingSource(
                    source_kind="external",
                    path="graph.y",
                ),
            ),
            ChainBinding(
                target_stage_id="A",
                target_input_path="in",
                source=ExternalBindingSource(
                    source_kind="external",
                    path="graph.x",
                ),
            ),
        ),
        decision_outputs=(
            DecisionOutput(
                output_id="required-result",
                source_stage_id="C",
                source_output_key="result",
                label="Required result",
                group="decision",
                role="primary_objective",
                required_for_complete_result=True,
            ),
            DecisionOutput(
                output_id="branch-diagnostic",
                source_stage_id="B",
                source_output_key="out",
                label="Branch diagnostic",
                group="diagnostic",
                role="diagnostic",
                required_for_complete_result=False,
            ),
        ),
    )


def _revision():
    contracts = _graph_contracts()
    return build_prediction_graph_revision(
        _graph(),
        revision=1,
        contracts=contracts,
        stage_locks={
            stage_id: ChainStageLock(
                contract_digest=contracts[
                    (
                        "deterministic_transform",
                        f"runtime-{stage_id.lower()}",
                    )
                ].contract_digest,
                package_manifest_digest=(
                    "sha256:" + stage_id.lower() * 64
                ),
            )
            for stage_id in ("A", "B", "C")
        },
    )


def _project_identity(
    revision_id: str,
    revision_digest: str,
    values: dict[str, float | str] | None = None,
) -> PredictionGraphProjectIdentity:
    binding_payload = {
        "schema_version": "prediction-graph-project-binding/v1",
        "revision": 1,
        "values": values or {},
    }
    return PredictionGraphProjectIdentity(
        identity_kind="prediction_graph",
        graph_revision_id=revision_id,
        graph_revision_digest=revision_digest,
        project_binding=PredictionGraphProjectBinding(
            **binding_payload,
            digest=semantic_digest(binding_payload),
        ),
    )


class _Adapter:
    adapter_id = "graph-test/v1"

    @staticmethod
    def snapshot_domain_references(_candidate):
        return ()


class _Planning:
    def __init__(self, store: Store, *, x: float = 2, y: float = 3) -> None:
        self.store = store
        self.definition = _graph()
        self.revision = _revision()
        self.x = x
        self.y = y
        self.adapter = _Adapter()

    def resolve(
        self,
        project_id: str,
        candidate_id: str,
        candidate_revision: int,
    ):
        project = self.store.get_project(project_id)
        assert project is not None
        identity = project.scientific_identity
        assert isinstance(identity, PredictionGraphProjectIdentity)
        candidate = self.store.get_candidate_revision(
            candidate_id,
            candidate_revision,
            project_id,
        )
        assert candidate is not None
        return (
            candidate,
            self.definition,
            self.revision,
            identity,
            self.adapter,
            {
                "graph.x": candidate.inputs.process["x"],
                "graph.y": candidate.inputs.process["y"],
            },
        )


class _StageExecutor:
    def __init__(self) -> None:
        self.failures: set[str] = set()
        self.calls: list[str] = []

    @staticmethod
    def _canonical_input(definition, stage_id, external, upstream_outputs):
        result: dict[str, Any] = {}
        for binding in definition.bindings:
            if binding.target_stage_id != stage_id:
                continue
            if binding.source.source_kind == "external":
                value = external[binding.source.path]
            else:
                value = upstream_outputs[binding.source.stage_id][
                    binding.source.output_key
                ]
            set_path(result, binding.target_input_path, value)
        return result

    @staticmethod
    def _memo_key(stage, input_digest):
        return semantic_digest(
            {
                "stage": stage.stage_id,
                "contract": stage.contract_digest,
                "package": stage.package_manifest_digest,
                "input": input_digest,
            }
        )

    @staticmethod
    def _assert_runtime_identity(stage, candidate, adapter):
        return None

    def _run_stage(self, stage, canonical_input, candidate, adapter):
        self.calls.append(stage.stage_id)
        if stage.stage_id in self.failures:
            raise ChainExecutionError(f"{stage.stage_id} direct failure")
        value = canonical_input["in"]
        outputs = (
            {"out": value * 2}
            if stage.stage_id == "A"
            else {"out": value * 3}
            if stage.stage_id == "B"
            else {"result": value + 1}
        )
        return {"outputs": outputs}, outputs

    @staticmethod
    def _outputs_from_payload(stage, payload, adapter):
        return dict(payload["outputs"])

    @staticmethod
    def _output_definitions(stage):
        return ()


def _workspace(tmp_path: Path):
    store = Store(tmp_path / "graph-runtime.db")
    graph = _graph()
    revision = _revision()
    store.register_chain_definition(graph)
    revision_id = store.register_chain_revision(
        revision,
        contracts=_graph_contracts(),
    )
    identity = _project_identity(revision_id, revision.revision_digest)
    project = store.create_prediction_graph_project(
        ProjectCreateInput(
            name="Graph runtime",
            task_id="",
            scientific_identity=identity,
        ),
        identity,
    )
    candidate = store.create_candidate(
        CandidateInput(
            name="Candidate",
            inputs=CandidateInputs(
                composition={},
                process={"x": 2, "y": 3},
            ),
        ),
        project.id,
    )
    planning = _Planning(store)
    executor = _StageExecutor()
    execution = PredictionGraphExecutionUseCase(
        planning,
        executor,
        ChainExecutionCoordinator(),
    )
    snapshots = PredictionGraphSnapshotUseCase(planning)
    return store, project, candidate, executor, execution, snapshots


def test_direct_failure_blocks_only_descendants_and_independent_branch_continues(
    tmp_path: Path,
) -> None:
    _store, project, candidate, executor, execution, _snapshots = _workspace(
        tmp_path
    )
    executor.failures.add("A")

    result = execution.execute(
        project_id=project.id,
        candidate_id=candidate.id,
        candidate_revision=candidate.revision,
        request_id="failure",
        debounce_ms=0,
    )

    assert [stage.stage_id for stage in result.stages] == ["A", "B", "C"]
    assert [stage.status for stage in result.stages] == [
        "failed",
        "latest",
        "blocked_by_upstream",
    ]
    assert result.failed_stage_ids == ("A",)
    assert result.blocked_stage_ids == ("C",)
    assert result.stages[2].blocked_by_stage_ids == ("A",)
    assert result.status == "partial"
    outputs = {item.output_id: item for item in result.terminal_outputs}
    assert outputs["required-result"].status == "blocked_by_upstream"
    assert outputs["branch-diagnostic"].status == "latest"
    assert outputs["branch-diagnostic"].value == 9
    assert executor.calls == ["A", "B"]


def test_candidate_change_marks_only_affected_branch_and_descendants_stale(
    tmp_path: Path,
) -> None:
    store, project, candidate, _executor, execution, snapshots = _workspace(
        tmp_path
    )
    first = execution.execute(
        project_id=project.id,
        candidate_id=candidate.id,
        candidate_revision=candidate.revision,
        request_id="first",
        debounce_ms=0,
    )
    assert first.status == "complete"
    updated, generation = store.update_chain_candidate(
        candidate.id,
        project.id,
        CandidateInput(
            name=candidate.name,
            inputs=CandidateInputs(
                composition={},
                process={"x": 2, "y": 4},
            ),
        ),
        candidate.revision,
        "changed-y",
    )
    assert updated is not None

    stale = execution.mark_candidate_changed(
        project_id=project.id,
        candidate_id=candidate.id,
        candidate_revision=updated.revision,
        request_id="changed-y",
        generation=generation,
    )

    assert stale is not None
    assert stale.status == "complete"
    assert [stage.status for stage in stale.stages] == [
        "latest",
        "stale",
        "latest",
    ]
    assert stale.stages[0].result == first.stages[0].result
    assert stale.stages[2].result == first.stages[2].result
    frozen = snapshots.snapshot(
        project_id=project.id,
        candidate_id=candidate.id,
        candidate_revision=updated.revision,
    )
    terminal = {item.output_id: item for item in frozen.terminal_outputs}
    assert terminal["required-result"].status == "latest"
    assert terminal["branch-diagnostic"].status == "stale"


def test_snapshot_requires_complete_required_outputs_and_round_trips(
    tmp_path: Path,
) -> None:
    store, project, candidate, executor, execution, snapshots = _workspace(
        tmp_path
    )
    complete = execution.execute(
        project_id=project.id,
        candidate_id=candidate.id,
        candidate_revision=candidate.revision,
        request_id="complete",
        debounce_ms=0,
    )
    assert complete.status == "complete"
    snapshot = snapshots.snapshot(
        project_id=project.id,
        candidate_id=candidate.id,
        candidate_revision=candidate.revision,
    )
    assert isinstance(snapshot, PredictionGraphSnapshot)
    assert snapshot.required_output_ids == ("required-result",)
    assert (
        store.get_prediction_graph_snapshot(
            snapshot.snapshot_id,
            project_id=project.id,
        )
        == snapshot
    )
    assert parse_snapshot_json(snapshot.model_dump_json()) == snapshot
    with pytest.raises(
        ValueError,
        match="required output summary is incomplete",
    ):
        PredictionGraphSnapshot.model_validate(
            {
                **snapshot.model_dump(mode="json"),
                "required_output_ids": ["branch-diagnostic"],
            }
        )
    assert (
        store.get_prediction_graph_execution(project.id, candidate.id)
        == complete
    )
    assert parse_execution_json(complete.model_dump_json()) == complete

    executor.failures.add("A")
    partial = execution.execute(
        project_id=project.id,
        candidate_id=candidate.id,
        candidate_revision=candidate.revision,
        request_id="partial",
        debounce_ms=0,
    )
    # Memo intentionally keeps an identical successful input reusable.
    assert partial.status == "complete"
    with store._connect() as conn:  # noqa: SLF001 - force the failure fixture
        conn.execute("DELETE FROM chain_stage_memo")
    partial = execution.execute(
        project_id=project.id,
        candidate_id=candidate.id,
        candidate_revision=candidate.revision,
        request_id="partial-without-memo",
        debounce_ms=0,
    )
    assert partial.status == "partial"
    with pytest.raises(ChainExecutionError, match="required terminal outputs"):
        snapshots.snapshot(
            project_id=project.id,
            candidate_id=candidate.id,
            candidate_revision=candidate.revision,
        )


def test_informational_failure_keeps_complete_required_result_snapshotable(
    tmp_path: Path,
) -> None:
    _store, project, candidate, executor, execution, snapshots = _workspace(
        tmp_path
    )
    executor.failures.add("B")
    result = execution.execute(
        project_id=project.id,
        candidate_id=candidate.id,
        candidate_revision=candidate.revision,
        request_id="optional-failure",
        debounce_ms=0,
    )

    assert result.status == "complete"
    assert [stage.status for stage in result.stages] == [
        "latest",
        "failed",
        "latest",
    ]
    outputs = {item.output_id: item for item in result.terminal_outputs}
    assert outputs["required-result"].status == "latest"
    assert outputs["branch-diagnostic"].status == "failed"
    assert snapshots.snapshot(
        project_id=project.id,
        candidate_id=candidate.id,
        candidate_revision=candidate.revision,
    ).required_output_ids == ("required-result",)


def test_prediction_graph_planner_rejects_legacy_or_missing_revision() -> None:
    identity = ChainProjectIdentity(
        identity_kind="chain",
        chain_revision_id="legacy:r1",
        chain_revision_digest="sha256:" + "a" * 64,
    )
    store = SimpleNamespace(
        get_project=lambda _project_id: SimpleNamespace(
            scientific_identity=identity
        ),
        get_chain_revision=lambda _revision_id: None,
    )
    planning = PredictionGraphPlanningUseCase(store, transform_catalog=None)

    with pytest.raises(ChainExecutionError, match="固定Graph Revision"):
        planning.resolve("project", "candidate", 1)


def test_prediction_graph_project_uses_dedicated_store_creation_entry(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "graph-project-entry.db")
    graph = _graph()
    revision = _revision()
    store.register_chain_definition(graph)
    revision_id = store.register_chain_revision(
        revision,
        contracts=_graph_contracts(),
    )
    identity = _project_identity(revision_id, revision.revision_digest)
    payload = ProjectCreateInput(
        name="Dedicated Graph Project",
        task_id="",
        scientific_identity=identity,
    )

    with pytest.raises(
        ChainCatalogConflictError,
        match="現行Chain Projectとして保存できません",
    ):
        store.create_chain_project(
            payload,
            ChainProjectIdentity(
                identity_kind="chain",
                chain_revision_id=revision_id,
                chain_revision_digest=revision.revision_digest,
            ),
        )
    project = store.create_prediction_graph_project(payload, identity)
    assert project.scientific_identity == identity


def test_prediction_graph_planner_uses_only_persisted_project_binding() -> None:
    definition = _graph()
    inputs = tuple(
        (
            item.model_copy(
                update={
                    "value_source": ProjectGraphInputSource(
                        source_kind="project_binding",
                        binding_key="scenario.y",
                    )
                }
            )
            if item.input_id == "graph.y"
            else item
        )
        for item in definition.inputs
    )
    definition = definition.model_copy(update={"inputs": inputs})
    identity = _project_identity(
        "dependency-runtime:r1",
        "sha256:" + "a" * 64,
        {"scenario.y": 7.0},
    )
    candidate = CandidateInput(
        name="Candidate",
        inputs=CandidateInputs(
            composition={},
            process={"x": 2, "y": 999},
        ),
    )
    adapter = SimpleNamespace(
        external_values=lambda _candidate: {
            "candidate.process.x": 2,
            "candidate.process.y": 999,
        }
    )

    external = PredictionGraphPlanningUseCase._external_values(
        definition,
        identity,
        adapter,
        candidate,
    )

    assert external == {"graph.x": 2, "graph.y": 7.0}
    assert "project_bindings" not in inspect.signature(
        PredictionGraphPlanningUseCase.resolve
    ).parameters
    assert "project_bindings" not in inspect.signature(
        PredictionGraphExecutionUseCase.execute
    ).parameters
    assert "project_bindings" not in inspect.signature(
        PredictionGraphSnapshotUseCase.snapshot
    ).parameters


def test_prediction_graph_api_and_runtime_are_composed(client) -> None:
    assert client.app.state.prediction_graph_use_cases is not None
    task_id = "welding-consumable-stage-b-v1"
    catalog_response = client.get("/api/chains/studio/catalog")
    assert catalog_response.status_code == 200, catalog_response.text
    stage_catalog = next(
        item
        for item in catalog_response.json()["stages"]
        if item["contract_id"] == task_id
    )
    surface = stage_catalog["surface"]
    graph_inputs = []
    bindings = []
    for index, target in enumerate(surface["input_ports"]):
        input_id = f"graph.input.{index}"
        graph_inputs.append(
            {
                "input_id": input_id,
                "label": target["path"],
                "port": {**target, "path": input_id},
                "role": "design_variable",
                "value_source": {
                    "source_kind": "candidate",
                    "candidate_path": target["path"],
                },
                "default_presentation_group": "design",
            }
        )
        bindings.append(
            {
                "target_stage_id": "model",
                "target_input_path": target["path"],
                "source": {
                    "source_kind": "external",
                    "path": input_id,
                },
            }
        )
    decision_outputs = [
        {
            "output_id": f"decision.{index}",
            "source_stage_id": "model",
            "source_output_key": output["path"],
            "label": output["path"],
            "group": "decision",
            "role": (
                "primary_objective"
                if index == 0
                else "secondary_outcome"
            ),
            "required_for_complete_result": True,
        }
        for index, output in enumerate(surface["output_ports"])
    ]
    publish = client.post(
        "/api/prediction-graphs/publish",
        json={
            "definition": {
                "graph_id": "api-published-graph",
                "label": "API-published graph",
                "stages": [
                    {
                        "stage_id": "model",
                        "stage_kind": "task",
                        "contract_id": task_id,
                    }
                ],
                "inputs": graph_inputs,
                "bindings": bindings,
                "decision_outputs": decision_outputs,
            }
        },
    )
    assert publish.status_code == 201, publish.text
    published = publish.json()
    revision = published["revision"]
    response = client.post(
        "/api/prediction-graphs/projects",
        json={
            "project": {"name": "API Graph Project"},
            "graph_revision_id": published["graph_revision_id"],
            "graph_revision_digest": revision["revision_digest"],
            "project_binding_values": {},
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["scientific_identity"]["identity_kind"] == (
        "prediction_graph"
    )
    project_id = response.json()["id"]
    task_catalog = client.get("/api/task-definitions")
    assert task_catalog.status_code == 200, task_catalog.text
    starter = next(
        item["starter_candidate"]
        for item in task_catalog.json()
        if item["definition"]["task_definition"]["id"] == task_id
    )
    starter["blend"] = None
    candidate_response = client.post(
        f"/api/prediction-graphs/projects/{project_id}/candidates",
        json=starter,
    )
    assert candidate_response.status_code == 201, candidate_response.text
    candidate = candidate_response.json()
    execution_response = client.post(
        f"/api/prediction-graphs/projects/{project_id}/candidates/"
        f"{candidate['id']}/executions",
        json={
            "candidate_revision": candidate["revision"],
            "request_id": "api-only-execution",
            "debounce_ms": 0,
        },
    )
    assert execution_response.status_code == 200, execution_response.text
    assert execution_response.json()["status"] == "complete"
    snapshot_response = client.post(
        f"/api/prediction-graphs/projects/{project_id}/candidates/"
        f"{candidate['id']}/snapshots",
        json={
            "candidate_revision": candidate["revision"],
            "request_id": "api-only-snapshot",
            "debounce_ms": 0,
        },
    )
    assert snapshot_response.status_code == 201, snapshot_response.text
    assert snapshot_response.json()["request_id"] == (
        execution_response.json()["request_id"]
    )
    paths = client.app.openapi()["paths"]
    assert "/api/prediction-graphs/publish" in paths
    assert "/api/prediction-graphs/projects" in paths
    assert (
        "/api/prediction-graphs/projects/{project_id}/candidates/"
        "{candidate_id}/executions"
    ) in paths
    assert (
        "/api/prediction-graphs/projects/{project_id}/candidates/"
        "{candidate_id}/snapshots"
    ) in paths


def test_v1_execution_parser_remains_byte_shape_compatible() -> None:
    payload = {
        "schema_version": "chain-execution/v1",
        "request_id": "legacy",
        "project_id": "project",
        "candidate_id": "candidate",
        "candidate_revision": 1,
        "chain_revision_id": "chain:r1",
        "chain_revision_digest": "sha256:" + "a" * 64,
        "status": "stale",
        "stages": [
            {
                "stage_id": "A",
                "status": "stale",
                "requested_input_digest": "sha256:" + "b" * 64,
                "contract_digest": "sha256:" + "c" * 64,
                "package_manifest_digest": "sha256:" + "d" * 64,
                "canonical_input": {},
            }
        ],
        "created_at": "2026-08-02T00:00:00Z",
        "updated_at": "2026-08-02T00:00:00Z",
    }
    parsed = parse_execution_json(ChainExecution.model_validate(payload).model_dump_json())
    assert isinstance(parsed, ChainExecution)
    assert parsed.model_dump(mode="json") == ChainExecution.model_validate(
        payload
    ).model_dump(mode="json")
