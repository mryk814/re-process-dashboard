from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
from pydantic import BaseModel

from material_workbench.contracts.blend_contracts import (
    CommercialMaterialCatalog,
    SparseBlendDesignSpace,
)
from material_workbench.contracts.schemas import CandidateInputs
from material_workbench.persistence.store import Store


ROOT = Path(__file__).resolve().parents[2]
STAGE_A_SMOKE = ROOT / "models/packages/welding-stage-a-deterministic-v1/smoke/input.json"
STAGE_B_SMOKE = ROOT / "models/packages/welding-consumable-stage-b-ridge-v1/smoke/input.json"
STAGE_C_SMOKE = ROOT / "models/packages/welding-stage-c-ridge-v1/smoke/input.json"


def _json_payload(value):
    return json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            default=lambda item: (
                item.model_dump(mode="json")
                if isinstance(item, BaseModel)
                else str(item)
            ),
        )
    )


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

    stored_candidate = client.app.state.store.get_candidate_revision(
        candidate["id"], candidate["revision"], project["id"]
    )
    assert stored_candidate is not None and stored_candidate.blend is not None
    resolution = client.app.state.deterministic_transform_catalog.resolve_execution(
        "welding-stage-a-v1", stored_candidate.blend
    )
    independent_a = resolution.transform.transform(stored_candidate.blend)
    independent_a_payload = independent_a.model_dump(mode="json")
    assert independent_a_payload == stage_a["result"]

    independent_b_candidate = stored_candidate.model_copy(
        deep=True,
        update={
            "inputs": CandidateInputs.model_validate(
                {
                    **stage_b["canonical_input"],
                    "heat_pattern": None,
                    "heat_time_basis": "line_speed",
                }
            ),
            "blend": None,
        },
    )
    independent_b = client.app.state.task_registry.entry_for(
        "welding-consumable-stage-b-v1"
    ).predictor_runtime.predict_core(independent_b_candidate, detailed=False)
    independent_b_payload = _json_payload(independent_b)
    assert independent_b_payload == stage_b["result"]

    independent_c_candidate = stored_candidate.model_copy(
        deep=True,
        update={
            "inputs": CandidateInputs.model_validate(
                {
                    **stage_c["canonical_input"],
                    "heat_pattern": None,
                    "heat_time_basis": "line_speed",
                }
            ),
            "blend": None,
        },
    )
    independent_c = client.app.state.task_registry.entry_for(
        "welding-stage-c-properties-v1"
    ).predictor_runtime.predict_core(independent_c_candidate, detailed=False)
    independent_c_payload = _json_payload(independent_c)
    assert independent_c_payload == stage_c["result"]

    temperature_payload = _candidate_payload(client, project["id"])
    temperature_payload["inputs"]["process"]["test_temperature_c"] = -45.0
    temperature = _update(client, project, candidate, temperature_payload)
    stale_temperature = client.get(
        f"/api/projects/{project['id']}/chain/candidates/"
        f"{candidate['id']}/execution"
    ).json()
    assert stale_temperature["status"] == "stale"
    assert [stage["status"] for stage in stale_temperature["stages"]] == [
        "latest",
        "latest",
        "stale",
    ]
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
    stale_material = client.get(
        f"/api/projects/{project['id']}/chain/candidates/"
        f"{candidate['id']}/execution"
    ).json()
    assert stale_material["status"] == "stale"
    assert [stage["status"] for stage in stale_material["stages"]] == [
        "stale",
        "stale",
        "stale",
    ]
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
    assert "完全一致revision" in response.text


def test_chain_candidate_accepts_a_registered_historical_catalog_pair(
    client: TestClient,
) -> None:
    project_response = client.post(
        "/api/projects",
        json={"name": "Historical refs", "scientific_identity": _chain_identity(client)},
    )
    assert project_response.status_code == 201
    project = project_response.json()
    payload = _candidate_payload(client, project["id"])
    historical_catalog = CommercialMaterialCatalog.model_validate_json(
        (ROOT / "models/catalogs/welding-stage-a-commercial-v1.json").read_text(
            encoding="utf-8"
        )
    )
    historical_space = SparseBlendDesignSpace.model_validate_json(
        (ROOT / "models/design-spaces/welding-stage-a-v1.json").read_text(
            encoding="utf-8"
        )
    )
    payload["blend"]["commercial_catalog"] = historical_catalog.ref.model_dump(
        mode="json"
    )
    payload["blend"]["design_space"] = historical_space.ref.model_dump(mode="json")
    response = client.post(
        f"/api/projects/{project['id']}/chain/candidates",
        json=payload,
    )
    assert response.status_code == 201, response.text
    assert response.json()["blend"]["commercial_catalog"]["revision"] == 1
    assert response.json()["blend"]["design_space"]["revision"] == 1


