"""Bundled material Graphs comparing multi-output and split Packages."""
from __future__ import annotations

from collections.abc import Mapping

from decision_workbench.contracts.chain_contracts import (
    CandidateGraphInputSource,
    ChainBinding,
    ChainPort,
    ChainStage,
    ChainStageLock,
    DecisionOutput,
    DecisionOutputEvidence,
    ExternalBindingSource,
    GraphInput,
    PredictionGraphDefinition,
    StageContractSurface,
    StageOutputBindingSource,
    build_prediction_graph_revision,
)
from decision_workbench.modeling.transform_catalog import (
    DeterministicTransformCatalog,
)
from decision_workbench.persistence.store import Store
from decision_workbench.persistence.welding_chain_bootstrap import (
    STAGE_A_ID,
    STAGE_B_ID,
    STAGE_C_ID,
    _stage_a_surface,
    _task_lock,
    _task_surface,
)
from decision_workbench.persistence.workspace_catalog import WorkspaceCatalog
from decision_workbench.task_composition.builtin.tabular import (
    WELDING_GRAPH_DEPOSITION_EFFICIENCY_TASK_ID,
)
from decision_workbench.task_composition.builtin.welding import (
    WELDING_GRAPH_CORROSION_TASK_ID,
    WELDING_GRAPH_TENSILE_TASK_ID,
    WELDING_GRAPH_TOUGHNESS_TASK_ID,
)
from decision_workbench.tasks.task_registry import TaskRegistry

WELDING_MULTI_OUTPUT_GRAPH_ID = "welding-material-multi-output-demo-v1"
WELDING_SPLIT_OUTPUT_GRAPH_ID = "welding-material-split-output-demo-v1"


def _input(
    input_id: str,
    *,
    label: str,
    value_kind: str,
    quantity: str,
    unit: str | None,
    role: str,
    group: str,
) -> GraphInput:
    return GraphInput(
        input_id=input_id,
        label=label,
        port=ChainPort(
            path=input_id,
            value_kind=value_kind,
            quantity=quantity,
            unit=unit,
            basis="core" if value_kind == "sparse_blend" else None,
        ),
        role=role,
        value_source=CandidateGraphInputSource(
            source_kind="candidate",
            candidate_path=input_id.removeprefix("candidate."),
        ),
        default_presentation_group=group,
    )


def _inputs() -> tuple[GraphInput, ...]:
    return (
        _input(
            "candidate.blend",
            label="原料配合",
            value_kind="sparse_blend",
            quantity="blend",
            unit="sparse-blend/v1",
            role="design_variable",
            group="design",
        ),
        _input(
            "candidate.welding_context.heat_input_kj_per_mm",
            label="入熱",
            value_kind="number",
            quantity="heat_input_kj_per_mm",
            unit="kJ/mm",
            role="scenario_context",
            group="welding",
        ),
        _input(
            "candidate.welding_context.voltage_v",
            label="電圧",
            value_kind="number",
            quantity="voltage_v",
            unit="V",
            role="design_variable",
            group="workability",
        ),
        _input(
            "candidate.welding_context.gas_flow_l_per_min",
            label="ガス流量",
            value_kind="number",
            quantity="gas_flow_l_per_min",
            unit="L/min",
            role="design_variable",
            group="workability",
        ),
        _input(
            "candidate.welding_context.wire_feed_speed_m_per_min",
            label="ワイヤ送給速度",
            value_kind="number",
            quantity="wire_feed_speed_m_per_min",
            unit="m/min",
            role="design_variable",
            group="workability",
        ),
        _input(
            "candidate.welding_context.shielding_gas",
            label="シールドガス",
            value_kind="categorical",
            quantity="shielding_gas",
            unit=None,
            role="design_variable",
            group="welding",
        ),
        _input(
            "candidate.welding_context.welding_position",
            label="溶接姿勢",
            value_kind="categorical",
            quantity="welding_position",
            unit=None,
            role="scenario_context",
            group="welding",
        ),
        _input(
            "candidate.welding_context.preheat_temp_c",
            label="予熱温度",
            value_kind="number",
            quantity="preheat_temp_c",
            unit="°C",
            role="scenario_context",
            group="welding",
        ),
        _input(
            "candidate.test_context.test_temperature_c",
            label="シャルピー試験温度",
            value_kind="number",
            quantity="test_temperature_c",
            unit="°C",
            role="scenario_context",
            group="test",
        ),
        _input(
            "candidate.test_context.test_solution",
            label="腐食試験液",
            value_kind="categorical",
            quantity="test_solution",
            unit=None,
            role="scenario_context",
            group="test",
        ),
    )


