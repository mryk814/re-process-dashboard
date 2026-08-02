from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient

from decision_workbench.application.workspace_bundle import (
    commit_workspace_restore,
    create_workspace_backup,
    finalize_workspace_restore,
    prepare_workspace_restore,
)
from decision_workbench.contracts.chain_contracts import (
    CandidateGraphInputSource,
    ChainBinding,
    ChainPort,
    ChainStage,
    ChainStageLock,
    DecisionOutput,
    ExternalBindingSource,
    GraphInput,
    PredictionGraphDefinition,
    StageContractSurface,
    build_prediction_graph_revision,
)
from decision_workbench.contracts.prediction_graph_draft_contracts import (
    PredictionGraphDraftContent,
    PredictionGraphDraftDefinition,
)
from decision_workbench.persistence.store import Store
from decision_workbench.tasks.task_registry import TaskRegistry


def _incomplete_content(
    *,
    label: str = "途中のGraph",
) -> PredictionGraphDraftContent:
    return PredictionGraphDraftContent(
        definition=PredictionGraphDraftDefinition(
            graph_id="draft-graph",
            label=label,
            stages=(
                ChainStage(
                    stage_id="model",
                    stage_kind="task",
                    contract_id="task-no-longer-in-catalog",
                ),
            ),
            # Intentionally incomplete: no inputs, bindings, or decision output.
        ),
        project_name="再開するProject",
    )


def _request_content(*, label: str = "途中のGraph") -> dict[str, object]:
    return _incomplete_content(label=label).model_dump(mode="json")


def test_draft_api_retains_incomplete_and_unavailable_references(
    client: TestClient,
) -> None:
    missing_response = client.get("/api/prediction-graph-drafts/missing-draft")
    assert missing_response.status_code == 404
    assert missing_response.json() == {
        "code": "not_found",
        "message": "Prediction Graph draftが見つかりません: missing-draft",
        "field_errors": [],
    }

    created_response = client.post(
        "/api/prediction-graph-drafts",
        json={"content": _request_content()},
    )
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    assert created["version"] == 1
    assert (
        created["content"]["definition"]["stages"][0]["contract_id"]
        == "task-no-longer-in-catalog"
    )
    assert created["content"]["definition"]["bindings"] == []

    loaded_response = client.get(
        f"/api/prediction-graph-drafts/{created['draft_id']}"
    )
    assert loaded_response.status_code == 200
    assert loaded_response.json() == created


def test_draft_api_rejects_stale_expected_version_without_overwriting(
    client: TestClient,
) -> None:
    created = client.post(
        "/api/prediction-graph-drafts",
        json={"content": _request_content()},
    ).json()
    draft_url = f"/api/prediction-graph-drafts/{created['draft_id']}"
    saved_response = client.put(
        draft_url,
        json={
            "expected_version": 1,
            "content": _request_content(label="保存済みGraph"),
        },
    )
    assert saved_response.status_code == 200
    saved = saved_response.json()
    assert saved["version"] == 2

    conflict_response = client.put(
        draft_url,
        json={
            "expected_version": 1,
            "content": _request_content(label="古い画面のGraph"),
        },
    )
    assert conflict_response.status_code == 409
    conflict = conflict_response.json()
    assert conflict["code"] == "revision_conflict"
    assert conflict["current"] == saved
    assert client.get(draft_url).json() == saved

    openapi = client.get("/openapi.json").json()
    draft_path = openapi["paths"]["/api/prediction-graph-drafts/{draft_id}"]
    assert (
        draft_path["get"]["responses"]["404"]["content"]["application/json"][
            "schema"
        ]["$ref"]
        == "#/components/schemas/ApiError"
    )
    assert (
        draft_path["put"]["responses"]["409"]["content"]["application/json"][
            "schema"
        ]["$ref"]
        == "#/components/schemas/PredictionGraphDraftConflictResponse"
    )


