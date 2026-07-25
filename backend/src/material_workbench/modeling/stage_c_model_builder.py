"""Build the safe, code-free Stage C observation-family Model Package."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from material_workbench.modeling.model_lifecycle import (
    QualityReport,
    TargetQualityMetric,
    canonical_training_dataset_digest,
    dataset_profile_digest,
    runtime_capability_digest,
    staged_package_destination,
    task_input_contract_digest,
)
from material_workbench.modeling.model_package_verify import verify_model_package
from material_workbench.modeling.stage_c_regression import (
    FEATURE_DEFINITIONS,
    PIPELINE_FEATURES,
    PROFILE_PATH,
    TARGET_FAMILY,
    TARGET_FEATURES,
    TASK_ID,
    candidate_feature_values,
    feature_values,
    load_stage_c_data,
    stage_c_starter_candidates,
)
from material_workbench.modeling.tabular_model_builder import (
    _cross_fitted_quantile_coverage,
    _fit,
    _grouped_oof,
)
from material_workbench.tasks.task_registry import load_task_contracts


PACKAGE_ID = "welding-stage-c-ridge-v1"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact(root: Path, path: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _digest(path),
        "bytes": path.stat().st_size,
    }


def _build(source: Path, destination: Path) -> None:
    data = load_stage_c_data(source)
    contract = load_task_contracts()[TASK_ID]
    artifact_dir = destination / "model-artifacts"
    feature_dir = destination / "feature-pipeline"
    reference_dir = destination / "reference"
    smoke_dir = destination / "smoke"
    report_dir = destination / "reports"
    for folder in (artifact_dir, feature_dir, reference_dir, smoke_dir, report_dir):
        folder.mkdir(parents=True, exist_ok=True)

    canonical_paths = tuple(
        field.path
        for group in sorted(contract.task_definition.input_groups, key=lambda item: item.order)
        for field in sorted(group.fields, key=lambda item: item.order)
    )
    pipeline_path = feature_dir / "pipeline.json"
    pipeline_path.write_text(json.dumps({
        "id": "welding-stage-c-observation-transform",
        "version": "1.0.0",
        "canonical_input_paths": list(canonical_paths),
        "features": [
            {"name": item.name, "unit": item.unit, "meaning": item.meaning, "group": item.group}
            for item in FEATURE_DEFINITIONS
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    files = [pipeline_path]
    predictors = []
    metrics = []
    records: dict[str, int] = {}
    groups_by_target: dict[str, int] = {}
    fitted_by_target: dict[str, tuple[np.ndarray, float]] = {}
    output_units = {item.key: item.unit for item in contract.task_definition.outputs}
    for target, names in TARGET_FEATURES.items():
        rows = [
            row for row in data.observations
            if row["target_status"].get(target, {}).get("usable")
        ]
        x = np.asarray([
            [feature_values(row["canonical_inputs"])[name] for name in names]
            for row in rows
        ])
        y = np.asarray([float(row["outputs"][target]) for row in rows])
        groups = [str(row["parent_key"]) for row in rows]
        residuals, fold_ids, folds = _grouped_oof(x, y, groups, 1.0)
        mean, scale, weights = _fit(x, y, 1.0)
        raw_weights = weights[1:] / scale
        raw_bias = float(weights[0] - np.sum(weights[1:] * mean / scale))
        lower, upper = np.quantile(residuals, (0.05, 0.95))
        artifact_path = artifact_dir / f"{target}.npz"
        np.savez(
            artifact_path,
            weights=raw_weights,
            bias=np.asarray(raw_bias),
            lower_offset=np.asarray(float(lower)),
            upper_offset=np.asarray(float(upper)),
        )
        files.append(artifact_path)
        records[target] = len(rows)
        groups_by_target[target] = len(set(groups))
        fitted_by_target[target] = (raw_weights, raw_bias)
        predictors.append({
            "id": f"{target}-ridge",
            "target": target,
            "unit": output_units[target],
            "target_kind": "continuous",
            "runtime_type": "builtin.linear.v1",
            "architecture_id": "stage_c_family_ridge_v1",
            "artifact": artifact_path.relative_to(destination).as_posix(),
            "predictive_family": "empirical_quantiles",
            "feature_names": list(names),
            "config": {
                "training_unit": "individual_observation",
                "validation": f"{folds}-fold grouped by weld-run key",
                "observation_family": TARGET_FAMILY[target],
                "training_cohort": f"{TARGET_FAMILY[target]}:target-usable",
                "training_rows": len(rows),
                "evaluation_groups": len(set(groups)),
                "profile_digest": data.profile_digest,
                "ridge_alpha": 1.0,
            },
        })
        metrics.append(TargetQualityMetric(
            target=target,
            parent_conditions=len(set(groups)),
            mae=float(np.mean(np.abs(residuals))),
            rmse=float(np.sqrt(np.mean(residuals ** 2))),
            interval_coverage_90=_cross_fitted_quantile_coverage(residuals, fold_ids),
            interval_coverage_method="cross-fitted-oof-residual-quantiles",
            interval_coverage_observations=len(residuals),
        ))

    stats_path = reference_dir / "training_stats.json"
    stats_path.write_text(json.dumps({
        "schema_version": "stage-c-training-stats/v1",
        "records": records,
        "groups_by_target": groups_by_target,
        "families": {
            family: {
                "source_rows": view.summary.source_rows,
                "usable_input_rows": view.summary.usable_input_rows,
                "split_groups": view.summary.split_groups,
            }
            for family, view in data.training_dataset.views.items()
        },
        "profile_id": data.profile_id,
        "profile_digest": data.profile_digest,
        "source_sha256": data.source_sha256,
    }, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    files.append(stats_path)
    quality_path = report_dir / "quality-report.json"
    quality_path.write_text(QualityReport(
        schema_version="model-quality-report/v1",
        split="grouped-parent-condition-k-fold",
        folds=5,
        targets=tuple(metrics),
    ).model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    files.append(quality_path)

    sample = stage_c_starter_candidates(data.medians)[1].model_copy(
        update={"name": "Stage C package smoke"}
    )
    smoke_input = smoke_dir / "input.json"
    smoke_input.write_text(sample.model_dump_json(indent=2), encoding="utf-8", newline="\n")
    sample_values = candidate_feature_values(sample)
    smoke_expected = smoke_dir / "expected.json"
    smoke_expected.write_text(json.dumps({
        target: round(float(
            np.asarray([sample_values[name] for name in TARGET_FEATURES[target]]) @ weights + bias
        ), 8)
        for target, (weights, bias) in fitted_by_target.items()
    }, indent=2), encoding="utf-8", newline="\n")
    files.extend((smoke_input, smoke_expected))

    canonical = data.canonical_training_dataset(contract)
    manifest = {
        "schema_version": "model-package/v1",
        "package_id": PACKAGE_ID,
        "package_version": "1.0.0",
        "task_id": TASK_ID,
        "input_schema_version": "canonical-candidate/v1",
        "input_contract_digest": task_input_contract_digest(contract.task_definition),
        "runtime_capability_digest": runtime_capability_digest(contract.runtime_capability),
        "feature_pipeline": {
            "id": "welding-stage-c-observation-transform",
            "version": "1.0.0",
            "spec": pipeline_path.relative_to(destination).as_posix(),
            "canonical_input_paths": list(canonical_paths),
            "output_features": list(PIPELINE_FEATURES),
            "artifacts": [stats_path.relative_to(destination).as_posix()],
        },
        "predictors": predictors,
        "provenance": {
            "training_data_id": f"sha256:{data.source_sha256}",
            "feature_dataset_id": canonical_training_dataset_digest(canonical),
            "training_code_revision": "stage-c-family-ridge-grouped-v1",
            "dataset_profile_id": dataset_profile_digest(PROFILE_PATH),
        },
        "artifacts": [_artifact(destination, path) for path in files],
        "smoke_test": {
            "input": smoke_input.relative_to(destination).as_posix(),
            "expected": smoke_expected.relative_to(destination).as_posix(),
        },
        "quality_report": quality_path.relative_to(destination).as_posix(),
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )


def build(source: Path, destination: Path, *, replace: bool = False) -> None:
    with staged_package_destination(destination, replace=replace) as staging:
        _build(source, staging)
        verify_model_package(staging, task_id=TASK_ID, source=source)
