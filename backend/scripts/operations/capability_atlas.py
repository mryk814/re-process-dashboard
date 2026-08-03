from __future__ import annotations

import argparse
import hashlib
import json
import sys
from tempfile import TemporaryDirectory
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
BACKEND_SRC = REPOSITORY_ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))
if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

from readiness_inventory import build_readiness_inventory  # noqa: E402
from task_inventory import build_inventory_from_data  # noqa: E402
from decision_workbench.application.workspace_catalog_bootstrap import (  # noqa: E402
    bootstrap_workspace_catalog,
)
from decision_workbench.bootstrap.resources import prepare_app_resources  # noqa: E402
from decision_workbench.developer_experience.capability_atlas import (  # noqa: E402
    build_capability_atlas,
    summarize_missingness_policy,
    summarize_missingness_promotion,
)
from decision_workbench.application.decision_activity_registry import build_registry  # noqa: E402
from decision_workbench.application.proposal_strategy_registry import STRATEGIES  # noqa: E402
from decision_workbench.modeling.model_lifecycle import (  # noqa: E402
    ACTIVE_PACKAGES_PATH,
    AVAILABLE_PACKAGES_PATH,
    MODELS_ROOT,
    load_active_packages,
    load_available_packages,
)
from decision_workbench.modeling.packages.loader import ModelPackageLoader  # noqa: E402
from decision_workbench.modeling.packages.contracts import (  # noqa: E402
    FeaturePipelineDocument,
)
from decision_workbench.modeling.training.readiness import standard_estimator_catalog  # noqa: E402
from decision_workbench.execution.inference_work_graph import semantic_digest  # noqa: E402
from decision_workbench.modeling.transform_catalog import (  # noqa: E402
    load_deterministic_transform_catalog,
)
from decision_workbench.persistence.welding_prediction_graph_bootstrap import (  # noqa: E402
    bundled_welding_prediction_graph_definitions,
)
from decision_workbench.persistence.store import Store  # noqa: E402
from decision_workbench.task_composition.builtin.catalog import (  # noqa: E402
    BUILTIN_TASK_MODULES,
)
from decision_workbench.task_composition.external_tasks import (  # noqa: E402
    without_personal_task_discovery,
)
from decision_workbench.tasks.task_registry import load_task_contracts  # noqa: E402

DEFAULT_OUTPUT = REPOSITORY_ROOT / "docs" / "contracts" / "capability-atlas.json"
DESIGN_PRIOR_REPLAY_REPORT = (
    REPOSITORY_ROOT
    / "docs"
    / "research"
    / "real-task-design-prior-replay-report.json"
)
PROMOTION_REPORTS = (
    REPOSITORY_ROOT / "docs" / "reports" / "mpea-missingness-promotion.json",
)


def _profile_missingness_inputs(data: Any) -> list[dict[str, Any]]:
    profile = getattr(data, "profile", None)
    inputs = getattr(profile, "inputs", ())
    result: list[dict[str, Any]] = []
    for item in inputs:
        if item.kind == "number" and hasattr(item, "numeric_missing"):
            policies = {
                "missing": item.numeric_missing.model_dump(mode="json"),
            }
        elif item.kind == "categorical" and hasattr(item, "categorical_missing"):
            policies = {
                "missing": item.categorical_missing.model_dump(mode="json"),
                "unknown_category": item.unknown_category.model_dump(mode="json"),
            }
        else:
            continue
        result.append({"path": item.path, "kind": item.kind, "policies": policies})
    return result


def _repository_locator(path: Path) -> str:
    return path.resolve().relative_to(MODELS_ROOT.resolve()).as_posix()


def _bundled_resources() -> Any:
    active = load_active_packages(ACTIVE_PACKAGES_PATH)
    with without_personal_task_discovery():
        return prepare_app_resources(
            source_overrides={
                task_id: REPOSITORY_ROOT / module.default_source
                for task_id, module in BUILTIN_TASK_MODULES.items()
            },
            package_roots={
                task_id: MODELS_ROOT / selection.active
                for task_id, selection in active.tasks.items()
            },
            active_packages_path=ACTIVE_PACKAGES_PATH,
        )


