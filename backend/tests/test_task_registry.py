from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from material_workbench.task_registry import TaskRegistry, TaskRegistryError


TASK_IDS = ("annealed-properties-v1", "hot-rolled-properties-v1")
SOURCE_ROOT = Path(__file__).parents[1] / "src" / "material_workbench" / "task_definitions"


@pytest.mark.parametrize("task_id", TASK_IDS)
def test_registry_resolves_definition_runtime_and_package_from_one_task_id(client, task_id: str) -> None:
    registry = client.app.state.task_registry
    contract = registry.contract_for(task_id)
    runtime = registry.runtime_for(task_id)
    entry = registry.entry_for(task_id)
    resolved = registry.resolved_definition_for(task_id)
    expected = {output.key for output in contract.task_definition.outputs}

    assert resolved.task_definition.id == task_id
    assert resolved.runtime_capability.task_id == task_id
    assert runtime.task_id == task_id
    assert entry.task_definition is contract.task_definition
    assert entry.predictor_runtime is runtime
    assert entry.model_package is runtime.model_package
    assert entry.feature_pipeline is runtime.model_package.manifest.feature_pipeline
    assert entry.capability is contract.runtime_capability
    assert runtime.output_keys == frozenset(expected)
    assert {predictor.target for predictor in runtime.model_package.manifest.predictors} == expected
    assert {target.target for target in resolved.runtime_capability.targets} == expected


def test_registry_fails_fast_when_manifest_outputs_disagree_with_task_definition(
    client, tmp_path: Path
) -> None:
    contract_root = tmp_path / "task_definitions"
    contract_root.mkdir()
    for source in SOURCE_ROOT.glob("*.json"):
        (contract_root / source.name).write_bytes(source.read_bytes())

    hot_path = contract_root / "hot-rolled-properties-v1.json"
    hot = json.loads(hot_path.read_text(encoding="utf-8"))
    hot["task_definition"]["outputs"].append(
        {"key": "YS", "label": "降伏強さ", "unit": "MPa", "goal_direction": "at_least"}
    )
    ys_capability = copy.deepcopy(hot["runtime_capability"]["targets"][0])
    ys_capability["target"] = "YS"
    hot["runtime_capability"]["targets"].append(ys_capability)
    hot_path.write_text(json.dumps(hot, ensure_ascii=False), encoding="utf-8")

    registry = client.app.state.task_registry
    runtimes = {task_id: registry.runtime_for(task_id) for task_id in TASK_IDS}
    with pytest.raises(TaskRegistryError, match="model package outputs do not match"):
        TaskRegistry(runtimes, contract_root=contract_root)


def test_project_contracts_have_stable_openapi_operations_and_named_errors(client) -> None:
    schema = client.get("/openapi.json").json()
    task_operation = schema["paths"]["/api/projects/{project_id}/task-definition"]["get"]
    preview_operation = schema["paths"]["/api/projects/{project_id}/candidates/{candidate_id}/preview"]["post"]

    assert task_operation["operationId"] == "getProjectTaskDefinition"
    assert preview_operation["operationId"] == "previewProjectCandidate"
    assert preview_operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/PredictionResponse"
    )
    for operation in (task_operation, preview_operation):
        for status in ("404", "409", "422"):
            assert operation["responses"][status]["content"]["application/json"]["schema"]["$ref"].endswith(
                "/ApiError"
            )

    assert {"ResolvedTaskDefinition", "PredictionResponse", "DetailedPredictionResponse", "ApiError"} <= set(
        schema["components"]["schemas"]
    )


def test_project_api_errors_have_machine_readable_code_and_fields(client) -> None:
    missing = client.get("/api/projects/missing/task-definition")
    assert missing.status_code == 404
    assert missing.json() == {
        "code": "not_found",
        "message": "プロジェクトが見つかりません",
        "field_errors": [],
    }

    invalid = client.post("/api/projects", json={"name": ""})
    assert invalid.status_code == 422
    body = invalid.json()
    assert body["code"] == "validation_error"
    assert body["message"] == "入力内容を確認してください"
    assert any(error["path"].endswith("name") for error in body["field_errors"])


@pytest.mark.parametrize("task_id", TASK_IDS)
def test_both_tasks_use_the_same_project_preview_contract(client, task_id: str) -> None:
    if task_id == "annealed-properties-v1":
        project_id = "default"
        candidate_id = client.get("/api/candidates?project_id=default").json()[0]["id"]
    else:
        project = client.post(
            "/api/projects",
            json={"name": "熱延preview契約", "task_id": task_id},
        ).json()
        project_id = project["id"]
        candidate_id = client.get("/api/hot-rolling/candidates").json()[0]["id"]

    response = client.post(f"/api/projects/{project_id}/candidates/{candidate_id}/preview")
    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == task_id
    assert body["candidate_id"] == candidate_id
    assert body["mode"] == "preview"
    assert set(body) == {
        "task_id", "candidate_id", "mode", "predictions", "support", "warnings", "model_meta",
        "canonical_input", "similar", "heat_pattern", "response_curve",
    }
