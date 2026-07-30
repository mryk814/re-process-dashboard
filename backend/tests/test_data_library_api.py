from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3

from fastapi.testclient import TestClient

from material_workbench.app import create_app
from material_workbench.application.workspace_catalog_bootstrap import bootstrap_workspace_catalog
from material_workbench.tasks.task_registry import load_task_contracts


EXPECTED_DATASET_IDENTITIES = {
    (
        "thin-sheet-tutorial-v1",
        "material_workbench_tutorial_v2.xlsx",
        ("annealed-properties-v1", "hot-rolled-properties-v1"),
    ),
    (
        "material-workbench-process-v1",
        "material_workbench_process_v1.xlsx",
        ("annealed-properties-v1", "hot-rolled-properties-v1"),
    ),
    (
        "cutting-flank-wear-v1",
        "cutting_tool_flank_wear_synthetic_dataset.xlsx",
        ("flank-wear-v1",),
    ),
    ("external-heat-treatment-v1", "heat_treatment_tradeoff_samples.csv", ("heat-treatment-tradeoff-v1",)),
    ("external-concrete-v1", "concrete_mix_samples.csv", ("concrete-strength-v1",)),
    ("external-wear-curve-v1", "wear_curve_samples.csv", ("wear-curve-v1",)),
    ("calce-cs2-battery-capacity-v1", "battery_calce_cs2_cycles.csv", ("battery-degradation-v1",)),
    ("uci-secom-yield-v1", "secom_stress.csv", ("secom-yield-risk-v1",)),
    ("mpea-zenodo-18021833-v1", "mpea_ground_truth_18021833.csv", ("mpea-literature-tys-v1",)),
    ("mpea-zenodo-18021833-room-tensile-v1", "mpea_ground_truth_18021833.csv", ("mpea-room-tensile-v1",)),
    ("mpea-zenodo-18021833-hardness-v1", "mpea_ground_truth_18021833.csv", ("mpea-hardness-process-v1",)),
    (
        "welding-consumable-stage-b-v1",
        "welding_consumable_multistage_synthetic_dataset.xlsx",
        ("welding-consumable-stage-b-v1",),
    ),
    (
        "welding-consumable-stage-c-observations-v1",
        "welding_consumable_multistage_synthetic_dataset.xlsx",
        ("welding-stage-c-properties-v1",),
    ),
}


def test_data_library_exposes_semantic_dataset_records_and_creation_options(client) -> None:
    datasets = client.get(
        "/api/data-library/datasets",
        params={"include_gallery": True},
    )
    assert datasets.status_code == 200
    items = datasets.json()
    assert len(items) == len(EXPECTED_DATASET_IDENTITIES)
    assert {
        (
            item["profile_revision"]["profile_id"],
            item["data_asset"]["original_filename"],
            tuple(item["supported_task_ids"]),
        )
        for item in items
    } == EXPECTED_DATASET_IDENTITIES
    assert all(item["data_asset"]["sha256"] for item in items)
    assert all(item["profile_revision"]["profile_digest"] for item in items)
    assert all(item["dataset_revision"]["dataset_digest"] for item in items)
    tutorial = next(
        item for item in items
        if item["data_asset"]["original_filename"] == "material_workbench_tutorial_v2.xlsx"
    )
    assert {"shared", "tasks"}.issubset(
        tutorial["profile_revision"]["effective_profile_json"]
    )
    assert Path(tutorial["profile_locator"]).is_file()
    legacy_tabular = next(
        item for item in items
        if item["data_asset"]["original_filename"] == "heat_treatment_tradeoff_samples.csv"
    )
    assert "task_id" in legacy_tabular["profile_revision"]["effective_profile_json"]
    assert "shared" not in legacy_tabular["profile_revision"]["effective_profile_json"]
    assert legacy_tabular["profile_locator"] is None
    mpea = [
        item for item in items
        if item["data_asset"]["original_filename"] == "mpea_ground_truth_18021833.csv"
    ]
    assert {item["supported_task_ids"][0] for item in mpea} == {
        "mpea-literature-tys-v1",
        "mpea-room-tensile-v1",
        "mpea-hardness-process-v1",
    }
    assert len({item["profile_revision"]["profile_digest"] for item in mpea}) == 3

    options = client.get("/api/project-creation-options")
    assert options.status_code == 200
    payload = options.json()
    visible_items = client.get("/api/data-library/datasets").json()
    assert len(payload["dataset_views"]) == len(visible_items)
    assert all(
        view["kind"] == "single" and len(view["members"]) == 1
        for view in payload["dataset_views"]
    )
    assert {
        member["dataset_revision_id"]
        for view in payload["dataset_views"]
        for member in view["members"]
    } == {
        item["dataset_revision"]["id"]
        for item in visible_items
    }
    assert len(payload["model_packages"]) >= 12
    assert payload["project_series"] == []
    assert set(payload["task_contract_digests"]) >= {
        "annealed-properties-v1",
        "hot-rolled-properties-v1",
        "flank-wear-v1",
        "heat-treatment-tradeoff-v1",
        "concrete-strength-v1",
        "wear-curve-v1",
        "battery-degradation-v1",
        "secom-yield-risk-v1",
        "mpea-room-tensile-v1",
        "mpea-hardness-process-v1",
    }
    assert "mpea-literature-tys-v1" not in payload["task_contract_digests"]
    assert all(
        package["task_id"] != "mpea-literature-tys-v1"
        for package in payload["model_packages"]
    )
    assert all(
        package["task_contract_digest"]
        == payload["task_contract_digests"][package["task_id"]]
        for package in payload["model_packages"]
    )


