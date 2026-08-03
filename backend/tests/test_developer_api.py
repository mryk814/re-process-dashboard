from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

from fastapi.testclient import TestClient
from decision_workbench.api import developer


def test_change_guide_is_machine_readable_and_requires_human_review(client: TestClient) -> None:
    response = client.get("/api/developer/change-guide")
    assert response.status_code == 200
    items = response.json()
    assert {item["id"] for item in items} >= {"new-excel", "input", "task", "presentation"}
    task = next(item for item in items if item["id"] == "task")
    assert task["risk"] == "specialist"
    assert task["human_review"]
    profile_command = next(
        command
        for item in items
        for command in item["commands"]
        if "profile_workbench.py" in command["display_text"]
    )
    assert profile_command["arguments"][-2:] == ["inspect", "path/to/file.xlsx"]
    assert "--source" not in profile_command["arguments"]
    verify_edit_commands = [
        command
        for item in items
        for command in item["commands"]
        if command["arguments"][:2] == ["run", "verify:edit"]
    ]
    assert verify_edit_commands
    assert all(
        "--" in command["arguments"]
        and any(argument.startswith("backend/tests/") for argument in command["arguments"])
        for command in verify_edit_commands
    )


def test_change_guide_exposes_distinct_decision_activity_workflows(
    client: TestClient,
) -> None:
    response = client.get("/api/developer/change-guide")
    assert response.status_code == 200
    entries = {item["id"]: item for item in response.json()}

    create = entries["decision-activity-new"]
    change = entries["decision-activity-change"]
    assert create["label"] == "新しいDecision Activityを追加したい"
    assert change["label"] == "既存Decision Activityを変更したい"
    assert create["risk"] == "specialist"
    assert change["risk"] == "review"

    expected_steps = [
        "1. Python contract",
        "2. Registry",
        "3. Application",
        "4. API",
        "5. Generated contract",
        "6. React View",
        "7. Contract / UI / E2E test",
    ]
    assert [step["label"] for step in create["steps"]] == expected_steps
    assert [step["label"] for step in change["steps"]] == expected_steps
    assert create["steps"][0]["paths"] == [
        "backend/src/decision_workbench/contracts/decision_activity_contracts.py"
    ]
    assert "e2e/decision-activity.spec.ts" in create["steps"][-1]["paths"]
    assert any("直接編集せず" in warning for warning in create["warnings"])
    assert any("保存済みRun" in warning for warning in change["warnings"])
    assert "docs/contracts/decision-activities.md" in create["documents"]
    assert "docs/learning/chapters/contract-through-stack.qmd" in change["documents"]
    assert create["commands"][0]["display_text"] == "npm run api:generate"


def test_overview_connects_project_to_runtime_contracts(client: TestClient) -> None:
    response = client.get("/api/developer/overview")
    assert response.status_code == 200, response.text
    items = [
        item for item in response.json()["items"] if item["identity_kind"] == "single_task"
    ]
    assert items
    assert all(item["project_id"] and item["task_id"] and item["package_id"] for item in items)
    assert all(item["feature_pipeline_id"] and item["runtime_type"] for item in items)
    assert all(isinstance(item["active_package"], bool) for item in items)


def test_capability_atlas_exposes_bundled_technical_detail(
    client: TestClient,
) -> None:
    response = client.get("/api/developer/capability-atlas")
    assert response.status_code == 200, response.text
    atlas = response.json()
    assert atlas["schema_version"] == "capability-atlas/v1"
    assert atlas["authority"] == "bundled"
    assert atlas["stochastic_reproducibility"] == {
        "status": "effective_sampling_identity_recorded",
        "identity_schema_version": "sampling-identity/v1",
        "runtime_types": ["numpyro.dense_posterior.v1"],
        "limitations": [
            "response_curve_sampling_identity_unavailable",
            "legacy_evidence_sampling_conditions_unavailable",
        ],
    }
    assert atlas["project_modes"] == [
        "single_task",
        "chain",
        "prediction_graph",
    ]
    assert atlas["task_count"] == len(atlas["tasks"]) == 17
    assert atlas["graph_count"] == 2
    assert atlas["available_package_count"] >= atlas["task_count"]
    assert {task["missingness_status"] for task in atlas["tasks"]} == {
        "reject_only",
        "runtime_contract_not_exposed",
    }
    assert all(
        task["missingness_policy_digest"].startswith("sha256:")
        for task in atlas["tasks"]
    )


def test_overview_lists_chain_projects_instead_of_failing_on_their_empty_task(
    client: TestClient,
) -> None:
    chain = next(
        item
        for item in client.get("/api/chains").json()
        if item["definition"]["chain_id"] == "welding-consumable-a-b-c-v1"
    )
    created = client.post(
        "/api/projects",
        json={
            "name": "Chain構成一覧",
            "scientific_identity": {
                "identity_kind": "chain",
                "chain_revision_id": "welding-consumable-a-b-c-v1:r1",
                "chain_revision_digest": chain["revisions"][0]["revision_digest"],
            },
        },
    )
    assert created.status_code == 201, created.text
    project = created.json()

    response = client.get("/api/developer/overview")
    assert response.status_code == 200, response.text
    item = next(
        item for item in response.json()["items"] if item["project_id"] == project["id"]
    )
    assert item["identity_kind"] == "chain"
    assert item["chain_revision_id"] == "welding-consumable-a-b-c-v1:r1"
    assert item["task_id"] == ""
    # 単一TaskのPackage参照が無いことは、Chainでは参照不足ではない。
    assert item["validation_status"] == "ok"
    assert item["active_package"] is False


