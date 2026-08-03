from __future__ import annotations

import json
from pathlib import Path

from backend.scripts.operations.capability_atlas import _bundled_resources
from decision_workbench.application.workspace_catalog_bootstrap import (
    bootstrap_workspace_catalog,
)
from decision_workbench.developer_experience.capability_atlas import (
    build_capability_atlas,
    source_support_status,
    stochastic_reproducibility_capability,
    summarize_missingness_policy,
    summarize_missingness_promotion,
)
from decision_workbench.modeling.model_lifecycle import (
    ACTIVE_PACKAGES_PATH,
    MODELS_ROOT,
    load_active_packages,
)
from decision_workbench.modeling.model_hypothesis_catalog import (
    model_hypothesis_catalog,
)
from decision_workbench.modeling.packages.loader import ModelPackageLoader
from decision_workbench.modeling.transform_catalog import (
    load_deterministic_transform_catalog,
)
from decision_workbench.persistence.welding_prediction_graph_bootstrap import (
    bundled_welding_prediction_graph_definitions,
)
from decision_workbench.persistence.store import Store
from decision_workbench.tasks.task_registry import load_task_contracts


ROOT = Path(__file__).resolve().parents[2]
ATLAS_PATH = ROOT / "docs" / "contracts" / "capability-atlas.json"
TASK_INVENTORY_PATH = ROOT / "docs" / "contracts" / "task-inventory.json"
MISSINGNESS_REPORT_PATH = (
    ROOT / "docs" / "reports" / "mpea-missingness-promotion.json"
)
TASK_DEFINITIONS_ROOT = (
    ROOT / "backend" / "src" / "decision_workbench" / "tasks" / "task_definitions"
)


def test_atlas_joins_authorities_without_personal_workspace_state() -> None:
    atlas = build_capability_atlas(
        task_inventory={
            "tasks": [{
                "task_id": "task-a",
                "label": "Task A",
                "source": {"kind": "synthetic"},
                "active_package": {"runtime_types": ["builtin.tabular.v1"]},
                "runtime_operations": {"actual_measurement": False},
                "application_capability": {
                    "project_creation": False,
                    "workbench_surfaces": ["compare"],
                },
            }],
        },
        readiness_inventory={"tasks": [{
            "task_id": "task-a",
            "source_shape": "tabular",
            "profile_family": "tabular",
            "input_kinds": ["number"],
            "standard_authoring": False,
        }]},
        standard_estimator_catalog={"entries": []},
        model_hypothesis_catalog=model_hypothesis_catalog().model_dump(
            mode="json"
        ),
        package_details={"task-a": {
            "targets": [{"key": "strength", "kind": "continuous", "unit": "MPa"}],
            "validation_plans": [{"strategy": "grouped_kfold"}],
            "feature_pipeline": {"id": "recipe-a", "version": "1"},
            "available_packages": [{"path": "packages/task-a-v1", "is_active": True}],
            "missingness": {
                "status": "reject_only",
                "policy_digest": "sha256:fixture",
            },
        }},
        decision_activities=[{"activity_id": "robustness"}],
        proposal_strategies=[{"strategy_id": "sobol"}],
        graph_capability={"status": "implemented_fixture", "task_ids": ["task-a"], "graphs": []},
    )

    task = atlas["tasks"][0]
    assert atlas["schema_version"] == "capability-atlas/v1"
    assert atlas["stochastic_reproducibility"] == (
        stochastic_reproducibility_capability()
    )
    assert atlas["model_hypothesis_catalog"]["schema_version"] == (
        "model-hypothesis-catalog/v1"
    )
    assert {
        card["id"]
        for card in atlas["model_hypothesis_catalog"]["cards"]
    } >= {
        "ridge-linear-baseline",
        "bayesian-additive-spline",
        "exact-rbf-gaussian-process",
        "welding-charpy-observation-family",
    }
    assert [mode["mode"] for mode in atlas["project_modes"]] == [
        "single_task", "chain", "prediction_graph",
    ]
    assert task["feature_recipe"]["id"] == "recipe-a"
    assert task["validation_plans"] == [{"strategy": "grouped_kfold"}]
    assert task["graph_compatible"] is True
    assert task["missingness_modes"]["status"] == "reject_only"
    assert task["available_packages"] == [
        {"path": "packages/task-a-v1", "is_active": True}
    ]
    assert task["decision_activities"] == {
        "status": "project_dependent",
        "catalog_ref": "global_catalogs.decision_activities",
    }
    assert atlas["global_catalogs"]["decision_activities"] == [
        {"activity_id": "robustness"}
    ]
    assert task["support_status"] == {
        "runtime": "available",
        "standard_builder": "unavailable",
        "project_creation_ui": "unavailable",
        "source": "research_or_fixture",
    }
    assert "personal Workspace Model Library entries" in atlas["out_of_scope"]
    assert atlas["candidate_provenance"] == [
        "direct", "lineage", "screening", "historical_observation",
        "experiment_batch", "copy", "snapshot", "manual",
    ]
    authoring = atlas["complex_data_authoring"]
    assert authoring["selected_family"] == {
        "family": "repeated_measurements",
        "status": "available",
        "profile_family": "observation-dataset-profile/v1",
        "source_contract": "single_visible_table",
        "validation_plan": "grouped_kfold",
        "feature_recipe": "observation-identity-v1",
        "estimator": "ridge.v1",
        "ui_entry": "profile_workbench",
    }
    assert {
        item["family"]: item["status"]
        for item in authoring["unselected_families"]
    } == {
        "longitudinal_curve": "specialized_only",
        "relational_workbook": "specialized_only",
    }


