from pathlib import Path

from fastapi.testclient import TestClient

from material_workbench.app import _prepare_app_resources, create_app


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "source" / "process_dashboard_two_equipment_v8.xlsx"


def test_v8_registers_and_runs_both_individual_observation_packages(tmp_path: Path) -> None:
    resources = _prepare_app_resources(
        SOURCE,
        package_roots={
            "annealed-properties-v1": ROOT / "models" / "packages" / "annealed-gp-2026-07-v8-feature-design-v3-r2",
            "hot-rolled-properties-v1": ROOT / "models" / "packages" / "hot-rolled-horseshoe-2026-07-v8-feature-design-v3-r2",
        },
    )
    app = create_app(db_path=tmp_path / "workbench.db", _resources=resources)
    with TestClient(app) as client:
        options = client.get("/api/project-creation-options").json()
        annealed_packages = [
            item for item in options["model_packages"]
            if item["task_id"] == "annealed-properties-v1"
        ]
        assert {item["package_id"] for item in annealed_packages} >= {
            "annealed-gp-2026-07-v8-feature-design-v3-r2",
            "annealed-heteroscedastic-gp-2026-07-v8-v1",
            "annealed-hierarchical-bayes-2026-07-v8-v1",
        }
        dataset = next(
            item for item in options["datasets"]
            if "annealed-properties-v1" in item["supported_task_ids"]
            and item["data_asset"]["sha256"] == resources.runtimes["annealed-properties-v1"].data.source_sha256
        )
        dataset_view = dataset["dataset_views"][0]
        for package_id in (
            "annealed-heteroscedastic-gp-2026-07-v8-v1",
            "annealed-hierarchical-bayes-2026-07-v8-v1",
        ):
            package = next(item for item in annealed_packages if item["package_id"] == package_id)
            created = client.post("/api/projects", json={
                "name": package_id,
                "description": "",
                "purpose": "個々値モデル確認",
                "task_id": "annealed-properties-v1",
                "target_values": {"TS": 500},
                "notes": "",
                "dataset_view_revision_id": dataset_view["id"],
                "model_package_ref_id": package["id"],
            })
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
            assert training.json()["training_unit"] == "individual_observation"
            assert training.json()["total"] > training.json()["parent_conditions"]
