from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from decision_workbench.app import create_app
from decision_workbench.bootstrap.resources import prepare_app_resources
from decision_workbench.contracts.candidate_project_contracts import Candidate
from decision_workbench.data.profile_family_registry import supported_task_ids
import decision_workbench.modeling.observation_regression as observation_module
import decision_workbench.modeling.tabular.runtime as tabular_module
from decision_workbench.persistence.store import Store
from decision_workbench.tasks.task_registry import load_task_contracts

EXTERNAL_TASKS = (
    "heat-treatment-tradeoff-v1",
    "concrete-strength-v1",
    "wear-curve-v1",
    "battery-degradation-v1",
    "secom-yield-risk-v1",
)


@pytest.fixture(scope="module")
def resources():
    return prepare_app_resources()


def _candidate(task_id: str) -> Candidate:
    raw = load_task_contracts()[task_id].canonical_candidate
    now = datetime.now(UTC)
    return Candidate(
        id=f"{task_id}-smoke",
        project_id="external-task-smoke",
        revision=1,
        created_at=now,
        updated_at=now,
        name="外部データ smoke",
        inputs={
            "composition": dict(raw.composition),
            "process": dict(raw.process),
            "categorical": dict(raw.categorical),
            "heat_pattern": None,
        },
        provenance=raw.provenance,
    )


def test_external_tasks_are_registered_with_their_source_rows(resources) -> None:
    assert set(EXTERNAL_TASKS).issubset(resources.task_registry.task_ids)
    assert len(resources.data_by_task["heat-treatment-tradeoff-v1"].observations) == 2400
    assert len(resources.data_by_task["concrete-strength-v1"].observations) == 1600
    assert len(resources.data_by_task["wear-curve-v1"].observations) == 14640
    assert len(resources.data_by_task["battery-degradation-v1"].observations) == 3131
    secom_rows = resources.data_by_task["secom-yield-risk-v1"].observations
    assert len(secom_rows) == 1567
    assert sum(row["eligible"] for row in secom_rows) == 1468
    assert len(resources.data_by_task["mpea-literature-tys-v1"].observations) == 396
    assert "mpea-literature-tys-v1" in resources.task_registry.task_ids
    assert "mpea-room-tensile-v1" in resources.task_registry.task_ids
    assert "mpea-hardness-process-v1" in resources.task_registry.task_ids
    hardness_rows = resources.data_by_task["mpea-hardness-process-v1"].observations
    assert sum(row["eligible"] and "HV" in row["outputs"] for row in hardness_rows) == 52
    assert (
        len({row["parent_key"] for row in hardness_rows if row["eligible"] and "HV" in row["outputs"]})
        == 17
    )


def test_mpea_hardness_curation_keeps_only_explicit_hv_scalars(resources) -> None:
    rows = resources.data_by_task["mpea-hardness-process-v1"].observations

    def state(raw_value: str) -> dict[str, object]:
        row = next(
            item
            for item in rows
            if item["run_context"]["curation"]["values"]["Hardness (HV)"]["raw"] == raw_value
        )
        return row["run_context"]["curation"]["target_status"]["HV"]

    assert state("216 ± 2 HV")["usable"] is True
    assert state("444")["usable"] is True
    assert state("4.6 ± 0.3 GPa")["usable"] is False
    assert state("5.96 GPa (peak nanoindentation hardness)")["usable"] is False
    assert state("10")["usable"] is False


def test_mpea_similarity_uses_the_requested_measurement_cohort(resources) -> None:
    runtime = resources.task_registry.runtime_for("mpea-room-tensile-v1")
    candidate = _candidate("mpea-room-tensile-v1")
    for target in ("TYS", "UTS", "EL"):
        similar = runtime.similarity(candidate, target=target)
        assert similar
        assert all(target in item["outputs"] for item in similar)