def test_generated_atlas_tracks_bundled_package_and_graph_authorities(
    tmp_path: Path,
) -> None:
    atlas = json.loads(ATLAS_PATH.read_text(encoding="utf-8"))
    tasks = {item["task_id"]: item for item in atlas["tasks"]}
    inventory_tasks = {
        item["task_id"]: item
        for item in json.loads(
            TASK_INVENTORY_PATH.read_text(encoding="utf-8")
        )["tasks"]
    }
    assert len(tasks) == 17
    assert atlas["stochastic_reproducibility"] == {
        "status": "effective_sampling_identity_recorded",
        "identity_schema_version": "sampling-identity/v1",
        "runtime_types": ["numpyro.dense_posterior.v1"],
        "limitations": [
            "response_curve_sampling_identity_unavailable",
            "legacy_evidence_sampling_conditions_unavailable",
        ],
    }
    assert atlas["model_hypothesis_catalog"]["authority"] == (
        "bundled_allow_list"
    )
    assert {
        task_id
        for task_id, task in tasks.items()
        if task["support_status"]["source"] == "research_or_fixture"
    } == {
        task_id
        for task_id, task in inventory_tasks.items()
        if source_support_status(task["source"]) == "research_or_fixture"
    }

    active = load_active_packages(ACTIVE_PACKAGES_PATH)
    expected_paths_by_task: dict[str, set[str]] = {}
    resources = _bundled_resources()
    Store(tmp_path / "catalog.db")
    catalog = bootstrap_workspace_catalog(
        tmp_path / "catalog.db",
        resources.task_registry,
    )
    for reference in catalog.list_model_package_refs():
        locator = (
            Path(reference.locator)
            .resolve()
            .relative_to(MODELS_ROOT.resolve())
            .as_posix()
        )
        expected_paths_by_task.setdefault(reference.task_id, set()).add(locator)
    for task_id, expected_paths in expected_paths_by_task.items():
        actual = tasks[task_id]["available_packages"]
        assert {item["path"] for item in actual} == expected_paths
        selection = active.tasks[task_id]
        assert sum(item["is_active"] for item in actual) == 1
        assert next(item for item in actual if item["is_active"])["path"] == selection.active
        assert {item["path"] for item in actual if item["is_previous"]} <= (
            {selection.previous} if selection.previous is not None else set()
        )

    definitions = bundled_welding_prediction_graph_definitions(
        task_contracts=load_task_contracts(root=TASK_DEFINITIONS_ROOT),
        transform_catalog=load_deterministic_transform_catalog(),
    )
    graph_mode = next(
        item for item in atlas["project_modes"] if item["mode"] == "prediction_graph"
    )
    actual_graphs = {item["graph_id"]: item for item in graph_mode["bundled_graphs"]}
    assert set(actual_graphs) == {item.graph_id for item in definitions}
    for definition in definitions:
        projected = actual_graphs[definition.graph_id]
        assert projected["stages"] == [
            stage.model_dump(mode="json") for stage in definition.stages
        ]
        assert projected["decision_outputs"] == [
            output.model_dump(mode="json") for output in definition.decision_outputs
        ]

    assert any(task["known_limitations"] for task in tasks.values())
    assert {task["missingness_modes"]["status"] for task in tasks.values()} == {
        "reject_only",
        "runtime_contract_not_exposed",
    }
    assert all(task["missingness_modes"]["policy_digest"].startswith("sha256:") for task in tasks.values())
    design_prior_promotion = atlas["design_prior_promotion"]
    replay = json.loads(
        (
            ROOT
            / "docs"
            / "research"
            / "real-task-design-prior-replay-report.json"
        ).read_text(encoding="utf-8")
    )
    assert design_prior_promotion["status"] == "evaluated_no_production_promotion"
    assert design_prior_promotion["task_id"] == "mpea-room-tensile-v1"
    assert design_prior_promotion["report_digest"] == replay["result_digest"]
    assert design_prior_promotion["production_promotion"] is False
    assert design_prior_promotion["proposal_registry_changed"] is False
    assert design_prior_promotion["generator_decisions"] == {
        "knn_local": replay["decisions"]["knn_local"],
        "gaussian_rank_copula": replay["decisions"]["gaussian_rank_copula"],
    }
    mpea = tasks["mpea-room-tensile-v1"]["missingness_modes"]
    assert mpea["status"] == "reject_only"
    promotion = mpea["promotion_evidence"]
    assert promotion["status"] == "evaluated_not_promoted"
    assert promotion["report"] == (
        "docs/reports/mpea-missingness-promotion.json"
    )
    assert promotion["evaluation_package_authority"]["verified"] is True
    assert promotion["operation_policy"]["authority"] == (
        "evaluation_package_feature_pipeline_artifact"
    )
    assert promotion["operation_policy"]["preview"] == "block"
    assert promotion["operation_policy"]["proposal"] == "block"
    assert promotion["operation_policy"]["export"] == "require_complete"
    assert promotion["pattern_support"]["by_status"] == {
        "supported": ["frequent-process-multi"],
        "sparse": ["sparse-process-multi"],
        "unseen": [
            "correlated-process-pair",
            "high-impact-numeric-single",
            "low-impact-numeric-single",
            "unseen-mixed-mode",
        ],
        "incompatible": [],
    }
    assert "missingness_evaluation_not_promoted" in tasks[
        "mpea-room-tensile-v1"
    ]["known_limitations"]


