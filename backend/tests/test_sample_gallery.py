from fastapi.testclient import TestClient

from material_workbench.app import create_app


def test_fresh_workspace_starts_with_quickstart_and_installs_gallery(
    tmp_path,
    app_resources,
) -> None:
    database = tmp_path / "workbench.db"
    with TestClient(
        create_app(
            db_path=database,
            data_library_path=tmp_path / "data-library",
            _resources=app_resources,
        )
    ) as client:
        projects = client.get("/api/projects").json()
        assert [(project["id"], project["starter"]) for project in projects] == [
            ("default", True)
        ]
        initial_datasets = client.get("/api/data-library/datasets").json()
        assert {
            item["data_asset"]["original_filename"]
            for item in initial_datasets
        } == {"material_workbench_tutorial_v2.xlsx"}

        gallery = client.get("/api/sample-gallery").json()
        assert len(gallery) == 10
        assert all(not item["installed"] for item in gallery)
        selected = next(item for item in gallery if item["available"])

        installed = client.post(
            "/api/sample-gallery",
            json={"project_ids": [selected["project_id"]]},
        )
        assert installed.status_code == 200
        assert [project["id"] for project in installed.json()] == [
            selected["project_id"]
        ]
        assert client.get(
            f"/api/projects/{selected['project_id']}/candidates"
        ).json()
        bound = client.get(f"/api/projects/{selected['project_id']}").json()
        assert bound["dataset_view_revision_id"]
        assert bound["model_package_ref_id"]
        visible_datasets = client.get("/api/data-library/datasets").json()
        assert any(
            selected["task_id"] in item["supported_task_ids"]
            for item in visible_datasets
        )

        repeated = client.post(
            "/api/sample-gallery",
            json={"project_ids": [selected["project_id"]]},
        )
        assert repeated.status_code == 200
        assert len(client.get("/api/projects").json()) == 2


def test_existing_workspace_does_not_reinstall_removed_gallery_projects(
    tmp_path,
    app_resources,
) -> None:
    database = tmp_path / "workbench.db"
    data_library = tmp_path / "data-library"
    with TestClient(
        create_app(
            db_path=database,
            data_library_path=data_library,
            _resources=app_resources,
        )
    ) as client:
        assert [item["id"] for item in client.get("/api/projects").json()] == [
            "default"
        ]

    with TestClient(
        create_app(
            db_path=database,
            data_library_path=data_library,
            _resources=app_resources,
        )
    ) as client:
        assert [item["id"] for item in client.get("/api/projects").json()] == [
            "default"
        ]
