from copy import deepcopy
from itertools import combinations
from time import perf_counter

import numpy as np
import pytest

from material_workbench.application.catalog import (
    CatalogValidationError,
    _INPUT_SPACE_CANONICAL,
    _INPUT_SPACE_CANONICAL_LOCK,
    _output_space_evidence_points,
)
from material_workbench.application.input_space import (
    _CACHE_LOCK,
    _TRAINING_EMBEDDINGS,
)
from material_workbench.contracts.candidate_project_contracts import CandidateInput
from material_workbench.modeling.model_lifecycle import canonical_training_dataset
from material_workbench.modeling.training_distance import (
    evidence_context_id,
    resolve_training_metric_space,
    training_context_distances,
)

ELEMENTS = ("C", "Si", "Mn", "P", "S", "Al", "Cu", "Ni", "Cr", "Mo", "Ti", "B", "O", "N")


def _payload(name: str = "試験候補") -> dict:
    return {
        "name": name,
        "inputs": {
            "composition": {**{key: 0.0 for key in ELEMENTS}, "C": 0.08, "Si": 0.3, "Mn": 1.5},
            "process": {"ls_mpm": 103.0},
            "categorical": {},
            "heat_pattern": [
                {"time_s": 0, "temperature_c": 25},
                {"time_s": 280, "temperature_c": 800},
                {"time_s": 340, "temperature_c": 810},
                {"time_s": 650, "temperature_c": 120},
            ],
        },
    }


def test_heat_pattern_rejects_non_monotonic_time() -> None:
    invalid = _payload()
    invalid["inputs"]["heat_pattern"][2]["time_s"] = 280
    try:
        CandidateInput.model_validate(invalid)
    except ValueError as exc:
        assert "厳密な昇順" in str(exc)
    else:
        raise AssertionError("non-monotonic heat pattern must not be accepted")


def test_existing_candidate_payload_defaults_to_line_speed_time_basis() -> None:
    candidate = CandidateInput.model_validate(_payload())

    assert candidate.inputs.heat_time_basis == "line_speed"


def test_data_library_model_packages_can_include_archived_refs(client) -> None:
    current = client.get("/api/data-library/model-packages").json()
    archived_id = current[0]["id"]
    client.app.state.workspace_catalog.archive_model_package_ref(archived_id)

    active = client.get("/api/data-library/model-packages")
    complete = client.get("/api/data-library/model-packages", params={"include_archived": True})

    assert active.status_code == 200
    assert complete.status_code == 200
    assert archived_id not in {item["id"] for item in active.json()}
    archived = next(item for item in complete.json() if item["id"] == archived_id)
    assert archived["archived_at"] is not None