def test_pinned_stage_a_ignores_a_new_active_transform_package(
    client: TestClient,
    monkeypatch,
) -> None:
    project_response = client.post(
        "/api/projects",
        json={
            "name": "Pinned Stage A",
            "scientific_identity": _chain_identity(client),
        },
    )
    assert project_response.status_code == 201
    project = project_response.json()
    catalog = client.app.state.deterministic_transform_catalog
    active = catalog.entry("welding-stage-a-v1")
    historical_catalog = CommercialMaterialCatalog.model_validate_json(
        (ROOT / "models/catalogs/welding-stage-a-commercial-v1.json").read_text(
            encoding="utf-8"
        )
    )
    historical_space = SparseBlendDesignSpace.model_validate_json(
        (ROOT / "models/design-spaces/welding-stage-a-v1.json").read_text(
            encoding="utf-8"
        )
    )
    monkeypatch.setitem(
        catalog._entries,  # noqa: SLF001
        "welding-stage-a-v1",
        replace(
            active,
            package=SimpleNamespace(manifest_sha256="f" * 64),
            commercial_catalog=historical_catalog,
            design_space=historical_space,
        ),
    )

    contract = client.get(
        f"/api/projects/{project['id']}/chain/candidate-contract"
    )
    assert contract.status_code == 200, contract.text
    assert contract.json()["commercial_catalog"]["revision"] == 2
    assert contract.json()["design_space_ref"]["revision"] == 2

    candidate_response = client.post(
        f"/api/projects/{project['id']}/chain/candidates",
        json=_candidate_payload(client, project["id"]),
    )
    assert candidate_response.status_code == 201, candidate_response.text
    execution = _execute(client, project, candidate_response.json())
    assert execution["status"] == "latest"
    assert execution["stages"][0]["package_manifest_digest"] != (
        "sha256:" + "f" * 64
    )


def test_model_stage_rejects_task_contract_drift_with_the_same_package(
    client: TestClient,
    monkeypatch,
) -> None:
    project, candidate = _project_and_candidate(client)
    first = _execute(client, project, candidate)
    assert first["status"] == "latest"
    registry = client.app.state.task_registry
    task_id = "welding-consumable-stage-b-v1"
    contract = registry.contract_for(task_id)
    drifted_definition = contract.task_definition.model_copy(
        update={"label": contract.task_definition.label + " drifted"}
    )
    monkeypatch.setitem(
        registry._contracts,  # noqa: SLF001
        task_id,
        contract.model_copy(update={"task_definition": drifted_definition}),
    )

    response = client.post(
        f"/api/projects/{project['id']}/chain/candidates/"
        f"{candidate['id']}/executions",
        json={
            "candidate_revision": candidate["revision"],
            "request_id": "contract-drift",
            "debounce_ms": 0,
        },
    )
    assert response.status_code == 200, response.text
    execution = response.json()
    assert execution["status"] == "failed"
    assert [stage["status"] for stage in execution["stages"]] == [
        "latest",
        "failed",
        "stale",
    ]
    assert "contract digest" in execution["stages"][1]["error"]


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
    assert stale_c["requested_input_digest"] != stale_c["result_input_digest"]


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


def test_actual_conditioned_variant_requires_complete_measured_b_and_never_overwrites_chain(
    client: TestClient,
) -> None:
    project, candidate = _project_and_candidate(client)
    execution = _execute(client, project, candidate)
    snapshot_response = client.post(
        f"/api/projects/{project['id']}/chain/candidates/{candidate['id']}/snapshots",
        json={"candidate_revision": candidate["revision"], "debounce_ms": 0},
    )
    assert snapshot_response.status_code == 201, snapshot_response.text
    snapshot = snapshot_response.json()
    stage_b_values = {
        key: prediction["value"]
        for key, prediction in execution["stages"][1]["result"]["predictions"].items()
    }
    partial = dict(stage_b_values)
    partial.pop("C")
    variant_url = (
        f"/api/projects/{project['id']}/chain/candidates/"
        f"{candidate['id']}/analysis-variants"
    )
    rejected = client.post(
        variant_url,
        json={
            "candidate_revision": candidate["revision"],
            "comparison_snapshot_id": snapshot["snapshot_id"],
            "actual_records": [{"actual_id": "WM-001", "values": partial}],
        },
    )
    assert rejected.status_code == 409
    assert "予測値では補完しません" in rejected.text
    assert "C" in rejected.text

    measured = {key: value + 0.001 for key, value in stage_b_values.items()}
    created = client.post(
        variant_url,
        json={
            "candidate_revision": candidate["revision"],
            "comparison_snapshot_id": snapshot["snapshot_id"],
            "actual_records": [{"actual_id": "WM-001", "values": measured}],
        },
    )
    assert created.status_code == 201, created.text
    variant = created.json()
    assert variant["source"] == "actual"
    assert variant["identity"]["comparison_snapshot_id"] == snapshot["snapshot_id"]
    assert variant["identity"]["base_candidate_revision"] == candidate["revision"]
    assert variant["identity"]["actual_ids"] == ["WM-001"]
    assert set(variant["identity"]["coverage"]) == set(stage_b_values)
    assert variant["stage_c_input"]["composition"] == measured
    assert (
        variant["identity"]["stage_c_package_manifest_digest"]
        == execution["stages"][2]["package_manifest_digest"]
    )

    normal_after = client.get(
        f"/api/projects/{project['id']}/chain/candidates/{candidate['id']}/execution"
    ).json()
    assert normal_after == execution
    listed = client.get(variant_url)
    assert listed.status_code == 200
    assert listed.json() == [variant]
    restarted = Store(client.app.state.store.path)
    restored = restarted.get_chain_analysis_variant(variant["variant_id"])
    assert restored is not None
    assert restored.model_dump(mode="json") == variant


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


