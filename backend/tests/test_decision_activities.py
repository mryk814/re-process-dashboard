from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from material_workbench.application.decision_activity_registry import build_registry
from material_workbench.contracts.schemas import Candidate
from material_workbench.domain.candidate_inputs import (
    heat_time_driver_path,
    with_input_values,
)
from material_workbench.persistence.store import Store
from material_workbench.tasks.task_registry import load_task_contracts


ELEMENTS = ("C", "Si", "Mn", "P", "S", "Al", "Cu", "Ni", "Cr", "Mo", "Ti", "B", "O", "N")


def _candidate_payload() -> dict:
    return {
        "name": "ロバストネス確認",
        "inputs": {
            "composition": {
                **{key: 0.0 for key in ELEMENTS},
                "C": 0.08,
                "Si": 0.3,
                "Mn": 1.5,
            },
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


def _run_payload(revision: int, *, amount: float = 0.002, seed: int = 17) -> dict:
    return {
        "expected_revision": revision,
        "parameters": {
            "schema_version": "robustness-parameters/v1",
            "sample_count": 8,
            "seed": seed,
            "tolerance_profile": {
                "fields": {
                    "composition.C": {"kind": "absolute", "amount": amount},
                },
            },
        },
    }


def test_decision_activity_migration_is_additive_and_idempotent(tmp_path) -> None:
    database = tmp_path / "workbench.db"
    Store(database)
    Store(database)

    with sqlite3.connect(database) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(decision_activity_runs)")
        }
        marker = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE id='decision-activity-run-v1'"
        ).fetchone()

    assert {
        "id", "semantic_identity", "project_id", "candidate_id",
        "activity_id", "activity_version", "payload", "created_at",
    } <= columns
    assert marker == ("immutable-decision-activity-run-v1",)


def _annealed_definition():
    return load_task_contracts()["annealed-properties-v1"].task_definition


def test_line_speed_tolerance_preserves_physical_heat_positions() -> None:
    now = datetime.now(UTC)
    candidate = Candidate.model_validate({
        **_candidate_payload(),
        "id": "candidate",
        "project_id": "default",
        "revision": 1,
        "created_at": now,
        "updated_at": now,
    })

    varied = with_input_values(
        candidate, {"process.ls_mpm": 206.0}, _annealed_definition()
    )

    assert [point.time_s for point in varied.inputs.heat_pattern or []] == [
        0.0, 140.0, 170.0, 325.0,
    ]


def test_heat_time_driver_comes_from_the_task_contract_not_a_field_name() -> None:
    """公差解析が ls_mpm を直接知らないことを固定する。"""

    assert heat_time_driver_path(_annealed_definition()) == "process.ls_mpm"
    assert (
        heat_time_driver_path(
            load_task_contracts()["concrete-strength-v1"].task_definition
        )
        is None
    )


