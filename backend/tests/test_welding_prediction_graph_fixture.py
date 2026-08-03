from __future__ import annotations

import copy
from pathlib import Path

import pytest
from decision_workbench.application.chain.plan import ChainExecutionError
from decision_workbench.contracts.chain_contracts import (
    DecisionOutputEvidence,
    PredictionGraphDefinition,
)
from decision_workbench.persistence.store import Store
from decision_workbench.persistence.welding_prediction_graph_bootstrap import (
    WELDING_MULTI_OUTPUT_GRAPH_ID,
    WELDING_SPLIT_OUTPUT_GRAPH_ID,
    WeldingPredictionGraphBootstrapError,
    bootstrap_welding_prediction_graphs,
)
from fastapi.testclient import TestClient


def _bundled_graphs(client: TestClient) -> dict[str, PredictionGraphDefinition]:
    response = client.get("/api/chains")
    assert response.status_code == 200, response.text
    return {
        item["definition"]["graph_id"]: PredictionGraphDefinition.model_validate(
            item["definition"]
        )
        for item in response.json()
        if item["definition"]["schema_version"] == "prediction-graph-definition/v1"
        and item["is_default"]
    }


def test_bundled_material_graphs_keep_terminal_identity_and_package_boundaries(
    client: TestClient,
) -> None:
    graphs = _bundled_graphs(client)
    multi = graphs[WELDING_MULTI_OUTPUT_GRAPH_ID]
    split = graphs[WELDING_SPLIT_OUTPUT_GRAPH_ID]

    assert {item.output_id for item in multi.decision_outputs} == {
        item.output_id for item in split.decision_outputs
    }
    assert multi.digest != split.digest
    assert {(stage.stage_id, stage.contract_id) for stage in multi.stages} >= {
        ("C", "welding-stage-c-properties-v1"),
        ("W", "welding-graph-deposition-efficiency-v1"),
    }
    assert {(stage.stage_id, stage.contract_id) for stage in split.stages} >= {
        ("T", "welding-graph-tensile-ts-v1"),
        ("U", "welding-graph-toughness-v1"),
        ("R", "welding-graph-corrosion-v1"),
        ("W", "welding-graph-deposition-efficiency-v1"),
    }

    workability = next(
        item
        for item in split.decision_outputs
        if item.output_id == "deposition-efficiency"
    )
    assert workability.group == "processability"
    assert workability.evidence is not None
    assert workability.evidence.evidence_kind == "synthetic_demonstration"
    assert workability.evidence.unit_or_scale == "%"
    assert workability.evidence.goal_direction == "at_least"
    assert workability.evidence.production_use == "prohibited"
    assert workability.evidence.causal_claim == "none"
    assert workability.evidence.source_variables == (
        "candidate.welding_context.heat_input_kj_per_mm",
        "candidate.welding_context.voltage_v",
        "candidate.welding_context.gas_flow_l_per_min",
        "candidate.welding_context.wire_feed_speed_m_per_min",
    )
    for graph in (multi, split):
        response = client.post(
            "/api/prediction-graphs/validate",
            json={"definition": graph.model_dump(mode="json")},
        )
        assert response.status_code == 200, response.text
        validation = response.json()
        assert validation["valid"] is True, validation["findings"]
        assert validation["candidate_adapter_id"] == "sparse_blend/v1"


