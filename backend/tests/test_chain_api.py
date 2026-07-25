from __future__ import annotations

import json

from fastapi.testclient import TestClient

from material_workbench.contracts.chain_contracts import (
    ChainBinding,
    ChainDefinition,
    ChainPort,
    ChainRevision,
    ChainStage,
    ChainStageLock,
    ExternalBindingSource,
    StageContractSurface,
    build_chain_revision,
    task_contract_surface,
)
from material_workbench.persistence.store import Store
from material_workbench.persistence.welding_chain_bootstrap import (
    bootstrap_welding_chain,
)
from material_workbench.persistence.workspace_catalog_bootstrap import (
    bootstrap_workspace_catalog,
)


SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
SHA_C = "sha256:" + "c" * 64
SHA_D = "sha256:" + "d" * 64


def _register_chain(client: TestClient) -> tuple[ChainDefinition, ChainRevision]:
    definition = ChainDefinition(
        chain_id="annealed-chain-test",
        label="固定Revision確認用",
        stages=(
            ChainStage(
                stage_id="C",
                stage_kind="task",
                contract_id="annealed-properties-v1",
            ),
        ),
        external_inputs=(
            ChainPort(
                path="candidate.C",
                value_kind="number",
                quantity="C",
                unit="mass%",
            ),
        ),
        bindings=(
            ChainBinding(
                target_stage_id="C",
                target_input_path="composition.C",
                source=ExternalBindingSource(
                    source_kind="external",
                    path="candidate.C",
                ),
            ),
        ),
    )
    surface = StageContractSurface(
        stage_kind="task",
        contract_id="annealed-properties-v1",
        contract_digest=SHA_C,
        input_ports=(
            ChainPort(
                path="composition.C",
                value_kind="number",
                quantity="C",
                unit="mass%",
            ),
        ),
        output_ports=(
            ChainPort(
                path="TS",
                value_kind="number",
                quantity="TS",
                unit="MPa",
            ),
        ),
    )
    revision = build_chain_revision(
        definition,
        revision=1,
        contracts={(surface.stage_kind, surface.contract_id): surface},
        stage_locks={
            "C": ChainStageLock(
                contract_digest=SHA_C,
                package_manifest_digest=SHA_D,
                dataset_view_revision_id="view-r1",
                dataset_profile_digest=SHA_A,
            )
        },
    )
    client.app.state.store.register_chain_definition(definition)
    client.app.state.store.register_chain_revision(
        revision,
        contracts={(surface.stage_kind, surface.contract_id): surface},
    )
    return definition, revision


def test_chain_catalog_and_project_creation_pin_exact_revision(
    client: TestClient,
) -> None:
    definition, revision = _register_chain(client)

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
    assert project["scientific_identity"] == {
        "identity_kind": "chain",
        "chain_revision_id": f"{definition.chain_id}:r1",
        "chain_revision_digest": revision.revision_digest,
    }
    reopened = client.get(f"/api/projects/{project['id']}")
    assert reopened.status_code == 200
    assert reopened.json()["scientific_identity"] == project["scientific_identity"]

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
    definition, _revision = _register_chain(client)
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


def test_chain_project_rejects_conflicting_legacy_fields_and_manual_seed(
    client: TestClient,
) -> None:
    definition, revision = _register_chain(client)
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
