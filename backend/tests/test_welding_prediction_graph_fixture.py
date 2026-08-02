from __future__ import annotations

from decision_workbench.contracts.chain_contracts import PredictionGraphDefinition
from decision_workbench.persistence.welding_prediction_graph_bootstrap import (
    WELDING_MULTI_OUTPUT_GRAPH_ID,
    WELDING_SPLIT_OUTPUT_GRAPH_ID,
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
        if item["definition"]["schema_version"]
        == "prediction-graph-definition/v1"
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
    assert {
        (stage.stage_id, stage.contract_id) for stage in multi.stages
    } >= {
        ("C", "welding-stage-c-properties-v1"),
        ("W", "welding-graph-deposition-efficiency-v1"),
    }
    assert {
        (stage.stage_id, stage.contract_id) for stage in split.stages
    } >= {
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


def test_split_fixture_recomputes_only_the_scientifically_affected_branch(
    client: TestClient,
) -> None:
    split = _bundled_graphs(client)[WELDING_SPLIT_OUTPUT_GRAPH_ID]
    affected = split.topology.affected_nodes_by_input

    assert affected[
        "candidate.welding_context.wire_feed_speed_m_per_min"
    ] == ("W",)
    assert affected["candidate.test_context.test_temperature_c"] == ("U",)
    assert affected["candidate.test_context.test_solution"] == ("R",)
    assert set(
        affected["candidate.welding_context.heat_input_kj_per_mm"]
    ) == {"B", "T", "U", "R", "W"}


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