def test_canonical_fixture_is_additive_and_keeps_revision_one_immutable(
    client: TestClient,
) -> None:
    before = client.get("/api/chains")
    assert before.status_code == 200, before.text
    templates = before.json()
    for graph_id in (
        WELDING_MULTI_OUTPUT_GRAPH_ID,
        WELDING_SPLIT_OUTPUT_GRAPH_ID,
    ):
        graph_templates = [
            item for item in templates if item["definition"].get("graph_id") == graph_id
        ]
        assert graph_templates[0]["is_default"] is True
        assert sum(item["is_default"] for item in graph_templates) == 1
        assert {item["default_revision_id"] for item in graph_templates} == {
            f"{graph_id}:r2"
        }
        assert {item["latest_revision_id"] for item in graph_templates} == {
            f"{graph_id}:r2"
        }
        assert {
            revision["revision"]
            for item in graph_templates
            for revision in item["revisions"]
        } == {1, 2}
        legacy = next(
            item
            for item in graph_templates
            if any(revision["revision"] == 1 for revision in item["revisions"])
        )
        canonical = next(item for item in graph_templates if item["is_default"])
        legacy_heat = next(
            item
            for item in legacy["definition"]["inputs"]
            if item["input_id"] == "candidate.welding_context.heat_input_kj_per_mm"
        )
        canonical_heat = next(
            item
            for item in canonical["definition"]["inputs"]
            if item["input_id"] == "candidate.welding_context.heat_input_kj_per_mm"
        )
        assert legacy_heat["value_source"]["candidate_path"] == (
            "welding_context.heat_input_kj_per_mm"
        )
        assert canonical_heat["value_source"]["candidate_path"] == (
            "process.heat_input_kj_per_mm"
        )

    revision_one_before = {
        graph_id: client.app.state.store.get_chain_revision(f"{graph_id}:r1")
        for graph_id in (
            WELDING_MULTI_OUTPUT_GRAPH_ID,
            WELDING_SPLIT_OUTPUT_GRAPH_ID,
        )
    }
    revision_ids = bootstrap_welding_prediction_graphs(
        store=client.app.state.store,
        workspace_catalog=client.app.state.workspace_catalog,
        task_registry=client.app.state.task_registry,
        transform_catalog=client.app.state.deterministic_transform_catalog,
    )
    assert revision_ids == (
        f"{WELDING_MULTI_OUTPUT_GRAPH_ID}:r2",
        f"{WELDING_SPLIT_OUTPUT_GRAPH_ID}:r2",
    )
    assert {
        graph_id: client.app.state.store.get_chain_revision(f"{graph_id}:r1")
        for graph_id in revision_one_before
    } == revision_one_before


def test_split_fixture_recomputes_only_the_scientifically_affected_branch(
    client: TestClient,
) -> None:
    split = _bundled_graphs(client)[WELDING_SPLIT_OUTPUT_GRAPH_ID]
    affected = split.topology.affected_nodes_by_input

    assert affected["candidate.welding_context.wire_feed_speed_m_per_min"] == ("W",)
    assert affected["candidate.test_context.test_temperature_c"] == ("U",)
    assert affected["candidate.test_context.test_solution"] == ("R",)
    assert set(affected["candidate.welding_context.heat_input_kj_per_mm"]) == {
        "B",
        "T",
        "U",
        "R",
        "W",
    }


def test_graph_presentation_order_does_not_change_scientific_identity(
    client: TestClient,
) -> None:
    split = _bundled_graphs(client)[WELDING_SPLIT_OUTPUT_GRAPH_ID]
    presentation_only = split.model_copy(
        update={
            "label": "別の表示名",
            "stages": tuple(reversed(split.stages)),
            "decision_outputs": tuple(reversed(split.decision_outputs)),
        }
    )

    assert presentation_only.digest == split.digest


def test_production_evidence_fails_closed_without_server_authority() -> None:
    base = {
        "unit_or_scale": "MPa",
        "goal_direction": "at_least",
        "source_variables": ("measured.TS",),
        "causal_claim": "none",
        "limitation": "One pinned validation dataset.",
    }
    with pytest.raises(
        ValueError,
        match="prohibited",
    ):
        DecisionOutputEvidence(
            **base,
            evidence_kind="measured",
            production_use="allowed",
            provenance={
                "dataset_view_revision_id": "dataset-view:fake:r999",
                "dataset_profile_digest": f"sha256:{'1' * 64}",
                "source_snapshot_digest": f"sha256:{'2' * 64}",
            },
        )
    with pytest.raises(
        ValueError,
        match="prohibited",
    ):
        DecisionOutputEvidence(
            **base,
            evidence_kind="unverified",
            production_use="allowed",
        )

    measured = DecisionOutputEvidence(
        **base,
        evidence_kind="measured",
        production_use="prohibited",
    )
    assert measured.production_use == "prohibited"