def test_available_package_manifest_can_be_reloaded_without_restart(client) -> None:
    projects = client.get("/api/projects", params={"include_archived": True}).json()
    referenced_ids = {
        project["model_package_ref_id"]
        for project in projects
        if project["model_package_ref_id"]
    }
    packages = client.get("/api/project-creation-options").json()["model_packages"]
    contracts = load_task_contracts()
    removed = next(
        package
        for package in packages
        if package["id"] not in referenced_ids
        and contracts[package["task_id"]].task_definition.response_curve_variables
    )

    with sqlite3.connect(client.app.state.store.path) as conn:
        conn.execute("DELETE FROM model_package_refs WHERE id=?", (removed["id"],))

    assert removed["id"] not in {
        package["id"]
        for package in client.get(
            "/api/data-library/model-packages",
            params={"include_archived": True, "include_gallery": True},
        ).json()
    }

    refreshed = client.post("/api/data-library/model-packages/refresh")

    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["warnings"] == []
    assert removed["id"] in {
        package["id"]
        for package in refreshed.json()["model_packages"]
    }
    options = client.get("/api/project-creation-options").json()
    assert removed["id"] in {package["id"] for package in options["model_packages"]}

    dataset = next(
        item
        for item in options["datasets"]
        if (
            f"sha256:{item['data_asset']['sha256']}"
            == removed["manifest_json"]["provenance"]["training_data_id"]
            and item["profile_revision"]["profile_digest"]
            == removed["manifest_json"]["provenance"]["dataset_profile_id"]
        )
    )
    project = client.post(
        "/api/projects",
        json={
            "name": "live package refresh smoke",
            "task_id": removed["task_id"],
            "dataset_view_revision_id": dataset["dataset_views"][0]["id"],
            "model_package_ref_id": removed["id"],
        },
    )
    assert project.status_code == 201, project.text

    fixture = contracts[removed["task_id"]]
    canonical = fixture.canonical_candidate
    dumped = canonical.model_dump(mode="json")
    candidate_payload = {
        "name": "live package candidate",
        "inputs": {
            key: dumped[key]
            for key in ("composition", "process", "categorical", "heat_pattern")
        },
        "provenance": dumped["provenance"],
    }
    candidate = client.post(
        f"/api/projects/{project.json()['id']}/candidates",
        json=candidate_payload,
    )
    assert candidate.status_code == 201, candidate.text
    curve = client.get(
        f"/api/projects/{project.json()['id']}/candidates/{candidate.json()['id']}/response-curve",
        params={
            "expected_revision": candidate.json()["revision"],
            "target": fixture.task_definition.outputs[0].key,
            "variable": fixture.task_definition.response_curve_variables[0].path,
            "points": 9,
        },
    )
    assert curve.status_code == 200, curve.text
    assert len(curve.json()["points"]) == 9