def test_robustness_activity_is_available_deterministic_and_persistent(client) -> None:
    candidate = client.post(
        "/api/projects/default/candidates", json=_candidate_payload()
    ).json()
    availability = client.get(
        "/api/projects/default/decision-activities",
        params={
            "candidate_id": candidate["id"],
            "expected_revision": candidate["revision"],
        },
    )

    assert availability.status_code == 200
    robustness = next(
        item for item in availability.json()
        if item["definition"]["activity_id"] == "robustness-analysis-v1"
    )
    assert robustness["available"] is True

    url = (
        f"/api/projects/default/candidates/{candidate['id']}"
        "/decision-activities/robustness-analysis-v1/runs"
    )
    first = client.post(url, json=_run_payload(candidate["revision"]))
    second = client.post(url, json=_run_payload(candidate["revision"]))

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json() == second.json()
    run = first.json()
    assert run["provenance"]["candidate_revision"] == candidate["revision"]
    assert run["provenance"]["model_package_digest"]
    assert run["provenance"]["feature_pipeline_digest"]
    assert run["result"]["accepted_samples"] == 8
    assert run["result"]["target_summaries"]
    target = run["result"]["target_summaries"][0]
    assert set(target) >= {"input_variation", "model_uncertainty"}
    assert target["input_variation"]["coverage"] == "central_90_percent"
    assert target["model_uncertainty"]["semantics"] == "runtime_predictive_interval"
    assert "因果効果ではありません" in " ".join(run["result"]["warnings"])

    listed = client.get(
        "/api/projects/default/decision-activity-runs",
        params={"candidate_id": candidate["id"]},
    )
    restored = client.get(
        f"/api/projects/default/decision-activity-runs/{run['id']}"
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [run["id"]]
    assert restored.json() == run


def test_robustness_uses_the_project_design_space_and_pins_it(client) -> None:
    reference = client.get("/api/projects/default").json()
    seed_project = client.post(
        "/api/projects",
        json={
            "name": "Design Space seed",
            "task_id": reference["task_id"],
            "dataset_view_revision_id": reference["dataset_view_revision_id"],
            "model_package_ref_id": reference["model_package_ref_id"],
        },
    ).json()
    space = seed_project["design_space"]
    space["design_space_id"] = "robustness-carbon-window"
    space["revision"] = 2
    for domain in space["numeric_domains"]:
        if domain["path"] == "composition.C":
            domain["range"] = {"min": 0.075, "max": 0.085}
            break
    project_response = client.post(
        "/api/projects",
        json={
            "name": "公差用Design Space",
            "task_id": reference["task_id"],
            "dataset_view_revision_id": reference["dataset_view_revision_id"],
            "model_package_ref_id": reference["model_package_ref_id"],
            "design_space": space,
        },
    )
    assert project_response.status_code == 201, project_response.text
    project = project_response.json()
    candidate = client.post(
        f"/api/projects/{project['id']}/candidates", json=_candidate_payload()
    ).json()
    url = (
        f"/api/projects/{project['id']}/candidates/{candidate['id']}"
        "/decision-activities/robustness-analysis-v1/runs"
    )

    outside = client.post(
        url, json=_run_payload(candidate["revision"], amount=0.02)
    )
    assert outside.status_code == 422
    assert "Project Design Space" in (
        outside.json().get("detail") or outside.json().get("message", "")
    )

    accepted = client.post(
        url, json=_run_payload(candidate["revision"], amount=0.002)
    )
    assert accepted.status_code == 201, accepted.text
    provenance = accepted.json()["provenance"]
    assert provenance["project_design_space_digest"] == project["design_space_digest"]
    assert provenance["project_design_space_binding_provenance"] == "explicit"


def test_robustness_activity_rejects_out_of_task_range_without_clipping(client) -> None:
    candidate = client.post(
        "/api/projects/default/candidates", json=_candidate_payload()
    ).json()
    url = (
        f"/api/projects/default/candidates/{candidate['id']}"
        "/decision-activities/robustness-analysis-v1/runs"
    )

    response = client.post(
        url,
        json=_run_payload(candidate["revision"], amount=1_000.0),
    )

    assert response.status_code == 422
    assert "許容範囲を超えています" in response.json()["message"]


def test_activity_run_keeps_candidate_as_an_archived_record(client) -> None:
    candidate = client.post(
        "/api/projects/default/candidates", json=_candidate_payload()
    ).json()
    run_url = (
        f"/api/projects/default/candidates/{candidate['id']}"
        "/decision-activities/robustness-analysis-v1/runs"
    )
    assert client.post(
        run_url, json=_run_payload(candidate["revision"])
    ).status_code == 201

    deleted = client.delete(
        f"/api/projects/default/candidates/{candidate['id']}",
        params={"expected_revision": candidate["revision"]},
    )
    archived = client.get(
        f"/api/projects/default/candidates/{candidate['id']}",
        params={"include_archived": True},
    )

    assert deleted.status_code == 204
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None


def test_robustness_activity_uses_only_the_declared_composition_balance(client) -> None:
    project = next(
        item for item in client.get("/api/projects").json()
        if item["task_id"] == "mpea-room-tensile-v1"
    )
    candidate = client.get(
        f"/api/projects/{project['id']}/candidates"
    ).json()[0]
    url = (
        f"/api/projects/{project['id']}/candidates/{candidate['id']}"
        "/decision-activities/robustness-analysis-v1/runs"
    )
    payload = {
        "expected_revision": candidate["revision"],
        "parameters": {
            "schema_version": "robustness-parameters/v1",
            "sample_count": 8,
            "seed": 4,
            "tolerance_profile": {
                "fields": {
                    "composition.Ni": {"kind": "absolute", "amount": 0.2},
                },
            },
        },
    }

    accepted = client.post(url, json=payload)
    direct_balance = client.post(
        url,
        json={
            **payload,
            "parameters": {
                **payload["parameters"],
                "tolerance_profile": {
                    "fields": {
                        "composition.Fe": {"kind": "absolute", "amount": 0.2},
                    },
                },
            },
        },
    )

    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["result"]["accepted_samples"] == 8
    assert direct_balance.status_code == 422
    assert "balance項目" in direct_balance.json()["message"]


def _difference_payload(revision: int, comparison: dict) -> dict:
    return {
        "expected_revision": revision,
        "parameters": {
            "schema_version": "candidate-difference-parameters/v1",
            "comparison_candidate_id": comparison["id"],
            "comparison_revision": comparison["revision"],
        },
    }


def _difference_url(project_id: str, candidate_id: str) -> str:
    return (
        f"/api/projects/{project_id}/candidates/{candidate_id}"
        "/decision-activities/candidate-difference-v1/runs"
    )


def test_candidate_difference_attributes_the_gap_and_keeps_a_residual(client) -> None:
    base = client.post(
        "/api/projects/default/candidates", json=_candidate_payload()
    ).json()
    other = dict(_candidate_payload())
    other["name"] = "比較候補"
    other["inputs"] = {
        **other["inputs"],
        "composition": {**other["inputs"]["composition"], "C": 0.12, "Mn": 1.2},
        "process": {"ls_mpm": 96.0},
    }
    comparison = client.post("/api/projects/default/candidates", json=other).json()

    response = client.post(
        _difference_url("default", base["id"]),
        json=_difference_payload(base["revision"], comparison),
    )

    assert response.status_code == 201, response.text
    run = response.json()
    result = run["result"]
    assert result["schema_version"] == "candidate-difference-summary/v1"
    assert run["definition"]["result_kind"] == "candidate-difference-summary/v1"
    assert result["comparison_candidate_id"] == comparison["id"]
    changed = {item["path"] for item in result["input_changes"]}
    assert {"composition.C", "composition.Mn", "process.ls_mpm"} <= changed
    for summary in result["target_summaries"]:
        attributed = summary["attributed_difference"]
        unexplained = summary["unexplained_difference"]
        assert summary["difference"] == pytest.approx(attributed + unexplained, abs=1e-9)
        assert summary["base_prediction"]["value"] != summary["comparison_prediction"]["value"]
    assert "因果効果ではありません" in " ".join(result["warnings"])
    assert "残差" in " ".join(result["warnings"])


def test_candidate_difference_compares_the_current_candidate_with_its_history(
    client,
) -> None:
    created = client.post(
        "/api/projects/default/candidates", json=_candidate_payload()
    ).json()
    edited = _candidate_payload()
    edited["inputs"] = {
        **edited["inputs"],
        "composition": {**edited["inputs"]["composition"], "C": 0.11},
    }
    current = client.put(
        f"/api/projects/default/candidates/{created['id']}",
        json={**edited, "expected_revision": created["revision"]},
    ).json()

    response = client.post(
        _difference_url("default", current["id"]),
        json=_difference_payload(
            current["revision"],
            {"id": current["id"], "revision": created["revision"]},
        ),
    )

    assert response.status_code == 201, response.text
    result = response.json()["result"]
    assert result["comparison_candidate_id"] == current["id"]
    assert result["comparison_candidate_revision"] == created["revision"]
    assert {item["path"] for item in result["input_changes"]} == {"composition.C"}


def test_candidate_difference_is_deterministic_and_persistent(client) -> None:
    base = client.post(
        "/api/projects/default/candidates", json=_candidate_payload()
    ).json()
    other = dict(_candidate_payload())
    other["name"] = "比較候補"
    other["inputs"] = {
        **other["inputs"],
        "composition": {**other["inputs"]["composition"], "Si": 0.45},
    }
    comparison = client.post("/api/projects/default/candidates", json=other).json()
    url = _difference_url("default", base["id"])

    first = client.post(url, json=_difference_payload(base["revision"], comparison))
    second = client.post(url, json=_difference_payload(base["revision"], comparison))
    restored = client.get(
        f"/api/projects/default/decision-activity-runs/{first.json()['id']}"
    )

    assert first.status_code == 201, first.text
    assert first.json() == second.json()
    assert restored.json() == first.json()


def test_candidate_difference_rejects_comparing_a_candidate_with_itself(client) -> None:
    base = client.post(
        "/api/projects/default/candidates", json=_candidate_payload()
    ).json()
    client.post("/api/projects/default/candidates", json=_candidate_payload())

    response = client.post(
        _difference_url("default", base["id"]),
        json=_difference_payload(base["revision"], base),
    )

    assert response.status_code == 422
    assert "別の候補" in response.json()["message"]


def test_candidate_difference_rejects_identical_inputs(client) -> None:
    base = client.post(
        "/api/projects/default/candidates", json=_candidate_payload()
    ).json()
    twin = client.post(
        "/api/projects/default/candidates",
        json={**_candidate_payload(), "name": "同一入力"},
    ).json()

    response = client.post(
        _difference_url("default", base["id"]),
        json=_difference_payload(base["revision"], twin),
    )

    assert response.status_code == 422
    assert "入力が同じ" in response.json()["message"]


def test_activity_rejects_parameters_belonging_to_another_activity(client) -> None:
    candidate = client.post(
        "/api/projects/default/candidates", json=_candidate_payload()
    ).json()
    other = client.post(
        "/api/projects/default/candidates",
        json={**_candidate_payload(), "name": "比較候補"},
    ).json()

    crossed = client.post(
        _difference_url("default", candidate["id"]),
        json=_run_payload(candidate["revision"]),
    )
    reversed_crossed = client.post(
        f"/api/projects/default/candidates/{candidate['id']}"
        "/decision-activities/robustness-analysis-v1/runs",
        json=_difference_payload(candidate["revision"], other),
    )

    assert crossed.status_code == 422, crossed.text
    assert reversed_crossed.status_code == 422, reversed_crossed.text


def test_activity_requires_an_explicit_parameters_schema_version(client) -> None:
    candidate = client.post(
        "/api/projects/default/candidates", json=_candidate_payload()
    ).json()
    payload = _run_payload(candidate["revision"])
    del payload["parameters"]["schema_version"]

    response = client.post(
        f"/api/projects/default/candidates/{candidate['id']}"
        "/decision-activities/robustness-analysis-v1/runs",
        json=payload,
    )

    assert response.status_code == 422


SHARED_ACTIVITY_SHELLS = (
    "backend/src/material_workbench/application/decision_activities.py",
    "backend/src/material_workbench/api/decision_activities.py",
    "apps/web/src/features/workbench/DecisionActivityPanel.tsx",
)


def test_shared_activity_shells_do_not_name_a_specific_activity() -> None:
    """service / API / UIの共通部分がactivity_idで分岐しないことを固定する。

    activity_idを名指しできるのは registry と各Activity自身だけ。ここが破れると
    2件目以降の追加で共通部分へ分岐が増える。
    """

    root = Path(__file__).resolve().parents[2]
    activity_ids = tuple(build_registry())

    offenders = {}
    for relative in SHARED_ACTIVITY_SHELLS:
        source = (root / relative).read_text(encoding="utf-8")
        named = [activity_id for activity_id in activity_ids if activity_id in source]
        if named:
            offenders[relative] = named

    assert offenders == {}, f"共通部分がactivity_idを名指ししています: {offenders}"


def test_activity_id_allow_list_is_declared_in_one_place_per_layer() -> None:
    """backendのregistryとfrontendのview registryが同じ集合を宣言している。"""

    root = Path(__file__).resolve().parents[2]
    view_registry = (
        root / "apps/web/src/features/workbench/decisionActivities/registry.ts"
    ).read_text(encoding="utf-8")

    for activity_id in build_registry():
        assert f'"{activity_id}"' in view_registry, activity_id


def test_service_resolves_activities_only_through_its_registry(client) -> None:
    from material_workbench.application.decision_activities import DecisionActivityService
    from material_workbench.application.decision_activity_difference import (
        CANDIDATE_DIFFERENCE_HANDLER,
    )
    from material_workbench.application.decision_activity_registry import (
        DecisionActivityNotFoundError,
    )

    state = client.app.state
    service = DecisionActivityService(
        state.store,
        state.task_registry,
        state.inference_work_graph,
        state.project_runtime_resolver,
        activities={"candidate-difference-v1": CANDIDATE_DIFFERENCE_HANDLER},
    )

    available = service.availability("default")
    assert [item.definition.activity_id for item in available] == [
        "candidate-difference-v1"
    ]
    with pytest.raises(DecisionActivityNotFoundError):
        service.run("default", "any", "robustness-analysis-v1", _run_request())


def _run_request():
    from material_workbench.contracts.decision_activity_contracts import (
        DecisionActivityRunRequest,
    )

    return DecisionActivityRunRequest.model_validate(_run_payload(1))


def test_registered_activities_declare_distinct_parameter_and_result_kinds() -> None:
    handlers = build_registry()

    parameter_kinds = [handler.parameters_kind for handler in handlers.values()]
    result_kinds = [handler.definition.result_kind for handler in handlers.values()]

    assert len(set(parameter_kinds)) == len(parameter_kinds)
    assert len(set(result_kinds)) == len(result_kinds)
    assert {"robustness-analysis-v1", "candidate-difference-v1"} <= set(handlers)
    for handler in handlers.values():
        assert set(handler.definition.required_resources) <= {
            "candidate",
            "comparison_candidate",
            "objective_definition",
            "project_design_space",
        }