def test_mpea_screening_uses_fe_as_an_exact_balance_component(client) -> None:
    project = next(
        item for item in client.get("/api/projects").json() if item["task_id"] == "mpea-room-tensile-v1"
    )
    candidate = client.get(f"/api/projects/{project['id']}/candidates").json()[0]
    response = client.post(
        "/api/screening",
        params={"project_id": project["id"]},
        json={
            "purpose": "design_space_map",
            "base_candidate_id": candidate["id"],
            "base_inputs": candidate["inputs"],
            "samples": 48,
            "target": "TYS",
            "proposal": {"support_policy": "allow_with_warning"},
            "variables": {
                "composition.Ni": {"mode": "range", "min": 20, "max": 30},
                "composition.Co": {"mode": "range", "min": 20, "max": 30},
            },
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["design_space"]["composition_constraints"][0]["balance_path"] == "composition.Fe"
    assert all(
        sum(point["candidate"]["inputs"]["composition"].values()) == pytest.approx(100, abs=0.01)
        for point in payload["points"]
    )


def test_mpea_hardness_screening_uses_the_same_composition_balance(client) -> None:
    project = next(
        item
        for item in client.get("/api/projects").json()
        if item["task_id"] == "mpea-hardness-process-v1"
    )
    candidate = client.get(f"/api/projects/{project['id']}/candidates").json()[0]
    response = client.post(
        "/api/screening",
        params={"project_id": project["id"]},
        json={
            "purpose": "design_space_map",
            "base_candidate_id": candidate["id"],
            "base_inputs": candidate["inputs"],
            "samples": 48,
            "target": "HV",
            "proposal": {"support_policy": "allow_with_warning"},
            "variables": {"composition.Ni": {"mode": "range", "min": 20, "max": 30}},
        },
    )
    assert response.status_code == 201, response.text
    assert all(
        sum(point["candidate"]["inputs"]["composition"].values()) == pytest.approx(100, abs=0.01)
        for point in response.json()["points"]
    )


@pytest.mark.parametrize("task_id", EXTERNAL_TASKS)
def test_external_task_package_predicts_and_draws_a_curve(resources, task_id: str) -> None:
    fixture = load_task_contracts()[task_id]
    candidate = _candidate(task_id)
    runtime = resources.task_registry.runtime_for(task_id)
    prediction = runtime.predict(candidate)
    assert set(prediction["predictions"]) == {item.key for item in fixture.task_definition.outputs}
    assert all(item.lower <= item.value <= item.upper for item in prediction["predictions"].values())
    curve_variable = fixture.task_definition.response_curve_variables[0].path
    assert curve_variable is not None
    curve = runtime.response_curve_result(
        candidate,
        fixture.task_definition.outputs[0].key,
        curve_variable,
        17,
    )
    assert len(curve["points"]) == 17
    assert all(point["lower"] <= point["value"] <= point["upper"] for point in curve["points"])


def test_wear_curve_never_displays_negative_wear(resources) -> None:
    runtime = resources.task_registry.runtime_for("wear-curve-v1")
    candidate = _candidate("wear-curve-v1")
    curve = runtime.response_curve_result(
        candidate,
        "wear_vb_um",
        "process.cutting_distance_m",
        17,
    )
    assert min(point["lower"] for point in curve["points"]) >= 0
    prediction = runtime.predict(candidate)["predictions"]["wear_vb_um"]
    assert min(prediction.quantiles.values()) >= 0
    assert prediction.quantiles["0.50"] == pytest.approx(prediction.value, abs=1e-4)


def test_concrete_age_curve_does_not_decrease(resources) -> None:
    runtime = resources.task_registry.runtime_for("concrete-strength-v1")
    candidate = _candidate("concrete-strength-v1")
    curve = runtime.response_curve_result(
        candidate,
        "compressive_strength_mpa",
        "process.age_days",
        51,
    )
    values = [point["value"] for point in curve["points"]]
    assert values == sorted(values)


def test_battery_curves_are_monotone_and_discharge_rate_changes_degradation(
    resources,
) -> None:
    runtime = resources.task_registry.runtime_for("battery-degradation-v1")
    candidate = _candidate("battery-degradation-v1")
    end_values: list[float] = []
    for discharge_rate in (0.5, 1.0):
        candidate.inputs.process["discharge_rate_c"] = discharge_rate
        curve = runtime.response_curve_result(
            candidate,
            "capacity_percent",
            "process.cycle_index",
            101,
        )
        values = [point["value"] for point in curve["points"]]
        assert values == sorted(values, reverse=True)
        end_values.append(values[-1])
    assert len(set(end_values)) == 2
    candidate.inputs.process["cycle_index"] = 1.0
    prediction = runtime.predict(candidate)
    capacity = prediction["predictions"]["capacity_percent"]
    assert capacity.value <= 110
    assert capacity.upper <= 110
    assert max(capacity.quantiles.values()) <= 110
    assert prediction["model_meta"]["model"]["method"].startswith("monotonic")
    assert prediction["model_meta"]["package"]["runtime_types"] == ["lightgbm.booster.v1"]


def test_battery_curve_family_and_screening_use_task_numeric_domains(
    client,
    resources,
) -> None:
    runtime = resources.task_registry.runtime_for("battery-degradation-v1")
    candidate = _candidate("battery-degradation-v1")
    family = runtime.curve_family_result(
        candidate,
        "capacity_percent",
        "process.discharge_rate_c",
        3,
        15,
    )
    assert [series["level"] for series in family["series"]] == pytest.approx(
        [0.5, 2 ** -0.5, 1.0],
        abs=5e-6,
    )
    project_id = "battery-degradation-v1-default"
    base = client.get(f"/api/projects/{project_id}/candidates").json()[0]
    response = client.post(
        f"/api/screening?project_id={project_id}",
        json={
            "purpose": "design_space_map",
            "base_candidate_id": base["id"],
            "base_inputs": base["inputs"],
            "samples": 48,
            "seed": 671,
            "target": "capacity_percent",
            "variables": {
                "process.cycle_index": {"mode": "range", "min": 1, "max": 100},
                "process.discharge_rate_c": {"mode": "range", "min": 0.5, "max": 1.0},
            },
            "proposal": {"support_policy": "allow_with_warning"},
        },
    )
    assert response.status_code == 201, response.text
    points = response.json()["points"]
    assert points
    assert all(
        float(point["inputs"]["process.cycle_index"]).is_integer()
        for point in points
    )


def test_battery_similarity_deduplicates_cells(resources) -> None:
    runtime = resources.task_registry.runtime_for("battery-degradation-v1")
    similar = runtime.similarity(_candidate("battery-degradation-v1"))
    assert len(similar) == 4
    assert len({item["parent_key"] for item in similar}) == 4


def test_lightgbm_native_batch_preserves_order_and_scalar_semantics(
    resources,
) -> None:
    runtime = resources.task_registry.runtime_for("battery-degradation-v1")
    candidates = []
    for index, cycle in enumerate((1.0, 250.0, 900.0), start=1):
        candidate = _candidate("battery-degradation-v1")
        candidate.id = f"battery-batch-{index}"
        candidate.inputs.process["cycle_index"] = cycle
        candidates.append(candidate)

    assert runtime.supports_batch_prediction is True
    batch = runtime.predict_batch(candidates)
    scalar = [runtime.predict(candidate) for candidate in candidates]

    assert [item["candidate_id"] for item in batch] == [candidate.id for candidate in candidates]
    for batch_item, scalar_item in zip(batch, scalar, strict=True):
        assert batch_item["predictions"] == scalar_item["predictions"]
        assert batch_item["canonical_input"] == scalar_item["canonical_input"]
        assert batch_item["support"] == scalar_item["support"]
        assert batch_item["warnings"] == scalar_item["warnings"]
        assert batch_item["similar"] == []


def test_non_batch_tabular_runtime_does_not_claim_native_batch(resources) -> None:
    runtime = resources.task_registry.runtime_for("mpea-room-tensile-v1")

    assert runtime.supports_batch_prediction is False


def test_runtime_predictions_do_not_reload_task_documents(
    resources,
    monkeypatch,
) -> None:
    def fail_reload():
        raise AssertionError("Task document was reloaded during prediction")

    tabular = resources.task_registry.runtime_for("mpea-room-tensile-v1")
    observation = resources.task_registry.runtime_for("welding-stage-c-properties-v1")
    monkeypatch.setattr(
        tabular_module,
        "load_task_definitions",
        fail_reload,
    )
    monkeypatch.setattr(
        observation_module,
        "load_task_contracts",
        fail_reload,
    )

    assert tabular.predict_core(_candidate("mpea-room-tensile-v1"))
    assert observation.predict_core(_candidate("welding-stage-c-properties-v1"))


def test_secom_prediction_is_a_calibrated_binary_probability(resources) -> None:
    runtime = resources.task_registry.runtime_for("secom-yield-risk-v1")
    prediction = runtime.predict(_candidate("secom-yield-risk-v1"))
    risk = prediction["predictions"]["fail_probability"]
    assert risk.target_kind == "binary"
    assert risk.point_statistic == "probability"
    assert risk.predictive_family == "bernoulli_logit"
    assert 0 <= risk.value <= 1
    assert risk.lower == risk.value == risk.upper
    assert prediction["model_meta"]["model"]["method"].startswith("calibrated")


def test_external_sources_are_bundled_with_readme_provenance() -> None:
    root = Path(__file__).resolve().parents[2] / "data/source/external"
    assert {path.name for path in root.glob("*_README.md")} == {
        "concrete_README.md",
        "heat_treatment_README.md",
        "wear_curve_README.md",
        "battery_README.md",
        "mpea_zenodo_18021833_README.md",
        "secom_README.md",
    }


def test_external_starters_are_installed_explicitly_in_an_existing_database(
    tmp_path: Path, resources, monkeypatch
) -> None:
    database = tmp_path / "existing-workbench.db"
    Store(database)
    monkeypatch.setenv("WORKBENCH_DEMO_SEED", "all")
    app = create_app(db_path=database, _resources=resources)
    with TestClient(app) as client:
        for task_id in EXTERNAL_TASKS:
            project_id = f"{task_id}-default"
            candidates = client.get(f"/api/projects/{project_id}/candidates")
            assert candidates.status_code == 200
            assert len(candidates.json()) == 3


@pytest.mark.parametrize(
    ("profile_name", "task_id"),
    (
        ("tabular-profile-heat-treatment-v1.json", "heat-treatment-tradeoff-v1"),
        ("tabular-profile-concrete-v1.json", "concrete-strength-v1"),
        ("tabular-profile-wear-curve-v1.json", "wear-curve-v1"),
        ("tabular-profile-battery-degradation-v1.json", "battery-degradation-v1"),
        ("tabular-profile-secom-yield-v1.json", "secom-yield-risk-v1"),
    ),
)
def test_tabular_profile_exposes_its_task_to_project_creation(profile_name: str, task_id: str) -> None:
    profile_path = Path(__file__).resolve().parents[1] / "src/decision_workbench/data" / profile_name
    import json

    document = json.loads(profile_path.read_text(encoding="utf-8"))
    assert supported_task_ids(document) == (task_id,)


@pytest.mark.parametrize("task_id", EXTERNAL_TASKS)
def test_external_dataset_and_package_can_create_a_project(client, task_id: str) -> None:
    options = client.get("/api/project-creation-options").json()
    dataset = next(item for item in options["datasets"] if task_id in item["supported_task_ids"])
    view_id = dataset["dataset_views"][0]["id"]
    package = next(
        item
        for item in options["model_packages"]
        if item["task_id"] == task_id
        and item["manifest_json"]["provenance"]["training_data_id"]
        == f"sha256:{dataset['data_asset']['sha256']}"
    )
    response = client.post(
        "/api/projects",
        json={
            "name": f"{task_id} project creation smoke",
            "task_id": task_id,
            "dataset_view_revision_id": view_id,
            "model_package_ref_id": package["id"],
        },
    )
    assert response.status_code == 201, response.text
    project_id = response.json()["id"]
    canonical = load_task_contracts()[task_id].canonical_candidate
    candidate_response = client.post(
        f"/api/projects/{project_id}/candidates",
        json={
            "name": "外部データ API smoke",
            "inputs": {
                "composition": canonical.composition,
                "process": canonical.process,
                "categorical": canonical.categorical,
                "heat_pattern": canonical.heat_pattern,
            },
            "provenance": canonical.provenance.model_dump(),
        },
    )
    assert candidate_response.status_code == 201, candidate_response.text
    candidate = candidate_response.json()
    fixture = load_task_contracts()[task_id].task_definition
    curve_response = client.get(
        f"/api/projects/{project_id}/candidates/{candidate['id']}/response-curve",
        params={
            "expected_revision": candidate["revision"],
            "target": fixture.outputs[0].key,
            "variable": fixture.response_curve_variables[0].path,
            "points": 9,
        },
    )
    assert curve_response.status_code == 200, curve_response.text
    assert len(curve_response.json()["points"]) == 9
    if task_id == "battery-degradation-v1":
        family_response = client.get(
            f"/api/projects/{project_id}/candidates/{candidate['id']}/curve-family",
            params={
                "expected_revision": candidate["revision"],
                "target": fixture.outputs[0].key,
                "points": 9,
            },
        )
        assert family_response.status_code == 200, family_response.text
        family = family_response.json()
        assert len(family["series"]) == 1
        assert len(family["series"][0]["points"]) == 9
        assert family["output_range"]["min"] < family["output_range"]["max"]


def test_calce_battery_rows_are_real_measurements_without_synthetic_quality_flags(
    client,
) -> None:
    response = client.get("/api/projects/battery-degradation-v1-default/quality")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["detected_total"] == 0
    assert payload["detected_by_type"] == {}
    candidates = client.get("/api/projects/battery-degradation-v1-default/candidates").json()
    assert len(candidates) == 3
    assert len({item["inputs"]["process"]["cycle_index"] for item in candidates}) == 1


def test_response_contour_is_revision_bound_and_masks_extrapolated_cells(
    client,
) -> None:
    project_id = "battery-degradation-v1-default"
    candidate = client.get(f"/api/projects/{project_id}/candidates").json()[0]
    response = client.get(
        f"/api/projects/{project_id}/candidates/{candidate['id']}/response-contour",
        params={
            "expected_revision": candidate["revision"],
            "target": "capacity_percent",
            "x_variable": "process.cycle_index",
            "y_variable": "process.discharge_rate_c",
            "points": 7,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["candidate_revision"] == candidate["revision"]
    assert payload["grid_shape"] == [7, 7]
    assert len(payload["cells"]) == 49
    assert payload["x_axis"]["min"] == payload["x_axis"]["training_range"]["min"]
    assert payload["x_axis"]["max"] == payload["x_axis"]["training_range"]["max"]
    runtime = client.app.state.task_registry.runtime_for("battery-degradation-v1")
    assert (
        payload["x_axis"]["training_range"]["min"],
        payload["x_axis"]["training_range"]["max"],
    ) == runtime.training_range_for("capacity_percent", "process.cycle_index")
    assert (
        payload["y_axis"]["training_range"]["min"],
        payload["y_axis"]["training_range"]["max"],
    ) == runtime.training_range_for("capacity_percent", "process.discharge_rate_c")
    assert all(
        cell["prediction"] is not None and cell["support"] is not None
        for cell in payload["cells"]
        if not cell["invalid_reason"]
    )
    assert all(
        cell["displayable"] == (cell["support"]["status"] != "extrapolated")
        for cell in payload["cells"]
        if cell["support"] is not None
    )
    renamed = client.put(
        f"/api/projects/{project_id}/candidates/{candidate['id']}",
        json={
            "name": "表示名だけ変更",
            "inputs": candidate["inputs"],
            "provenance": candidate["provenance"],
            "expected_revision": candidate["revision"],
        },
    ).json()
    refreshed = client.get(
        f"/api/projects/{project_id}/candidates/{candidate['id']}/response-contour",
        params={
            "expected_revision": renamed["revision"],
            "target": "capacity_percent",
            "x_variable": "process.cycle_index",
            "y_variable": "process.discharge_rate_c",
            "points": 7,
        },
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["candidate_revision"] == renamed["revision"]

    stale = client.get(
        f"/api/projects/{project_id}/candidates/{candidate['id']}/response-contour",
        params={
            "expected_revision": renamed["revision"] + 1,
            "target": "capacity_percent",
            "x_variable": "process.cycle_index",
            "y_variable": "process.discharge_rate_c",
            "points": 7,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "revision_conflict"