def test_personal_duplicate_never_replaces_the_bundled_package(
    app_resources,
    tmp_path: Path,
) -> None:
    app = create_app(db_path=tmp_path / "workbench.db", _resources=app_resources)
    with TestClient(app) as client:
        bundled = next(
            item
            for item in client.get("/api/project-creation-options").json()[
                "model_packages"
            ]
            if item["storage_scope"] == "bundled"
        )
        personal_store = tmp_path / "personal-model-store"
        copied_package = personal_store / "packages" / bundled["package_id"]
        shutil.copytree(Path(bundled["locator"]), copied_package)
        available = personal_store / "available-packages.json"
        available.write_text(
            json.dumps({
                "schema_version": "available-model-packages/v1",
                "packages": [f"packages/{bundled['package_id']}"],
            }),
            encoding="utf-8",
        )
        client.app.state.available_packages_paths += (available,)
        client.app.state.personal_available_packages_paths = (available,)

        refreshed = client.post("/api/data-library/model-packages/refresh")

        assert refreshed.status_code == 200, refreshed.text
        duplicate = next(
            item
            for item in refreshed.json()["model_packages"]
            if item["id"] == bundled["id"]
        )
        assert duplicate["storage_scope"] == "bundled"
        assert duplicate["locator"] == bundled["locator"]

        shutil.rmtree(copied_package)
        degraded = client.post("/api/data-library/model-packages/refresh")

        assert degraded.status_code == 200, degraded.text
        assert len(degraded.json()["warnings"]) == 1
        option = next(
            item
            for item in client.get("/api/project-creation-options").json()[
                "model_packages"
            ]
            if item["id"] == bundled["id"]
        )
        assert option["storage_scope"] == "bundled"
        assert option["locator"] == bundled["locator"]


def test_personal_package_cannot_reuse_a_bundled_package_id(
    app_resources,
    tmp_path: Path,
) -> None:
    app = create_app(db_path=tmp_path / "workbench.db", _resources=app_resources)
    with TestClient(app) as client:
        bundled = next(
            item
            for item in client.get("/api/project-creation-options").json()[
                "model_packages"
            ]
            if item["storage_scope"] == "bundled"
        )
        personal_store = tmp_path / "personal-model-store"
        copied_package = personal_store / "packages" / bundled["package_id"]
        shutil.copytree(Path(bundled["locator"]), copied_package)
        manifest_path = copied_package / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["package_version"] = (
            f"{manifest['package_version']}-different-content"
        )
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        available = personal_store / "available-packages.json"
        available.write_text(
            json.dumps({
                "schema_version": "available-model-packages/v1",
                "packages": [f"packages/{bundled['package_id']}"],
            }),
            encoding="utf-8",
        )
        client.app.state.available_packages_paths += (available,)
        client.app.state.personal_available_packages_paths = (available,)

        refreshed = client.post("/api/data-library/model-packages/refresh")

        assert refreshed.status_code == 200, refreshed.text
        assert len(refreshed.json()["warnings"]) == 1
        assert "同じpackage_id" in refreshed.json()["warnings"][0]["message"]
        unchanged = next(
            item
            for item in refreshed.json()["model_packages"]
            if item["id"] == bundled["id"]
        )
        assert unchanged["manifest_digest"] == bundled["manifest_digest"]
        assert unchanged["locator"] == bundled["locator"]