def test_candidate_update_invalidates_an_inflight_older_revision(
    client: TestClient,
    monkeypatch,
) -> None:
    project, candidate = _project_and_candidate(client)
    runtime = client.app.state.task_registry.entry_for(
        "welding-consumable-stage-b-v1"
    ).predictor_runtime
    original_predict = runtime.predict_core
    entered_stage_b = threading.Event()
    release_stage_b = threading.Event()
    responses: dict[str, dict] = {}

    def paused_predict(*args, **kwargs):
        entered_stage_b.set()
        assert release_stage_b.wait(timeout=3)
        return original_predict(*args, **kwargs)

    monkeypatch.setattr(runtime, "predict_core", paused_predict)
    url = (
        f"/api/projects/{project['id']}/chain/candidates/"
        f"{candidate['id']}/executions"
    )

    def older_revision() -> None:
        response = client.post(
            url,
            json={
                "candidate_revision": candidate["revision"],
                "request_id": "revision-1-inflight",
                "debounce_ms": 0,
            },
        )
        assert response.status_code == 200, response.text
        responses["old"] = response.json()

    thread = threading.Thread(target=older_revision)
    thread.start()
    assert entered_stage_b.wait(timeout=3)

    changed_payload = _candidate_payload(client, project["id"])
    changed_payload["inputs"]["process"]["test_temperature_c"] = -45.0
    changed = _update(client, project, candidate, changed_payload)
    assert changed["revision"] == 2
    invalidated = client.get(
        f"/api/projects/{project['id']}/chain/candidates/"
        f"{candidate['id']}/execution"
    ).json()
    assert invalidated["candidate_revision"] == 2
    assert invalidated["status"] == "stale"

    release_stage_b.set()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert responses["old"]["status"] == "superseded"
    persisted = client.get(
        f"/api/projects/{project['id']}/chain/candidates/"
        f"{candidate['id']}/execution"
    ).json()
    assert persisted["candidate_revision"] == 2
    assert persisted["request_id"].startswith("candidate-revision:")


def test_final_save_uses_store_compare_and_swap(
    client: TestClient,
    monkeypatch,
) -> None:
    project, candidate = _project_and_candidate(client)
    store = client.app.state.store
    original_save = store.save_chain_execution_if_current
    old_final_entered = threading.Event()
    release_old_final = threading.Event()
    responses: dict[str, dict] = {}

    def pause_old_final(execution, generation):
        if execution.request_id == "old-final" and execution.status == "latest":
            old_final_entered.set()
            assert release_old_final.wait(timeout=3)
        return original_save(execution, generation)

    monkeypatch.setattr(store, "save_chain_execution_if_current", pause_old_final)
    url = (
        f"/api/projects/{project['id']}/chain/candidates/"
        f"{candidate['id']}/executions"
    )

    def older_request() -> None:
        response = client.post(
            url,
            json={
                "candidate_revision": candidate["revision"],
                "request_id": "old-final",
                "debounce_ms": 0,
            },
        )
        assert response.status_code == 200, response.text
        responses["old"] = response.json()

    thread = threading.Thread(target=older_request)
    thread.start()
    assert old_final_entered.wait(timeout=3)
    latest = client.post(
        url,
        json={
            "candidate_revision": candidate["revision"],
            "request_id": "new-final",
            "debounce_ms": 0,
        },
    )
    assert latest.status_code == 200, latest.text
    assert latest.json()["status"] == "latest"

    release_old_final.set()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert responses["old"]["status"] == "superseded"
    persisted = client.get(
        f"/api/projects/{project['id']}/chain/candidates/"
        f"{candidate['id']}/execution"
    ).json()
    assert persisted["request_id"] == "new-final"
