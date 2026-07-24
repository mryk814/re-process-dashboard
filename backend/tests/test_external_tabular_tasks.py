from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from material_workbench.app import _prepare_app_resources, create_app
from material_workbench.contracts.schemas import Candidate
from material_workbench.tasks.task_registry import load_task_contracts
from material_workbench.data.profile_document import supported_task_ids
from material_workbench.persistence.store import Store


EXTERNAL_TASKS = (
    "heat-treatment-tradeoff-v1",
    "concrete-strength-v1",
    "wear-curve-v1",
    "battery-degradation-v1",
)


@pytest.fixture(scope="module")
def resources():
    return _prepare_app_resources()


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
    assert len(resources.data_by_source["external_heat_treatment"].observations) == 2400
    assert len(resources.data_by_source["external_concrete"].observations) == 1600
    assert len(resources.data_by_source["external_wear_curve"].observations) == 14640
    assert len(resources.data_by_source["external_battery_degradation"].observations) == 9090
    assert len(resources.data_by_source["external_mpea_literature"].observations) == 396
    assert "mpea-literature-tys-v1" in resources.task_registry.task_ids
    assert "mpea-room-tensile-v1" in resources.task_registry.task_ids
    assert "mpea-hardness-process-v1" in resources.task_registry.task_ids
    hardness_rows = resources.data_by_source["mpea-hardness-process-v1"].observations
    assert sum(row["eligible"] and "HV" in row["outputs"] for row in hardness_rows) == 52
    assert len({
        row["parent_key"]
        for row in hardness_rows
        if row["eligible"] and "HV" in row["outputs"]
    }) == 17


def test_mpea_hardness_curation_keeps_only_explicit_hv_scalars(resources) -> None:
    rows = resources.data_by_source["mpea-hardness-process-v1"].observations

    def state(raw_value: str) -> dict[str, object]:
        row = next(
            item for item in rows
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
        item for item in client.get("/api/projects").json()
        if item["task_id"] == "mpea-room-tensile-v1"
    )
    candidate = client.get(f"/api/projects/{project['id']}/candidates").json()[0]
    response = client.post("/api/screening", params={"project_id": project["id"]}, json={
        "base_candidate_id": candidate["id"],
        "base_inputs": candidate["inputs"],
        "samples": 48,
        "target": "TYS",
        "variables": {
            "composition.Ni": {"mode": "range", "min": 20, "max": 30},
            "composition.Co": {"mode": "range", "min": 20, "max": 30},
        },
    })
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["design_space"]["composition_constraints"][0]["balance_path"] == "composition.Fe"
    assert all(
        sum(point["candidate"]["inputs"]["composition"].values()) == pytest.approx(100, abs=0.01)
        for point in payload["points"]
    )


def test_mpea_hardness_screening_uses_the_same_composition_balance(client) -> None:
    project = next(
        item for item in client.get("/api/projects").json()
        if item["task_id"] == "mpea-hardness-process-v1"
    )
    candidate = client.get(f"/api/projects/{project['id']}/candidates").json()[0]
    response = client.post("/api/screening", params={"project_id": project["id"]}, json={
        "base_candidate_id": candidate["id"],
        "base_inputs": candidate["inputs"],
        "samples": 48,
        "target": "HV",
        "variables": {"composition.Ni": {"mode": "range", "min": 20, "max": 30}},
    })
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


def test_battery_curves_are_monotone_and_conditions_change_degradation(resources) -> None:
    runtime = resources.task_registry.runtime_for("battery-degradation-v1")
    candidate = _candidate("battery-degradation-v1")
    end_values: list[float] = []
    for temperature in (15.0, 25.0, 45.0):
        candidate.inputs.process["ambient_temp_c"] = temperature
        curve = runtime.response_curve_result(
            candidate,
            "capacity_percent",
            "process.cycle_index",
            101,
        )
        values = [point["value"] for point in curve["points"]]
        assert values == sorted(values, reverse=True)
        end_values.append(values[-1])
    assert len(set(end_values)) == 3
    candidate.inputs.process["cycle_index"] = 0.0
    prediction = runtime.predict(candidate)
    capacity = prediction["predictions"]["capacity_percent"]
    assert capacity.value <= 110
    assert capacity.upper <= 110
    assert max(capacity.quantiles.values()) <= 110
    assert prediction["model_meta"]["model"]["method"].startswith("monotonic")
    assert prediction["model_meta"]["package"]["runtime_types"] == ["lightgbm.booster.v1"]


def test_battery_similarity_deduplicates_cells(resources) -> None:
    runtime = resources.task_registry.runtime_for("battery-degradation-v1")
    similar = runtime.similarity(_candidate("battery-degradation-v1"))
    assert len(similar) == 6
    assert len({item["parent_key"] for item in similar}) == 6


def test_external_sources_are_bundled_with_readme_provenance() -> None:
    root = Path(__file__).resolve().parents[2] / "data/source/external"
    assert {path.name for path in root.glob("*_README.md")} == {
        "concrete_README.md",
        "heat_treatment_README.md",
        "wear_curve_README.md",
        "battery_README.md",
        "mpea_zenodo_18021833_README.md",
    }


def test_new_external_starters_are_seeded_when_opening_an_existing_database(
    tmp_path: Path, resources
) -> None:
    database = tmp_path / "existing-workbench.db"
    Store(database)
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
    ),
)
def test_tabular_profile_exposes_its_task_to_project_creation(
    profile_name: str, task_id: str
) -> None:
    profile_path = (
        Path(__file__).resolve().parents[1]
        / "src/material_workbench/data"
        / profile_name
    )
    import json

    document = json.loads(profile_path.read_text(encoding="utf-8"))
    assert supported_task_ids(document) == (task_id,)


@pytest.mark.parametrize("task_id", EXTERNAL_TASKS)
def test_external_dataset_and_package_can_create_a_project(client, task_id: str) -> None:
    options = client.get("/api/project-creation-options").json()
    dataset = next(
        item for item in options["datasets"] if task_id in item["supported_task_ids"]
    )
    view_id = dataset["dataset_views"][0]["id"]
    package = next(
        item
        for item in options["model_packages"]
        if item["task_id"] == task_id
        and item["manifest_json"]["provenance"]["training_data_id"]
        == f"sha256:{dataset['data_asset']['sha256']}"
    )
    response = client.post("/api/projects", json={
        "name": f"{task_id} project creation smoke",
        "task_id": task_id,
        "dataset_view_revision_id": view_id,
        "model_package_ref_id": package["id"],
    })
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


def test_battery_quality_flags_keep_dirty_rows_visible_and_trainable(client) -> None:
    response = client.get("/api/projects/battery-degradation-v1-default/quality")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["detected_total"] == 2
    assert payload["detected_by_type"] == {
        "out_of_range": 1,
        "suspicious_distribution": 1,
    }
    details = " ".join(item["detail"] for item in payload["detected_issues"])
    assert "4,577/9,090" in details
    assert "5,864/9,090" in details
    assert "自動除外していません" in details
    candidates = client.get(
        "/api/projects/battery-degradation-v1-default/candidates"
    ).json()
    assert len(candidates) == 3
    assert len({item["inputs"]["process"]["cycle_index"] for item in candidates}) == 1
