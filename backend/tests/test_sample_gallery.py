from fastapi.testclient import TestClient

from material_workbench.app import create_app
from material_workbench.contracts.candidate_project_contracts import CandidateInput


FEATURED_GALLERY_PROJECT_IDS = {
    "battery-degradation-v1-default",
    "mpea-room-tensile-v1-default",
    "welding-stage-b-default",
}


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
        assert {item["project_id"] for item in gallery} == FEATURED_GALLERY_PROJECT_IDS
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

        installed_gallery = client.get("/api/sample-gallery").json()
        installed_item = next(
            item for item in installed_gallery
            if item["project_id"] == selected["project_id"]
        )
        assert installed_item["removable"] is True
        assert installed_item["remove_blocked_reason"] == ""

        own_project = client.post(
            "/api/projects",
            json={
                "name": "同じDatasetを使う自分の検討",
                "task_id": bound["task_id"],
                "dataset_view_revision_id": bound["dataset_view_revision_id"],
                "task_contract_digest": bound["task_contract_digest"],
                "model_package_ref_id": bound["model_package_ref_id"],
                "model_package_manifest_digest": (
                    bound["model_package_manifest_digest"]
                ),
            },
        )
        assert own_project.status_code == 201

        removed = client.delete(
            f"/api/sample-gallery/{selected['project_id']}"
        )
        assert removed.status_code == 204
        assert client.get(
            f"/api/projects/{selected['project_id']}",
        ).status_code == 404
        assert client.get(f"/api/projects/{own_project.json()['id']}").status_code == 200
        assert any(
            selected["task_id"] in item["supported_task_ids"]
            for item in client.get("/api/data-library/datasets").json()
        )
        assert next(
            item for item in client.get("/api/sample-gallery").json()
            if item["project_id"] == selected["project_id"]
        )["installed"] is False

        repeated = client.post(
            "/api/sample-gallery",
            json={"project_ids": [selected["project_id"]]},
        )
        assert repeated.status_code == 200
        assert len(client.get("/api/projects").json()) == 3

        candidate = client.get(
            f"/api/projects/{selected['project_id']}/candidates"
        ).json()[0]
        snapshot = client.post(
            f"/api/projects/{selected['project_id']}/candidates/"
            f"{candidate['id']}/snapshots"
        )
        assert snapshot.status_code == 201
        protected = client.delete(
            f"/api/sample-gallery/{selected['project_id']}"
        )
        assert protected.status_code == 409
        assert protected.json()["code"] == "sample_has_saved_work"
        protected_item = next(
            item for item in client.get("/api/sample-gallery").json()
            if item["project_id"] == selected["project_id"]
        )
        assert protected_item["removable"] is False
        assert "保存した予測" in protected_item["remove_blocked_reason"]


def test_installed_legacy_sample_can_be_removed_but_not_reinstalled(
    tmp_path,
    app_resources,
    monkeypatch,
) -> None:
    monkeypatch.setenv("WORKBENCH_DEMO_SEED", "all")
    legacy_project_id = "hot-rolling-default"
    with TestClient(
        create_app(
            db_path=tmp_path / "workbench.db",
            data_library_path=tmp_path / "data-library",
            _resources=app_resources,
        )
    ) as client:
        item = next(
            item for item in client.get("/api/sample-gallery").json()
            if item["project_id"] == legacy_project_id
        )
        assert item["installed"] is True
        assert item["removable"] is True

        assert client.delete(
            f"/api/sample-gallery/{legacy_project_id}"
        ).status_code == 204
        assert legacy_project_id not in {
            item["project_id"]
            for item in client.get("/api/sample-gallery").json()
        }
        rejected = client.post(
            "/api/sample-gallery",
            json={"project_ids": [legacy_project_id]},
        )
        assert rejected.status_code == 409
        assert rejected.json()["code"] == "validation_error"


def test_sample_removal_is_atomic_when_a_successor_blocks_purge(
    tmp_path,
    app_resources,
) -> None:
    with TestClient(
        create_app(
            db_path=tmp_path / "workbench.db",
            data_library_path=tmp_path / "data-library",
            _resources=app_resources,
        )
    ) as client:
        project_id = "battery-degradation-v1-default"
        assert client.post(
            "/api/sample-gallery",
            json={"project_ids": [project_id]},
        ).status_code == 200
        project = client.get(f"/api/projects/{project_id}").json()
        successor = client.post(
            "/api/projects",
            json={
                "name": "サンプルから続ける検討",
                "task_id": project["task_id"],
                "dataset_view_revision_id": project["dataset_view_revision_id"],
                "task_contract_digest": project["task_contract_digest"],
                "model_package_ref_id": project["model_package_ref_id"],
                "model_package_manifest_digest": (
                    project["model_package_manifest_digest"]
                ),
                "predecessor_project_id": project_id,
            },
        )
        assert successor.status_code == 201

        rejected = client.delete(f"/api/sample-gallery/{project_id}")
        assert rejected.status_code == 409
        assert rejected.json()["code"] == "sample_has_saved_work"
        active = client.get(f"/api/projects/{project_id}")
        assert active.status_code == 200
        assert active.json()["archived_at"] is None


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


def test_sample_removal_rejects_an_edited_candidate(
    tmp_path,
    app_resources,
) -> None:
    with TestClient(
        create_app(
            db_path=tmp_path / "workbench.db",
            data_library_path=tmp_path / "data-library",
            _resources=app_resources,
        )
    ) as client:
        selected = next(
            item for item in client.get("/api/sample-gallery").json()
            if item["available"]
        )
        assert client.post(
            "/api/sample-gallery",
            json={"project_ids": [selected["project_id"]]},
        ).status_code == 200
        candidate = client.get(
            f"/api/projects/{selected['project_id']}/candidates"
        ).json()[0]
        payload = CandidateInput.model_validate(candidate).model_dump(mode="json")
        payload["name"] = f"{candidate['name']} 編集"
        payload["expected_revision"] = candidate["revision"]
        updated = client.put(
            f"/api/projects/{selected['project_id']}/candidates/{candidate['id']}",
            json=payload,
        )
        assert updated.status_code == 200

        removed = client.delete(
            f"/api/sample-gallery/{selected['project_id']}"
        )
        assert removed.status_code == 409
        assert "候補が編集" in removed.json()["message"]