def test_unused_dataset_can_be_disabled_and_restored_with_its_views(client) -> None:
    projects = client.get("/api/projects").json()
    used_view_ids = {project["dataset_view_revision_id"] for project in projects}
    datasets = client.get(
        "/api/data-library/datasets",
        params={"include_archived": True, "include_gallery": True},
    ).json()
    dataset = next(
        item
        for item in datasets
        if not ({view["id"] for view in item["dataset_views"]} & used_view_ids)
    )
    revision_id = dataset["dataset_revision"]["id"]
    related_view_ids = {view["id"] for view in dataset["dataset_views"]}

    disabled = client.patch(
        f"/api/data-library/datasets/{revision_id}", json={"archived": True}
    )

    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["dataset_revision"]["archived_at"] is not None
    active_ids = {
        item["dataset_revision"]["id"]
        for item in client.get("/api/data-library/datasets").json()
    }
    assert revision_id not in active_ids
    active_view_ids = {
        view["id"] for view in client.get("/api/data-library/views").json()
    }
    assert related_view_ids.isdisjoint(active_view_ids)

    restarted_catalog = bootstrap_workspace_catalog(
        client.app.state.store.path,
        client.app.state.task_registry,
    )
    assert restarted_catalog.get_dataset_revision(
        revision_id, include_archived=True
    ).archived_at is not None

    restored = client.patch(
        f"/api/data-library/datasets/{revision_id}", json={"archived": False}
    )

    assert restored.status_code == 200, restored.text
    assert restored.json()["dataset_revision"]["archived_at"] is None
    assert related_view_ids <= {
        view["id"] for view in client.get("/api/data-library/views").json()
    }


def test_referenced_dataset_and_model_package_cannot_be_disabled(client) -> None:
    project = client.get("/api/projects/default").json()
    view = next(
        item
        for item in client.get("/api/data-library/views").json()
        if item["id"] == project["dataset_view_revision_id"]
    )
    dataset_revision_id = view["members"][0]["dataset_revision_id"]

    dataset_response = client.patch(
        f"/api/data-library/datasets/{dataset_revision_id}",
        json={"archived": True},
    )
    package_response = client.patch(
        f"/api/data-library/model-packages/{project['model_package_ref_id']}",
        json={"archived": True},
    )

    assert dataset_response.status_code == 409
    assert "参照中のプロジェクト" in dataset_response.json()["message"]
    assert package_response.status_code == 409
    assert "参照中のプロジェクト" in package_response.json()["message"]


def test_unused_model_package_can_be_disabled_and_restored(client) -> None:
    used_package_ids = {
        project["model_package_ref_id"]
        for project in client.get("/api/projects").json()
    }
    package = next(
        item
        for item in client.get(
            "/api/data-library/model-packages",
            params={"include_archived": True},
        ).json()
        if item["id"] not in used_package_ids
    )

    disabled = client.patch(
        f"/api/data-library/model-packages/{package['id']}",
        json={"archived": True},
    )
    restored = client.patch(
        f"/api/data-library/model-packages/{package['id']}",
        json={"archived": False},
    )

    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["archived_at"] is not None
    assert restored.status_code == 200, restored.text
    assert restored.json()["archived_at"] is None


def test_model_package_restore_revalidates_the_package_files(client) -> None:
    used_package_ids = {
        project["model_package_ref_id"]
        for project in client.get("/api/projects").json()
    }
    package = next(
        item
        for item in client.get(
            "/api/data-library/model-packages",
            params={"include_archived": True},
        ).json()
        if item["id"] not in used_package_ids
    )
    assert client.patch(
        f"/api/data-library/model-packages/{package['id']}",
        json={"archived": True},
    ).status_code == 200
    with sqlite3.connect(client.app.state.store.path) as conn:
        conn.execute(
            "UPDATE model_package_refs SET locator=? WHERE id=?",
            ("missing-model-package", package["id"]),
        )

    rejected = client.patch(
        f"/api/data-library/model-packages/{package['id']}",
        json={"archived": False},
    )

    assert rejected.status_code == 409
    assert "実体を検証できません" in rejected.json()["message"]