def test_fixture_bundle_rolls_back_when_second_definition_insert_fails(
    client: TestClient,
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "atomic-fixture.db")
    with store._connect() as connection:
        connection.execute(
            "CREATE TRIGGER reject_second_graph "
            "BEFORE INSERT ON chain_definitions "
            f"WHEN NEW.chain_id='{WELDING_SPLIT_OUTPUT_GRAPH_ID}' "
            "BEGIN SELECT RAISE(ABORT, 'injected second registration failure'); END"
        )

    with pytest.raises(
        WeldingPredictionGraphBootstrapError,
        match="injected second registration failure",
    ):
        bootstrap_welding_prediction_graphs(
            store=store,
            workspace_catalog=client.app.state.workspace_catalog,
            task_registry=client.app.state.task_registry,
            transform_catalog=client.app.state.deterministic_transform_catalog,
        )

    with store._connect() as connection:
        definitions = connection.execute(
            "SELECT COUNT(*) FROM chain_definitions WHERE chain_id IN (?, ?)",
            (WELDING_MULTI_OUTPUT_GRAPH_ID, WELDING_SPLIT_OUTPUT_GRAPH_ID),
        ).fetchone()[0]
        revisions = connection.execute(
            "SELECT COUNT(*) FROM chain_revisions WHERE chain_id IN (?, ?)",
            (WELDING_MULTI_OUTPUT_GRAPH_ID, WELDING_SPLIT_OUTPUT_GRAPH_ID),
        ).fetchone()[0]
    assert definitions == 0
    assert revisions == 0


def _graph_project(
    client: TestClient,
    *,
    revision_number: int = 2,
) -> tuple[dict, dict]:
    templates = client.get("/api/chains")
    assert templates.status_code == 200, templates.text
    items = templates.json()
    split = next(
        item
        for item in items
        if item["definition"].get("graph_id") == WELDING_SPLIT_OUTPUT_GRAPH_ID
        and (item["is_default"] if revision_number == 2 else not item["is_default"])
    )
    graph_revision = next(
        revision
        for revision in split["revisions"]
        if revision["revision"] == revision_number
    )
    project_response = client.post(
        "/api/prediction-graphs/projects",
        json={
            "project": {"name": "Split fixture API execution"},
            "graph_revision_id": (
                f"{graph_revision['graph_id']}:r{graph_revision['revision']}"
            ),
            "graph_revision_digest": graph_revision["revision_digest"],
            "project_binding_values": {},
        },
    )
    assert project_response.status_code == 201, project_response.text
    project = project_response.json()
    input_response = client.get(
        f"/api/prediction-graphs/projects/{project['id']}/candidate-inputs"
    )
    assert input_response.status_code == 200, input_response.text
    input_contract = input_response.json()
    assert any(
        item["role"] == "design_variable"
        and item["kind"] == "number"
        and item["affected_output_ids"]
        for item in input_contract
    )
    assert any(item["role"] == "scenario_context" for item in input_contract)
    assert all(
        item["role"] in {"design_variable", "scenario_context"}
        for item in input_contract
    )

    starter_response = client.get(
        f"/api/prediction-graphs/projects/{project['id']}/starter-candidate"
    )
    assert starter_response.status_code == 200, starter_response.text
    candidate_payload = starter_response.json()
    candidate_payload["name"] = "Split fixture candidate"
    candidate_payload["inputs"]["process"].update(
        {
            "heat_input_kj_per_mm": 1.43,
            "voltage_v": 28.36,
            "gas_flow_l_per_min": 25.4,
            "wire_feed_speed_m_per_min": 7.5,
            "preheat_temp_c": 80.0,
            "test_temperature_c": -20.0,
        }
    )
    candidate_payload["inputs"]["categorical"].update(
        {
            "shielding_gas": "100%CO2",
            "welding_position": "下向",
            "test_solution": "5%H2SO4",
        }
    )
    candidate_response = client.post(
        f"/api/prediction-graphs/projects/{project['id']}/candidates",
        json=candidate_payload,
    )
    assert candidate_response.status_code == 201, candidate_response.text
    return project, candidate_response.json()


