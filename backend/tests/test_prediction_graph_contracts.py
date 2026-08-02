from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from decision_workbench.contracts.chain_contracts import (
    ChainBinding,
    ChainDefinition,
    ChainPort,
    ChainStage,
    ChainStageLock,
    DecisionOutput,
    ExternalBindingSource,
    GraphInput,
    PredictionGraphDefinition,
    PredictionGraphRevision,
    StageContractSurface,
    build_prediction_graph_revision,
    parse_graph_definition_json,
    parse_graph_revision_json,
    project_prediction_graph,
    validate_prediction_graph_definition,
    validate_prediction_graph_revision,
)
from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.persistence.store import Store


def _port(path: str, quantity: str) -> ChainPort:
    return ChainPort(
        path=path,
        value_kind="number",
        quantity=quantity,
        unit="unit",
    )


def _surface(
    contract_id: str,
    *,
    inputs: tuple[ChainPort, ...],
    outputs: tuple[ChainPort, ...],
    digest_character: str,
) -> StageContractSurface:
    return StageContractSurface(
        stage_kind="deterministic_transform",
        contract_id=contract_id,
        contract_digest="sha256:" + digest_character * 64,
        input_ports=inputs,
        output_ports=outputs,
    )


def _contracts() -> dict[tuple[str, str], StageContractSurface]:
    values = (
        _surface(
            "transform-a",
            inputs=(_port("x", "x"),),
            outputs=(_port("shared", "shared"),),
            digest_character="a",
        ),
        _surface(
            "transform-b",
            inputs=(_port("x", "x"),),
            outputs=(_port("other", "other"),),
            digest_character="b",
        ),
        _surface(
            "transform-c",
            inputs=(_port("shared", "shared"), _port("other", "other")),
            outputs=(_port("result", "result"),),
            digest_character="c",
        ),
        _surface(
            "transform-d",
            inputs=(_port("shared", "shared"),),
            outputs=(_port("work", "work"),),
            digest_character="d",
        ),
    )
    return {(item.stage_kind, item.contract_id): item for item in values}


def _graph_payload() -> dict:
    return {
        "schema_version": "prediction-graph-definition/v1",
        "graph_id": "branch-and-merge",
        "label": "Branch and merge",
        "stages": [
            {
                "stage_id": stage_id,
                "stage_kind": "deterministic_transform",
                "contract_id": f"transform-{stage_id.lower()}",
            }
            for stage_id in ("D", "C", "B", "A")
        ],
        "inputs": [
            {
                "input_id": "candidate.x",
                "label": "X",
                "port": _port("candidate.x", "x").model_dump(mode="json"),
                "role": "design_variable",
                "value_source": {
                    "source_kind": "candidate",
                    "candidate_path": "x",
                },
                "required": True,
                "default_presentation_group": "conditions",
            }
        ],
        "bindings": [
            {
                "target_stage_id": "D",
                "target_input_path": "shared",
                "source": {
                    "source_kind": "stage_output",
                    "stage_id": "A",
                    "output_key": "shared",
                },
            },
            {
                "target_stage_id": "C",
                "target_input_path": "other",
                "source": {
                    "source_kind": "stage_output",
                    "stage_id": "B",
                    "output_key": "other",
                },
            },
            {
                "target_stage_id": "C",
                "target_input_path": "shared",
                "source": {
                    "source_kind": "stage_output",
                    "stage_id": "A",
                    "output_key": "shared",
                },
            },
            {
                "target_stage_id": "B",
                "target_input_path": "x",
                "source": {
                    "source_kind": "external",
                    "path": "candidate.x",
                },
            },
            {
                "target_stage_id": "A",
                "target_input_path": "x",
                "source": {
                    "source_kind": "external",
                    "path": "candidate.x",
                },
            },
        ],
        "decision_outputs": [
            {
                "output_id": "work",
                "source_stage_id": "D",
                "source_output_key": "work",
                "label": "Work",
                "group": "secondary",
                "role": "secondary_outcome",
                "required_for_complete_result": False,
            },
            {
                "output_id": "result",
                "source_stage_id": "C",
                "source_output_key": "result",
                "label": "Result",
                "group": "objectives",
                "role": "primary_objective",
                "required_for_complete_result": True,
            },
            {
                "output_id": "shared-diagnostic",
                "source_stage_id": "A",
                "source_output_key": "shared",
                "label": "Shared",
                "group": "diagnostics",
                "role": "diagnostic",
                "required_for_complete_result": False,
            },
        ],
    }