def _package_details(
    task_data: dict[str, Any],
    task_registry: Any,
) -> dict[str, dict[str, Any]]:
    loader = ModelPackageLoader()
    active = load_active_packages(ACTIVE_PACKAGES_PATH)
    registered = load_available_packages(AVAILABLE_PACKAGES_PATH)
    registered_locators = set(registered.packages)
    catalog_by_task: dict[str, list[dict[str, Any]]] = {}
    loaded_by_locator = {}
    with TemporaryDirectory(prefix="capability-atlas-") as directory:
        database = Path(directory) / "catalog.db"
        Store(database)
        catalog = bootstrap_workspace_catalog(
            database,
            task_registry,
            available_packages_paths=(AVAILABLE_PACKAGES_PATH,),
        )
        for reference in catalog.list_model_package_refs():
            package = loader.load(Path(reference.locator))
            locator = _repository_locator(package.root)
            loaded_by_locator[locator] = package
            manifest = package.manifest
            selection = active.tasks.get(manifest.task_id)
            catalog_by_task.setdefault(manifest.task_id, []).append({
                "path": locator,
                "package_id": manifest.package_id,
                "version": manifest.package_version,
                "manifest_sha256": package.manifest_sha256,
                "runtime_types": sorted({item.runtime_type for item in manifest.predictors}),
                "registered_available": locator in registered_locators,
                "is_active": selection is not None and locator == selection.active,
                "is_previous": selection is not None and locator == selection.previous,
            })
    for packages in catalog_by_task.values():
        packages.sort(key=lambda item: item["path"])

    details: dict[str, dict[str, Any]] = {}
    for task_id, selection in active.tasks.items():
        package = loaded_by_locator[selection.active]
        manifest = package.manifest
        if manifest.task_id != task_id:
            raise ValueError(f"active Package Task mismatch: {task_id} -> {manifest.task_id}")
        validation_plans: Any = []
        if manifest.quality_report:
            report = json.loads(package.artifact_path(manifest.quality_report).read_text(encoding="utf-8"))
            validation_plans = report.get("validation_plans", [])
        pipeline_document = (
            FeaturePipelineDocument.model_validate_json(
                package.artifact_path(manifest.feature_pipeline.spec).read_text(
                    encoding="utf-8"
                )
            )
            if manifest.feature_pipeline is not None
            else None
        )
        stats_path = next(
            (
                path
                for path in (manifest.feature_pipeline.artifacts if manifest.feature_pipeline else ())
                if path.endswith("training_stats.json")
            ),
            None,
        )
        training_stats = (
            json.loads(package.artifact_path(stats_path).read_text(encoding="utf-8"))
            if stats_path is not None
            else {}
        )
        details[task_id] = {
            "feature_pipeline": None if manifest.feature_pipeline is None else manifest.feature_pipeline.model_dump(mode="json"),
            "validation_plans": validation_plans,
            "targets": [
                {"key": item.target, "unit": item.unit, "kind": item.target_kind}
                for item in manifest.predictors
            ],
            "available_packages": catalog_by_task.get(task_id, []),
            "missingness": summarize_missingness_policy(
                profile_inputs=_profile_missingness_inputs(task_data[task_id]),
                pipeline_identity=(
                    {
                        "id": pipeline_document.id,
                        "version": pipeline_document.version,
                        "document_digest": semantic_digest(
                            pipeline_document.model_dump(mode="json")
                        ),
                    }
                    if pipeline_document is not None
                    else None
                ),
                pipeline_policy=(
                    pipeline_document.missing_policy.model_dump(mode="json")
                    if pipeline_document is not None
                    and pipeline_document.missing_policy is not None
                    else None
                ),
                training_stats_digest=(
                    semantic_digest(training_stats)
                    if stats_path is not None
                    else None
                ),
                training_policy=training_stats.get("missing_policy"),
            ),
        }
    return details