def _external(stage: str, target: str, source: str) -> ChainBinding:
    return ChainBinding(
        target_stage_id=stage,
        target_input_path=target,
        source=ExternalBindingSource(source_kind="external", path=source),
    )


def _stage(stage: str, target: str, source_stage: str, output: str) -> ChainBinding:
    return ChainBinding(
        target_stage_id=stage,
        target_input_path=target,
        source=StageOutputBindingSource(
            source_kind="stage_output",
            stage_id=source_stage,
            output_key=output,
        ),
    )


def _upstream_bindings(
    stage_a: StageContractSurface,
    stage_b: StageContractSurface,
) -> list[ChainBinding]:
    bindings = [_external("A", "blend", "candidate.blend")]
    bindings.extend(
        _stage("B", f"composition.{port.path}", "A", port.path)
        for port in stage_a.output_ports
        if port.basis == "whole_wire"
    )
    bindings.append(
        _stage("B", "process.alloy_powder_d50_um", "A", "alloy_powder_d50_um")
    )
    for path in ("heat_input_kj_per_mm", "voltage_v", "gas_flow_l_per_min"):
        bindings.append(
            _external(
                "B",
                f"process.{path}",
                f"candidate.welding_context.{path}",
            )
        )
    for path in ("shielding_gas", "welding_position"):
        bindings.append(
            _external(
                "B",
                f"categorical.{path}",
                f"candidate.welding_context.{path}",
            )
        )
    return bindings


def _property_bindings(
    target_stage: str,
    surface: StageContractSurface,
    stage_b: StageContractSurface,
) -> list[ChainBinding]:
    output_keys = {port.path for port in stage_b.output_ports}
    result: list[ChainBinding] = []
    for port in surface.input_ports:
        if port.path.startswith("composition."):
            result.append(
                _stage(
                    target_stage,
                    port.path,
                    "B",
                    port.path.removeprefix("composition."),
                )
            )
        elif port.path.removeprefix("process.") in {
            "heat_input_kj_per_mm",
            "preheat_temp_c",
        }:
            result.append(
                _external(
                    target_stage,
                    port.path,
                    f"candidate.welding_context.{port.path.removeprefix('process.')}",
                )
            )
        elif port.path == "process.test_temperature_c":
            result.append(
                _external(
                    target_stage,
                    port.path,
                    "candidate.test_context.test_temperature_c",
                )
            )
        elif port.path == "categorical.test_solution":
            result.append(
                _external(
                    target_stage,
                    port.path,
                    "candidate.test_context.test_solution",
                )
            )
        else:
            raise ValueError(
                f"unsupported property fixture input: {target_stage}.{port.path}"
            )
    assert output_keys
    return result


def _workability_bindings() -> list[ChainBinding]:
    return [
        _external(
            "W",
            f"process.{path}",
            f"candidate.welding_context.{path}",
        )
        for path in (
            "heat_input_kj_per_mm",
            "voltage_v",
            "gas_flow_l_per_min",
            "wire_feed_speed_m_per_min",
        )
    ]


_SYNTHETIC_LIMITATION = (
    "リポジトリ同梱の合成教材データによるdemonstrationであり、"
    "実材料の品質または因果効果を保証しません。"
)


