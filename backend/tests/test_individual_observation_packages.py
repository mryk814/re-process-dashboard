from pathlib import Path

from fastapi.testclient import TestClient
from material_workbench.app import create_app
from material_workbench.bootstrap.resources import prepare_app_resources

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "source" / "material_workbench_process_v1.xlsx"


def test_process_registers_and_runs_standard_and_individual_observation_packages(
    tmp_path: Path,
) -> None:
    resources = prepare_app_resources(
        source_overrides={
            "annealed-properties-v1": SOURCE,
            "hot-rolled-properties-v1": SOURCE,
        },
        package_roots={
            "annealed-properties-v1": ROOT / "models" / "packages" / "annealed-gp-stable-ard-process-v2",
            "hot-rolled-properties-v1": ROOT / "models" / "packages" / "hot-rolled-horseshoe-process-v2",
        },
    )
    app = create_app(db_path=tmp_path / "workbench.db", _resources=resources)
    with TestClient(app) as client:
        lineage_index = client.get(
            "/api/projects/default/lineage",
            params={"limit": 200},
        )
        assert lineage_index.status_code == 200
        assert len(lineage_index.json()["items"]) == 200
        assert lineage_index.json()["matched_entities"] > 200
        with_issues = client.get(
            "/api/projects/default/lineage",
            params={"issue_filter": "with_issues", "limit": 20},
        )
        assert with_issues.status_code == 200
        assert with_issues.json()["items"]
        assert all(item["has_issue"] for item in with_issues.json()["items"])
        without_issues = client.get(
            "/api/projects/default/lineage",
            params={"issue_filter": "without_issues", "limit": 20},
        )
        assert without_issues.status_code == 200
        assert without_issues.json()["items"]
        assert all(not item["has_issue"] for item in without_issues.json()["items"])
        saved_review = client.put(
            "/api/projects/default/lineage-reviews/ME-00001",
            json={
                "entity_type": "溶製",
                "status": "needs_fix",
                "note": "元データで成分値を確認する",
            },
        )
        assert saved_review.status_code == 200
        assert saved_review.json()["status"] == "needs_fix"
        hidden_review = client.put(
            "/api/projects/default/lineage-reviews/ME-00002",
            json={"entity_type": "溶製", "status": "hidden", "note": "確認済み"},
        )
        assert hidden_review.status_code == 200
        hidden_default = client.get(
            "/api/projects/default/lineage",
            params={"query": "ME-00002"},
        )
        assert hidden_default.status_code == 200
        assert all(item["key"] != "ME-00002" for item in hidden_default.json()["items"])
        hidden_included = client.get(
            "/api/projects/default/lineage",
            params={"query": "ME-00002", "include_hidden": True},
        )
        assert hidden_included.json()["items"][0]["review_status"] == "hidden"
        reviews = client.get("/api/projects/default/lineage-reviews")
        assert reviews.status_code == 200
        assert reviews.json()["counts_by_status"] == {"needs_fix": 1, "hidden": 1}
        reviews_csv = client.get("/api/projects/default/lineage-reviews/export.csv")
        assert reviews_csv.status_code == 200
        assert "元データで成分値を確認する" in reviews_csv.text
        lineage = client.get("/api/projects/default/lineage/ME-00001")
        assert lineage.status_code == 200
        assert lineage.json()["review"]["status"] == "needs_fix"
        options_for_annealing = [
            option for option in lineage.json()["candidate_options"] if option["process_role"] == "annealing"
        ]
        assert len(options_for_annealing) == 2
        ambiguous = client.post("/api/projects/default/lineage/ME-00001/candidate")
        assert ambiguous.status_code == 422
        selected = options_for_annealing[0]
        created_from_selected_upstream = client.post(
            "/api/projects/default/lineage/ME-00001/candidate",
            params={
                "process_key": selected["process_key"],
                "melt_key": selected["melt_key"],
            },
        )
        assert created_from_selected_upstream.status_code == 201
        assert (
            created_from_selected_upstream.json()["provenance"]["source_ref"]["composition_entity_key"]
            == selected["melt_key"]
        )
        options = client.get("/api/project-creation-options").json()
        annealed_packages = [
            item for item in options["model_packages"] if item["task_id"] == "annealed-properties-v1"
        ]
        assert {item["package_id"] for item in annealed_packages} >= {
            "annealed-gp-stable-ard-process-v2",
            "annealed-lightgbm-standard-process-v2",
            "annealed-heteroscedastic-gp-process-v2",
            "annealed-hierarchical-bayes-process-v2",
        }
        dataset = next(
            item
            for item in options["datasets"]
            if "annealed-properties-v1" in item["supported_task_ids"]
            and item["data_asset"]["sha256"] == resources.runtimes["annealed-properties-v1"].data.source_sha256
        )
        dataset_view = dataset["dataset_views"][0]
        package_training_units = {
            "annealed-gp-stable-ard-process-v2": "parent_condition_mean",
            "annealed-lightgbm-standard-process-v2": "parent_condition_mean",
            "annealed-heteroscedastic-gp-process-v2": "individual_observation",
            "annealed-hierarchical-bayes-process-v2": "individual_observation",
        }
        for package_id, training_unit in package_training_units.items():
            package = next(item for item in annealed_packages if item["package_id"] == package_id)
            created = client.post(
                "/api/projects",
                json={
                    "name": package_id,
                    "description": "",
                    "purpose": "標準モデルと個々値モデルの確認",
                    "task_id": "annealed-properties-v1",
                    "target_values": {"TS": 500},
                    "notes": "",
                    "dataset_view_revision_id": dataset_view["id"],
                    "model_package_ref_id": package["id"],
                },
            )
            assert created.status_code == 201, created.text
            project_id = created.json()["id"]
            status = client.get(f"/api/projects/{project_id}/model-package")
            assert status.status_code == 200
            assert status.json()["id"] == package_id
            training = client.get(
                f"/api/projects/{project_id}/model-package/training-data",
                params={"stage": "features", "target": "TS", "limit": 1},
            )
            assert training.status_code == 200, training.text
            assert training.json()["training_unit"] == training_unit
            if training_unit == "individual_observation":
                assert training.json()["total"] > training.json()["parent_conditions"]
            else:
                assert training.json()["total"] == training.json()["parent_conditions"]
