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


def _scalar_studio_definition(client: TestClient, *, label: str = "scalar Studio test") -> dict:
    catalog = client.get("/api/chains/studio/catalog")
    assert catalog.status_code == 200, catalog.text
    stages = {item["contract_id"]: item for item in catalog.json()["stages"]}
    stage_b = stages["welding-consumable-stage-b-v1"]
    # The same Task is a legitimate scalar Chain case: C emitted by the first
    # prediction is consumed as a typed input by the second.  B→C is not used
    # here because its basis changes and must not be hidden by a conversion.
    stage_c = stage_b
    assert stage_b["status"] == stage_c["status"] == "available"
    external_inputs: dict[str, dict] = {}
    bindings: list[dict] = []
    outputs = {port["path"]: port for port in stage_b["surface"]["output_ports"]}
    for stage_id, surface in (("B1", stage_b["surface"]), ("B2", stage_c["surface"])):
        for target in surface["input_ports"]:
            source_output = outputs.get(target["path"]) if stage_id == "B2" else None
            if source_output is not None:
                bindings.append({
                    "target_stage_id": stage_id,
                    "target_input_path": target["path"],
                    "source": {
                        "source_kind": "stage_output",
                        "stage_id": "B1",
                        "output_key": target["path"],
                    },
                })
                continue
            path = f"candidate.{target['path']}"
            external_inputs.setdefault(path, {**target, "path": path})
            bindings.append({
                "target_stage_id": stage_id,
                "target_input_path": target["path"],
                "source": {"source_kind": "external", "path": path},
            })
    return {
        "chain_id": "scalar-studio-test",
        "label": label,
        "stages": [
            {"stage_id": "B1", "stage_kind": "task", "contract_id": stage_b["contract_id"]},
            {"stage_id": "B2", "stage_kind": "task", "contract_id": stage_c["contract_id"]},
        ],
        "external_inputs": list(external_inputs.values()),
        "bindings": bindings,
    }


def _scalar_b_to_c_definition(client: TestClient) -> dict:
    catalog = client.get("/api/chains/studio/catalog")
    assert catalog.status_code == 200, catalog.text
    stages = {item["contract_id"]: item for item in catalog.json()["stages"]}
    stage_b = stages["welding-consumable-stage-b-v1"]["surface"]
    stage_c = stages["welding-stage-c-properties-v1"]["surface"]
    outputs = {port["quantity"]: port for port in stage_b["output_ports"]}
    external_inputs: dict[str, dict] = {}
    bindings: list[dict] = []
    for stage_id, surface in (("B", stage_b), ("C", stage_c)):
        for target in surface["input_ports"]:
            upstream = outputs.get(target["quantity"]) if stage_id == "C" else None
            if (
                upstream is not None
                and upstream["value_kind"] == target["value_kind"]
                and upstream["unit"] == target["unit"]
                and upstream["basis"] == target["basis"]
            ):
                bindings.append({
                    "target_stage_id": stage_id,
                    "target_input_path": target["path"],
                    "source": {
                        "source_kind": "stage_output",
                        "stage_id": "B",
                        "output_key": upstream["path"],
                    },
                })
            else:
                path = f"candidate.{target['path']}"
                external_inputs[path] = {**target, "path": path}
                bindings.append({
                    "target_stage_id": stage_id,
                    "target_input_path": target["path"],
                    "source": {"source_kind": "external", "path": path},
                })
    return {
        "chain_id": "scalar-studio-b-c",
        "label": "scalar B → C",
        "stages": [
            {
                "stage_id": "B",
                "stage_kind": "task",
                "contract_id": "welding-consumable-stage-b-v1",
            },
            {
                "stage_id": "C",
                "stage_kind": "task",
                "contract_id": "welding-stage-c-properties-v1",
            },
        ],
        "external_inputs": list(external_inputs.values()),
        "bindings": bindings,
    }


def test_scalar_chain_studio_validates_publishes_and_pins_server_catalog(
    client: TestClient,
) -> None:
    definition = _scalar_studio_definition(client)
    validated = client.post("/api/chains/studio/validate", json={"definition": definition})
    assert validated.status_code == 200, validated.text
    assert validated.json()["valid"] is True

    published = client.post("/api/chains/studio/publish", json={"definition": definition})
    assert published.status_code == 201, published.text
    revision = published.json()["revisions"][0]
    assert revision["revision"] == 1
    assert all(stage["dataset_view_revision_id"] for stage in revision["stages"])
    assert all(stage["package_manifest_digest"].startswith("sha256:") for stage in revision["stages"])

    # A published revision is immutable; an edited draft becomes the next revision.
    definition["label"] = "scalar Studio test revised"
    republished = client.post("/api/chains/studio/publish", json={"definition": definition})
    assert republished.status_code == 201, republished.text
    assert republished.json()["revisions"][0]["revision"] == 2