def test_local_web_origin_can_preflight_patch_requests(client) -> None:
    response = client.options(
        "/api/data-library/datasets/example",
        headers={
            "Origin": "http://127.0.0.1:5180",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert "PATCH" in response.headers["access-control-allow-methods"]


def test_candidate_update_canonicalizes_line_speed_times_and_rejects_direct_edits(client) -> None:
    candidate = client.post("/api/projects/default/candidates", json=_payload("LS基準")).json()
    changed_speed = _payload("LS基準")
    changed_speed["inputs"]["process"]["ls_mpm"] = 206.0
    changed_speed["inputs"]["heat_pattern"][1]["time_s"] = 290

    updated = client.put(
        f"/api/projects/default/candidates/{candidate['id']}",
        json={**changed_speed, "expected_revision": candidate["revision"]},
    )

    assert updated.status_code == 200
    assert updated.json()["inputs"]["heat_time_basis"] == "line_speed"
    assert [point["time_s"] for point in updated.json()["inputs"]["heat_pattern"]] == [0.0, 140.0, 170.0, 325.0]

    direct_edit = deepcopy(updated.json())
    direct_edit["inputs"]["heat_pattern"][1]["time_s"] = 150.0
    rejected = client.put(
        f"/api/projects/default/candidates/{candidate['id']}",
        json={
            key: value
            for key, value in direct_edit.items()
            if key not in {"id", "project_id", "created_at", "updated_at", "archived_at", "revision"}
        }
        | {"expected_revision": updated.json()["revision"]},
    )
    assert rejected.status_code == 422
    assert "経過時間基準" in rejected.json()["message"]


def test_elapsed_time_candidate_update_allows_independent_time_and_speed_edits(client) -> None:
    candidate = client.post("/api/projects/default/candidates", json=_payload("経過時間基準")).json()
    switch = _payload("経過時間基準")
    switch["inputs"]["heat_time_basis"] = "elapsed_time"
    switch["inputs"]["heat_pattern"][1]["time_s"] = 290
    switched = client.put(
        f"/api/projects/default/candidates/{candidate['id']}",
        json={**switch, "expected_revision": candidate["revision"]},
    )

    assert switched.status_code == 200
    assert [point["time_s"] for point in switched.json()["inputs"]["heat_pattern"]] == [0.0, 290.0, 340.0, 650.0]

    edit = _payload("経過時間基準")
    edit["inputs"]["heat_time_basis"] = "elapsed_time"
    edit["inputs"]["process"]["ls_mpm"] = 206.0
    edit["inputs"]["heat_pattern"][1]["time_s"] = 300.0
    edited = client.put(
        f"/api/projects/default/candidates/{candidate['id']}",
        json={**edit, "expected_revision": switched.json()["revision"]},
    )
    assert edited.status_code == 200
    assert edited.json()["inputs"]["process"]["ls_mpm"] == 206.0
    assert [point["time_s"] for point in edited.json()["inputs"]["heat_pattern"]] == [0.0, 300.0, 340.0, 650.0]


def test_time_basis_switch_accepts_simultaneous_line_speed_change(client) -> None:
    candidate = client.post("/api/projects/default/candidates", json=_payload("基準切替")).json()
    update = _payload("基準切替")
    update["inputs"]["heat_time_basis"] = "elapsed_time"
    update["inputs"]["process"]["ls_mpm"] = 120.0
    update["inputs"]["heat_pattern"][1]["time_s"] = 300.0

    response = client.put(
        f"/api/projects/default/candidates/{candidate['id']}",
        json={**update, "expected_revision": candidate["revision"]},
    )

    assert response.status_code == 200
    assert response.json()["inputs"]["process"]["ls_mpm"] == 120.0
    assert response.json()["inputs"]["heat_pattern"][1]["time_s"] == 300.0


def test_line_speed_candidate_accepts_speed_and_point_count_change_as_new_layout(client) -> None:
    candidate = client.post("/api/projects/default/candidates", json=_payload("点数変更")).json()
    changed = _payload("点数変更")
    changed["inputs"]["process"]["ls_mpm"] = 120.0
    scale = 103.0 / 120.0
    for point in changed["inputs"]["heat_pattern"]:
        point["time_s"] *= scale
    changed["inputs"]["heat_pattern"].append({"time_s": 700 * scale, "temperature_c": 80})

    response = client.put(
        f"/api/projects/default/candidates/{candidate['id']}",
        json={**changed, "expected_revision": candidate["revision"]},
    )

    assert response.status_code == 200
    assert len(response.json()["inputs"]["heat_pattern"]) == 5
    assert response.json()["inputs"]["heat_pattern"][-1]["time_s"] == 700 * scale


def test_line_speed_candidate_allows_point_count_change_without_speed_change(client) -> None:
    candidate = client.post("/api/projects/default/candidates", json=_payload("点追加")).json()
    changed = _payload("点追加")
    changed["inputs"]["heat_pattern"].insert(2, {"time_s": 310, "temperature_c": 805})

    response = client.put(
        f"/api/projects/default/candidates/{candidate['id']}",
        json={**changed, "expected_revision": candidate["revision"]},
    )

    assert response.status_code == 200
    assert [point["time_s"] for point in response.json()["inputs"]["heat_pattern"]] == [
        0.0,
        280.0,
        310.0,
        340.0,
        650.0,
    ]


def test_candidate_rejects_unknown_or_non_physical_composition(client) -> None:
    unknown = _payload()
    unknown["inputs"]["composition"]["Unobtainium"] = 0.1
    assert client.post("/api/projects/default/candidates", json=unknown).status_code == 422
    negative = _payload()
    negative["inputs"]["composition"]["C"] = -0.01
    assert client.post("/api/projects/default/candidates", json=negative).status_code == 422


def _validation_error(payload: dict) -> ValueError:
    try:
        CandidateInput.model_validate(payload)
    except ValueError as exc:
        return exc
    raise AssertionError("invalid candidate must not be accepted")


def test_model_training_data_exposes_selected_observations_and_actual_model_rows(client) -> None:
    selected_response = client.get(
        "/api/projects/default/model-package/training-data",
        params={"stage": "selected", "target": "TS", "limit": 5},
    )
    assert selected_response.status_code == 200
    selected = selected_response.json()
    assert selected["training_unit"] == "parent_condition_mean"
    assert selected["total"] > selected["parent_conditions"]
    assert selected["stage_counts"]["source_rows"] >= selected["stage_counts"]["selected_rows"]
    assert selected["stage_counts"]["selected_rows"] == selected["total"]
    assert selected["stage_counts"]["model_rows"] == selected["parent_conditions"]
    assert len(selected["rows"]) == 5
    assert "composition.C" in {column["key"] for column in selected["columns"]}
    assert all("output.TS" in row["values"] for row in selected["rows"])

    features_response = client.get(
        "/api/projects/default/model-package/training-data",
        params={"stage": "features", "target": "TS", "limit": 5},
    )
    assert features_response.status_code == 200
    features = features_response.json()
    assert features["total"] == selected["parent_conditions"]
    assert features["columns"][0]["key"] == "parent_key"
    assert features["columns"][1]["key"] == "composition_key"
    assert features["columns"][2]["key"] == "observation_ids"
    assert features["columns"][3]["key"] == "replicate_count"
    assert all(row["values"]["observation_ids"].startswith("TT-") for row in features["rows"])
    assert any(column["key"].startswith("feature.") for column in features["columns"])
    assert features["feature_dataset_digest"].startswith("sha256:")


def test_output_space_evidence_uses_only_conditions_with_both_actuals(client) -> None:
    candidate = client.get("/api/projects/default/candidates").json()[0]
    params = {
        "x_target": "TS",
        "y_target": "YS",
        "candidate_id": candidate["id"],
        "expected_revision": candidate["revision"],
        "distance_filter": "all",
    }
    response = client.get(
        "/api/projects/default/model-package/output-space-evidence",
        params=params,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["pairing_unit"] == "condition_mean"
    assert payload["source_scope"] == "model_training_data"
    assert payload["returned_contexts"] == payload["total_contexts"] > 0
    assert payload["eligible_contexts"] == payload["total_contexts"]
    assert payload["truncated"] is False
    assert payload["source_data_digest"].startswith("sha256:")
    assert payload["cohort_digest"].startswith("sha256:")
    assert payload["distance_method"]
    assert payload["distance_version"] == "1.0.0"
    assert payload["sampling_policy"] == "task_distance"
    assert all(point["x"]["count"] > 0 and point["y"]["count"] > 0 for point in payload["points"])
    assert all(point["x"]["min"] <= point["x"]["mean"] <= point["x"]["max"] for point in payload["points"])
    assert all(point["y"]["min"] <= point["y"]["mean"] <= point["y"]["max"] for point in payload["points"])
    assert all(point["x"]["observation_ids"] for point in payload["points"])
    assert all(point["y"]["observation_ids"] for point in payload["points"])
    assert all(point["pairing_relationship"] == "same_observations" for point in payload["points"])
    assert [point["distance"] for point in payload["points"]] == sorted(
        point["distance"] for point in payload["points"]
    )

    invalid = client.get(
        "/api/projects/default/model-package/output-space-evidence",
        params={**params, "y_target": "TS"},
    )
    assert invalid.status_code == 422


def test_output_space_distance_filters_and_axis_swap_are_invariant(client) -> None:
    candidate = client.get("/api/projects/default/candidates").json()[0]
    base = {
        "candidate_id": candidate["id"],
        "expected_revision": candidate["revision"],
        "limit": 200,
    }
    payloads = {}
    for distance_filter in ("supported", "caution", "all"):
        response = client.get(
            "/api/projects/default/model-package/output-space-evidence",
            params={
                **base,
                "x_target": "TS",
                "y_target": "YS",
                "distance_filter": distance_filter,
            },
        )
        assert response.status_code == 200
        payloads[distance_filter] = response.json()
    supported_ids = {point["context_id"] for point in payloads["supported"]["points"]}
    caution_ids = {point["context_id"] for point in payloads["caution"]["points"]}
    all_ids = {point["context_id"] for point in payloads["all"]["points"]}
    assert supported_ids <= caution_ids <= all_ids
    assert all(
        point["distance_status"] == "supported"
        for point in payloads["supported"]["points"]
    )
    assert all(
        point["distance_status"] in {"supported", "caution"}
        for point in payloads["caution"]["points"]
    )

    swapped = client.get(
        "/api/projects/default/model-package/output-space-evidence",
        params={
            **base,
            "x_target": "YS",
            "y_target": "TS",
            "distance_filter": "all",
        },
    )
    assert swapped.status_code == 200
    swapped_payload = swapped.json()
    assert swapped_payload["cohort_digest"] == payloads["all"]["cohort_digest"]
    original = {point["context_id"]: point for point in payloads["all"]["points"]}
    swapped_points = {
        point["context_id"]: point for point in swapped_payload["points"]
    }
    assert original.keys() == swapped_points.keys()
    for context_id, point in original.items():
        other = swapped_points[context_id]
        assert other["distance"] == pytest.approx(point["distance"])
        assert other["x"] == point["y"]
        assert other["y"] == point["x"]

    limited = client.get(
        "/api/projects/default/model-package/output-space-evidence",
        params={
            **base,
            "x_target": "TS",
            "y_target": "YS",
            "distance_filter": "all",
            "limit": 1,
        },
    ).json()
    assert limited["eligible_contexts"] == payloads["all"]["eligible_contexts"]
    assert limited["returned_contexts"] == 1
    assert limited["truncated"] is (limited["eligible_contexts"] > 1)


def test_output_space_pairing_preserves_axis_specific_observation_identity() -> None:
    rows = [
        {
            "observation_id": "x-only",
            "parent_key": "parent-1",
            "condition_context_id": "context-1",
            "outputs": {"X": 10.0},
        },
        {
            "observation_id": "y-only",
            "parent_key": "parent-1",
            "condition_context_id": "context-1",
            "outputs": {"Y": 20.0},
        },
    ]

    points = _output_space_evidence_points(rows, x_target="X", y_target="Y")

    assert points == [{
        "context_id": "context-1",
        "parent_key": "parent-1",
        "process_key": "parent-1",
        "composition_key": None,
        "relation_context_ids": [],
        "pairing_relationship": "distinct_observations",
        "x": {"mean": 10.0, "std": 0.0, "min": 10.0, "max": 10.0, "count": 1, "observation_ids": ["x-only"]},
        "y": {"mean": 20.0, "std": 0.0, "min": 20.0, "max": 20.0, "count": 1, "observation_ids": ["y-only"]},
    }]

    with pytest.raises(CatalogValidationError) as caught:
        _output_space_evidence_points(
            [
                rows[0],
                {**rows[1], "parent_key": "parent-2"},
            ],
            x_target="X",
            y_target="Y",
        )
    assert "multiple validation groups" in str(caught.value)


def test_output_space_mpea_rows_remain_individual_training_contexts() -> None:
    rows = [
        {
            "observation_id": "mpea-row-1",
            "parent_key": "paper-1",
            "outputs": {"TYS": 500.0, "UTS": 720.0},
        },
        {
            "observation_id": "mpea-row-2",
            "parent_key": "paper-1",
            "outputs": {"TYS": 530.0, "UTS": 750.0},
        },
        {
            "observation_id": "mpea-row-3",
            "parent_key": "paper-1",
            "outputs": {"TYS": 560.0},
        },
    ]

    points = _output_space_evidence_points(
        rows,
        x_target="TYS",
        y_target="UTS",
    )

    assert [point["context_id"] for point in points] == [
        "mpea-row-1",
        "mpea-row-2",
    ]
    assert all(point["parent_key"] == "paper-1" for point in points)


@pytest.mark.parametrize(
    "task_id",
    (
        "welding-consumable-stage-b-v1",
        "annealed-properties-v1",
        "flank-wear-v1",
        "heat-treatment-tradeoff-v1",
        "mpea-room-tensile-v1",
        "welding-stage-c-properties-v1",
    ),
)
def test_every_prediction_space_task_serves_distance_evidence(
    client,
    task_id: str,
) -> None:
    task = next(
        item
        for item in client.get("/api/task-definitions").json()
        if item["definition"]["task_definition"]["id"] == task_id
    )
    existing = next(
        (
            project
            for project in client.get("/api/projects").json()
            if project["task_id"] == task_id
        ),
        None,
    )
    if existing is not None:
        project_id = existing["id"]
        candidate_payload = client.get(
            f"/api/projects/{project_id}/candidates"
        ).json()[0]
    else:
        created = client.post(
            "/api/projects",
            json={"name": f"{task_id} output-space smoke", "task_id": task_id},
        )
        assert created.status_code == 201, created.text
        project_id = created.json()["id"]
        candidate = client.post(
            f"/api/projects/{project_id}/candidates",
            json=task["starter_candidate"],
        )
        assert candidate.status_code == 201, candidate.text
        candidate_payload = candidate.json()
    surface = next(
        item
        for item in task["definition"]["application"]["workbench_surfaces"]
        if item["kind"] == "prediction_space"
    )
    response = client.get(
        f"/api/projects/{project_id}/model-package/output-space-evidence",
        params={
            "x_target": surface["target_keys"][0],
            "y_target": surface["target_keys"][1],
            "candidate_id": candidate_payload["id"],
            "expected_revision": candidate_payload["revision"],
            "distance_filter": "all",
            "limit": 1,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["candidate_id"] == candidate_payload["id"]
    assert payload["total_contexts"] > 0
    assert payload["eligible_contexts"] == payload["total_contexts"]
    assert payload["returned_contexts"] == 1
    assert payload["distance_method"]
    assert payload["cohort_digest"].startswith("sha256:")
    if task_id == "mpea-room-tensile-v1":
        project = client.app.state.store.get_project(project_id)
        resolved = client.app.state.project_runtime_resolver.resolve(project)
        package = resolved.runtime.model_package
        canonical = canonical_training_dataset(
            task_id,
            resolved.runtime.data,
            client.app.state.task_registry.contract_for(task_id),
            pipeline_version=package.manifest.feature_pipeline.version,
        )
        expected_ids = {
            str(row["observation_id"])
            for row in canonical["rows"]
            if surface["target_keys"][0] in row["outputs"]
            and surface["target_keys"][1] in row["outputs"]
        }
        actual_points = _output_space_evidence_points(
            canonical["rows"],
            x_target=surface["target_keys"][0],
            y_target=surface["target_keys"][1],
        )
        assert {point["context_id"] for point in actual_points} == expected_ids
        assert payload["total_contexts"] == len(expected_ids)


def test_real_task_input_space_is_reproducible_and_keeps_task_neighbour_order(
    client,
) -> None:
    project_id = "default"
    candidate = client.get(f"/api/projects/{project_id}/candidates").json()[0]
    task = client.get(
        f"/api/projects/{project_id}/task-definition"
    ).json()
    surface = next(
        item
        for item in task["application"]["workbench_surfaces"]
        if item["kind"] == "input_space"
    )
    params = {
        "candidate_id": candidate["id"],
        "expected_revision": candidate["revision"],
    }
    first = client.get(
        f"/api/projects/{project_id}/model-package/input-space",
        params=params,
    )
    second = client.get(
        f"/api/projects/{project_id}/model-package/input-space",
        params=params,
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json() == second.json()
    payload = first.json()
    assert payload["embedding_method"] == "landmark-classical-mds-oos"
    assert payload["embedding_version"] == "1.0.0"
    assert payload["seed"] == surface["seed"]
    assert payload["distance_target_key"] == surface["distance_target_key"]
    assert payload["cohort_digest"].startswith("sha256:")
    assert payload["vector_space_digest"].startswith("sha256:")
    assert payload["displayed_training_contexts"] <= payload[
        "total_training_contexts"
    ]

    project = client.app.state.store.get_project(project_id)
    resolved = client.app.state.project_runtime_resolver.resolve(project)
    package = resolved.runtime.model_package
    canonical = canonical_training_dataset(
        project["task_id"] if isinstance(project, dict) else project.task_id,
        resolved.runtime.data,
        client.app.state.task_registry.contract_for(
            project["task_id"] if isinstance(project, dict) else project.task_id
        ),
        pipeline_version=package.manifest.feature_pipeline.version,
    )
    allowed = {
        evidence_context_id(row, surface["evidence_context"])
        for row in canonical["rows"]
        if surface["distance_target_key"] in row["outputs"]
    }
    distance = training_context_distances(
        resolved.runtime,
        client.app.state.store.get_candidate(candidate["id"], project_id),
        target_keys=(surface["distance_target_key"],),
        allowed_context_ids=allowed,
        evidence_context=surface["evidence_context"],
    )
    expected_index = int(np.argmin(distance.distances))
    selected = next(
        point
        for point in payload["candidate_points"]
        if point["candidate_id"] == candidate["id"]
    )
    assert selected["nearest_training_context_id"] == distance.context_ids[
        expected_index
    ]
    assert selected["island_distance"] == pytest.approx(
        float(distance.distances[expected_index])
    )


@pytest.mark.parametrize(
    "task_id",
    ("wear-curve-v1", "battery-degradation-v1"),
)
def test_input_space_uses_runtime_precomputed_support_thresholds(
    client,
    task_id: str,
) -> None:
    task = next(
        item
        for item in client.get("/api/task-definitions").json()
        if item["definition"]["task_definition"]["id"] == task_id
    )
    project = next(
        (
            item
            for item in client.get("/api/projects").json()
            if item["task_id"] == task_id
        ),
        None,
    )
    if project is None:
        response = client.post(
            "/api/projects",
            json={"name": f"{task_id} threshold regression", "task_id": task_id},
        )
        assert response.status_code == 201, response.text
        project = response.json()
        candidate_response = client.post(
            f"/api/projects/{project['id']}/candidates",
            json=task["starter_candidate"],
        )
        assert candidate_response.status_code == 201, candidate_response.text
        candidate = candidate_response.json()
    else:
        candidate = client.get(
            f"/api/projects/{project['id']}/candidates"
        ).json()[0]
    surface = next(
        item
        for item in task["definition"]["application"]["workbench_surfaces"]
        if item["kind"] == "input_space"
    )
    response = client.get(
        f"/api/projects/{project['id']}/model-package/input-space",
        params={
            "candidate_id": candidate["id"],
            "expected_revision": candidate["revision"],
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    stored_project = client.app.state.store.get_project(project["id"])
    runtime = client.app.state.project_runtime_resolver.resolve(
        stored_project
    ).runtime
    reference = runtime.support_references[surface["distance_target_key"]]
    assert payload["supported_threshold"] == reference["supported_threshold"]
    assert payload["caution_threshold"] == reference["caution_threshold"]
    runtime_support = runtime.support_by_target(
        client.app.state.store.get_candidate(candidate["id"], project["id"])
    )[surface["distance_target_key"]]
    assert runtime_support.supported_threshold == round(
        payload["supported_threshold"], 4
    )
    assert runtime_support.caution_threshold == round(
        payload["caution_threshold"], 4
    )


def test_large_wear_input_space_reuses_fixed_training_embedding(
    client,
) -> None:
    task_id = "wear-curve-v1"
    task = next(
        item
        for item in client.get("/api/task-definitions").json()
        if item["definition"]["task_definition"]["id"] == task_id
    )
    project = next(
        item
        for item in client.get("/api/projects").json()
        if item["task_id"] == task_id
    )
    candidates = client.get(
        f"/api/projects/{project['id']}/candidates"
    ).json()
    while len(candidates) < 3:
        starter = deepcopy(task["starter_candidate"])
        starter["name"] = f"速度回帰候補{len(candidates) + 1}"
        created = client.post(
            f"/api/projects/{project['id']}/candidates",
            json=starter,
        )
        assert created.status_code == 201, created.text
        candidates.append(created.json())
    stored_project = client.app.state.store.get_project(project["id"])
    runtime = client.app.state.project_runtime_resolver.resolve(
        stored_project
    ).runtime
    target = next(iter(runtime.support_references))
    assert len(runtime.support_references[target]["vectors"]) >= 14_000
    candidate = candidates[0]
    request = {
        "candidate_id": candidate["id"],
        "expected_revision": candidate["revision"],
    }
    with _INPUT_SPACE_CANONICAL_LOCK:
        _INPUT_SPACE_CANONICAL.pop(runtime, None)
    with _CACHE_LOCK:
        _TRAINING_EMBEDDINGS.pop(runtime, None)

    first_started = perf_counter()
    first = client.get(
        f"/api/projects/{project['id']}/model-package/input-space",
        params=request,
    )
    first_elapsed = perf_counter() - first_started
    second_started = perf_counter()
    second = client.get(
        f"/api/projects/{project['id']}/model-package/input-space",
        params=request,
    )
    second_elapsed = perf_counter() - second_started

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert len(first.json()["candidate_points"]) >= 3
    assert first.json() == second.json()
    assert first_elapsed < 8.0
    assert second_elapsed < 1.5


def test_flank_wear_input_space_uses_one_point_per_independent_run(
    client,
) -> None:
    task_id = "flank-wear-v1"
    task = next(
        item
        for item in client.get("/api/task-definitions").json()
        if item["definition"]["task_definition"]["id"] == task_id
    )
    project = next(
        (
            item
            for item in client.get("/api/projects").json()
            if item["task_id"] == task_id
        ),
        None,
    )
    if project is None:
        response = client.post(
            "/api/projects",
            json={"name": "flank run-level input-space", "task_id": task_id},
        )
        assert response.status_code == 201, response.text
        project = response.json()
        candidate_response = client.post(
            f"/api/projects/{project['id']}/candidates",
            json=task["starter_candidate"],
        )
        assert candidate_response.status_code == 201, candidate_response.text
        candidate = candidate_response.json()
    else:
        candidate = client.get(
            f"/api/projects/{project['id']}/candidates"
        ).json()[0]
    input_surface = next(
        item
        for item in task["definition"]["application"]["workbench_surfaces"]
        if item["kind"] == "input_space"
    )
    prediction_surface = next(
        item
        for item in task["definition"]["application"]["workbench_surfaces"]
        if item["kind"] == "prediction_space"
    )
    assert input_surface["evidence_context"] == "parent_condition"
    assert prediction_surface["evidence_context"] == "parent_condition"

    stored_project = client.app.state.store.get_project(project["id"])
    resolved = client.app.state.project_runtime_resolver.resolve(stored_project)
    runtime = resolved.runtime
    stored_candidate = client.app.state.store.get_candidate(
        candidate["id"],
        project["id"],
    )
    run_ids = {
        str(rows[0]["parent_key"])
        for rows in runtime.reference_rows
    }
    space = resolve_training_metric_space(
        runtime,
        target_keys=(input_surface["distance_target_key"],),
        allowed_context_ids=run_ids,
        evidence_context="parent_condition",
    )
    assert runtime.reference_vectors.shape[0] == 180
    assert space.vectors.shape == runtime.reference_vectors.shape
    assert len(space.context_ids) == 180

    response = client.get(
        f"/api/projects/{project['id']}/model-package/input-space",
        params={
            "candidate_id": candidate["id"],
            "expected_revision": candidate["revision"],
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_training_contexts"] == 180
    assert payload["displayed_training_contexts"] == 180
    selected = next(
        point
        for point in payload["candidate_points"]
        if point["candidate_id"] == candidate["id"]
    )
    runtime_support, nearest = runtime.evidence(stored_candidate)
    assert selected["island_status"] == runtime_support.status
    assert selected["island_distance"] == pytest.approx(
        runtime_support.distance,
        abs=5e-5,
    )
    assert selected["nearest_training_context_id"] == nearest[0]["parent_key"]


@pytest.mark.parametrize(
    "task_id",
    (
        "annealed-properties-v1",
        "battery-degradation-v1",
        "concrete-strength-v1",
        "flank-wear-v1",
        "heat-treatment-tradeoff-v1",
        "hot-rolled-properties-v1",
        "mpea-hardness-process-v1",
        "mpea-room-tensile-v1",
        "secom-yield-risk-v1",
        "wear-curve-v1",
        "welding-consumable-stage-b-v1",
        "welding-stage-c-properties-v1",
    ),
)
def test_every_declared_input_space_task_can_place_its_starter_candidate(
    client,
    task_id: str,
) -> None:
    task = next(
        item
        for item in client.get("/api/task-definitions").json()
        if item["definition"]["task_definition"]["id"] == task_id
    )
    existing = next(
        (
            project
            for project in client.get("/api/projects").json()
            if project["task_id"] == task_id
        ),
        None,
    )
    if existing is None:
        created = client.post(
            "/api/projects",
            json={"name": f"{task_id} input-space smoke", "task_id": task_id},
        )
        assert created.status_code == 201, created.text
        project_id = created.json()["id"]
        candidate_response = client.post(
            f"/api/projects/{project_id}/candidates",
            json=task["starter_candidate"],
        )
        assert candidate_response.status_code == 201, candidate_response.text
        candidate = candidate_response.json()
    else:
        project_id = existing["id"]
        candidate = client.get(
            f"/api/projects/{project_id}/candidates"
        ).json()[0]
    response = client.get(
        f"/api/projects/{project_id}/model-package/input-space",
        params={
            "candidate_id": candidate["id"],
            "expected_revision": candidate["revision"],
        },
    )
    assert response.status_code == 200, f"{task_id}: {response.text}"
    payload = response.json()
    assert payload["candidate_points"]
    assert payload["training_points"]
    assert payload["total_training_contexts"] >= payload[
        "displayed_training_contexts"
    ]


def test_welding_stage_c_prediction_space_pairs_every_target_on_weld_run(
    client,
) -> None:
    task_id = "welding-stage-c-properties-v1"
    task = next(
        item
        for item in client.get("/api/task-definitions").json()
        if item["definition"]["task_definition"]["id"] == task_id
    )
    surface = next(
        item
        for item in task["definition"]["application"]["workbench_surfaces"]
        if item["kind"] == "prediction_space"
    )
    assert surface["evidence_context"] == "parent_condition"
    project = next(
        (
            item
            for item in client.get("/api/projects").json()
            if item["task_id"] == task_id
        ),
        None,
    )
    if project is None:
        created = client.post(
            "/api/projects",
            json={"name": "Stage C prediction-space regression", "task_id": task_id},
        )
        assert created.status_code == 201, created.text
        project = created.json()
        created_candidate = client.post(
            f"/api/projects/{project['id']}/candidates",
            json=task["starter_candidate"],
        )
        assert created_candidate.status_code == 201, created_candidate.text
        candidate = created_candidate.json()
    else:
        candidate = client.get(
            f"/api/projects/{project['id']}/candidates"
        ).json()[0]

    stored_project = client.app.state.store.get_project(project["id"])
    resolved = client.app.state.project_runtime_resolver.resolve(stored_project)
    package = resolved.runtime.model_package
    canonical = canonical_training_dataset(
        task_id,
        resolved.runtime.data,
        client.app.state.task_registry.contract_for(task_id),
        pipeline_version=package.manifest.feature_pipeline.version,
    )
    outputs_by_parent: dict[str, set[str]] = {}
    for row in canonical["rows"]:
        outputs_by_parent.setdefault(str(row["parent_key"]), set()).update(
            row["outputs"]
        )

    results = {}
    for x_target, y_target in combinations(surface["target_keys"], 2):
        expected_contexts = {
            parent_key
            for parent_key, outputs in outputs_by_parent.items()
            if x_target in outputs and y_target in outputs
        }
        response = client.get(
            f"/api/projects/{project['id']}/model-package/output-space-evidence",
            params={
                "x_target": x_target,
                "y_target": y_target,
                "candidate_id": candidate["id"],
                "expected_revision": candidate["revision"],
                "distance_filter": "all",
                "limit": 200,
            },
        )
        assert response.status_code == 200, (
            f"{x_target} x {y_target}: {response.text}"
        )
        payload = response.json()
        assert payload["evidence_context"] == "parent_condition"
        assert payload["total_contexts"] == len(expected_contexts) > 0
        assert {
            point["context_id"] for point in payload["points"]
        } <= expected_contexts
        results[(x_target, y_target)] = payload["total_contexts"]

    assert results[("TS", "CHARPY_ENERGY")] > 0
    assert results[("TS", "CORROSION_RATE")] > 0
    assert results[("CHARPY_ENERGY", "CORROSION_RATE")] > 0


def test_model_training_data_preserves_row_and_validation_group_semantics(client) -> None:
    response = client.get(
        "/api/projects/battery-degradation-v1-default/model-package/training-data",
        params={"stage": "features", "target": "capacity_percent"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["training_unit"] == "source_row_grouped_by_parent"
    assert payload["stage_counts"]["model_rows"] == payload["stage_counts"]["selected_rows"]
    assert payload["stage_counts"]["model_rows"] > payload["parent_conditions"]
    assert [column["key"] for column in payload["columns"][:2]] == [
        "observation_id",
        "parent_key",
    ]


def test_health_and_candidate_prediction_flow_is_deterministic(client) -> None:
    assert client.get("/api/health").json()["ok"] is True
    assert client.get("/api/model-package").status_code == 404
    package = client.get("/api/projects/default/model-package").json()
    assert package["id"] == "annealed-gp-stable-ard-tutorial-v2"
    assert len(package["manifest_sha256"]) == 64
    assert package["quality_report"]["split"] == "leave-one-parent-condition-out"
    assert {item["target"] for item in package["quality_report"]["targets"]} == {"TS", "YS", "EL", "lambda"}
    assert {item["runtime_type"] for item in package["supported_runtimes"]} == {
        "builtin.linear.v1", "builtin.exact_gp.v1", "builtin.heteroscedastic_exact_gp.v1", "builtin.additive_terms.v1", "builtin.quantile_linear.v1", "builtin.posterior_linear.v1", "sklearn.skops.v1", "lightgbm.booster.v1",
        "gpytorch.static_exact_rbf.v1", "numpyro.dense_posterior.v1",
    }
    task = client.get("/api/projects/default/task-definition").json()
    definition = task["task_definition"]
    assert definition["id"] == "annealed-properties-v1"
    composition = next(group for group in definition["input_groups"] if group["key"] == "composition")
    assert [item["path"].removeprefix("composition.") for item in composition["fields"]] == [
        "C", "Si", "Mn", "P", "S", "Al", "Cu", "Ni", "Cr", "Mo", "Ti", "B", "O", "N",
    ]
    assert {item["key"] for item in definition["outputs"]} == {"TS", "YS", "EL", "lambda"}
    assert all(item["goal_direction"] == "at_least" for item in definition["outputs"])
    assert task["runtime_capability"]["operations"]["response_curve"] is True
    candidate = client.post("/api/projects/default/candidates", json=_payload()).json()
    params = {"expected_revision": candidate["revision"]}
    first = client.post(f"/api/projects/default/candidates/{candidate['id']}/preview", params=params).json()
    second = client.post(f"/api/projects/default/candidates/{candidate['id']}/preview", params=params).json()
    assert first["mode"] == "preview"
    assert first["predictions"] == second["predictions"]
    assert {"TS", "YS", "EL", "lambda"} <= set(first["predictions"])
    assert first["support"]["status"] in {"supported", "caution", "extrapolated"}
    assert 0 <= first["support"]["percentile"] <= 100
    assert {"composition", "metallurgy", "heat_pattern"} == set(first["support"]["components"])
    assert first["support"]["reference_count"] > 1
    assert first["model_meta"]["prediction_interval"]["method"] == "gaussian_process_predictive_distribution"
    assert first["model_meta"]["prediction_interval"]["grouping"] == "condition_context_id"
    assert all(prediction["uncertainty_components"] for prediction in first["predictions"].values())
    assert first["canonical_input"]["input_schema_version"] == "candidate-v2"
    assert first["canonical_input"]["heat_time_basis"] == "line_speed"
    atomic_result = client.post(f"/api/projects/default/candidates/{candidate['id']}/predict", params={"expected_revision": candidate["revision"]}).json()
    detailed = atomic_result["prediction"]
    assert detailed["mode"] == "detailed"
    assert detailed["response_curve"] is None
    curve = client.get(
        f"/api/projects/default/candidates/{candidate['id']}/response-curve",
        params={**params, "target": "TS", "variable": "composition.C", "points": 9},
    ).json()
    assert curve["target"] == "TS"
    assert curve["variable"]["id"] == "composition.C"
    assert len(curve["points"]) == 9
    assert curve["variable"]["current"] in {point["x"] for point in curve["points"]}
    stage_payload = _payload("工程温度")
    stage_payload["inputs"]["heat_pattern"][1]["stage_name"] = "加熱1"
    stage_candidate = client.post("/api/projects/default/candidates", json=stage_payload).json()
    stage_curve = client.get(
        f"/api/projects/default/candidates/{stage_candidate['id']}/response-curve",
        params={"expected_revision": stage_candidate["revision"], "target": "TS", "variable": "heat.stage_temperature_c", "stage_name": "加熱1", "stage_position_m": 480.6667, "points": 5},
    )
    assert stage_curve.status_code == 422
    assert stage_curve.json()["code"] == "response_curve_training_range_unavailable"
    assert "学習範囲を決められない" in stage_curve.json()["message"]
    point_time = client.get(
        f"/api/projects/default/candidates/{candidate['id']}/response-curve",
        params={**params, "target": "TS", "variable": "heat.1.time_min", "points": 5},
    )
    assert point_time.status_code == 422
    assert "ラインスピード" in point_time.json()["message"]
    blank_stage = client.get(
        f"/api/projects/default/candidates/{candidate['id']}/response-curve",
        params={**params, "target": "TS", "variable": "heat.stage_temperature_c", "stage_name": "   ", "stage_position_m": 10, "points": 5},
    )
    assert blank_stage.status_code == 422
    indexed_temperature = client.get(
        f"/api/projects/default/candidates/{candidate['id']}/response-curve",
        params={**params, "target": "TS", "variable": "heat.1.temperature_c", "points": 5},
    )
    assert indexed_temperature.status_code == 422
    assert atomic_result["snapshot"]["payload"]["prediction"] == detailed
    similar = client.get(
        f"/api/projects/default/candidates/{candidate['id']}/similar",
        params=params,
    ).json()
    assert len(similar) == 3
    assert {item["layer"] for item in similar} == {"historical"}
    assert {item["source_scope"] for item in similar} == {"project_reference_data"}
    assert all({"composition", "metallurgy", "heat_pattern"} == set(item["components"]) for item in similar)


def test_snapshot_is_immutable_after_candidate_edit(client) -> None:
    candidate = client.post("/api/projects/default/candidates", json=_payload("固定化テスト")).json()
    snapshot = client.post(f"/api/projects/default/candidates/{candidate['id']}/snapshots").json()
    original = deepcopy(snapshot["payload"])
    changed = _payload("編集後")
    changed["inputs"]["process"]["ls_mpm"] = 145
    assert client.put(f"/api/projects/default/candidates/{candidate['id']}", json={**changed, "expected_revision": candidate["revision"]}).status_code == 200
    stored = client.get(f"/api/projects/default/candidates/{candidate['id']}/snapshots").json()
    assert stored[0]["payload"] == original
    assert stored[0]["payload"]["candidate_id"] == candidate["id"]
    assert stored[0]["payload"]["raw_candidate"]["name"] == "固定化テスト"
    assert "feature_vector" in stored[0]["payload"]["canonical_input"]
    assert stored[0]["payload"]["canonical_input"]["heat_time_basis"] == "line_speed"
    assert "TS" in stored[0]["payload"]["canonical_input"]["normalized_feature_vectors"]
    provenance = stored[0]["payload"]["provenance"]
    assert provenance["model"]["version"]
    assert provenance["feature_pipeline"]["version"]
    assert provenance["training_data"]["source_sha256"]
    assert provenance["similarity"]["version"]


def test_candidate_provenance_is_typed_persisted_and_immutable(client) -> None:
    source = client.post("/api/projects/default/candidates", json=_payload("コピー元")).json()
    copied_payload = _payload("コピー先")
    copied_payload["provenance"] = {
        "source_kind": "copy",
        "source_ref": {
            "project_id": "default",
            "candidate_id": source["id"],
            "candidate_revision": source["revision"],
        },
    }
    copied = client.post("/api/projects/default/candidates", json=copied_payload)
    assert copied.status_code == 201
    assert copied.json()["provenance"] == copied_payload["provenance"]

    renamed = deepcopy(copied_payload)
    renamed["name"] = "名前だけ変更"
    renamed["expected_revision"] = copied.json()["revision"]
    assert client.put(
        f"/api/projects/default/candidates/{copied.json()['id']}", json=renamed
    ).status_code == 200

    rewritten = deepcopy(renamed)
    rewritten["provenance"] = {"source_kind": "direct", "source_ref": None}
    rejected = client.put(
        f"/api/projects/default/candidates/{copied.json()['id']}", json=rewritten
    )
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "candidate_provenance_immutable"
    assert "作成元は変更できません" in rejected.json()["message"]

    missing_source = deepcopy(copied_payload)
    missing_source["provenance"]["source_ref"]["candidate_id"] = "missing"
    invalid = client.post("/api/projects/default/candidates", json=missing_source)
    assert invalid.status_code == 422
    assert "コピー元候補が見つかりません" in invalid.json()["message"]

    stale_source = deepcopy(copied_payload)
    stale_source["provenance"]["source_ref"]["candidate_revision"] = source["revision"] + 1
    invalid_revision = client.post("/api/projects/default/candidates", json=stale_source)
    assert invalid_revision.status_code == 422
    assert "revisionが一致しません" in invalid_revision.json()["message"]


def test_snapshot_source_can_be_deep_linked_only_inside_its_project(client) -> None:
    candidate = client.post("/api/projects/default/candidates", json=_payload("保存元")).json()
    snapshot = client.post(
        f"/api/projects/default/candidates/{candidate['id']}/snapshots"
    ).json()
    opened = client.get(f"/api/projects/default/snapshots/{snapshot['id']}")
    assert opened.status_code == 200
    assert opened.json()["candidate_id"] == candidate["id"]
    assert client.get(
        f"/api/projects/hot-rolling-default/snapshots/{snapshot['id']}"
    ).status_code == 404


def test_archived_copy_source_remains_resolvable(client) -> None:
    source = client.post("/api/projects/default/candidates", json=_payload("archive元")).json()
    client.post(f"/api/projects/default/candidates/{source['id']}/snapshots")
    archived = client.delete(
        f"/api/projects/default/candidates/{source['id']}",
        params={"expected_revision": source["revision"]},
    )
    assert archived.status_code == 204
    source_after_archive = client.get(
        f"/api/projects/default/candidates/{source['id']}",
        params={"include_archived": True},
    ).json()
    assert source_after_archive["archived_at"] is not None

    copied_payload = _payload("archive元から複製")
    copied_payload["provenance"] = {
        "source_kind": "copy",
        "source_ref": {
            "project_id": "default",
            "candidate_id": source["id"],
            "candidate_revision": source_after_archive["revision"],
        },
    }
    copied = client.post("/api/projects/default/candidates", json=copied_payload)
    assert copied.status_code == 201


def test_file_origin_is_rejected_without_desktop_launch_token(client) -> None:
    response = client.options(
        "/api/health",
        headers={"Origin": "null", "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


def test_local_web_origin_allows_parallel_development_ports(client) -> None:
    response = client.options(
        "/api/health",
        headers={
            "Origin": "http://127.0.0.1:5212",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5212"


def test_quality_and_lineage(client) -> None:
    quality = client.get("/api/projects/default/quality").json()
    assert quality["total"] == 0
    assert quality["by_category"] == {}
    assert quality["reference_scenarios"] == quality["issues"]
    assert quality["detected_total"] == 0
    assert quality["detected_issues"] == []
    lineage = client.get("/api/projects/default/lineage/AN-01")
    assert lineage.status_code == 200
    assert "relations" in lineage.json()
    node = lineage.json()["node"]
    assert node["entity_type"] == "焼鈍"
    assert node["source_sheet"] == "焼鈍"
    assert node["source_row"]["焼鈍_key"] == "AN-01"
    assert node["composition"]
    assert len(node["heat_pattern"]) == 6
    assert node["property_summary"]["TS[MPa]"]["count"] > 0
    assert isinstance(node["property_summary"]["TS[MPa]"]["mean"], float)
    assert isinstance(node["property_summary"]["TS[MPa]"]["std"], float)
    assert node["connected_observations"]
    assert node["connected_observations"][0]["id"]
    assert node["observation_groups"]
    assert all(group["stage"] in {"熱延後", "焼鈍後"} for group in node["observation_groups"])
    assert lineage.json()["graph"]["relation_row_count"] > 0
    assert any(edge["route_rows"] for edge in lineage.json()["graph"]["edges"])
    assert lineage.json()["candidate_eligible"] is True
    assert any(edge["source"] == "CR-01" and edge["target"] == "AN-01" for edge in lineage.json()["graph"]["edges"])


def test_lineage_candidate_options_do_not_invent_routes_from_flat_adjacency(client, monkeypatch) -> None:
    project = client.app.state.store.get_project("default")
    assert project is not None
    data = client.app.state.project_runtime_resolver.data_explorer_for(project).data
    melt_column = data.role_to_key["melt"]
    existing_melts = list(data.lineage["AN-01"][melt_column])
    assert len(existing_melts) == 1
    shared_process_melt = "ME-SHARED-PROCESS"
    monkeypatch.setitem(data.composition, shared_process_melt, deepcopy(data.composition[existing_melts[0]]))
    monkeypatch.setitem(data.lineage["AN-01"], melt_column, [*existing_melts, shared_process_melt])

    lineage = client.get("/api/projects/default/lineage/AN-01")
    assert lineage.status_code == 200
    options = [
        option
        for option in lineage.json()["candidate_options"]
        if option["process_key"] == "AN-01"
    ]
    assert {option["melt_key"] for option in options} == {existing_melts[0]}
    created = client.post(
        "/api/projects/default/lineage/AN-01/candidate",
        params={"process_key": "AN-01", "melt_key": shared_process_melt},
    )
    assert created.status_code == 422


def test_lineage_index_is_inspectable(client) -> None:
    index = client.get("/api/projects/default/lineage", params={"query": "AN-01"}).json()
    assert index["relation_rows"] == 51
    assert index["total_entities"] > index["relation_rows"]
    assert index["items"][0]["key"] == "AN-01"
    assert index["items"][0]["family"]
    assert index["items"][0]["route"]
    assert index["items"][0]["peak_temperature_c"] > 0
    assert index["items"][0]["observation_summary"]
    assert client.get("/api/projects/default/lineage", params={"query": index["items"][0]["family"], "entity_type": "焼鈍"}).json()["items"]

    shared = client.get("/api/projects/default/lineage", params={"query": "AN-02"}).json()["items"][0]
    assert shared["melt_keys"] == ["ME-01", "ME-02"]
def test_lineage_graph_can_expand_beyond_the_initial_node_limit(client) -> None:
    assert client.get("/api/projects/default/lineage/AN-01", params={"limit": 0}).status_code == 422
    assert client.get("/api/projects/default/lineage/AN-01", params={"limit": 201}).status_code == 422
    initial = client.get("/api/projects/default/lineage/AN-01", params={"limit": 1})
    assert initial.status_code == 200
    initial_graph = initial.json()["graph"]
    assert initial_graph["node_limit"] == 1
    assert initial_graph["visible_node_count"] == 1
    assert initial_graph["nodes"][0]["key"] == "AN-01"
    assert initial_graph["total_node_count"] > 1
    assert initial_graph["has_more"] is True
    assert initial_graph["omitted_node_count"] == initial_graph["total_node_count"] - 1

    expanded = client.get("/api/projects/default/lineage/AN-01", params={"limit": 200})
    assert expanded.status_code == 200
    expanded_graph = expanded.json()["graph"]
    assert expanded_graph["visible_node_count"] == expanded_graph["total_node_count"]
    assert expanded_graph["has_more"] is False
    assert expanded_graph["omitted_node_count"] == 0


def test_lineage_graph_can_show_every_node_reachable_from_the_selected_key(client) -> None:
    direct = client.get("/api/projects/default/lineage/AN-01", params={"limit": 200})
    assert direct.status_code == 200
    reachable = client.get(
        "/api/projects/default/lineage/AN-01",
        params={"all_reachable": True},
    )
    assert reachable.status_code == 200
    direct_graph = direct.json()["graph"]
    reachable_graph = reachable.json()["graph"]
    assert direct_graph["all_reachable"] is False
    assert reachable_graph["all_reachable"] is True
    assert reachable_graph["visible_node_count"] == reachable_graph["total_node_count"]
    assert reachable_graph["has_more"] is False
    assert reachable_graph["omitted_node_count"] == 0
    assert reachable_graph["visible_node_count"] > direct_graph["visible_node_count"]


def test_lineage_keeps_hot_rolled_and_annealed_observations_separate(client) -> None:
    payload = client.get("/api/projects/default/lineage/AN-03").json()
    node = payload["node"]
    ts_groups = [group for group in node["observation_groups"] if group["property"] == "TS[MPa]"]
    assert {group["stage"] for group in ts_groups} == {"熱延後", "焼鈍後"}
    assert all(group["count"] == len(group["observations"]) for group in ts_groups)
    annealed_properties = {group["property"] for group in node["observation_groups"] if group["stage"] == "焼鈍後"}
    assert {"TS[MPa]", "YS[MPa]", "EL[%]", "均一伸び[%]"} <= annealed_properties
    assert any(point["stage_category"] for point in node["heat_pattern"])
    assert any(point["set_temperature_c"] is not None for point in node["heat_pattern"])
    edge_pairs = {(edge["source"], edge["target"]) for edge in payload["graph"]["edges"]}
    assert ("HR-03", "CR-03") in edge_pairs
    assert ("CR-03", "AN-03") in edge_pairs
    assert ("HR-03", "AN-03") not in edge_pairs


def test_lineage_keeps_partial_observations_without_inventing_outputs(client) -> None:
    response = client.get("/api/projects/default/lineage/HT-07")
    assert response.status_code == 200
    observation = next(item for item in response.json()["node"]["connected_observations"] if item["id"] == "HT-07")
    assert observation["outputs"]["TS[MPa]"] == 538
    assert "YS[MPa]" not in observation["outputs"]
    assert "output_warnings" not in observation

    incompatible = client.post(
        "/api/projects/hot-rolling-default/lineage/AN-01/candidate",
    )
    assert incompatible.status_code == 422
    incompatible_detail = client.get("/api/projects/hot-rolling-default/lineage/AN-01").json()
    assert incompatible_detail["candidate_eligible"] is False

    compatible_detail = client.get("/api/projects/hot-rolling-default/lineage/HR-01").json()
    assert compatible_detail["candidate_eligible"] is True
    compatible = client.post("/api/projects/hot-rolling-default/lineage/HR-01/candidate")
    assert compatible.status_code == 201
    assert compatible.json()["project_id"] == "hot-rolling-default"


def test_candidate_origin_evidence_stays_on_selected_composition_and_relation_route(client) -> None:
    first_candidate = client.post(
        "/api/projects/hot-rolling-default/lineage/HR-02/candidate",
        params={"process_key": "HR-02", "melt_key": "ME-01"},
    )
    assert first_candidate.status_code == 201
    first = client.get(
        f"/api/projects/hot-rolling-default/candidates/{first_candidate.json()['id']}/origin-evidence",
    )
    assert first.status_code == 200
    assert first.json()["observation_ids"] == ["HT-02"]
    assert first.json()["repeat_summary"]["TS[MPa]"] == {
        "mean": 470.0,
        "std": 0.0,
        "n": 1,
    }

    second_candidate = client.post(
        "/api/projects/hot-rolling-default/lineage/HR-02/candidate",
        params={"process_key": "HR-02", "melt_key": "ME-02"},
    )
    assert second_candidate.status_code == 201
    second = client.get(
        f"/api/projects/hot-rolling-default/candidates/{second_candidate.json()['id']}/origin-evidence",
    )
    assert second.status_code == 200
    assert second.json()["observation_ids"] == ["HT-03"]
    assert second.json()["repeat_summary"]["TS[MPa]"]["mean"] == 505.0