def test_mpea_bundled_task_runs_from_registered_dataset_and_package(client) -> None:
    project = next(
        item for item in client.get("/api/projects").json()
        if item["task_id"] == "mpea-room-tensile-v1"
    )
    candidates = client.get(
        f"/api/projects/{project['id']}/candidates"
    ).json()

    assert len(candidates) == 3
    preview = client.post(
        f"/api/projects/{project['id']}/candidates/{candidates[1]['id']}/preview",
        params={"expected_revision": candidates[1]["revision"]},
    )
    assert preview.status_code == 200, preview.text
    payload = preview.json()
    assert set(payload["predictions"]) == {"TYS", "UTS", "EL"}
    assert {
        target: support["reference_count"]
        for target, support in payload["model_support"].items()
    } == {"TYS": 99, "UTS": 90, "EL": 71}
    assert set(payload["canonical_input"]["composition"]) == {
        "Fe", "Ni", "Co", "Mn", "Cr", "Al", "Ti", "Cu",
        "Si", "V", "Nb", "B", "Mo", "Ta",
    }
    assert set(payload["canonical_input"]["process"]) == {
        "homogenization_temp_c", "homogenization_time_h",
        "rolling_temp_c", "rolling_reduction_pct",
        "recrystallization_temp_c", "recrystallization_time_min",
        "aging_temp_c", "aging_time_h",
    }
    assert payload["model_meta"]["package"]["id"] == "mpea-room-tensile-ridge-v2"
    curation = client.get(
        f"/api/projects/{project['id']}/model-package/training-data",
        params={"stage": "curation", "target": "TYS", "limit": 5},
    )
    assert curation.status_code == 200, curation.text
    curation_payload = curation.json()
    assert curation_payload["total"] == 396
    assert [
        {key: item[key] for key in ("target", "usable_rows", "source_groups")}
        for item in curation_payload["curation_summary"]["targets"]
    ] == [
        {"target": "TYS", "usable_rows": 99, "source_groups": 39},
        {"target": "UTS", "usable_rows": 90, "source_groups": 38},
        {"target": "EL", "usable_rows": 71, "source_groups": 33},
    ]
    assert curation_payload["curation_summary"]["targets"][0]["exclusion_reasons"]
    assert {column["group"] for column in curation_payload["columns"]} >= {
        "原値", "正規化", "判定",
    }
    assert "curation.status" in curation_payload["rows"][0]["values"]
    similar = client.get(
        f"/api/projects/{project['id']}/candidates/{candidates[1]['id']}/similar",
        params={
            "expected_revision": candidates[1]["revision"],
            "target": "EL",
            "limit": 6,
        },
    )
    assert similar.status_code == 200, similar.text
    assert all("EL" in row["outputs"] for row in similar.json())


def test_project_creation_pins_explicit_references_and_rejects_rebinding(client) -> None:
    options = client.get("/api/project-creation-options").json()
    reference = client.get("/api/projects/default").json()
    view = next(item for item in options["dataset_views"] if item["id"] == reference["dataset_view_revision_id"])
    package = next(item for item in options["model_packages"] if item["id"] == reference["model_package_ref_id"])
    series = client.post(
        "/api/project-series", json={"name": "一連の材料検討", "description": "2026年の検討"}
    ).json()

    created = client.post("/api/projects", json={
        "name": "固定参照の確認",
        "task_id": "annealed-properties-v1",
        "dataset_view_revision_id": view["id"],
        "model_package_ref_id": package["id"],
        "project_series_id": series["id"],
    })
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["binding_provenance"] == "explicit"
    assert project["dataset_view_revision_id"] == view["id"]
    assert project["model_package_manifest_digest"] == package["manifest_digest"]
    assert project["project_series_id"] == series["id"]

    rejected = client.put(
        f"/api/projects/{project['id']}",
        json={"name": project["name"], "task_id": "hot-rolled-properties-v1"},
    )
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "project_task_locked"


