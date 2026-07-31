from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from decision_workbench.contracts.chain_contracts import (
    ChainDefinition,
    ChainRevision,
    task_contract_surface,
)
from decision_workbench.persistence.store import Store
from decision_workbench.persistence.welding_chain_bootstrap import (
    WELDING_CHAIN_ID,
    bootstrap_welding_chain,
)
from decision_workbench.application.workspace_catalog_bootstrap import (
    bootstrap_workspace_catalog,
)


SHA_D = "sha256:" + "d" * 64


def _bundled_chain(client: TestClient) -> tuple[ChainDefinition, ChainRevision]:
    revision = client.app.state.store.get_chain_revision(
        f"{WELDING_CHAIN_ID}:r1"
    )
    assert revision is not None
    definition = client.app.state.store.get_chain_definition(
        WELDING_CHAIN_ID,
        revision.chain_definition_digest,
    )
    assert definition is not None
    return definition, revision


def test_chain_catalog_and_project_creation_pin_exact_revision(
    client: TestClient,
) -> None:
    definition, revision = _bundled_chain(client)

    catalog = client.get("/api/chains")
    assert catalog.status_code == 200
    item = next(
        item
        for item in catalog.json()
        if item["definition"]["chain_id"] == definition.chain_id
    )
    assert item["definition"]["chain_id"] == definition.chain_id
    assert (
        item["revisions"][0]["revision_digest"]
        == revision.revision_digest
    )

    created = client.post(
        "/api/projects",
        json={
            "name": "Chain Project",
            "scientific_identity": {
                "identity_kind": "chain",
                "chain_revision_id": f"{definition.chain_id}:r1",
                "chain_revision_digest": revision.revision_digest,
            },
        },
    )
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["task_id"] == ""
    assert project["project_series_id"] is None
    assert project["scientific_identity"] == {
        "identity_kind": "chain",
        "chain_revision_id": f"{definition.chain_id}:r1",
        "chain_revision_digest": revision.revision_digest,
    }
    reopened = client.get(f"/api/projects/{project['id']}")
    assert reopened.status_code == 200
    assert reopened.json()["scientific_identity"] == project["scientific_identity"]

    grouped = client.post(
        "/api/projects",
        json={
            "name": "Grouped Chain Project",
            "scientific_identity": {
                "identity_kind": "chain",
                "chain_revision_id": f"{definition.chain_id}:r1",
                "chain_revision_digest": revision.revision_digest,
            },
            "new_project_series": {
                "name": "Chainの一連検討",
                "description": "",
            },
        },
    )
    assert grouped.status_code == 201, grouped.text
    assert grouped.json()["project_series_id"] is not None

    database = client.app.state.store.path
    bootstrap_workspace_catalog(database, client.app.state.task_registry)
    reopened_store = Store(database)
    assert (
        reopened_store.get_project(project["id"]).scientific_identity
        == client.app.state.store.get_project(project["id"]).scientific_identity
    )


