from __future__ import annotations

from pathlib import Path
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
from decision_workbench.persistence.store import Store


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
        *,
        project_bindings=None,
    ):
        project = self.store.get_project(project_id)
        assert project is not None
        identity = project.scientific_identity
        assert isinstance(identity, ChainProjectIdentity)
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
    identity = ChainProjectIdentity(
        identity_kind="chain",
        chain_revision_id=revision_id,
        chain_revision_digest=revision.revision_digest,
    )
    project = store.create_chain_project(
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
    store, project, candidate, _executor, execution, _snapshots = _workspace(
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
    assert [stage.status for stage in stale.stages] == [
        "latest",
        "stale",
        "latest",
    ]
    assert stale.stages[0].result == first.stages[0].result
    assert stale.stages[2].result == first.stages[2].result


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

    with pytest.raises(ChainExecutionError, match="legacy Chain Revision"):
        planning.resolve("project", "candidate", 1)


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