def test_overview_preserves_prediction_graph_project_identity(
    client: TestClient,
) -> None:
    graph = next(
        item
        for item in client.get("/api/chains").json()
        if item["definition"].get("graph_id")
        == "welding-material-split-output-demo-v1"
    )
    revision = graph["revisions"][0]
    graph_revision_id = f"{revision['graph_id']}:r{revision['revision']}"
    created = client.post(
        "/api/prediction-graphs/projects",
        json={
            "project": {
                "name": "Prediction Graph構成一覧",
                "purpose": "Developer overview identity contract",
                "description": "",
                "notes": "",
                "task_id": "",
                "task_contract_digest": "",
                "model_package_manifest_digest": "",
                "response_curve_points": 17,
                "continuation_reason": "",
                "decision_candidate_id": "",
                "decision_snapshot_id": "",
                "decision_note": "",
            },
            "graph_revision_id": graph_revision_id,
            "graph_revision_digest": revision["revision_digest"],
            "project_binding_revision": 1,
            "project_binding_values": {},
        },
    )
    assert created.status_code == 201, created.text

    response = client.get("/api/developer/overview")
    assert response.status_code == 200, response.text
    item = next(
        item
        for item in response.json()["items"]
        if item["project_id"] == created.json()["id"]
    )
    assert item["identity_kind"] == "prediction_graph"
    assert item["graph_revision_id"] == graph_revision_id
    assert item["task_id"] == ""
    assert item["validation_status"] == "ok"
    assert item["active_package"] is False


def test_observation_training_profile_is_inspectable_before_model_packaging(
    client: TestClient,
) -> None:
    profiles = client.get("/api/developer/observation-training-profiles")
    assert profiles.status_code == 200, profiles.text
    payload = profiles.json()
    profile = next(
        item
        for item in payload
        if item["profile_id"] == "welding-consumable-stage-c-observations-v1"
    )
    assert {
        item["family"]: (
            item["source_rows"],
            item["usable_input_rows"],
            item["split_groups"],
        )
        for item in profile["families"]
    } == {
        "tensile": (600, 600, 300),
        "charpy": (2700, 2700, 300),
        "corrosion": (103, 103, 103),
    }

    page = client.get(
        "/api/developer/observation-training-data",
        params={
            "profile_id": profile["profile_id"],
            "family": "charpy",
            "target": "CHARPY_ENERGY",
            "offset": 0,
            "limit": 5,
        },
    )
    assert page.status_code == 200, page.text
    inspected = page.json()
    assert inspected["source_rows"] == 2700
    assert inspected["usable_rows"] == 2700
    assert inspected["split_groups"] == 300
    assert len(inspected["rows"]) == 5
    assert all("process.test_temperature_c" in row["inputs"] for row in inspected["rows"])
    assert all(row["split_group_key"].startswith("WR-") for row in inspected["rows"])
    assert all(row["provenance"]["source_sheet"] == "シャルピー試験" for row in inspected["rows"])


def test_observation_training_endpoint_reads_from_packaged_resource_root(
    client: TestClient,
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "data"
        / "source"
        / "welding_consumable_multistage_synthetic_dataset.xlsx"
    )
    packaged_source = (
        tmp_path
        / "data"
        / "source"
        / "welding_consumable_multistage_synthetic_dataset.xlsx"
    )
    packaged_source.parent.mkdir(parents=True)
    shutil.copyfile(source, packaged_source)
    monkeypatch.setenv("WORKBENCH_RESOURCE_ROOT", str(tmp_path))
    developer._load_observation_dataset.cache_clear()

    profiles = client.get("/api/developer/observation-training-profiles")
    assert profiles.status_code == 200, profiles.text
    payload = profiles.json()[0]
    assert {
        item["family"]: item["source_rows"]
        for item in payload["families"]
    } == {"tensile": 600, "charpy": 2700, "corrosion": 103}
    page = client.get(
        "/api/developer/observation-training-data",
        params={
            "profile_id": payload["profile_id"],
            "family": "tensile",
            "target": "TS",
            "limit": 1,
        },
    )
    assert page.status_code == 200, page.text
    assert page.json()["rows"][0]["observation_id"] == "TT-00001"

    developer._load_observation_dataset.cache_clear()