def _graph_capability() -> dict[str, Any]:
    task_contracts = load_task_contracts(
        root=BACKEND_SRC / "decision_workbench" / "tasks" / "task_definitions"
    )
    definitions = bundled_welding_prediction_graph_definitions(
        task_contracts=task_contracts,
        transform_catalog=load_deterministic_transform_catalog(),
    )
    graphs = [
        {
            "graph_id": definition.graph_id,
            "label": definition.label,
            "stages": [stage.model_dump(mode="json") for stage in definition.stages],
            "decision_outputs": [
                output.model_dump(mode="json")
                for output in definition.decision_outputs
            ],
        }
        for definition in definitions
    ]
    return {
        "status": "implemented_fixture",
        "task_ids": sorted({
            stage.contract_id
            for definition in definitions
            for stage in definition.stages
            if stage.stage_kind == "task"
        }),
        "graphs": graphs,
    }


def _design_prior_promotion() -> dict[str, Any]:
    report = json.loads(
        DESIGN_PRIOR_REPLAY_REPORT.read_text(encoding="utf-8")
    )
    decisions = report["decisions"]
    return {
        "status": "evaluated_no_production_promotion",
        "task_id": report["protocol"]["task_id"],
        "report_schema_version": report["schema_version"],
        "report_digest": report["result_digest"],
        "design_prior_identity": report["protocol"]["design_prior"]["identity"],
        "design_prior_manifest_digest": report["protocol"]["design_prior"][
            "manifest_digest"
        ],
        "generator_decisions": {
            "knn_local": decisions["knn_local"],
            "gaussian_rank_copula": decisions["gaussian_rank_copula"],
        },
        "production_promotion": decisions["production_promotion"],
        "proposal_registry_changed": decisions["proposal_registry_changed"],
        "limitations": report["limitations"],
    }


def _promotion_evidence(
    package_details: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    promotions: dict[str, dict[str, Any]] = {}
    for path in PROMOTION_REPORTS:
        report = json.loads(path.read_text(encoding="utf-8"))
        task_id = str(report["task"]["task_id"])
        if task_id in promotions or task_id not in package_details:
            raise ValueError(
                f"missingness promotion report Task is duplicate or unknown: {task_id}"
            )
        active = next(
            item
            for item in package_details[task_id]["available_packages"]
            if item["is_active"]
        )
        reference = report["package_authority"][
            "active_recipe_reference_only"
        ]
        if (
            reference["package_id"] != active["package_id"]
            or reference["package_version"] != active["version"]
            or reference["manifest_digest"]
            != f"sha256:{active['manifest_sha256']}"
        ):
            raise ValueError(
                f"missingness promotion active Package reference drifted: {task_id}"
            )
        promotions[task_id] = summarize_missingness_promotion(
            report,
            report_ref=path.relative_to(REPOSITORY_ROOT).as_posix(),
            report_digest=(
                f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
            ),
        )
    return promotions


def build_atlas() -> dict[str, Any]:
    resources = _bundled_resources()
    packages_by_task = {
        task_id: resources.task_registry.entry_for(task_id).model_package
        for task_id in BUILTIN_TASK_MODULES
    }
    package_details = _package_details(
        dict(resources.data_by_task),
        resources.task_registry,
    )
    return build_capability_atlas(
        task_inventory=build_inventory_from_data(
            dict(resources.data_by_task),
            packages_by_task,
        ),
        readiness_inventory=build_readiness_inventory().model_dump(mode="json"),
        standard_estimator_catalog=standard_estimator_catalog().model_dump(mode="json"),
        package_details=package_details,
        decision_activities=[item.definition.model_dump(mode="json") for item in build_registry().values()],
        proposal_strategies=[item.model_dump(mode="json") for item in STRATEGIES],
        graph_capability=_graph_capability(),
        design_prior_promotion=_design_prior_promotion(),
        promotion_evidence=_promotion_evidence(package_details),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or verify the bundled Capability Atlas.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = json.dumps(build_atlas(), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != expected:
            print(f"Capability Atlas is stale: {args.output}", file=sys.stderr)
            return 1
        print(f"Capability Atlas is current: {args.output}")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(expected, encoding="utf-8", newline="\n")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