def _published_graph() -> tuple[
    PredictionGraphDefinition,
    dict[tuple[str, str], StageContractSurface],
    dict[str, ChainStageLock],
]:
    input_port = ChainPort(
        path="graph.x",
        value_kind="number",
        quantity="x",
        unit="unit",
    )
    stage_input = input_port.model_copy(update={"path": "in"})
    output_port = ChainPort(
        path="out",
        value_kind="number",
        quantity="result",
        unit="unit",
    )
    definition = PredictionGraphDefinition(
        graph_id="published-independent",
        label="Published independent",
        stages=(
            ChainStage(
                stage_id="transform",
                stage_kind="deterministic_transform",
                contract_id="transform/v1",
            ),
        ),
        inputs=(
            GraphInput(
                input_id="graph.x",
                label="X",
                port=input_port,
                role="design_variable",
                value_source=CandidateGraphInputSource(
                    source_kind="candidate",
                    candidate_path="process.x",
                ),
                default_presentation_group="design",
            ),
        ),
        bindings=(
            ChainBinding(
                target_stage_id="transform",
                target_input_path="in",
                source=ExternalBindingSource(
                    source_kind="external",
                    path="graph.x",
                ),
            ),
        ),
        decision_outputs=(
            DecisionOutput(
                output_id="result",
                source_stage_id="transform",
                source_output_key="out",
                label="Result",
                group="decision",
                role="primary_objective",
                required_for_complete_result=True,
            ),
        ),
    )
    surface = StageContractSurface(
        stage_kind="deterministic_transform",
        contract_id="transform/v1",
        contract_digest="sha256:" + "a" * 64,
        input_ports=(stage_input,),
        output_ports=(output_port,),
    )
    lock = ChainStageLock(
        contract_digest=surface.contract_digest,
        package_manifest_digest="sha256:" + "b" * 64,
    )
    return (
        definition,
        {("deterministic_transform", "transform/v1"): surface},
        {"transform": lock},
    )


def test_publishing_an_immutable_revision_does_not_mutate_the_draft(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "workbench.db")
    now = datetime(2026, 8, 2, tzinfo=UTC)
    draft = store.create_prediction_graph_draft(
        draft_id="mutable-draft",
        content=_incomplete_content(),
        now=now,
    )
    definition, contracts, locks = _published_graph()

    store.publish_prediction_graph(
        definition,
        contracts=contracts,
        revision_factory=lambda revision: build_prediction_graph_revision(
            definition,
            revision=revision,
            contracts=contracts,
            stage_locks=locks,
        ),
    )

    assert store.get_prediction_graph_draft("mutable-draft") == draft
    assert len(store.list_chain_revisions()) == 1


def test_workspace_backup_and_restore_preserve_graph_draft_exactly(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_database = source_root / "workbench.db"
    source_library = source_root / "data-library"
    source_library.mkdir(parents=True)
    source_store = Store(source_database)
    original = source_store.create_prediction_graph_draft(
        draft_id="resume-next-day",
        content=_incomplete_content(),
        now=datetime(2026, 8, 2, 9, 30, tzinfo=UTC),
    )
    bundle = tmp_path / "workspace.mdwb"
    create_workspace_backup(
        database=source_database,
        data_library_root=source_library,
        destination=bundle,
        app_version="test",
    )

    target_root = tmp_path / "target"
    target_database = target_root / "workbench.db"
    target_library = target_root / "data-library"
    prepared = prepare_workspace_restore(
        database=target_database,
        data_library_root=target_library,
        source=bundle,
        # No Project or published Revision exists, so no runtime ref is resolved.
        task_registry=cast(TaskRegistry, object()),
    )
    commit_workspace_restore(
        database=target_database,
        data_library_root=target_library,
        restore_token=prepared.restore_token,
    )
    finalize_workspace_restore(
        database=target_database,
        restore_token=prepared.restore_token,
    )

    assert (
        Store(target_database).get_prediction_graph_draft("resume-next-day")
        == original
    )
