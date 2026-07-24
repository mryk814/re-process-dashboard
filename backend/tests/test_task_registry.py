from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from material_workbench.contracts.task_contracts import DataExplorerCapability
from material_workbench.modeling.model_lifecycle import load_active_packages, validate_active_package_task_set
from material_workbench.modeling.model_packages import PackageContractError
from material_workbench.contracts.schemas import ProjectInput
from material_workbench.task_modules import DataDescriptor, registered_task_modules
from material_workbench.tasks.task_registry import DataExplorerEntry, TaskRegistry, TaskRegistryError


TASK_IDS = tuple(sorted(registered_task_modules()))
SOURCE_ROOT = Path(__file__).parents[1] / "src" / "material_workbench" / "tasks" / "task_definitions"
ACTIVE_PACKAGES = Path(__file__).parents[2] / "models" / "active-packages.json"


def test_allow_list_contracts_active_packages_and_runtimes_share_one_task_set(client) -> None:
    registered = set(registered_task_modules())
    registry = client.app.state.task_registry
    active = load_active_packages(ACTIVE_PACKAGES)

    assert registered == set(TASK_IDS) == set(registry.task_ids) == set(active.tasks)
    assert ProjectInput(task_id="future-allow-listed-task").task_id == "future-allow-listed-task"
    validate_active_package_task_set(active, registered)
    for task_id in registered:
        module = registered_task_modules()[task_id]
        runtime = registry.runtime_for(task_id)
        assert module.task_id == task_id
        assert callable(module.model_builder)
        assert isinstance(runtime.data, DataDescriptor)


def test_active_package_set_rejects_missing_or_unknown_task() -> None:
    active = load_active_packages(ACTIVE_PACKAGES)
    incomplete = active.model_copy(update={"tasks": {TASK_IDS[0]: active.tasks[TASK_IDS[0]]}})
    with pytest.raises(PackageContractError, match="must exactly match"):
        validate_active_package_task_set(incomplete)


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
    assert entry.application_capability.candidate_excel_import is (task_id == "annealed-properties-v1")
    assert entry.application_capability.candidate_excel_export is (task_id == "annealed-properties-v1")
    assert runtime.output_keys == frozenset(expected)
    assert {predictor.target for predictor in runtime.model_package.manifest.predictors} == expected
    assert {target.target for target in resolved.runtime_capability.targets} == expected
    for operation in (
        "preview", "detailed_prediction", "response_curve", "similarity", "snapshot", "actual_measurement"
    ):
        if getattr(resolved.runtime_capability.operations, operation):
            registry.require_operation(task_id, operation)
        else:
            with pytest.raises(TaskRegistryError, match="is not available"):
                registry.require_operation(task_id, operation)
    if registered_task_modules()[task_id].data_explorer is None:
        assert resolved.data_explorer is None
        with pytest.raises(TaskRegistryError, match="data explorer is not available"):
            registry.data_explorer_for(task_id)
    else:
        assert resolved.data_explorer is not None
        assert resolved.data_explorer == registered_task_modules()[task_id].data_explorer
        assert registry.data_explorer_for(task_id).data is entry.predictor_runtime.data


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
        {"key": "YS", "label": "降伏強さ", "unit": "MPa", "goal_direction": "at_least", "measurement_keys": ["YS[MPa]"], "plausibility_range": {"min": 50, "max": 2200}, "preferred_display_range": {"min": 100, "max": 1200}}
    )
    hot["task_definition"]["display_decimals"]["output.YS"] = 1
    ys_capability = copy.deepcopy(hot["runtime_capability"]["targets"][0])
    ys_capability["target"] = "YS"
    hot["runtime_capability"]["targets"].append(ys_capability)
    hot_path.write_text(json.dumps(hot, ensure_ascii=False), encoding="utf-8")

    registry = client.app.state.task_registry
    runtimes = {task_id: registry.runtime_for(task_id) for task_id in TASK_IDS}
    with pytest.raises(TaskRegistryError, match="model package outputs do not match"):
        TaskRegistry(runtimes, contract_root=contract_root)


def test_registry_fails_fast_when_declared_curve_has_no_handler(client) -> None:
    registry = client.app.state.task_registry
    runtimes = {task_id: registry.runtime_for(task_id) for task_id in TASK_IDS}
    modules = dict(registered_task_modules())
    modules[TASK_IDS[0]] = replace(modules[TASK_IDS[0]], response_curve=None)

    with pytest.raises(TaskRegistryError, match="capability and TaskModule handler disagree"):
        TaskRegistry(runtimes, modules=modules)


