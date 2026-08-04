import json
import os
from pathlib import Path
import sqlite3
import subprocess
import shutil

from fastapi.testclient import TestClient

from decision_workbench.app import create_app
from decision_workbench.bootstrap.startup import default_data_library_path
from decision_workbench.bootstrap.resources import AppResources
from decision_workbench.developer_experience.workspace_lifecycle import (
    checkout_identity,
)


def test_launch_token_protects_api_health_and_downloads(monkeypatch, tmp_path, app_resources: AppResources) -> None:
    monkeypatch.setenv("WORKBENCH_LAUNCH_TOKEN", "test-launch-token")
    app = create_app(db_path=tmp_path / "workbench.db", _resources=app_resources)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 401
        assert client.get("/api/projects").status_code == 401
        assert client.get("/api/projects/default/candidates/export.xlsx").status_code == 401
        assert client.get("/health", headers={"X-Workbench-Launch-Token": "wrong"}).status_code == 401

        headers = {
            "Origin": "null",
            "X-Workbench-Launch-Token": "test-launch-token",
        }
        preflight = client.options(
            "/api/projects",
            headers={
                "Origin": "null",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-Workbench-Launch-Token",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "null"
        health = client.get("/health", headers=headers)
        assert health.status_code == 200
        workspace = health.json()["workspace"]
        assert workspace["database_path"] == str((tmp_path / "workbench.db").resolve())
        assert workspace["data_library_path"] == str(
            (tmp_path / "data-library").resolve()
        )
        assert workspace["kind"] == "custom"
        assert workspace["id"] is None
        assert workspace["root_path"] is None
        assert workspace["storage_scope"] == "user-owned persistent storage"
        assert workspace["cleanup_policy"] == "自動削除しない"
        assert health.json()["storage"]["profile_store"]["path"]
        assert client.get("/api/projects", headers=headers).status_code == 200
        exported = client.get("/api/projects/default/candidates/export.xlsx", headers=headers)
        assert exported.status_code == 200
        assert exported.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )


def test_without_launch_token_only_non_browser_and_loopback_origins_are_allowed(
    monkeypatch, tmp_path, app_resources: AppResources
) -> None:
    monkeypatch.delenv("WORKBENCH_LAUNCH_TOKEN", raising=False)
    with TestClient(create_app(db_path=tmp_path / "workbench.db", _resources=app_resources)) as client:
        assert client.get("/health").status_code == 200
        assert client.get(
            "/api/projects", headers={"Origin": "http://127.0.0.1:5180"}
        ).status_code == 200
        assert client.get("/api/projects", headers={"Origin": "null"}).status_code == 403
        assert client.post(
            "/api/projects",
            headers={"Origin": "https://attacker.example"},
            json={},
        ).status_code == 403


def test_dev_launcher_uses_one_ephemeral_token_for_api_and_vite_proxy() -> None:
    root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment.pop("WORKBENCH_DB_PATH", None)
    environment.pop("WORKBENCH_DATA_LIBRARY_PATH", None)
    result = subprocess.run(
        ["node", "scripts/dev-launcher.mjs", "--check"],
        cwd=root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload == {
        "tokenBytes": 32,
        "apiProtected": True,
        "viteProxyProtected": True,
        "apiUrl": "http://127.0.0.1:8765",
        "webPort": "5180",
        "workspaceDatabase": payload["workspaceDatabase"],
        "workspaceDataLibrary": payload["workspaceDataLibrary"],
        "workspaceProfileStore": payload["workspaceProfileStore"],
        "workspaceTaskStore": payload["workspaceTaskStore"],
        "workspaceModelStore": payload["workspaceModelStore"],
        "workspaceRoot": payload["workspaceRoot"],
        "workspaceManifest": payload["workspaceManifest"],
        "workspaceId": payload["workspaceId"],
        "checkoutIdentity": payload["checkoutIdentity"],
        "workspaceSource": "branch-default",
    }
    database = Path(payload["workspaceDatabase"])
    data_library = Path(payload["workspaceDataLibrary"])
    workspace_root = Path(payload["workspaceRoot"])
    assert workspace_root.parent == root / ".dev-workspaces"
    assert database == workspace_root / "workspace.db"
    assert data_library == workspace_root / "data-library"
    assert Path(payload["workspaceProfileStore"]) == workspace_root / "profiles"
    task_store = Path(payload["workspaceTaskStore"])
    model_store = Path(payload["workspaceModelStore"])
    assert task_store == workspace_root / "tasks"
    assert model_store == workspace_root / "models"
    assert Path(payload["workspaceManifest"]) == workspace_root / "workspace-manifest.json"
    assert payload["checkoutIdentity"] == checkout_identity(root)
    assert payload["workspaceId"].endswith(f"-{checkout_identity(root)}")
    assert not workspace_root.exists()


def test_same_branch_in_two_clones_has_distinct_workspace_identity(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    clones = [tmp_path / "clone-a", tmp_path / "clone-b"]
    payloads = []
    for clone in clones:
        (clone / "scripts").mkdir(parents=True)
        shutil.copy2(
            root / "scripts" / "dev-workspace.mjs",
            clone / "scripts" / "dev-workspace.mjs",
        )
        shutil.copy2(
            root / "scripts" / "dev-launcher.mjs",
            clone / "scripts" / "dev-launcher.mjs",
        )
        subprocess.run(
            ["git", "init", "-b", "fixture-same-branch"],
            cwd=clone,
            check=True,
            capture_output=True,
            text=True,
        )
        result = subprocess.run(
            ["node", "scripts/dev-launcher.mjs", "--check"],
            cwd=clone,
            env={
                **os.environ,
                "WORKBENCH_DB_PATH": "",
                "WORKBENCH_DATA_LIBRARY_PATH": "",
            },
            check=True,
            capture_output=True,
            text=True,
        )
        payloads.append(json.loads(result.stdout))

    assert payloads[0]["workspaceId"] != payloads[1]["workspaceId"]
    for field in (
        "workspaceDatabase",
        "workspaceDataLibrary",
        "workspaceProfileStore",
        "workspaceTaskStore",
        "workspaceModelStore",
    ):
        assert payloads[0][field] != payloads[1][field]
    assert payloads[0]["workspaceSource"] == payloads[1]["workspaceSource"] == "branch-default"


def test_dev_launcher_respects_explicit_workspace_path(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    database = tmp_path / "review path" / "explicit workspace.db"
    environment = {
        **os.environ,
        "WORKBENCH_DB_PATH": str(database),
    }

    result = subprocess.run(
        ["node", "scripts/dev-launcher.mjs", "--check"],
        cwd=root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert Path(payload["workspaceDatabase"]) == database
    assert payload["workspaceSource"] == "environment"
    assert payload["workspaceProfileStore"] is None
    assert payload["workspaceRoot"] is None
    assert not database.exists()

    preflight = subprocess.run(
        ["node", "scripts/dev-launcher.mjs", "--preflight-only"],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert preflight.returncode == 0
    report = json.loads(preflight.stdout.splitlines()[-1])
    assert report["database"] == str(database.resolve())
    assert report["database_exists"] is False
    assert not database.exists()


def test_main_workspace_flag_ignores_environment_workspace_override(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    local_app_data = tmp_path / "user-owned-data"
    result = subprocess.run(
        ["node", "scripts/dev-launcher.mjs", "--check", "--main-workspace"],
        cwd=root,
        env={
            **os.environ,
            "LOCALAPPDATA": str(local_app_data),
            "WORKBENCH_DB_PATH": str(tmp_path / "wrong.db"),
            "WORKBENCH_DATA_LIBRARY_PATH": str(tmp_path / "wrong-library"),
        },
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(result.stdout)

    assert payload["workspaceSource"] == "main"
    assert payload["workspaceProfileStore"] is None
    assert payload["workspaceRoot"] is None
    assert Path(payload["workspaceDatabase"]) == root / "data" / "workbench.db"
    expected_library = local_app_data / "Material Decision Workbench" / "data-library"
    assert Path(payload["workspaceDataLibrary"]) == expected_library
    assert root not in expected_library.parents


def test_main_workspace_backend_defaults_to_a_user_owned_data_library(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    local_app_data = tmp_path / "user-owned-data"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    data_library = default_data_library_path(root / "data" / "workbench.db")

    assert data_library == (
        local_app_data / "Material Decision Workbench" / "data-library"
    ).resolve()
    assert root not in data_library.parents


def test_workspace_seed_refuses_environment_selected_workspace(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    database = tmp_path / "long-lived.db"
    result = subprocess.run(
        ["node", "scripts/workspace-seed.mjs", "--check"],
        cwd=root,
        env={
            **os.environ,
            "WORKBENCH_DB_PATH": str(database),
            "WORKBENCH_DATA_LIBRARY_PATH": str(tmp_path / "long-lived-library"),
        },
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 1
    assert "branch既定" in result.stderr
    assert not database.exists()

    library_only = subprocess.run(
        ["node", "scripts/workspace-seed.mjs", "--check"],
        cwd=root,
        env={
            key: value
            for key, value in {
                **os.environ,
                "WORKBENCH_DATA_LIBRARY_PATH": str(
                    tmp_path / "long-lived-library-only"
                ),
            }.items()
            if key != "WORKBENCH_DB_PATH"
        },
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert library_only.returncode == 1
    assert "branch既定" in library_only.stderr


def test_dev_launcher_preflight_stops_before_server_on_catalog_conflict(
    tmp_path: Path,
    app_resources: AppResources,
) -> None:
    root = Path(__file__).resolve().parents[2]
    database = tmp_path / "conflict.db"
    with TestClient(
        create_app(db_path=database, _resources=app_resources)
    ):
        pass
    with sqlite3.connect(database) as connection:
        package = connection.execute(
            "SELECT id FROM model_package_refs ORDER BY id LIMIT 1"
        ).fetchone()
        assert package is not None
        connection.execute(
            "UPDATE model_package_refs SET task_contract_digest=? WHERE id=?",
            ("sha256:" + "0" * 64, package[0]),
        )

    result = subprocess.run(
        ["node", "scripts/dev-launcher.mjs", "--preflight-only"],
        cwd=root,
        env={**os.environ, "WORKBENCH_DB_PATH": str(database)},
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert result.returncode == 1
    report = json.loads(result.stdout.splitlines()[-1])
    assert report["status"] == "error"
    assert report["read_only"] is True
    assert report["findings"][0]["stage"] == "catalog"
    assert report["findings"][0]["resource_id"]
    assert report["findings"][0]["registered_digest"] == "sha256:" + "0" * 64
    assert report["findings"][0]["current_digest"]
    assert report["findings"][0]["recovery_hint"]
