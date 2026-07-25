from __future__ import annotations

import json
from pathlib import Path
import threading
import time

from fastapi.testclient import TestClient

from material_workbench.persistence.store import Store


ROOT = Path(__file__).resolve().parents[2]
STAGE_A_SMOKE = ROOT / "models/packages/welding-stage-a-deterministic-v1/smoke/input.json"
STAGE_B_SMOKE = ROOT / "models/packages/welding-consumable-stage-b-ridge-v1/smoke/input.json"
STAGE_C_SMOKE = ROOT / "models/packages/welding-stage-c-ridge-v1/smoke/input.json"
def _chain_identity(client: TestClient) -> dict:
    item = next(
        item
        for item in client.get("/api/chains").json()
        if item["definition"]["chain_id"] == "welding-consumable-a-b-c-v1"
    )
    revision = item["revisions"][0]
    return {
        "identity_kind": "chain",
        "chain_revision_id": "welding-consumable-a-b-c-v1:r1",
        "chain_revision_digest": revision["revision_digest"],
    }


def _candidate_payload(client: TestClient, project_id: str) -> dict:
    scientific = json.loads(STAGE_A_SMOKE.read_text(encoding="utf-8"))
    stage_b = json.loads(STAGE_B_SMOKE.read_text(encoding="utf-8"))
    stage_c = json.loads(STAGE_C_SMOKE.read_text(encoding="utf-8"))
    contract_response = client.get(
        f"/api/projects/{project_id}/chain/candidate-contract"
    )
    assert contract_response.status_code == 200, contract_response.text
    contract = contract_response.json()
    return {
        "name": "Chain execution candidate",
        "inputs": {
            "composition": {},
            "process": {
                **stage_b["inputs"]["process"],
                "preheat_temp_c": stage_c["inputs"]["process"]["preheat_temp_c"],
                "test_temperature_c": stage_c["inputs"]["process"][
                    "test_temperature_c"
                ],
            },
            "categorical": {
                **stage_b["inputs"]["categorical"],
                **stage_c["inputs"]["categorical"],
            },
            "heat_pattern": None,
            "heat_time_basis": "line_speed",
        },
        "blend": {
            "schema_version": "sparse-blend/v1",
            "items": scientific["items"],
            "hoop_id": scientific["hoop_id"],
            "fill_ratio": scientific["fill_ratio"],
            "balance_material_id": scientific["items"][0]["material_id"],
            "scientific_master": scientific["scientific_master"],
            "commercial_catalog": contract["commercial_catalog"],
            "design_space": contract["design_space_ref"],
        },
    }


def _project_and_candidate(client: TestClient) -> tuple[dict, dict]:
    project_response = client.post(
        "/api/projects",
        json={"name": "Chain execution", "scientific_identity": _chain_identity(client)},
    )
    assert project_response.status_code == 201, project_response.text
    project = project_response.json()
    candidate_response = client.post(
        f"/api/projects/{project['id']}/chain/candidates",
        json=_candidate_payload(client, project["id"]),
    )
    assert candidate_response.status_code == 201, candidate_response.text
    return project, candidate_response.json()