def test_cohort_comparison_is_not_a_project_reference_dataset(client) -> None:
    options = client.get("/api/project-creation-options").json()
    members = [
        {"dataset_revision_id": item["dataset_revision"]["id"], "ordinal": index, "cohort_key": f"c{index}"}
        for index, item in enumerate(options["datasets"][:2])
    ]
    view_response = client.post("/api/data-library/views", json={
        "view_id": "comparison-not-project-input",
        "revision": 1,
        "name": "設備比較",
        "kind": "cohort_comparison",
        "members": members,
    })
    assert view_response.status_code == 201, view_response.text

    package = next(item for item in options["model_packages"] if item["task_id"] == "annealed-properties-v1")
    created = client.post("/api/projects", json={
        "name": "比較セットを誤用する検討",
        "task_id": "annealed-properties-v1",
        "dataset_view_revision_id": view_response.json()["id"],
        "model_package_ref_id": package["id"],
    })
    assert created.status_code == 422
    assert "比較Activity" in created.json()["message"]


def test_continuation_uses_the_explicit_group_and_accepts_an_empty_reason(client) -> None:
    original = client.get("/api/projects/default").json()
    created = client.post("/api/projects", json={
        "name": "継続検討",
        "task_id": original["task_id"],
        "dataset_view_revision_id": original["dataset_view_revision_id"],
        "model_package_ref_id": original["model_package_ref_id"],
        "project_series_id": original["project_series_id"],
        "predecessor_project_id": original["id"],
    })
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["project_series_id"] == original["project_series_id"]
    assert project["predecessor_project_id"] == original["id"]
    assert project["continuation_reason"] == ""


def test_continuation_can_switch_prediction_task_and_belong_to_another_group(client) -> None:
    options = client.get("/api/project-creation-options").json()
    original = client.get("/api/projects/default").json()
    other_series = client.get("/api/projects/hot-rolling-default").json()["project_series_id"]
    hot_reference = client.get("/api/projects/hot-rolling-default").json()
    payload = {
        "name": "同じテーマの熱延検討",
        "task_id": "hot-rolled-properties-v1",
        "dataset_view_revision_id": hot_reference["dataset_view_revision_id"],
        "model_package_ref_id": hot_reference["model_package_ref_id"],
        "project_series_id": other_series,
        "predecessor_project_id": original["id"],
        "continuation_reason": "同じ材料テーマを熱延特性でも評価するため",
    }

    created = client.post("/api/projects", json=payload)

    assert created.status_code == 201, created.text
    project = created.json()
    assert project["task_id"] == "hot-rolled-properties-v1"
    assert project["project_series_id"] == other_series
    assert project["predecessor_project_id"] == original["id"]


def test_project_with_successor_cannot_be_deleted(client) -> None:
    reference = client.get("/api/projects/default").json()
    binding = {
        "dataset_view_revision_id": reference["dataset_view_revision_id"],
        "model_package_ref_id": reference["model_package_ref_id"],
    }
    first = client.post("/api/projects", json={
        "name": "系譜の起点", "task_id": "annealed-properties-v1", **binding,
    })
    assert first.status_code == 201, first.text
    successor = client.post("/api/projects", json={
        "name": "系譜の続き",
        "task_id": "annealed-properties-v1",
        **binding,
        "predecessor_project_id": first.json()["id"],
        "continuation_reason": "データ追加",
    })
    assert successor.status_code == 201, successor.text

    archived = client.delete(f"/api/projects/{first.json()['id']}")
    assert archived.status_code == 204
    deleted = client.delete(
        f"/api/projects/{first.json()['id']}/purge",
        params={"confirm_project_id": first.json()["id"]},
    )
    assert deleted.status_code == 409
    assert deleted.json()["code"] == "project_has_successors"


def test_dataset_view_revision_conflict_returns_409(client) -> None:
    dataset = client.get("/api/project-creation-options").json()["datasets"][0]["dataset_revision"]
    payload = {
        "view_id": "stable-view-revision",
        "revision": 1,
        "name": "最初の名前",
        "kind": "single",
        "members": [{"dataset_revision_id": dataset["id"], "ordinal": 0}],
    }
    assert client.post("/api/data-library/views", json=payload).status_code == 201
    conflict = client.post("/api/data-library/views", json={**payload, "name": "別の名前"})
    assert conflict.status_code == 409