def test_observation_training_profiles_return_structured_error_when_source_is_missing(
    client: TestClient,
    monkeypatch,
    tmp_path: Path,
) -> None:
    registration = developer._ObservationProfileRegistration(
        profile_id="missing-observations-v1",
        source_relative=Path("data/source/missing.xlsx"),
        profile_path=developer._WELDING_PROFILE,
    )
    monkeypatch.setattr(developer, "_OBSERVATION_PROFILE_REGISTRY", (registration,))
    monkeypatch.setenv("WORKBENCH_RESOURCE_ROOT", str(tmp_path))
    developer._load_observation_dataset.cache_clear()

    response = client.get("/api/developer/observation-training-profiles")

    assert response.status_code == 503
    assert response.json()["code"] == "runtime_unavailable"
    assert "配布データが見つかりません" in response.json()["message"]
    assert "再インストール" in response.json()["message"]
    declared = client.app.openapi()["paths"]["/api/developer/observation-training-profiles"]["get"]["responses"]
    assert declared["503"]["content"]["application/json"]["schema"]["$ref"].endswith("/ApiError")
    developer._load_observation_dataset.cache_clear()


def test_observation_training_profiles_return_structured_error_when_source_is_corrupt(
    client: TestClient,
    monkeypatch,
    tmp_path: Path,
) -> None:
    relative = Path("data/source/corrupt.xlsx")
    corrupt = tmp_path / relative
    corrupt.parent.mkdir(parents=True)
    corrupt.write_bytes(b"not an xlsx workbook")
    registration = developer._ObservationProfileRegistration(
        profile_id="corrupt-observations-v1",
        source_relative=relative,
        profile_path=developer._WELDING_PROFILE,
    )
    monkeypatch.setattr(developer, "_OBSERVATION_PROFILE_REGISTRY", (registration,))
    monkeypatch.setenv("WORKBENCH_RESOURCE_ROOT", str(tmp_path))
    developer._load_observation_dataset.cache_clear()

    response = client.get("/api/developer/observation-training-profiles")

    assert response.status_code == 503
    assert response.json()["code"] == "runtime_unavailable"
    assert "元データを読み取れません" in response.json()["message"]
    assert "差し替えてください" in response.json()["message"]
    developer._load_observation_dataset.cache_clear()


def test_observation_training_registry_lists_and_inspects_multiple_profiles(
    client: TestClient,
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_relative = Path("data/source/welding.xlsx")
    source = Path(__file__).resolve().parents[2] / "data" / "source" / developer._WELDING_SOURCE_RELATIVE.name
    packaged_source = tmp_path / source_relative
    packaged_source.parent.mkdir(parents=True)
    shutil.copyfile(source, packaged_source)

    second_profile_path = tmp_path / "second-profile.json"
    second_profile = json.loads(developer._WELDING_PROFILE.read_text(encoding="utf-8"))
    second_profile["id"] = "welding-consumable-stage-c-observations-v2"
    second_profile_path.write_text(json.dumps(second_profile, ensure_ascii=False), encoding="utf-8")
    registry = (
        developer._ObservationProfileRegistration(
            profile_id="welding-consumable-stage-c-observations-v1",
            source_relative=source_relative,
            profile_path=developer._WELDING_PROFILE,
        ),
        developer._ObservationProfileRegistration(
            profile_id="welding-consumable-stage-c-observations-v2",
            source_relative=source_relative,
            profile_path=second_profile_path,
        ),
    )
    monkeypatch.setattr(developer, "_OBSERVATION_PROFILE_REGISTRY", registry)
    monkeypatch.setenv("WORKBENCH_RESOURCE_ROOT", str(tmp_path))
    developer._load_observation_dataset.cache_clear()

    profiles = client.get("/api/developer/observation-training-profiles")
    assert profiles.status_code == 200, profiles.text
    assert [item["profile_id"] for item in profiles.json()] == [
        "welding-consumable-stage-c-observations-v1",
        "welding-consumable-stage-c-observations-v2",
    ]
    page = client.get(
        "/api/developer/observation-training-data",
        params={
            "profile_id": "welding-consumable-stage-c-observations-v2",
            "family": "corrosion",
            "target": "CORROSION_RATE",
            "limit": 1,
        },
    )
    assert page.status_code == 200, page.text
    assert page.json()["rows"][0]["observation_id"] == "CR-00001"
    developer._load_observation_dataset.cache_clear()


def test_runtime_diagnostics_does_not_run_repository_commands(
    client: TestClient,
    monkeypatch,
) -> None:
    def reject_subprocess(*_args, **_kwargs):
        raise AssertionError("Runtime Diagnostics must not start repository tools")

    monkeypatch.setattr(subprocess, "run", reject_subprocess)
    response = client.get("/api/developer/diagnostics")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schema_version"] == "runtime-diagnostics/v1"
    assert {check["id"] for check in payload["checks"]} == {
        "database",
        "project-references",
        "archived-resources",
        "runtime-capabilities",
        "optional-subsystems",
        "sidecar",
        "secom-stress-fixture",
    }
    secom = next(check for check in payload["checks"] if check["id"] == "secom-stress-fixture")
    assert secom["severity"] == "ok"
    assert secom["details"]["sensor_features"] == 590
    assert secom["details"]["label_counts"] == {"pass": 1463, "fail": 104}
    assert all(
        "toolchain" not in check["id"] and "generated" not in check["id"]
        for check in payload["checks"]
    )