def _evidence(
    unit: str,
    goal: str,
    *sources: str,
    workability: bool = False,
) -> DecisionOutputEvidence:
    return DecisionOutputEvidence(
        evidence_kind="synthetic_demonstration",
        unit_or_scale=unit,
        goal_direction=goal,
        source_variables=tuple(sources),
        causal_claim="none",
        production_use="prohibited",
        limitation=(
            "決定論的な式で生成した溶着効率proxyです。実測値ではなく、"
            "因果効果やproduction品質を主張しません。"
            if workability
            else _SYNTHETIC_LIMITATION
        ),
    )


def _outputs(property_stage: Mapping[str, str]) -> tuple[DecisionOutput, ...]:
    return (
        DecisionOutput(
            output_id="deposited-composition-C",
            source_stage_id="B",
            source_output_key="C",
            label="溶着金属 C",
            group="composition",
            role="diagnostic",
            required_for_complete_result=False,
            evidence=_evidence(
                "mass% deposited metal",
                "none",
                "candidate.blend",
                "candidate.welding_context.heat_input_kj_per_mm",
            ),
        ),
        DecisionOutput(
            output_id="tensile-strength",
            source_stage_id=property_stage["TS"],
            source_output_key="TS",
            label="引張強さ",
            group="mechanical",
            role="primary_objective",
            required_for_complete_result=True,
            evidence=_evidence(
                "MPa",
                "at_least",
                "deposited_composition",
                "candidate.welding_context.heat_input_kj_per_mm",
                "candidate.welding_context.preheat_temp_c",
            ),
        ),
        DecisionOutput(
            output_id="charpy-energy",
            source_stage_id=property_stage["CHARPY_ENERGY"],
            source_output_key="CHARPY_ENERGY",
            label="吸収エネルギー",
            group="toughness",
            role="secondary_outcome",
            required_for_complete_result=False,
            evidence=_evidence(
                "J",
                "at_least",
                "deposited_composition",
                "candidate.test_context.test_temperature_c",
            ),
        ),
        DecisionOutput(
            output_id="corrosion-rate",
            source_stage_id=property_stage["CORROSION_RATE"],
            source_output_key="CORROSION_RATE",
            label="腐食速度",
            group="corrosion",
            role="hard_constraint",
            required_for_complete_result=False,
            evidence=_evidence(
                "mm/year",
                "at_most",
                "deposited_composition",
                "candidate.test_context.test_solution",
            ),
        ),
        DecisionOutput(
            output_id="deposition-efficiency",
            source_stage_id="W",
            source_output_key="deposition_efficiency_pct",
            label="溶着効率proxy",
            group="processability",
            role="secondary_outcome",
            required_for_complete_result=False,
            evidence=_evidence(
                "%",
                "at_least",
                "candidate.welding_context.heat_input_kj_per_mm",
                "candidate.welding_context.voltage_v",
                "candidate.welding_context.gas_flow_l_per_min",
                "candidate.welding_context.wire_feed_speed_m_per_min",
                workability=True,
            ),
        ),
    )