def _execute(client: TestClient, project: dict, candidate: dict) -> dict:
    response = client.post(
        f"/api/projects/{project['id']}/chain/candidates/{candidate['id']}/executions",
        json={
            "candidate_revision": candidate["revision"],
            "request_id": f"request-r{candidate['revision']}",
            "debounce_ms": 0,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _update(client: TestClient, project: dict, candidate: dict, payload: dict) -> dict:
    response = client.put(
        f"/api/projects/{project['id']}/chain/candidates/{candidate['id']}",
        json={**payload, "expected_revision": candidate["revision"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_explicit_a_b_c_execution_matches_bindings_and_partial_recomputation(
    client: TestClient,
) -> None:
    project, candidate = _project_and_candidate(client)
    first = _execute(client, project, candidate)
    assert first["status"] == "latest"
    assert [stage["stage_id"] for stage in first["stages"]] == ["A", "B", "C"]
    assert [stage["cache_hit"] for stage in first["stages"]] == [False, False, False]
    stage_a, stage_b, stage_c = first["stages"]
    assert stage_b["canonical_input"]["composition"]["C"] == stage_a["result"][
        "material_composition"
    ]["C"]
    assert stage_c["canonical_input"]["composition"]["C"] == stage_b["result"][
        "predictions"
    ]["C"]["value"]

    temperature_payload = _candidate_payload(client, project["id"])
    temperature_payload["inputs"]["process"]["test_temperature_c"] = -45.0
    temperature = _update(client, project, candidate, temperature_payload)
    second = _execute(client, project, temperature)
    assert [stage["cache_hit"] for stage in second["stages"]] == [True, True, False]
    assert (
        second["stages"][0]["requested_input_digest"]
        == first["stages"][0]["requested_input_digest"]
    )
    assert (
        second["stages"][1]["requested_input_digest"]
        == first["stages"][1]["requested_input_digest"]
    )
    assert (
        second["stages"][2]["requested_input_digest"]
        != first["stages"][2]["requested_input_digest"]
    )

    material_payload = _candidate_payload(client, project["id"])
    material_payload["inputs"]["process"]["test_temperature_c"] = -45.0
    material_payload["blend"]["items"][0]["ratio"] -= 1.0
    material_payload["blend"]["items"][1]["ratio"] += 1.0
    material = _update(client, project, temperature, material_payload)
    third = _execute(client, project, material)
    assert [stage["cache_hit"] for stage in third["stages"]] == [False, False, False]
    historical = client.get(
        f"/api/projects/{project['id']}/chain/candidates/{candidate['id']}/revisions/1"
    )
    assert historical.status_code == 200
    assert historical.json()["inputs"] == candidate["inputs"]


def test_chain_candidate_api_rejects_unregistered_revision_references(
    client: TestClient,
) -> None:
    project_response = client.post(
        "/api/projects",
        json={"name": "Chain refs", "scientific_identity": _chain_identity(client)},
    )
    assert project_response.status_code == 201
    project = project_response.json()
    payload = _candidate_payload(client, project["id"])
    payload["blend"]["design_space"]["digest"] = "sha256:" + "0" * 64
    response = client.post(
        f"/api/projects/{project['id']}/chain/candidates",
        json=payload,
    )
    assert response.status_code == 422
    assert "Design Space revision" in response.text


def test_failure_retains_previous_downstream_result_and_marks_freshness(
    client: TestClient,
    monkeypatch,
) -> None:
    project, candidate = _project_and_candidate(client)
    first = _execute(client, project, candidate)
    previous_b = first["stages"][1]
    previous_c = first["stages"][2]

    changed_payload = _candidate_payload(client, project["id"])
    changed_payload["blend"]["items"][0]["ratio"] -= 1.0
    changed_payload["blend"]["items"][1]["ratio"] += 1.0
    changed = _update(client, project, candidate, changed_payload)
    runtime = client.app.state.task_registry.entry_for(
        "welding-consumable-stage-b-v1"
    ).predictor_runtime

    def fail_stage_b(*_args, **_kwargs):
        raise RuntimeError("intentional Stage B failure")

    monkeypatch.setattr(runtime, "predict_core", fail_stage_b)
    failed = _execute(client, project, changed)
    assert failed["status"] == "failed"
    assert [stage["status"] for stage in failed["stages"]] == [
        "latest",
        "failed",
        "stale",
    ]
    failed_b = failed["stages"][1]
    assert failed_b["result"] == previous_b["result"]
    assert failed_b["result_input_digest"] == previous_b["result_input_digest"]
    assert failed_b["requested_input_digest"] != failed_b["result_input_digest"]
    assert "intentional Stage B failure" in failed_b["error"]
    stale_c = failed["stages"][2]
    assert stale_c["result"] == previous_c["result"]
    assert stale_c["result_input_digest"] == previous_c["result_input_digest"]


def test_chain_snapshot_pins_every_identity_and_survives_store_restart(
    client: TestClient,
) -> None:
    project, candidate = _project_and_candidate(client)
    execution = _execute(client, project, candidate)
    response = client.post(
        f"/api/projects/{project['id']}/chain/candidates/{candidate['id']}/snapshots",
        json={"candidate_revision": candidate["revision"], "debounce_ms": 0},
    )
    assert response.status_code == 201, response.text
    snapshot = response.json()
    assert snapshot["identity"]["chain_revision_digest"] == (
        project["scientific_identity"]["chain_revision_digest"]
    )
    assert snapshot["identity"]["candidate_revision"] == candidate["revision"]
    assert snapshot["identity"]["design_space"] == candidate["blend"]["design_space"]
    assert snapshot["identity"]["commercial_catalog"] == candidate["blend"][
        "commercial_catalog"
    ]
    assert [stage["package_manifest_digest"] for stage in snapshot["stages"]] == [
        stage["package_manifest_digest"] for stage in execution["stages"]
    ]
    assert all(stage["canonical_input"] and stage["result"] for stage in snapshot["stages"])

    restarted = Store(client.app.state.store.path)
    restored = restarted.get_chain_snapshot(snapshot["snapshot_id"])
    assert restored is not None
    assert restored.model_dump(mode="json") == snapshot
    persisted = restarted.get_chain_execution(project["id"], candidate["id"])
    assert persisted is not None
    assert persisted.request_id == execution["request_id"]


def test_debounce_discards_an_older_request_without_overwriting_latest(
    client: TestClient,
) -> None:
    project, candidate = _project_and_candidate(client)
    url = (
        f"/api/projects/{project['id']}/chain/candidates/"
        f"{candidate['id']}/executions"
    )
    responses: dict[str, dict] = {}

    def older_request() -> None:
        response = client.post(
            url,
            json={
                "candidate_revision": candidate["revision"],
                "request_id": "older",
                "debounce_ms": 150,
            },
        )
        assert response.status_code == 200, response.text
        responses["older"] = response.json()

    thread = threading.Thread(target=older_request)
    thread.start()
    time.sleep(0.03)
    latest = client.post(
        url,
        json={
            "candidate_revision": candidate["revision"],
            "request_id": "latest",
            "debounce_ms": 0,
        },
    )
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert latest.status_code == 200, latest.text
    assert latest.json()["status"] == "latest"
    assert responses["older"]["status"] == "superseded"
    persisted = client.get(
        f"/api/projects/{project['id']}/chain/candidates/"
        f"{candidate['id']}/execution"
    )
    assert persisted.status_code == 200
    assert persisted.json()["request_id"] == "latest"
