"""Project the checked-in capability authorities into one read-only Atlas.

The Atlas intentionally receives already-resolved facts.  It does not open a
Workspace or the Model Library service: those are personal, mutable read
models, while this document describes only bundled application capability.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from decision_workbench.contracts.model_hypothesis_contracts import (
    ModelHypothesisCatalog,
)
from decision_workbench.data.observation_authoring import (
    complex_data_authoring_capability,
)

from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.modeling.sampling_identity import SAMPLE_BASED_RUNTIME_TYPES


def stochastic_reproducibility_capability() -> dict[str, Any]:
    """Describe the bundled sample-based evidence contract and its limits."""

    return {
        "status": "effective_sampling_identity_recorded",
        "identity_schema_version": "sampling-identity/v1",
        "runtime_types": sorted(SAMPLE_BASED_RUNTIME_TYPES),
        "limitations": [
            "response_curve_sampling_identity_unavailable",
            "legacy_evidence_sampling_conditions_unavailable",
        ],
    }


def source_support_status(source: Mapping[str, Any]) -> str:
    """Classify source scope from the generated source authority."""

    kind = str(source.get("kind", "")).lower()
    path = str(source.get("path", "")).replace("\\", "/").lower()
    filename = path.rsplit("/", 1)[-1]
    if (
        kind in {"synthetic", "fixture"}
        or "synthetic" in kind
        or "/fixtures/" in f"/{path}"
        or "synthetic" in filename
    ):
        return "research_or_fixture"
    return "bundled_reference"


def summarize_missingness_policy(
    *,
    profile_inputs: Iterable[Mapping[str, Any]],
    pipeline_identity: Mapping[str, Any] | None,
    pipeline_policy: Mapping[str, Any] | None,
    training_stats_digest: str | None,
    training_policy: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Summarize the effective profile/package missing-input authority."""

    inputs = sorted(profile_inputs, key=lambda item: str(item["path"]))
    pipeline = dict(pipeline_policy or {})
    training = dict(training_policy or {})
    authority = {
        "profile_inputs": inputs,
        "pipeline_identity": dict(pipeline_identity or {}) or None,
        "pipeline_policy": pipeline or None,
        "training_stats_digest": training_stats_digest,
        "training_policy": training or None,
    }
    projection_digest = semantic_digest(authority)
    if not inputs:
        return {
            "status": "runtime_contract_not_exposed",
            "policy_digest": projection_digest,
            "declared_package_policy_digest": None,
            "feature_pipeline": authority["pipeline_identity"],
            "training_stats_digest": training_stats_digest,
            "profile_input_count": 0,
            "profile_strategies": [],
            "input_completeness": ["complete"],
            "support_outcomes": ["not_evaluated"],
            "native_missing_supported": False,
        }

    strategies = sorted({
        str(policy["strategy"])
        for item in inputs
        for policy in item["policies"].values()
    })
    has_imputation = any(strategy != "reject" for strategy in strategies)
    has_rejection = "reject" in strategies
    declared_digest = (
        training.get("policy_digest")
        or pipeline.get("digest")
    )
    status = (
        "evaluated_imputation"
        if has_imputation and training.get("pattern_evidence")
        else "declared_imputation"
        if has_imputation
        else "reject_only"
    )
    completeness = ["complete"]
    if has_imputation:
        completeness.append("imputed")
    if has_rejection or has_imputation:
        completeness.append("blocked")
    support = ["supported"]
    if has_imputation:
        support.extend(["sparse", "unseen"])
    if has_rejection:
        support.append("incompatible")
    return {
        "status": status,
        "policy_digest": str(declared_digest or projection_digest),
        "projection_digest": projection_digest,
        "declared_package_policy_digest": declared_digest,
        "feature_pipeline": authority["pipeline_identity"],
        "training_stats_digest": training_stats_digest,
        "profile_input_count": len(inputs),
        "profile_strategies": strategies,
        "input_completeness": completeness,
        "support_outcomes": support,
        "native_missing_supported": False,
    }


