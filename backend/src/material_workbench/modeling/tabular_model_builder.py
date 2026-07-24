"""Train safe, code-free model packages for profile-driven CSV tasks."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from material_workbench.modeling.model_lifecycle import (
    QualityReport,
    TargetQualityMetric,
    canonical_training_dataset,
    canonical_training_dataset_digest,
    dataset_profile_digest,
    runtime_capability_digest,
    staged_package_destination,
    task_input_contract_digest,
)
from material_workbench.modeling.model_package_verify import verify_model_package
from material_workbench.modeling.model_packages import validate_task_definition_canonical_inputs
from material_workbench.modeling.tabular_regression import (
    build_tabular_features,
    build_tabular_features_from_observation,
    candidate_from_observation,
    feature_definitions,
    load_tabular_data,
)
from material_workbench.tasks.task_registry import load_task_contracts


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact(root: Path, path: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _digest(path),
        "bytes": path.stat().st_size,
    }


def _fit(x: np.ndarray, y: np.ndarray, ridge: float = 1.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-9] = 1.0
    normalized = (x - mean) / scale
    design = np.column_stack([np.ones(len(x)), normalized])
    penalty = np.eye(design.shape[1]) * ridge
    penalty[0, 0] = 0.0
    weights = np.linalg.solve(design.T @ design + penalty, design.T @ y)
    return mean, scale, weights


def _predict(x: np.ndarray, fitted: tuple[np.ndarray, np.ndarray, np.ndarray]) -> np.ndarray:
    mean, scale, weights = fitted
    return np.column_stack([np.ones(len(x)), (x - mean) / scale]) @ weights


def _grouped_oof(x: np.ndarray, y: np.ndarray, groups: list[str]) -> tuple[np.ndarray, int]:
    unique = sorted(set(groups))
    folds = min(5, len(unique))
    if folds < 2:
        raise ValueError("At least two independent groups are required")
    assignment = {group: index % folds for index, group in enumerate(unique)}
    residuals = np.empty(len(y))
    for fold in range(folds):
        test = np.asarray([assignment[group] == fold for group in groups])
        residuals[test] = y[test] - _predict(x[test], _fit(x[~test], y[~test]))
    return residuals, folds


def _build(source: Path, profile_path: Path, destination: Path) -> None:
    data = load_tabular_data(source, profile_path)
    profile = data.profile
    contract = load_task_contracts()[profile.task_id]
    rows = [row for row in data.observations if row["eligible"]]
    bundles = [build_tabular_features_from_observation(row, data.medians, profile) for row in rows]
    x = np.vstack([bundle.values for bundle in bundles])
    groups = [str(row["parent_key"]) for row in rows]
    definitions = feature_definitions(profile)
    feature_names = tuple(item.name for item in definitions)

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
        "id": f"{profile.task_id}-profile-transform",
        "version": "1.0.0",
        "canonical_input_paths": list(canonical_paths),
        "features": [
            {"name": item.name, "unit": item.unit, "meaning": item.meaning, "group": item.group}
            for item in definitions
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    files = [pipeline_path]
    predictors: list[dict[str, object]] = []
    metrics: list[TargetQualityMetric] = []
    records: dict[str, int] = {}
    fitted_by_target: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for output in profile.outputs:
        y = np.asarray([float(row["outputs"][output.key]) for row in rows])
        residuals, folds = _grouped_oof(x, y, groups)
        fitted = _fit(x, y)
        fitted_by_target[output.key] = fitted
        mean, scale, weights = fitted
        raw_weights = weights[1:] / scale
        raw_bias = float(weights[0] - np.sum(weights[1:] * mean / scale))
        lower, upper = np.quantile(residuals, (0.05, 0.95))
        artifact_path = artifact_dir / f"{output.key}.npz"
        np.savez(
            artifact_path,
            weights=raw_weights,
            bias=np.asarray(raw_bias),
            lower_offset=np.asarray(float(lower)),
            upper_offset=np.asarray(float(upper)),
        )
        files.append(artifact_path)
        records[output.key] = len(y)
        metrics.append(TargetQualityMetric(
            target=output.key,
            parent_conditions=len(set(groups)),
            mae=float(np.mean(np.abs(residuals))),
            rmse=float(np.sqrt(np.mean(residuals ** 2))),
            interval_coverage_90=float(np.mean((residuals >= lower) & (residuals <= upper))),
        ))
        predictors.append({
            "id": f"{output.key}-ridge",
            "target": output.key,
            "unit": output.unit,
            "target_kind": "continuous",
            "runtime_type": "builtin.linear.v1",
            "architecture_id": "profile_transformed_ridge_v1",
            "artifact": artifact_path.relative_to(destination).as_posix(),
            "predictive_family": "empirical_quantiles",
            "feature_names": list(feature_names),
            "config": {
                "training_unit": "source_row",
                "validation": (
                    f"{folds}-fold grouped by {profile.group_column}"
                    if profile.group_column
                    else f"{folds}-fold by independent source row"
                ),
                "source_profile": profile.profile_id,
            },
        })

    stats_path = reference_dir / "training_stats.json"
    stats_path.write_text(json.dumps({
        "records": records,
        "groups": len(set(groups)),
        "rows": len(rows),
        "source_sha256": data.source_sha256,
    }, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    files.append(stats_path)
    quality_path = report_dir / "quality-report.json"
    quality_path.write_text(QualityReport(
        schema_version="model-quality-report/v1",
        split=(
            "grouped-parent-condition-k-fold"
            if profile.group_column
            else "independent-source-row-k-fold"
        ),
        folds=min(5, len(set(groups))),
        targets=tuple(metrics),
    ).model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    files.append(quality_path)

    sample = candidate_from_observation(rows[len(rows) // 2], profile).model_copy(
        update={"name": f"{profile.name} package smoke"}
    )
    smoke_input = smoke_dir / "input.json"
    smoke_input.write_text(sample.model_dump_json(indent=2), encoding="utf-8", newline="\n")
    sample_x = build_tabular_features(sample, profile).values.reshape(1, -1)
    smoke_expected = smoke_dir / "expected.json"
    smoke_expected.write_text(json.dumps({
        target: round(float(_predict(sample_x, fitted)[0]), 8)
        for target, fitted in fitted_by_target.items()
    }, indent=2), encoding="utf-8", newline="\n")
    files.extend((smoke_input, smoke_expected))

    canonical = canonical_training_dataset(profile.task_id, data, contract)
    manifest = {
        "schema_version": "model-package/v1",
        "package_id": profile.package_id,
        "package_version": "1.0.0",
        "task_id": profile.task_id,
        "input_schema_version": "canonical-candidate/v1",
        "input_contract_digest": task_input_contract_digest(contract.task_definition),
        "runtime_capability_digest": runtime_capability_digest(contract.runtime_capability),
        "feature_pipeline": {
            "id": f"{profile.task_id}-profile-transform",
            "version": "1.0.0",
            "spec": pipeline_path.relative_to(destination).as_posix(),
            "canonical_input_paths": list(canonical_paths),
            "output_features": list(feature_names),
            "artifacts": [stats_path.relative_to(destination).as_posix()],
        },
        "predictors": predictors,
        "provenance": {
            "training_data_id": f"sha256:{data.source_sha256}",
            "feature_dataset_id": canonical_training_dataset_digest(canonical),
            "training_code_revision": "tabular-ridge-v1",
            "dataset_profile_id": dataset_profile_digest(profile),
        },
        "artifacts": [_artifact(destination, path) for path in files],
        "smoke_test": {
            "input": smoke_input.relative_to(destination).as_posix(),
            "expected": smoke_expected.relative_to(destination).as_posix(),
        },
        "quality_report": quality_path.relative_to(destination).as_posix(),
    }
    validate_task_definition_canonical_inputs(contract.task_definition, type(
        "_Manifest", (), {"task_id": profile.task_id, "feature_pipeline": type(
            "_Pipeline", (), {"canonical_input_paths": canonical_paths}
        )()}
    )())
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )


def build(
    source: Path,
    profile_path: Path,
    destination: Path,
    *,
    replace: bool = False,
) -> None:
    with staged_package_destination(destination, replace=replace) as staging:
        _build(source, profile_path, staging)
        verify_model_package(staging, task_id=load_tabular_profile(profile_path).task_id, source=source)


# Kept local to avoid exporting profile parsing through the package builder API.
from material_workbench.modeling.tabular_regression import load_tabular_profile  # noqa: E402