def welding_prediction_graph_definitions(
    surfaces: Mapping[str, StageContractSurface],
) -> tuple[PredictionGraphDefinition, PredictionGraphDefinition]:
    stage_a = surfaces[STAGE_A_ID]
    stage_b = surfaces[STAGE_B_ID]
    stage_c = surfaces[STAGE_C_ID]

    shared_bindings = _upstream_bindings(stage_a, stage_b)
    shared_bindings.extend(_property_bindings("C", stage_c, stage_b))
    shared_bindings.extend(_workability_bindings())
    multi = PredictionGraphDefinition(
        graph_id=WELDING_MULTI_OUTPUT_GRAPH_ID,
        label="溶接材料判断 Multi-output Package比較fixture",
        stages=(
            ChainStage(
                stage_id="A",
                stage_kind="deterministic_transform",
                contract_id=STAGE_A_ID,
            ),
            ChainStage(stage_id="B", stage_kind="task", contract_id=STAGE_B_ID),
            ChainStage(stage_id="C", stage_kind="task", contract_id=STAGE_C_ID),
            ChainStage(
                stage_id="W",
                stage_kind="task",
                contract_id=WELDING_GRAPH_DEPOSITION_EFFICIENCY_TASK_ID,
            ),
        ),
        inputs=_inputs(),
        bindings=tuple(shared_bindings),
        decision_outputs=_outputs(
            {"TS": "C", "CHARPY_ENERGY": "C", "CORROSION_RATE": "C"}
        ),
    )

    split_stages = {
        "T": WELDING_GRAPH_TENSILE_TASK_ID,
        "U": WELDING_GRAPH_TOUGHNESS_TASK_ID,
        "R": WELDING_GRAPH_CORROSION_TASK_ID,
    }
    split_bindings = _upstream_bindings(stage_a, stage_b)
    for stage_id, task_id in split_stages.items():
        split_bindings.extend(
            _property_bindings(stage_id, surfaces[task_id], stage_b)
        )
    split_bindings.extend(_workability_bindings())
    split = PredictionGraphDefinition(
        graph_id=WELDING_SPLIT_OUTPUT_GRAPH_ID,
        label="溶接材料判断 Split Package比較fixture",
        stages=(
            ChainStage(
                stage_id="A",
                stage_kind="deterministic_transform",
                contract_id=STAGE_A_ID,
            ),
            ChainStage(stage_id="B", stage_kind="task", contract_id=STAGE_B_ID),
            *(
                ChainStage(stage_id=stage_id, stage_kind="task", contract_id=task_id)
                for stage_id, task_id in split_stages.items()
            ),
            ChainStage(
                stage_id="W",
                stage_kind="task",
                contract_id=WELDING_GRAPH_DEPOSITION_EFFICIENCY_TASK_ID,
            ),
        ),
        inputs=_inputs(),
        bindings=tuple(split_bindings),
        decision_outputs=_outputs(
            {"TS": "T", "CHARPY_ENERGY": "U", "CORROSION_RATE": "R"}
        ),
    )
    return multi, split


def bootstrap_welding_prediction_graphs(
    *,
    store: Store,
    workspace_catalog: WorkspaceCatalog,
    task_registry: TaskRegistry,
    transform_catalog: DeterministicTransformCatalog,
) -> tuple[str, str]:
    task_ids = (
        STAGE_B_ID,
        STAGE_C_ID,
        WELDING_GRAPH_TENSILE_TASK_ID,
        WELDING_GRAPH_TOUGHNESS_TASK_ID,
        WELDING_GRAPH_CORROSION_TASK_ID,
        WELDING_GRAPH_DEPOSITION_EFFICIENCY_TASK_ID,
    )
    for task_id in task_ids:
        task_registry.require_available(task_id)
    surfaces = {
        STAGE_A_ID: _stage_a_surface(transform_catalog),
        **{task_id: _task_surface(task_registry, task_id) for task_id in task_ids},
    }
    contracts = {
        (surface.stage_kind, surface.contract_id): surface
        for surface in surfaces.values()
    }
    stage_a_entry = transform_catalog.entry(STAGE_A_ID)
    definitions = welding_prediction_graph_definitions(surfaces)
    revision_ids: list[str] = []
    for definition in definitions:
        locks = {
            stage.stage_id: (
                ChainStageLock(
                    contract_digest=surfaces[stage.contract_id].contract_digest,
                    package_manifest_digest=(
                        f"sha256:{stage_a_entry.package.manifest_sha256}"
                    ),
                )
                if stage.stage_kind == "deterministic_transform"
                else _task_lock(
                    workspace_catalog,
                    task_registry,
                    surfaces[stage.contract_id],
                )
            )
            for stage in definition.stages
        }
        revision = build_prediction_graph_revision(
            definition,
            revision=1,
            contracts=contracts,
            stage_locks=locks,
        )
        store.register_chain_definition(definition)
        revision_ids.append(
            store.register_chain_revision(revision, contracts=contracts)
        )
    return revision_ids[0], revision_ids[1]
