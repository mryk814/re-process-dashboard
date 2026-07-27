from __future__ import annotations

from pathlib import Path
import json

import pytest
from fastapi.testclient import TestClient

import material_workbench.app as app_module
from material_workbench.app import _AppResources, create_app
from material_workbench.contracts.subsystem_availability import (
    WELDING_CHAIN_EVALUATION_SUBSYSTEM_ID,
    WELDING_CHAIN_SUBSYSTEM_ID,
    WELDING_TRANSFORM_SUBSYSTEM_ID,
)
from material_workbench.modeling.model_packages import PackageContractError
from material_workbench.persistence.store import ChainCatalogConflictError
from material_workbench.persistence.welding_chain_bootstrap import (
    WeldingChainBootstrapError,
)


def _chain_project(client: TestClient, name: str = "degraded Chain") -> dict:
    template = next(
        item
        for item in client.get("/api/chains").json()
        if item["definition"]["chain_id"] == "welding-consumable-a-b-c-v1"
    )
    revision = template["revisions"][0]
    created = client.post(
        "/api/projects",
        json={
            "name": name,
            "scientific_identity": {
                "identity_kind": "chain",
                "chain_revision_id": (
                    f"{revision['chain_id']}:r{revision['revision']}"
                ),
                "chain_revision_digest": revision["revision_digest"],
            },
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_broken_chain_evaluation_is_isolated_and_structured(
    tmp_path: Path,
    app_resources: _AppResources,
) -> None:
    broken = tmp_path / "broken-evaluation.json"
    broken.write_text('{"schema_version":"broken"}', encoding="utf-8")
    app = create_app(
        db_path=tmp_path / "workbench.db",
        data_library_path=tmp_path / "data-library",
        chain_evaluation_path=broken,
        _resources=app_resources,
    )

    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["ready"] is True
        assert health.json()["degraded"] is True
        availability = {
            item["subsystem_id"]: item
            for item in client.get("/api/subsystem-availability").json()
        }
        evaluation = availability[WELDING_CHAIN_EVALUATION_SUBSYSTEM_ID]
        assert evaluation["status"] == "unavailable"
        assert evaluation["cause"].startswith("ValidationError:")
        assert evaluation["owner_kind"] == "chain"
        assert evaluation["owner_resource_id"] == "welding-consumable-a-b-c-v1"
        assert "段単体／通し評価" in evaluation["impact"]
        assert "評価JSON" in evaluation["recovery_hint"]
        assert availability[WELDING_CHAIN_SUBSYSTEM_ID]["status"] == "available"

        # An unrelated Project and its immutable evidence remain readable.
        assert client.get("/api/projects/default").status_code == 200
        assert client.get("/api/projects/default/history").status_code == 200

        chain_project = _chain_project(client)
        response = client.get(
            f"/api/projects/{chain_project['id']}/chain/evaluation"
        )
        assert response.status_code == 503
        payload = response.json()
        assert payload["code"] == "subsystem_unavailable"
        assert payload["availability"]["subsystem_id"] == (
            WELDING_CHAIN_EVALUATION_SUBSYSTEM_ID
        )

        diagnostics = client.get("/api/developer/diagnostics").json()
        optional = next(
            item
            for item in diagnostics["checks"]
            if item["id"] == "optional-subsystems"
        )
        assert optional["severity"] == "warning"
        diagnosed_evaluation = next(
            item
            for item in optional["details"]["items"]
            if item["subsystem_id"] == WELDING_CHAIN_EVALUATION_SUBSYSTEM_ID
        )
        assert evaluation["recovery_hint"] == diagnosed_evaluation["recovery_hint"]


def test_broken_transform_disables_only_dependent_chain(
    tmp_path: Path,
    app_resources: _AppResources,
) -> None:
    broken = tmp_path / "broken-active-transforms.json"
    broken.write_text("not-json", encoding="utf-8")
    app = create_app(
        db_path=tmp_path / "workbench.db",
        data_library_path=tmp_path / "data-library",
        active_transforms_path=broken,
        _resources=app_resources,
    )

    with TestClient(app) as client:
        availability = {
            item["subsystem_id"]: item
            for item in client.get("/api/subsystem-availability").json()
        }
        assert availability[WELDING_TRANSFORM_SUBSYSTEM_ID][
            "status"
        ] == "unavailable"
        assert availability[WELDING_CHAIN_SUBSYSTEM_ID]["status"] == "unavailable"
        assert availability[WELDING_CHAIN_SUBSYSTEM_ID]["cause"].startswith(
            "dependency_unavailable:"
        )
        assert client.get("/api/projects/default/history").status_code == 200

        transforms = client.get("/api/transforms")
        assert transforms.status_code == 503
        assert transforms.json()["availability"]["subsystem_id"] == (
            WELDING_TRANSFORM_SUBSYSTEM_ID
        )


def test_broken_transform_preserves_saved_chain_inputs_read_only(
    tmp_path: Path,
    app_resources: _AppResources,
) -> None:
    database = tmp_path / "workbench.db"
    data_library = tmp_path / "data-library"
    healthy_app = create_app(
        db_path=database,
        data_library_path=data_library,
        _resources=app_resources,
    )
    with TestClient(healthy_app) as client:
        project = _chain_project(client)
        contract = client.get(
            f"/api/projects/{project['id']}/chain/candidate-contract"
        ).json()
        candidate = client.post(
            f"/api/projects/{project['id']}/chain/candidates",
            json=contract["starter_candidate"],
        )
        assert candidate.status_code == 201, candidate.text
        candidate_id = candidate.json()["id"]

    broken = tmp_path / "broken-active-transforms.json"
    broken.write_text("not-json", encoding="utf-8")
    degraded_app = create_app(
        db_path=database,
        data_library_path=data_library,
        active_transforms_path=broken,
        _resources=app_resources,
    )
    with TestClient(degraded_app) as client:
        availability = {
            item["subsystem_id"]: item
            for item in client.get("/api/subsystem-availability").json()
        }
        assert availability[WELDING_CHAIN_SUBSYSTEM_ID]["status"] == "unavailable"
        inputs = client.get(
            f"/api/projects/{project['id']}/chain/candidate-inputs"
        )
        assert inputs.status_code == 200, inputs.text
        assert len(inputs.json()) == 9
        assert {
            item["external_path"] for item in inputs.json()
        } == {
            item["external_path"] for item in contract["external_inputs"]
        }
        candidates = client.get(
            f"/api/projects/{project['id']}/chain/candidates"
        )
        assert candidates.status_code == 200
        assert candidates.json()[0]["id"] == candidate_id
        editable_contract = client.get(
            f"/api/projects/{project['id']}/chain/candidate-contract"
        )
        assert editable_contract.status_code == 503


def test_broken_chain_bootstrap_preserves_saved_chain_evidence_read_only(
    tmp_path: Path,
    app_resources: _AppResources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "workbench.db"
    healthy_app = create_app(
        db_path=database,
        data_library_path=tmp_path / "data-library",
        _resources=app_resources,
    )
    with TestClient(healthy_app) as client:
        project = _chain_project(client)
        contract = client.get(
            f"/api/projects/{project['id']}/chain/candidate-contract"
        ).json()
        candidate = client.post(
            f"/api/projects/{project['id']}/chain/candidates",
            json=contract["starter_candidate"],
        )
        assert candidate.status_code == 201, candidate.text
        candidate_value = candidate.json()
        candidate_id = candidate_value["id"]
        execution = client.post(
            f"/api/projects/{project['id']}/chain/candidates/"
            f"{candidate_id}/executions",
            json={
                "candidate_revision": candidate_value["revision"],
                "request_id": "degraded-read-only-evidence",
                "debounce_ms": 0,
            },
        )
        assert execution.status_code == 200, execution.text
        snapshot = client.post(
            f"/api/projects/{project['id']}/chain/candidates/"
            f"{candidate_id}/snapshots",
            json={
                "candidate_revision": candidate_value["revision"],
                "debounce_ms": 0,
            },
        )
        assert snapshot.status_code == 201, snapshot.text
        snapshot_id = snapshot.json()["snapshot_id"]

    def broken_bootstrap(**_: object) -> str:
        raise WeldingChainBootstrapError("injected broken Chain binding")

    monkeypatch.setattr(app_module, "bootstrap_welding_chain", broken_bootstrap)
    degraded_app = create_app(
        db_path=database,
        data_library_path=tmp_path / "data-library",
        _resources=app_resources,
    )
    with TestClient(degraded_app) as client:
        availability = {
            item["subsystem_id"]: item
            for item in client.get("/api/subsystem-availability").json()
        }
        assert availability[WELDING_CHAIN_SUBSYSTEM_ID]["status"] == "unavailable"
        assert "injected broken Chain binding" in availability[
            WELDING_CHAIN_SUBSYSTEM_ID
        ]["cause"]

        history = client.get(f"/api/projects/{project['id']}/history")
        assert history.status_code == 200
        assert any(
            item["candidate"]["id"] == candidate_id
            for item in history.json()["candidates"]
        )
        candidates = client.get(
            f"/api/projects/{project['id']}/chain/candidates"
        )
        assert candidates.status_code == 200
        assert candidates.json()[0]["id"] == candidate_id
        candidate_contract = client.get(
            f"/api/projects/{project['id']}/chain/candidate-inputs"
        )
        assert candidate_contract.status_code == 200, candidate_contract.text
        assert len(candidate_contract.json()) == 9
        saved_execution = client.get(
            f"/api/projects/{project['id']}/chain/candidates/"
            f"{candidate_id}/execution"
        )
        assert saved_execution.status_code == 200
        assert saved_execution.json()["request_id"] == "degraded-read-only-evidence"
        snapshots = client.get(
            f"/api/projects/{project['id']}/chain/candidates/"
            f"{candidate_id}/snapshots"
        )
        assert snapshots.status_code == 200
        assert snapshots.json()[0]["snapshot_id"] == snapshot_id

        capability = client.get(
            f"/api/projects/{project['id']}/chain/candidate-capability"
        )
        assert capability.status_code == 503
        assert capability.json()["availability"]["subsystem_id"] == (
            WELDING_CHAIN_SUBSYSTEM_ID
        )


def test_database_boundary_remains_fail_fast(
    tmp_path: Path,
    app_resources: _AppResources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_store(_: Path) -> object:
        raise RuntimeError("injected schema failure")

    monkeypatch.setattr(app_module, "Store", broken_store)
    app = create_app(
        db_path=tmp_path / "workbench.db",
        data_library_path=tmp_path / "data-library",
        _resources=app_resources,
    )
    with pytest.raises(RuntimeError, match="injected schema failure"):
        with TestClient(app):
            pass


@pytest.mark.parametrize(
    "failure",
    [
        PackageContractError("locator escapes models root"),
        PackageContractError("artifact digest mismatch"),
    ],
)
def test_transform_security_boundary_remains_fail_fast(
    tmp_path: Path,
    app_resources: _AppResources,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    def broken_transform_catalog(_: Path) -> object:
        raise failure

    monkeypatch.setattr(
        app_module,
        "load_deterministic_transform_catalog",
        broken_transform_catalog,
    )
    app = create_app(
        db_path=tmp_path / "workbench.db",
        data_library_path=tmp_path / "data-library",
        _resources=app_resources,
    )
    with pytest.raises(PackageContractError, match=str(failure)):
        with TestClient(app):
            pass


def test_transform_locator_escape_remains_fail_fast_through_real_loader(
    tmp_path: Path,
    app_resources: _AppResources,
) -> None:
    unsafe = tmp_path / "unsafe-active-transforms.json"
    unsafe.write_text(
        json.dumps(
            {
                "schema_version": "active-deterministic-transforms/v1",
                "transforms": {
                    "welding-stage-a-v1": {
                        "active": "../outside",
                        "available": ["../outside"],
                        "commercial_catalog": "catalog.json",
                        "available_commercial_catalogs": ["catalog.json"],
                        "design_space": "design-space.json",
                        "available_design_spaces": ["design-space.json"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    app = create_app(
        db_path=tmp_path / "workbench.db",
        data_library_path=tmp_path / "data-library",
        active_transforms_path=unsafe,
        _resources=app_resources,
    )
    with pytest.raises(
        PackageContractError,
        match="unsafe active deterministic transform locator",
    ):
        with TestClient(app):
            pass


def test_chain_catalog_conflict_remains_fail_fast(
    tmp_path: Path,
    app_resources: _AppResources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def conflicting_bootstrap(**_: object) -> str:
        raise ChainCatalogConflictError("immutable Chain digest conflict")

    monkeypatch.setattr(app_module, "bootstrap_welding_chain", conflicting_bootstrap)
    app = create_app(
        db_path=tmp_path / "workbench.db",
        data_library_path=tmp_path / "data-library",
        _resources=app_resources,
    )
    with pytest.raises(
        ChainCatalogConflictError,
        match="immutable Chain digest conflict",
    ):
        with TestClient(app):
            pass