def summarize_missingness_promotion(
    report: Mapping[str, Any],
    *,
    report_ref: str,
    report_digest: str,
) -> dict[str, Any]:
    """Project fixed promotion evidence without changing active capability."""

    if report.get("schema_version") != "missingness-promotion-report/v2":
        raise ValueError("unsupported missingness promotion report")
    decision = report["decision"]
    promotion = str(decision["production_promotion"])
    if promotion not in {"not_promoted", "promoted"}:
        raise ValueError(
            f"unsupported missingness promotion decision: {promotion}"
        )
    status = (
        "evaluated_not_promoted"
        if promotion == "not_promoted"
        else "promoted"
    )
    patterns = [
        item
        for item in report["patterns"]
        if item["pattern_id"] != "complete"
    ]
    by_support = {
        support: sorted(
            item["pattern_id"]
            for item in patterns
            if item["support"] == support
        )
        for support in ("supported", "sparse", "unseen", "incompatible")
    }
    operation = dict(report["operation_capability"])
    authority_digest = operation.pop("authority_digest")
    evaluation_package = report["package_authority"]["evaluation_package"]
    return {
        "status": status,
        "report": report_ref,
        "report_digest": report_digest,
        "protocol_version": report["fixed_protocol"]["protocol_version"],
        "target": report["task"]["target"],
        "active_package_reference": report["package_authority"][
            "active_recipe_reference_only"
        ],
        "evaluation_package_authority": {
            "package_id": evaluation_package["package_id"],
            "package_version": evaluation_package["package_version"],
            "manifest_digest": evaluation_package["manifest_digest"],
            "capability_source": evaluation_package["capability_source"],
            "verified": evaluation_package["verified"],
        },
        "operation_policy": {
            "authority": "evaluation_package_feature_pipeline_artifact",
            "authority_digest": authority_digest,
            **operation,
        },
        "pattern_support": {
            "classifier": report["fixed_protocol"]["support_policy"],
            "by_status": by_support,
            "evidence_rule": (
                "support uses observed training-pattern evidence; masking and "
                "completion simulation do not promote unseen patterns"
            ),
        },
        "limitations": list(decision["reasons"]),
    }


def capability_atlas_read_model(atlas: Mapping[str, Any]) -> dict[str, Any]:
    """Return the compact technical detail shown in Developer Control Center."""

    tasks = list(atlas["tasks"])
    hypothesis_catalog = atlas["model_hypothesis_catalog"]
    hypothesis_cards = list(hypothesis_catalog["cards"])
    graph_mode = next(
        mode for mode in atlas["project_modes"] if mode["mode"] == "prediction_graph"
    )
    return {
        "schema_version": atlas["schema_version"],
        "authority": "bundled",
        "stochastic_reproducibility": atlas["stochastic_reproducibility"],
        "project_modes": [mode["mode"] for mode in atlas["project_modes"]],
        "task_count": len(tasks),
        "available_package_count": sum(
            len(task["available_packages"]) for task in tasks
        ),
        "graph_count": len(graph_mode["bundled_graphs"]),
        "model_hypothesis_catalog": {
            "schema_version": hypothesis_catalog["schema_version"],
            "authority": hypothesis_catalog["authority"],
            "card_count": len(hypothesis_cards),
            "lifecycle_counts": {
                status: sum(
                    card["lifecycle_status"] == status
                    for card in hypothesis_cards
                )
                for status in ("standard", "shared_specialized", "research")
            },
            "playground_handoff_status": "not_implemented",
            "cards": [
                {
                    "id": card["id"],
                    "version": card["version"],
                    "label": card["label"],
                    "comparison_role": card["comparison_role"],
                    "lifecycle_status": card["lifecycle_status"],
                    "data_grain": card["data_grain"],
                    "target_support": card["target_support"],
                    "required_capabilities": card["required_capabilities"],
                    "recipe_id": card["recipe_identity"]["recipe_id"],
                    "execution_status": card["recipe_identity"][
                        "execution_status"
                    ],
                }
                for card in hypothesis_cards
            ],
        },
        "tasks": [
            {
                "task_id": task["task_id"],
                "runtime_available": task["runtime_capabilities"]["available"],
                "available_package_count": len(task["available_packages"]),
                "graph_compatible": task["graph_compatible"],
                "missingness_status": task["missingness_modes"]["status"],
                "missingness_policy_digest": task["missingness_modes"][
                    "policy_digest"
                ],
            }
            for task in tasks
        ],
    }