def test_bundled_welding_chain_pins_real_a_b_c_resources(
    client: TestClient,
) -> None:
    catalog = client.get("/api/chains")
    assert catalog.status_code == 200
    item = next(
        item
        for item in catalog.json()
        if item["definition"]["chain_id"] == "welding-consumable-a-b-c-v1"
    )
    definition = item["definition"]
    revision = item["revisions"][0]
    assert [stage["stage_id"] for stage in definition["stages"]] == ["A", "B", "C"]
    assert [stage["stage_kind"] for stage in definition["stages"]] == [
        "deterministic_transform",
        "task",
        "task",
    ]
    assert len(definition["bindings"]) == 58
    assert revision["revision"] == 1
    assert all(stage["package_manifest_digest"].startswith("sha256:") for stage in revision["stages"])
    assert revision["stages"][0]["dataset_view_revision_id"] is None
    assert all(stage["dataset_view_revision_id"] for stage in revision["stages"][1:])
    stage_c_definition = client.app.state.task_registry.contract_for(
        "welding-stage-c-properties-v1"
    ).task_definition
    stage_c_surface = task_contract_surface(
        stage_c_definition,
        contract_digest=revision["stages"][2]["contract_digest"],
    )
    composition_ports = [
        port for port in stage_c_surface.input_ports
        if port.path.startswith("composition.")
    ]
    assert len(composition_ports) == 16
    assert {port.basis for port in composition_ports} == {"deposited_metal"}

    stage_b_view = client.app.state.workspace_catalog.get_dataset_view_revision(
        revision["stages"][1]["dataset_view_revision_id"]
    )
    assert stage_b_view is not None
    client.app.state.workspace_catalog.ensure_single_dataset_view(
        stage_b_view.members[0].dataset_revision_id,
        name="ユーザー追加View",
        view_id="user-added-stage-b-view",
    )
    assert bootstrap_welding_chain(
        store=client.app.state.store,
        workspace_catalog=client.app.state.workspace_catalog,
        task_registry=client.app.state.task_registry,
        transform_catalog=client.app.state.deterministic_transform_catalog,
    ) == "welding-consumable-a-b-c-v1:r1"


def test_chain_project_rejects_revision_digest_drift(client: TestClient) -> None:
    definition, _revision = _bundled_chain(client)
    response = client.post(
        "/api/projects",
        json={
            "name": "drift",
            "scientific_identity": {
                "identity_kind": "chain",
                "chain_revision_id": f"{definition.chain_id}:r1",
                "chain_revision_digest": SHA_D,
            },
        },
    )
    assert response.status_code == 422
    assert "digest" in json.dumps(response.json(), ensure_ascii=False)


def test_chain_project_rejects_terminal_task_contract_drift(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition, revision = _bundled_chain(client)
    registry = client.app.state.task_registry
    terminal_task_id = [
        stage.contract_id
        for stage in revision.stages
        if stage.stage_kind == "task"
    ][-1]
    fixture = registry._contracts[terminal_task_id]  # noqa: SLF001
    drifted_definition = fixture.task_definition.model_copy(
        update={"label": fixture.task_definition.label + " drift"}
    )
    monkeypatch.setitem(
        registry._contracts,  # noqa: SLF001
        terminal_task_id,
        fixture.model_copy(update={"task_definition": drifted_definition}),
    )

    response = client.post(
        "/api/projects",
        json={
            "name": "contract drift",
            "scientific_identity": {
                "identity_kind": "chain",
                "chain_revision_id": f"{definition.chain_id}:r1",
                "chain_revision_digest": revision.revision_digest,
            },
        },
    )

    assert response.status_code == 422
    assert "Chain終端Taskのcontract digest" in json.dumps(
        response.json(),
        ensure_ascii=False,
    )


def test_chain_project_rejects_conflicting_legacy_fields_and_manual_seed(
    client: TestClient,
) -> None:
    definition, revision = _bundled_chain(client)
    identity = {
        "identity_kind": "chain",
        "chain_revision_id": f"{definition.chain_id}:r1",
        "chain_revision_digest": revision.revision_digest,
    }
    conflicting = client.post(
        "/api/projects",
        json={
            "name": "conflict",
            "task_id": "annealed-properties-v1",
            "scientific_identity": identity,
        },
    )
    assert conflicting.status_code == 422

    manual = client.post(
        "/api/projects",
        json={
            "name": "manual",
            "scientific_identity": identity,
            "initial_candidate": {
                "name": "不正な手入力",
                "inputs": {
                    "composition": {},
                    "process": {},
                    "categorical": {},
                    "heat_pattern": None,
                },
                "provenance": {"source_kind": "manual", "source_ref": None},
            },
        },
    )
    assert manual.status_code == 422
    assert "コピー由来" in json.dumps(manual.json(), ensure_ascii=False)
