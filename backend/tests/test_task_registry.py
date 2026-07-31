from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from material_workbench.bootstrap.resources import prepare_app_resources
from material_workbench.contracts.task_contracts import (
    DataExplorerCapability,
    InputSpaceSurfaceDefinition,
)
from material_workbench.modeling.model_lifecycle import load_active_packages, validate_active_package_task_set
from material_workbench.modeling.model_packages import PackageContractError
from material_workbench.contracts.schemas import ProjectInput
from material_workbench.persistence.demo_seed import initialize_demo_projects
from material_workbench.task_composition.catalog import registered_task_modules
from material_workbench.task_composition.ports import DataDescriptor
from material_workbench.tasks.task_registry import DataExplorerEntry, TaskRegistry, TaskRegistryError


TASK_IDS = tuple(sorted(registered_task_modules()))
SOURCE_ROOT = Path(__file__).parents[1] / "src" / "material_workbench" / "tasks" / "task_definitions"
ACTIVE_PACKAGES = Path(__file__).parents[2] / "models" / "active-packages.json"
REPOSITORY_ROOT = Path(__file__).parents[2]


def test_input_space_display_cohort_must_contain_every_landmark() -> None:
    with pytest.raises(
        ValueError,
        match="historical_limit must be greater than or equal to landmark_limit",
    ):
        InputSpaceSurfaceDefinition(
            kind="input_space",
            order=1,
            distance_target_key="target",
            landmark_limit=96,
            historical_limit=80,
        )


def test_flank_wear_surfaces_use_independent_run_contexts() -> None:
    module = registered_task_modules()["flank-wear-v1"]
    surfaces = {
        surface.kind: surface
        for surface in module.application.workbench_surfaces
    }
    assert surfaces["input_space"].evidence_context == "parent_condition"
    assert surfaces["prediction_space"].evidence_context == "parent_condition"


def test_app_resources_can_defer_tasks_without_skipping_their_contracts() -> None:
    resources = prepare_app_resources(task_ids=frozenset())
    registry = resources.task_registry

    assert registry.task_ids == TASK_IDS
    assert registry.available_task_ids == ()
    for task_id in TASK_IDS:
        availability = registry.availability_for(task_id)
        assert availability.status == "unavailable"
        assert availability.stage == "runtime"
        assert "準備" in availability.message


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
        assert (
            callable(module.specialized_package_builder)
            or (
                module.standard_model_authoring is not None
                and module.standard_model_authoring.default_estimator_id is not None
            )
        )
        assert isinstance(runtime.data, DataDescriptor)


def test_every_task_declares_an_ordered_allow_list_of_workbench_surfaces(client) -> None:
    registry = client.app.state.task_registry
    contour_tasks: set[str] = set()
    input_space_tasks: set[str] = set()
    prediction_space_tasks: set[str] = set()
    for task_id in registry.task_ids:
        surfaces = registry.resolved_definition_for(
            task_id
        ).application.workbench_surfaces
        assert surfaces
        assert len({surface.kind for surface in surfaces}) == len(surfaces)
        assert [surface.order for surface in surfaces] == sorted(
            surface.order for surface in surfaces
        )
        assert surfaces[-1].kind == "feature_engineering"
        for surface in surfaces:
            if surface.kind == "response_contour":
                contour_tasks.add(task_id)
                assert len(surface.axis_paths) >= 2
                assert surface.grid_size <= 17
            if surface.kind == "prediction_space":
                prediction_space_tasks.add(task_id)
                assert len(surface.target_keys) >= 2
                assert set(surface.target_keys) <= {
                    output.key
                    for output in registry.contract_for(
                        task_id
                    ).task_definition.outputs
                }
            if surface.kind == "input_space":
                input_space_tasks.add(task_id)
                assert surface.distance_target_key in {
                    output.key
                    for output in registry.contract_for(
                        task_id
                    ).task_definition.outputs
                }
                assert surface.embedding_method == "landmark-classical-mds-oos"
                assert surface.embedding_version == "1.0.0"

    assert contour_tasks == {
        "annealed-properties-v1",
        "battery-degradation-v1",
        "concrete-strength-v1",
        "heat-treatment-tradeoff-v1",
        "hot-rolled-properties-v1",
        "secom-yield-risk-v1",
        "wear-curve-v1",
    }
    assert prediction_space_tasks == {
        "annealed-properties-v1",
        "flank-wear-v1",
        "heat-treatment-tradeoff-v1",
        "mpea-room-tensile-v1",
        "welding-consumable-stage-b-v1",
        "welding-stage-c-properties-v1",
    }
    assert input_space_tasks == set(TASK_IDS) - {"mpea-literature-tys-v1"}


def test_active_package_set_rejects_missing_or_unknown_task() -> None:
    active = load_active_packages(ACTIVE_PACKAGES)
    incomplete = active.model_copy(update={"tasks": {TASK_IDS[0]: active.tasks[TASK_IDS[0]]}})
    with pytest.raises(PackageContractError, match="must exactly match"):
        validate_active_package_task_set(incomplete)