def _graph() -> PredictionGraphDefinition:
    return PredictionGraphDefinition.model_validate(_graph_payload())


def _locks() -> dict[str, ChainStageLock]:
    contracts = _contracts()
    return {
        stage_id: ChainStageLock(
            contract_digest=contracts[
                ("deterministic_transform", f"transform-{stage_id.lower()}")
            ].contract_digest,
            package_manifest_digest="sha256:" + stage_id.lower() * 64,
        )
        for stage_id in ("A", "B", "C", "D")
    }


def test_graph_derives_branch_merge_fanout_and_stable_scientific_identity() -> None:
    graph = _graph()

    assert graph.topology.direct_dependencies == {
        "A": (),
        "B": (),
        "C": ("A", "B"),
        "D": ("A",),
    }
    assert graph.topology.ancestors["C"] == ("A", "B")
    assert graph.topology.descendants["A"] == ("C", "D")
    assert graph.topology.topological_layers == (("A", "B"), ("C", "D"))
    assert graph.topology.affected_nodes_by_input["candidate.x"] == (
        "A",
        "B",
        "C",
        "D",
    )

    reordered = _graph_payload()
    reordered["stages"] = list(reversed(reordered["stages"]))
    reordered["bindings"] = list(reversed(reordered["bindings"]))
    reordered["decision_outputs"] = list(reversed(reordered["decision_outputs"]))
    assert PredictionGraphDefinition.model_validate(reordered).digest == graph.digest

    presentation_only = _graph_payload()
    presentation_only["label"] = "Presentation-only graph label"
    presentation_only["inputs"][0]["label"] = "Presentation-only input"
    presentation_only["inputs"][0]["default_presentation_group"] = "other-panel"
    presentation_only["decision_outputs"][0]["label"] = "Presentation-only output"
    presentation_only["decision_outputs"][0]["group"] = "other-panel"
    assert (
        PredictionGraphDefinition.model_validate(presentation_only).digest
        == graph.digest
    )

    scientific_change = _graph_payload()
    scientific_change["decision_outputs"][0]["role"] = "diagnostic"
    assert (
        PredictionGraphDefinition.model_validate(scientific_change).digest
        != graph.digest
    )

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PredictionGraphDefinition.model_validate(
            {**_graph_payload(), "layout": {"A": {"x": 1, "y": 2}}}
        )


def test_graph_input_roles_keep_candidate_project_and_fixed_authority_explicit() -> None:
    design = GraphInput(
        input_id="candidate.x",
        label="Design X",
        port=_port("candidate.x", "x"),
        role="design_variable",
        value_source={
            "source_kind": "candidate",
            "candidate_path": "x",
        },
        default_presentation_group="design",
    )
    scenario = GraphInput(
        input_id="scenario.temperature",
        label="Temperature",
        port=_port("scenario.temperature", "temperature"),
        role="scenario_context",
        value_source={
            "source_kind": "project_binding",
            "binding_key": "temperature",
        },
        default_presentation_group="scenario",
    )
    fixed = GraphInput(
        input_id="fixed.threshold",
        label="Threshold",
        port=_port("fixed.threshold", "threshold"),
        role="fixed_parameter",
        value_source={"source_kind": "fixed_value", "value": 1.5},
        default_presentation_group="parameters",
    )

    assert design.value_source.source_kind == "candidate"
    assert scenario.value_source.source_kind == "project_binding"
    assert fixed.value_source.source_kind == "fixed_value"
    with pytest.raises(ValidationError, match="design variables"):
        GraphInput.model_validate(
            {
                **design.model_dump(mode="json"),
                "value_source": {
                    "source_kind": "project_binding",
                    "binding_key": "x",
                },
            }
        )