def build_capability_atlas(
    *,
    task_inventory: Mapping[str, Any],
    readiness_inventory: Mapping[str, Any],
    standard_estimator_catalog: Mapping[str, Any],
    model_hypothesis_catalog: Mapping[str, Any],
    package_details: Mapping[str, Mapping[str, Any]],
    decision_activities: Iterable[Mapping[str, Any]],
    proposal_strategies: Iterable[Mapping[str, Any]],
    graph_capability: Mapping[str, Any],
    design_prior_promotion: Mapping[str, Any] | None = None,
    promotion_evidence: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a deterministic, bundled-only projection of capability facts."""

    hypothesis_catalog = ModelHypothesisCatalog.model_validate(
        model_hypothesis_catalog
    ).model_dump(mode="json")
    readiness_by_task = {
        item["task_id"]: item
        for item in readiness_inventory["tasks"]
    }
    activities = sorted(decision_activities, key=lambda item: item["activity_id"])
    strategies = sorted(proposal_strategies, key=lambda item: item["strategy_id"])
    tasks: list[dict[str, Any]] = []
    graph_task_ids = set(graph_capability["task_ids"])
    promotions = dict(promotion_evidence or {})
    task_ids = {
        str(item["task_id"])
        for item in task_inventory["tasks"]
    }
    if set(promotions) - task_ids:
        raise ValueError("promotion evidence references an unknown Task")

    for task in sorted(task_inventory["tasks"], key=lambda item: item["task_id"]):
        task_id = task["task_id"]
        readiness = readiness_by_task[task_id]
        package = package_details[task_id]
        application = task["application_capability"]
        runtime_operations = task["runtime_operations"]
        source_status = source_support_status(task["source"])
        has_runtime = bool(task["active_package"]["runtime_types"])
        standard_authoring = readiness["standard_authoring"]
        known_limitations: list[str] = []
        if not standard_authoring:
            known_limitations.append("standard_builder_unavailable")
        if not application["project_creation"]:
            known_limitations.append("project_creation_ui_unavailable")
        if not runtime_operations.get("actual_measurement", False):
            known_limitations.append("actual_measurement_unavailable")
        if source_status == "research_or_fixture":
            known_limitations.append("research_or_fixture_source")
        if task_id in promotions and promotions[task_id]["status"] != "promoted":
            known_limitations.append("missingness_evaluation_not_promoted")

        missingness = dict(package["missingness"])
        if task_id in promotions:
            missingness["promotion_evidence"] = promotions[task_id]
        tasks.append({
            "task_id": task_id,
            "label": task["label"],
            "source_shape": readiness["source_shape"],
            "profile_family": readiness["profile_family"],
            "input_semantics": readiness["input_kinds"],
            "target_semantics": package["targets"],
            "standard_authoring": standard_authoring,
            "validation_plans": package["validation_plans"],
            "feature_recipe": package["feature_pipeline"],
            "active_package": task["active_package"],
            "available_packages": package["available_packages"],
            "runtime_capabilities": {
                "available": has_runtime,
                "runtime_types": task["active_package"]["runtime_types"],
                "operations": runtime_operations,
            },
            "workbench_surfaces": application["workbench_surfaces"],
            "decision_activities": {
                "status": "project_dependent",
                "catalog_ref": "global_catalogs.decision_activities",
            },
            "proposal_strategies": {
                "status": "project_and_package_dependent",
                "catalog_ref": "global_catalogs.proposal_strategies",
            },
            "graph_compatible": task_id in graph_task_ids,
            "actual_supported": runtime_operations.get("actual_measurement", False),
            "missingness_modes": missingness,
            "historical_candidate_supported": {
                "status": "eligible_record_required",
                "provenance": ["historical_observation", "experiment_batch"],
            },
            "support_status": {
                "runtime": "available" if has_runtime else "unavailable",
                "standard_builder": "available" if standard_authoring else "unavailable",
                "project_creation_ui": "available" if application["project_creation"] else "unavailable",
                "source": source_status,
            },
            "known_limitations": known_limitations,
        })

    return {
        "schema_version": "capability-atlas/v1",
        "generated_from": [
            "docs/contracts/task-inventory.json authority",
            "docs/contracts/readiness-inventory.json authority",
            "docs/contracts/standard-estimator-readiness.json authority",
            "model-hypothesis-catalog/v1 bundled allow-list",
            "bundled Model Package manifests",
            "decision activity and proposal strategy registries",
            "bundled Prediction Graph definitions",
            "fixed real-Task Design Prior replay report",
            *(
                ["fixed promotion evidence reports"]
                if promotions
                else []
            ),
            "sampling-identity/v1 contract",
        ],
        "stochastic_reproducibility": stochastic_reproducibility_capability(),
        "project_modes": [
            {
                "mode": "single_task",
                "status": "implemented",
                "description": "one Task, Dataset View, and Model Package are fixed per Project",
            },
            {
                "mode": "chain",
                "status": "implemented_v1",
                "description": "a Chain Revision fixes ordered Task or deterministic-transform stages and bindings",
            },
            {
                "mode": "prediction_graph",
                "status": graph_capability["status"],
                "description": "a graph Project fixes explicit stage dependencies; bundled definitions are comparison fixtures",
                "bundled_graphs": graph_capability["graphs"],
            },
        ],
        "candidate_provenance": [
            "direct", "lineage", "screening", "historical_observation",
            "experiment_batch", "copy", "snapshot", "manual",
        ],
        "missingness_contract_vocabulary": {
            "input_completeness": ["complete", "imputed", "native_missing", "blocked"],
            "prediction_status": ["final", "provisional", "blocked"],
            "support": ["supported", "sparse", "unseen", "incompatible"],
        },
        "complex_data_authoring": complex_data_authoring_capability(),
        "design_prior_promotion": (
            dict(design_prior_promotion)
            if design_prior_promotion is not None
            else {
                "status": "not_evaluated",
                "production_promotion": False,
            }
        ),
        "standard_estimator_catalog": standard_estimator_catalog,
        "model_hypothesis_catalog": hypothesis_catalog,
        "global_catalogs": {
            "decision_activities": activities,
            "proposal_strategies": strategies,
            "scope": (
                "Registry membership is global; availability is resolved from "
                "the Project, Task runtime, Package capabilities, and request."
            ),
        },
        "tasks": tasks,
        "out_of_scope": [
            "personal Workspace Model Library entries",
            "arbitrary plugin discovery",
            "a universal cross-task score",
        ],
        "known_limitations": [
            "The Atlas is a bundled-capability projection, not a personal Workspace inventory.",
            "An eligible historical observation or experiment batch is required before its provenance can be used.",
        ],
    }