def test_source_scope_tracks_real_fixture_and_synthetic_authorities() -> None:
    assert source_support_status({
        "kind": "external_task:welding-graph-deposition-efficiency-v1",
        "path": "data/fixtures/prediction-graph/welding_deposition_efficiency_synthetic.csv",
    }) == "research_or_fixture"
    assert source_support_status({
        "kind": "welding_graph_synthetic_demonstration",
        "path": "data/source/welding_consumable_multistage_synthetic_dataset.xlsx",
    }) == "research_or_fixture"
    assert source_support_status({
        "kind": "external_concrete",
        "path": "data/source/external/concrete_strength.csv",
    }) == "bundled_reference"


def test_missingness_summary_digest_tracks_profile_policy_changes() -> None:
    reject = summarize_missingness_policy(
        profile_inputs=[{
            "path": "process.temperature_c",
            "kind": "number",
            "policies": {"missing": {"strategy": "reject"}},
        }],
        pipeline_identity={
            "id": "pipeline-a",
            "version": "1",
            "document_digest": "sha256:" + "1" * 64,
        },
        pipeline_policy=None,
        training_stats_digest="sha256:" + "2" * 64,
        training_policy=None,
    )
    imputed = summarize_missingness_policy(
        profile_inputs=[{
            "path": "process.temperature_c",
            "kind": "number",
            "policies": {
                "missing": {"strategy": "training_median_with_indicator"},
            },
        }],
        pipeline_identity={
            "id": "pipeline-a",
            "version": "2",
            "document_digest": "sha256:" + "3" * 64,
        },
        pipeline_policy={
            "digest": "sha256:" + "a" * 64,
            "imputation_values": {"process.temperature_c": 700.0},
        },
        training_stats_digest="sha256:" + "4" * 64,
        training_policy=None,
    )

    assert reject["status"] == "reject_only"
    assert reject["input_completeness"] == ["complete", "blocked"]
    assert imputed["status"] == "declared_imputation"
    assert imputed["input_completeness"] == ["complete", "imputed", "blocked"]
    assert imputed["support_outcomes"] == ["supported", "sparse", "unseen"]
    assert reject["policy_digest"] != imputed["policy_digest"]
    assert reject["projection_digest"] != imputed["projection_digest"]


def test_missingness_promotion_projection_keeps_evaluation_separate() -> None:
    report = json.loads(
        MISSINGNESS_REPORT_PATH.read_text(encoding="utf-8")
    )
    projection = summarize_missingness_promotion(
        report,
        report_ref="docs/reports/mpea-missingness-promotion.json",
        report_digest="sha256:" + "d" * 64,
    )

    assert projection["status"] == "evaluated_not_promoted"
    assert projection["active_package_reference"]["package_id"] == (
        "mpea-room-tensile-ridge-v2"
    )
    assert projection["evaluation_package_authority"]["package_id"] == (
        "mpea-room-tensile-missingness-evaluation-v1"
    )
    assert projection["operation_policy"]["completion_uncertainty"] == (
        "available"
    )
    assert "simulation do not promote unseen patterns" in projection[
        "pattern_support"
    ]["evidence_rule"]