def test_graph_rejects_cycle_unknown_unbound_unused_and_invalid_terminal() -> None:
    cyclic = _graph_payload()
    cyclic["bindings"][-1] = {
        "target_stage_id": "A",
        "target_input_path": "x",
        "source": {
            "source_kind": "stage_output",
            "stage_id": "C",
            "output_key": "result",
        },
    }
    with pytest.raises(ValidationError, match="acyclic"):
        PredictionGraphDefinition.model_validate(cyclic)

    unknown = _graph_payload()
    unknown["bindings"][0]["source"]["stage_id"] = "UNKNOWN"
    with pytest.raises(ValidationError, match="unknown source stage"):
        PredictionGraphDefinition.model_validate(unknown)

    unbound = _graph_payload()
    unbound["inputs"].append(
        {
            **unbound["inputs"][0],
            "input_id": "candidate.unused",
            "label": "Unused",
            "port": _port("candidate.unused", "unused").model_dump(mode="json"),
            "value_source": {
                "source_kind": "candidate",
                "candidate_path": "unused",
            },
        }
    )
    with pytest.raises(ValidationError, match="required Prediction Graph inputs"):
        PredictionGraphDefinition.model_validate(unbound)

    unused_stage = _graph_payload()
    unused_stage["decision_outputs"] = [
        item
        for item in unused_stage["decision_outputs"]
        if item["source_stage_id"] != "D"
    ]
    with pytest.raises(ValidationError, match="must feed a decision output"):
        PredictionGraphDefinition.model_validate(unused_stage)

    missing_stage_binding = _graph().model_copy(
        update={"bindings": _graph().bindings[:-1]}
    )
    with pytest.raises(ValueError, match="unbound required inputs"):
        validate_prediction_graph_definition(
            missing_stage_binding,
            contracts=_contracts(),
        )

    invalid_terminal = _graph().model_copy(
        update={
            "decision_outputs": (
                DecisionOutput(
                    output_id="missing",
                    source_stage_id="C",
                    source_output_key="missing",
                    label="Missing",
                    group="objectives",
                    role="primary_objective",
                    required_for_complete_result=True,
                ),
                _graph().decision_outputs[0],
            )
        }
    )
    with pytest.raises(ValueError, match="unknown Graph stage output"):
        validate_prediction_graph_definition(
            invalid_terminal,
            contracts=_contracts(),
        )


def test_graph_revision_pins_topology_contract_and_scientific_digests() -> None:
    graph = _graph()
    revision = build_prediction_graph_revision(
        graph,
        revision=1,
        contracts=_contracts(),
        stage_locks=_locks(),
    )

    assert isinstance(revision, PredictionGraphRevision)
    assert [stage.stage_id for stage in revision.stages] == ["A", "B", "C", "D"]
    assert revision.topology_digest == semantic_digest(
        graph.topology.model_dump(mode="json")
    )
    validate_prediction_graph_revision(graph, revision, contracts=_contracts())

    changed = revision.model_copy(
        update={"topology_digest": "sha256:" + "0" * 64}
    )
    with pytest.raises(ValueError, match="topology digest"):
        validate_prediction_graph_revision(graph, changed, contracts=_contracts())