def test_scalar_chain_studio_fails_closed_for_non_scalar_external_path(
    client: TestClient,
) -> None:
    definition = _scalar_studio_definition(client)
    definition["external_inputs"][0]["path"] = "arbitrary.value"
    for binding in definition["bindings"]:
        if binding["source"]["source_kind"] == "external":
            binding["source"]["path"] = "arbitrary.value"
            break
    response = client.post("/api/chains/studio/validate", json={"definition": definition})
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_published_scalar_chain_starter_partial_execution_and_snapshot(
    client: TestClient,
) -> None:
    published = client.post(
        "/api/chains/studio/publish",
        json={"definition": _scalar_b_to_c_definition(client)},
    )
    assert published.status_code == 201, published.text
    revision = published.json()["revisions"][0]
    project_response = client.post(
        "/api/projects",
        json={
            "name": "scalar Studio execution",
            "scientific_identity": {
                "identity_kind": "chain",
                "chain_revision_id": "scalar-studio-b-c:r1",
                "chain_revision_digest": revision["revision_digest"],
            },
        },
    )
    assert project_response.status_code == 201, project_response.text
    project_id = project_response.json()["id"]

    capability = client.get(
        f"/api/projects/{project_id}/chain/candidate-capability"
    )
    assert capability.status_code == 200, capability.text
    assert capability.json()["adapter_id"] == "scalar/v1"
    assert capability.json()["sparse_blend"] is False

    starter = client.get(
        f"/api/projects/{project_id}/chain/starter-candidate"
    )
    assert starter.status_code == 200, starter.text
    assert starter.json()["blend"] is None
    candidate_response = client.post(
        f"/api/projects/{project_id}/chain/candidates",
        json=starter.json(),
    )
    assert candidate_response.status_code == 201, candidate_response.text
    candidate = candidate_response.json()

    first_execution = client.post(
        f"/api/projects/{project_id}/chain/candidates/{candidate['id']}/executions",
        json={"candidate_revision": 1, "request_id": "scalar-first", "debounce_ms": 0},
    )
    assert first_execution.status_code == 200, first_execution.text
    assert [stage["status"] for stage in first_execution.json()["stages"]] == [
        "latest",
        "latest",
    ]
    first_b_digest = first_execution.json()["stages"][0]["result_input_digest"]

    updated_payload = starter.json()
    updated_payload["inputs"]["process"]["test_temperature_c"] += 1
    updated_payload["expected_revision"] = 1
    updated = client.put(
        f"/api/projects/{project_id}/chain/candidates/{candidate['id']}",
        json=updated_payload,
    )
    assert updated.status_code == 200, updated.text
    stale = client.get(
        f"/api/projects/{project_id}/chain/candidates/{candidate['id']}/execution"
    )
    assert stale.status_code == 200, stale.text
    assert [stage["status"] for stage in stale.json()["stages"]] == [
        "latest",
        "stale",
    ]

    recomputed = client.post(
        f"/api/projects/{project_id}/chain/candidates/{candidate['id']}/executions",
        json={
            "candidate_revision": updated.json()["revision"],
            "request_id": "scalar-second",
            "debounce_ms": 0,
        },
    )
    assert recomputed.status_code == 200, recomputed.text
    assert recomputed.json()["stages"][0]["result_input_digest"] == first_b_digest
    assert recomputed.json()["stages"][1]["status"] == "latest"

    snapshot = client.post(
        f"/api/projects/{project_id}/chain/candidates/{candidate['id']}/snapshots",
        json={"candidate_revision": updated.json()["revision"], "debounce_ms": 0},
    )
    assert snapshot.status_code == 201, snapshot.text
    assert snapshot.json()["identity"]["candidate_adapter_id"] == "scalar/v1"
    assert snapshot.json()["identity"]["domain_references"] == []


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

    graph = client.get(f"/api/projects/{project['id']}/chain/graph")
    assert graph.status_code == 200, graph.text
    graph_body = graph.json()
    assert graph_body["revision"]["revision_digest"] == revision.revision_digest
    assert graph_body["definition"]["chain_id"] == definition.chain_id
    assert [item["stage_id"] for item in graph_body["stage_contracts"]] == [
        stage.stage_id for stage in revision.stages
    ]
    assert all(item["status"] == "available" for item in graph_body["stage_contracts"])
    task_surface = next(
        item["surface"] for item in graph_body["stage_contracts"]
        if item["surface"]["stage_kind"] == "task"
    )
    assert task_surface["input_ports"]
    assert task_surface["output_ports"]
    assert {"path", "value_kind", "quantity", "basis", "unit"} <= set(
        task_surface["input_ports"][0]
    )

    missing_stage = revision.stages[0].stage_id
    with client.app.state.store._connect() as conn:  # noqa: SLF001 - legacy surface fixture
        conn.execute(
            "DELETE FROM chain_stage_contract_surfaces "
            "WHERE chain_revision_id=? AND stage_id=?",
            (f"{definition.chain_id}:r1", missing_stage),
        )
    degraded = client.get(f"/api/projects/{project['id']}/chain/graph")
    assert degraded.status_code == 200, degraded.text
    missing = next(
        item for item in degraded.json()["stage_contracts"]
        if item["stage_id"] == missing_stage
    )
    assert missing == {
        "stage_id": missing_stage,
        "status": "unavailable",
        "reason": "この固定RevisionのStage contract surfaceは保存されていません",
        "surface": None,
    }

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