def test_registered_default_source_bytes_match_active_package_provenance() -> None:
    active = load_active_packages(ACTIVE_PACKAGES)

    for task_id, module in registered_task_modules().items():
        source_path = REPOSITORY_ROOT / module.default_source
        package_path = ACTIVE_PACKAGES.parent / active.tasks[task_id].active
        manifest = json.loads((package_path / "manifest.json").read_text(encoding="utf-8"))

        assert source_path.is_file(), task_id
        source_digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
        assert manifest["provenance"]["training_data_id"] == f"sha256:{source_digest}", task_id


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


def test_annealing_starter_candidates_start_inside_model_support(client) -> None:
    candidates = client.get("/api/projects/default/candidates").json()
    starter_candidates = [
        candidate
        for candidate in candidates
        if candidate["name"] in {"基準候補", "高強度案", "延性重視案"}
    ]

    assert len(starter_candidates) == 3
    statuses = []
    for candidate in starter_candidates:
        preview = client.post(
            f"/api/projects/default/candidates/{candidate['id']}/preview",
            params={"expected_revision": candidate["revision"]},
        )
        assert preview.status_code == 200
        statuses.extend(
            support["status"]
            for support in preview.json()["model_support"].values()
        )
    assert "supported" in statuses
    assert "extrapolated" not in statuses


def test_annealing_starter_candidates_exclude_undeclared_dataset_fields(
    client,
) -> None:
    task_id = "annealed-properties-v1"
    registry = client.app.state.task_registry
    module = registered_task_modules()[task_id]
    starter = module.starter_project
    assert starter is not None
    runtime = registry.runtime_for(task_id)
    task_definition = registry.contract_for(task_id).task_definition

    candidates = starter.candidate_factory(runtime, task_definition)

    assert len(candidates) == 3
    for candidate in candidates:
        assert "Ca" not in candidate.inputs.composition
        registry.validate_candidate(task_id, candidate)


def test_untouched_legacy_annealing_starters_upgrade_in_existing_workspace(
    client,
) -> None:
    task_id = "annealed-properties-v1"
    registry = client.app.state.task_registry
    module = registered_task_modules()[task_id]
    starter = module.starter_project
    assert starter is not None
    assert starter.legacy_candidate_factory is not None
    runtime = registry.runtime_for(task_id)
    definition = registry.contract_for(task_id).task_definition
    store = client.app.state.store
    existing = store.list_candidates(starter.project_id)
    legacy = starter.legacy_candidate_factory(runtime, definition)
    for candidate, payload in zip(existing, legacy, strict=True):
        store.update_candidate(
            candidate.id,
            starter.project_id,
            payload,
            candidate.revision,
        )

    initialize_demo_projects(
        store,
        {task_id: module},
        {task_id: runtime},
        registry,
        seed_candidates=False,
    )

    upgraded = store.list_candidates(starter.project_id)
    expected = starter.candidate_factory(runtime, definition)
    assert [candidate.id for candidate in upgraded] == [
        candidate.id for candidate in existing
    ]
    assert [
        candidate.inputs.model_dump(mode="json") for candidate in upgraded
    ] == [candidate.inputs.model_dump(mode="json") for candidate in expected]
    assert all(candidate.revision == 3 for candidate in upgraded)


def test_edited_legacy_annealing_starters_are_not_rewritten(client) -> None:
    task_id = "annealed-properties-v1"
    registry = client.app.state.task_registry
    module = registered_task_modules()[task_id]
    starter = module.starter_project
    assert starter is not None
    assert starter.legacy_candidate_factory is not None
    runtime = registry.runtime_for(task_id)
    definition = registry.contract_for(task_id).task_definition
    store = client.app.state.store
    existing = store.list_candidates(starter.project_id)
    legacy = starter.legacy_candidate_factory(runtime, definition)
    legacy[0] = legacy[0].model_copy(update={"name": "利用者が編集した候補"})
    for candidate, payload in zip(existing, legacy, strict=True):
        store.update_candidate(
            candidate.id,
            starter.project_id,
            payload,
            candidate.revision,
        )

    initialize_demo_projects(
        store,
        {task_id: module},
        {task_id: runtime},
        registry,
        seed_candidates=False,
    )

    preserved = store.list_candidates(starter.project_id)
    assert preserved[0].name == "利用者が編集した候補"
    assert all(candidate.revision == 2 for candidate in preserved)


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


def test_task_definition_labels_are_reader_facing_not_internal_identifiers() -> None:
    """UIはこのlabelをそのまま出す。pathやsnake_caseの内部名を露出させない。"""

    internal: list[str] = []
    for path in sorted(SOURCE_ROOT.glob("*.json")):
        definition = json.loads(path.read_text(encoding="utf-8"))["task_definition"]
        labelled = [
            (field["path"], field["label"])
            for group in definition["input_groups"]
            for field in group["fields"]
        ] + [(output["key"], output["label"]) for output in definition["outputs"]]
        for identifier, label in labelled:
            # 元素記号や単位付きの短い表記（C、Si、TS）は読み手向けの正当なlabel。
            # 全小文字ASCIIの識別子だけを内部名として弾く。
            looks_internal = (
                label == identifier
                or re.fullmatch(r"[a-z][a-z0-9]*(_[a-z0-9]+)*", label) is not None
            )
            if looks_internal:
                internal.append(f"{path.name}: {identifier} -> {label}")
    assert internal == []