def test_catalog_round_trips_discriminated_v2_without_rewriting_v1(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "graph-catalog.db")
    graph = _graph()
    revision = build_prediction_graph_revision(
        graph,
        revision=1,
        contracts=_contracts(),
        stage_locks=_locks(),
    )
    store.register_chain_definition(graph)
    revision_id = store.register_chain_revision(
        revision,
        contracts=_contracts(),
    )

    loaded_graph = store.get_chain_definition(graph.graph_id, graph.digest)
    loaded_revision = store.get_chain_revision(revision_id)
    assert isinstance(loaded_graph, PredictionGraphDefinition)
    assert isinstance(loaded_revision, PredictionGraphRevision)
    assert loaded_graph == graph
    assert loaded_revision == revision
    assert parse_graph_definition_json(graph.model_dump_json()) == graph
    assert parse_graph_revision_json(revision.model_dump_json()) == revision

    v1 = ChainDefinition(
        chain_id="legacy-chain",
        label="Legacy",
        stages=(
            ChainStage(
                stage_id="A",
                stage_kind="deterministic_transform",
                contract_id="transform-a",
            ),
        ),
        external_inputs=(_port("candidate.x", "x"),),
        bindings=(
            ChainBinding(
                target_stage_id="A",
                target_input_path="x",
                source=ExternalBindingSource(
                    source_kind="external",
                    path="candidate.x",
                ),
            ),
        ),
    )
    original_payload = v1.model_dump_json()
    original_digest = v1.digest
    assert parse_graph_definition_json(original_payload).model_dump_json() == original_payload
    assert parse_graph_definition_json(original_payload).digest == original_digest


def test_v1_projection_is_conservative_about_roles_and_terminal_outputs() -> None:
    definition = ChainDefinition(
        chain_id="legacy-chain",
        label="Legacy",
        stages=(
            ChainStage(
                stage_id="A",
                stage_kind="deterministic_transform",
                contract_id="transform-a",
            ),
        ),
        external_inputs=(_port("candidate.x", "x"),),
        bindings=(
            ChainBinding(
                target_stage_id="A",
                target_input_path="x",
                source=ExternalBindingSource(
                    source_kind="external",
                    path="candidate.x",
                ),
            ),
        ),
    )
    projection = project_prediction_graph(
        definition,
        contracts={
            ("deterministic_transform", "transform-a"): _contracts()[
                ("deterministic_transform", "transform-a")
            ]
        },
    )

    assert projection.source_schema_version == "chain-definition/v1"
    assert projection.inputs[0].role == "legacy_unspecified"
    assert projection.decision_outputs[0].source_stage_id == "A"
    assert projection.decision_outputs[0].role == "secondary_outcome"
    assert projection.limitations == (
        "v1 external input roles are unspecified and are not inferred",
    )


def test_catalog_api_reads_v2_definition_and_revision(
    client: TestClient,
) -> None:
    graph = _graph()
    revision = build_prediction_graph_revision(
        graph,
        revision=1,
        contracts=_contracts(),
        stage_locks=_locks(),
    )
    client.app.state.store.register_chain_definition(graph)
    revision_id = client.app.state.store.register_chain_revision(
        revision,
        contracts=_contracts(),
    )

    response = client.get("/api/chains")
    assert response.status_code == 200, response.text
    item = next(
        item
        for item in response.json()
        if item["definition"].get("graph_id") == graph.graph_id
    )
    assert item["definition"]["schema_version"] == "prediction-graph-definition/v1"
    assert item["revisions"][0]["schema_version"] == "prediction-graph-revision/v1"

    revision_response = client.get(f"/api/chains/revisions/{revision_id}")
    assert revision_response.status_code == 200, revision_response.text
    assert revision_response.json()["schema_version"] == "prediction-graph-revision/v1"
    assert revision_response.json()["graph_id"] == graph.graph_id

    project_response = client.post(
        "/api/projects",
        json={
            "name": "Prediction Graph must use its own runtime",
            "scientific_identity": {
                "identity_kind": "chain",
                "chain_revision_id": revision_id,
                "chain_revision_digest": revision.revision_digest,
            },
        },
    )
    assert project_response.status_code == 422, project_response.text
    assert "Prediction Graph Revision" in project_response.text