def test_optional_operation_is_explicitly_unavailable(client) -> None:
    registry = client.app.state.task_registry

    with pytest.raises(TaskRegistryError, match="curve family is not available"):
        registry.curve_family_for(TASK_IDS[0])


def test_registry_fails_fast_when_manifest_output_unit_disagrees_with_task_definition(client) -> None:
    registry = client.app.state.task_registry
    runtimes = {task_id: registry.runtime_for(task_id) for task_id in TASK_IDS}
    runtime = copy.copy(runtimes["hot-rolled-properties-v1"])
    package = runtime.model_package
    assert package is not None
    predictors = tuple(
        predictor.model_copy(update={"unit": "ksi"}) if predictor.target == "TS" else predictor
        for predictor in package.manifest.predictors
    )
    runtime.model_package = replace(
        package,
        manifest=package.manifest.model_copy(update={"predictors": predictors}),
    )
    runtimes["hot-rolled-properties-v1"] = runtime

    with pytest.raises(TaskRegistryError, match="output units do not match"):
        TaskRegistry(runtimes)


def test_registry_rejects_an_explorer_bound_to_different_runtime_data(client) -> None:
    registry = client.app.state.task_registry
    runtimes = {task_id: registry.runtime_for(task_id) for task_id in TASK_IDS}
    mismatched_data = copy.copy(runtimes["annealed-properties-v1"].data)

    with pytest.raises(TaskRegistryError, match="data explorer source does not match runtime data"):
        TaskRegistry(
            runtimes,
            data_explorers={
                "annealed-properties-v1": DataExplorerEntry(
                    data=mismatched_data,
                    capability=DataExplorerCapability(quality=True, lineage=True, candidate_creation=True),
                ),
            },
        )


def test_project_contracts_have_stable_openapi_operations_and_named_errors(client) -> None:
    schema = client.get("/openapi.json").json()
    task_operation = schema["paths"]["/api/projects/{project_id}/task-definition"]["get"]
    preview_operation = schema["paths"]["/api/projects/{project_id}/candidates/{candidate_id}/preview"]["post"]
    package_operation = schema["paths"]["/api/projects/{project_id}/model-package"]["get"]

    assert task_operation["operationId"] == "getProjectTaskDefinition"
    assert preview_operation["operationId"] == "previewProjectCandidate"
    assert package_operation["operationId"] == "getProjectModelPackage"
    assert package_operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ModelPackageStatus"
    )
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


def test_annealing_starter_candidates_share_equipment_positions(client) -> None:
    candidates = client.get("/api/projects/default/candidates").json()
    line_speed_candidates = [
        candidate
        for candidate in candidates
        if candidate["name"] in {"基準候補", "高強度案", "延性重視案"}
    ]

    assert len(line_speed_candidates) == 3
    for point_index in range(4):
        positions = [
            candidate["inputs"]["heat_pattern"][point_index]["time_s"]
            * candidate["inputs"]["process"]["ls_mpm"]
            / 60.0
            for candidate in line_speed_candidates
        ]
        assert positions == pytest.approx([positions[0]] * len(positions))


@pytest.mark.parametrize("task_id", TASK_IDS)
def test_both_tasks_use_the_same_project_preview_contract(client, task_id: str) -> None:
    if task_id == "annealed-properties-v1":
        project_id = "default"
        candidate_id = client.get("/api/projects/default/candidates").json()[0]["id"]
    elif task_id == "hot-rolled-properties-v1":
        project_id = "hot-rolling-default"
        candidate_id = client.get(f"/api/projects/{project_id}/candidates").json()[0]["id"]
    else:
        catalog = client.get("/api/task-definitions").json()
        starter = next(
            item for item in catalog
            if item["definition"]["task_definition"]["id"] == task_id
        )["starter_candidate"]
        project_id = client.post("/api/projects", json={"name": "preview契約確認", "task_id": task_id}).json()["id"]
        candidate_id = client.post(f"/api/projects/{project_id}/candidates", json=starter).json()["id"]

    candidate = client.get(f"/api/projects/{project_id}/candidates/{candidate_id}").json()
    response = client.post(
        f"/api/projects/{project_id}/candidates/{candidate_id}/preview",
        params={"expected_revision": candidate["revision"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == task_id
    assert body["candidate_id"] == candidate_id
    assert body["mode"] == "preview"
    assert set(body) == {
        "task_id", "candidate_id", "mode", "predictions", "support", "warnings", "model_meta",
        "model_support", "canonical_input", "similar", "heat_pattern", "response_curve",
    }
