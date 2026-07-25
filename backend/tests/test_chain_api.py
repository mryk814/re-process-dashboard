from __future__ import annotations

import json

from fastapi.testclient import TestClient

from material_workbench.contracts.chain_contracts import (
    ChainBinding,
    ChainDefinition,
    ChainPort,
    ChainRevision,
    ChainStage,
    ChainStageRevision,
    ExternalBindingSource,
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
            ChainPort(path="candidate.C", value_kind="number", unit="mass%"),
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
    revision_payload = {
        "schema_version": "chain-revision/v1",
        "chain_id": definition.chain_id,
        "revision": 1,
        "chain_definition_digest": definition.digest,
        "binding_digest": SHA_A,
        "unit_conversion_digest": SHA_B,
        "stages": (
            ChainStageRevision(
                stage_id="C",
                stage_kind="task",
                contract_id="annealed-properties-v1",
                contract_digest=SHA_C,
                package_manifest_digest=SHA_D,
                dataset_view_revision_id="view-r1",
                dataset_profile_digest=SHA_A,
            ),
        ),
    }
    revision = ChainRevision(
        **revision_payload,
        revision_digest=SHA_C,
    )
    client.app.state.store.register_chain_definition(definition)
    client.app.state.store.register_chain_revision(revision)
    return definition, revision


def test_chain_catalog_and_project_creation_pin_exact_revision(
    client: TestClient,
) -> None:
    definition, revision = _register_chain(client)

    catalog = client.get("/api/chains")
    assert catalog.status_code == 200
    assert catalog.json()[0]["definition"]["chain_id"] == definition.chain_id
    assert catalog.json()[0]["revisions"][0]["revision_digest"] == SHA_C

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