def _execute_graph(
    client: TestClient,
    project: dict,
    candidate: dict,
    request_id: str,
) -> dict:
    response = client.post(
        f"/api/prediction-graphs/projects/{project['id']}/candidates/"
        f"{candidate['id']}/executions",
        json={
            "candidate_revision": candidate["revision"],
            "request_id": request_id,
            "debounce_ms": 0,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _candidate_update(candidate: dict) -> dict:
    return {
        key: copy.deepcopy(candidate[key])
        for key in (
            "name",
            "inputs",
            "blend",
            "editor_state",
            "blend_validation",
            "provenance",
            "input_missing_kinds",
        )
    } | {"expected_revision": candidate["revision"]}


@pytest.mark.parametrize("revision_number", [1, 2])
def test_legacy_and_canonical_revisions_execute_the_same_typed_candidate(
    client: TestClient,
    revision_number: int,
) -> None:
    project, candidate = _graph_project(
        client,
        revision_number=revision_number,
    )
    execution = _execute_graph(
        client,
        project,
        candidate,
        f"fixture-revision-{revision_number}",
    )
    assert execution["status"] == "complete"
    assert execution["graph_revision_id"] == (
        f"{WELDING_SPLIT_OUTPUT_GRAPH_ID}:r{revision_number}"
    )


def test_split_fixture_api_preserves_branches_and_failure_evidence(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, candidate = _graph_project(client)
    first = _execute_graph(client, project, candidate, "fixture-first")
    assert first["status"] == "complete"
    first_stages = {item["stage_id"]: item for item in first["stages"]}

    update = _candidate_update(candidate)
    update["inputs"]["process"]["wire_feed_speed_m_per_min"] = 8.25
    updated_response = client.put(
        f"/api/prediction-graphs/projects/{project['id']}/candidates/{candidate['id']}",
        json=update,
    )
    assert updated_response.status_code == 200, updated_response.text
    updated = updated_response.json()
    second = _execute_graph(client, project, updated, "fixture-wire-change")
    second_stages = {item["stage_id"]: item for item in second["stages"]}
    for stage_id in ("A", "B", "T", "U", "R"):
        assert (
            second_stages[stage_id]["result_input_digest"]
            == (first_stages[stage_id]["result_input_digest"])
        )
    assert (
        second_stages["W"]["result_input_digest"]
        != (first_stages["W"]["result_input_digest"])
    )

    snapshot_response = client.post(
        f"/api/prediction-graphs/projects/{project['id']}/candidates/"
        f"{candidate['id']}/snapshots",
        json={
            "candidate_revision": updated["revision"],
            "request_id": "fixture-snapshot",
            "debounce_ms": 0,
        },
    )
    assert snapshot_response.status_code == 201, snapshot_response.text
    snapshot = snapshot_response.json()
    identity = snapshot["identity"]
    assert identity["graph_revision_id"] == second["graph_revision_id"]
    assert identity["graph_revision_digest"] == second["graph_revision_digest"]
    assert identity["project_binding_revision"] == (second["project_binding_revision"])
    assert identity["project_binding_digest"] == second["project_binding_digest"]
    assert identity["candidate_id"] == second["candidate_id"]
    assert identity["candidate_revision"] == second["candidate_revision"]

    use_cases = client.app.state.prediction_graph_use_cases
    executor = use_cases.execution.stage_executor
    original_run_stage = executor._run_stage

    def fail_t(stage, canonical_input, stage_candidate, adapter):
        if stage.stage_id == "T":
            raise ChainExecutionError("injected T failure")
        return original_run_stage(stage, canonical_input, stage_candidate, adapter)

    with use_cases.store._connect() as connection:
        connection.execute("DELETE FROM chain_stage_memo")
    monkeypatch.setattr(executor, "_run_stage", fail_t)
    t_failed = _execute_graph(client, project, updated, "fixture-t-failure")
    t_stages = {item["stage_id"]: item for item in t_failed["stages"]}
    assert t_stages["T"]["status"] == "failed"
    assert t_stages["R"]["status"] == "latest"
    assert t_stages["W"]["status"] == "latest"
    t_outputs = {item["output_id"]: item for item in t_failed["terminal_outputs"]}
    assert t_outputs["tensile-strength"]["status"] == "failed"
    assert t_outputs["corrosion-rate"]["status"] == "latest"
    assert t_outputs["deposition-efficiency"]["status"] == "latest"

    def fail_b(stage, canonical_input, stage_candidate, adapter):
        if stage.stage_id == "B":
            raise ChainExecutionError("injected B failure")
        return original_run_stage(stage, canonical_input, stage_candidate, adapter)

    with use_cases.store._connect() as connection:
        connection.execute("DELETE FROM chain_stage_memo")
    monkeypatch.setattr(executor, "_run_stage", fail_b)
    b_failed = _execute_graph(client, project, updated, "fixture-b-failure")
    b_stages = {item["stage_id"]: item for item in b_failed["stages"]}
    assert b_stages["B"]["status"] == "failed"
    for stage_id in ("T", "U", "R"):
        assert b_stages[stage_id]["status"] == "blocked_by_upstream"
        assert b_stages[stage_id]["blocked_by_stage_ids"] == ["B"]
    assert b_stages["W"]["status"] == "latest"


def test_graph_goal_search_pins_objective_and_promotes_only_selected_result(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, candidate = _graph_project(client)
    project_id = project["id"]
    objective_payload = {
        "name": "強度と耐食性",
        "primary": {
            "output_id": "tensile-strength",
            "direction": "at_least",
            "threshold": 10_000.0,
        },
        "hard_constraint": {
            "output_id": "corrosion-rate",
            "direction": "at_most",
            "threshold": 1_000_000.0,
        },
        "incumbent_candidate_id": candidate["id"],
        "incumbent_candidate_revision": candidate["revision"],
    }
    production = client.post(
        f"/api/prediction-graphs/projects/{project_id}/objectives",
        json={**objective_payload, "use_context": "production"},
    )
    assert production.status_code == 422, production.text
    assert "measured output" in production.text

    use_cases = client.app.state.prediction_graph_use_cases
    graph_revision_id = project["scientific_identity"]["graph_revision_id"]
    original_surfaces = use_cases.store.get_chain_stage_contract_surfaces
    surfaces = original_surfaces(graph_revision_id)
    tensile_surface = surfaces["T"]
    mismatched_surface = tensile_surface.model_copy(
        update={
            "output_ports": tuple(
                output.model_copy(update={"unit": "wrong-unit"})
                if output.path == "TS"
                else output
                for output in tensile_surface.output_ports
            )
        }
    )
    monkeypatch.setattr(
        use_cases.store,
        "get_chain_stage_contract_surfaces",
        lambda _revision_id: {
            **surfaces,
            "T": mismatched_surface,
        },
    )
    mismatched_unit = client.post(
        f"/api/prediction-graphs/projects/{project_id}/objectives",
        json={**objective_payload, "use_context": "demonstration"},
    )
    assert mismatched_unit.status_code == 422, mismatched_unit.text
    assert "Stage output port" in mismatched_unit.text
    monkeypatch.setattr(
        use_cases.store,
        "get_chain_stage_contract_surfaces",
        original_surfaces,
    )

    objective_response = client.post(
        f"/api/prediction-graphs/projects/{project_id}/objectives",
        json={**objective_payload, "use_context": "demonstration"},
    )
    assert objective_response.status_code == 201, objective_response.text
    objective = objective_response.json()
    assert objective["primary"]["source_stage_id"] == "T"
    assert objective["primary"]["source_output_key"] == "TS"
    assert objective["hard_constraint"]["source_stage_id"] == "R"
    assert objective["hard_constraint"]["source_output_key"] == ("CORROSION_RATE")
    objectives_response = client.get(
        f"/api/prediction-graphs/projects/{project_id}/objectives"
    )
    assert objectives_response.status_code == 200, objectives_response.text
    assert objectives_response.json() == [objective]

    design_response = client.get(
        f"/api/prediction-graphs/projects/{project_id}/goal-search/design-space"
    )
    assert design_response.status_code == 200, design_response.text
    design_space = design_response.json()
    input_ids = {item["input_id"] for item in design_space["variables"]}
    assert input_ids == {
        "candidate.welding_context.voltage_v",
        "candidate.welding_context.gas_flow_l_per_min",
        "candidate.welding_context.wire_feed_speed_m_per_min",
        "candidate.welding_context.shielding_gas",
    }
    assert "candidate.test_context.test_solution" not in input_ids
    assert "candidate.welding_context.heat_input_kj_per_mm" not in input_ids

    request = {
        "objective_id": objective["objective_id"],
        "base_candidate_id": candidate["id"],
        "base_candidate_revision": candidate["revision"],
        "sample_count": 2,
        "seed": 756,
    }
    mismatched_base = client.post(
        f"/api/prediction-graphs/projects/{project_id}/goal-search-runs",
        json={
            **request,
            "base_candidate_revision": candidate["revision"] + 1,
        },
    )
    assert mismatched_base.status_code == 422, mismatched_base.text
    assert "incumbent Candidate revision" in mismatched_base.text

    run_response = client.post(
        f"/api/prediction-graphs/projects/{project_id}/goal-search-runs",
        json=request,
    )
    assert run_response.status_code == 201, run_response.text
    run = run_response.json()
    assert run["objective_digest"].startswith("sha256:")
    assert run["design_space_digest"].startswith("sha256:")
    assert (
        run["graph_revision_id"]
        == (project["scientific_identity"]["graph_revision_id"])
    )
    assert (
        run["project_binding_digest"]
        == (project["scientific_identity"]["project_binding"]["digest"])
    )
    assert run["base_candidate_id"] == candidate["id"]
    assert run["base_candidate_revision"] == candidate["revision"]
    assert run["base_candidate_digest"].startswith("sha256:")
    assert set(run["package_manifest_digests"]) == {
        "A",
        "B",
        "T",
        "U",
        "R",
        "W",
    }
    assert len(run["selected_point_indices"]) == 2, [
        (
            point["rejection_reason"],
            {key: output["status"] for key, output in point["outputs"].items()},
        )
        for point in run["points"]
    ]
    assert all(point["primary_achieved"] is False for point in run["points"])
    assert all(point["hard_constraint_achieved"] is True for point in run["points"])
    assert all(point["feasible"] is True for point in run["points"])
    tensile = run["points"][0]["outputs"]["tensile-strength"]
    assert tensile["status"] == "latest"
    assert tensile["prediction"] is not None
    assert {"lower", "upper"} <= set(tensile["prediction"])
    assert "support" in tensile

    saved_response = client.get(
        f"/api/prediction-graphs/projects/{project_id}/goal-search-runs/{run['run_id']}"
    )
    assert saved_response.status_code == 200, saved_response.text
    assert saved_response.json() == run
    runs_response = client.get(
        f"/api/prediction-graphs/projects/{project_id}/goal-search-runs"
    )
    assert runs_response.status_code == 200, runs_response.text
    assert runs_response.json() == [run]

    selected_index = run["selected_point_indices"][0]
    promotion_response = client.post(
        f"/api/prediction-graphs/projects/{project_id}/goal-search-runs/"
        f"{run['run_id']}/promotions",
        json={"point_index": selected_index, "name": "Goal-search候補"},
    )
    assert promotion_response.status_code == 201, promotion_response.text
    promoted = promotion_response.json()
    assert promoted["provenance"]["source_kind"] == ("prediction_graph_goal_search")
    assert promoted["provenance"]["source_ref"] == {
        "run_id": run["run_id"],
        "point_index": selected_index,
        "objective_digest": run["objective_digest"],
        "design_space_digest": run["design_space_digest"],
    }

    original_run_stage = use_cases.execution.stage_executor._run_stage

    def fail_constraint_branch(
        stage,
        canonical_input,
        stage_candidate,
        adapter,
    ):
        if stage.stage_id == "R":
            raise ChainExecutionError("injected hard constraint failure")
        return original_run_stage(
            stage,
            canonical_input,
            stage_candidate,
            adapter,
        )

    monkeypatch.setattr(
        use_cases.execution.stage_executor,
        "_run_stage",
        fail_constraint_branch,
    )
    failed_response = client.post(
        f"/api/prediction-graphs/projects/{project_id}/goal-search-runs",
        json={**request, "seed": 757},
    )
    assert failed_response.status_code == 201, failed_response.text
    failed = failed_response.json()
    assert failed["selected_point_indices"] == []
    assert all(point["feasible"] is False for point in failed["points"])
    assert all(
        point["outputs"]["corrosion-rate"]["status"] == "failed"
        for point in failed["points"]
    )
    assert all(
        point["outputs"]["corrosion-rate"]["error"]
        == "injected hard constraint failure"
        for point in failed["points"]
    )
