"""Build the two experimental annealing packages that retain repeated observations."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from material_workbench.contracts.feature_contracts import feature_index_families
from material_workbench.contracts.schemas import CandidateInput
from material_workbench.data.importer import load_workbook_data, training_context_key
from material_workbench.modeling.feature_pipeline import (
    CANONICAL_INPUT_PATHS,
    FEATURE_DEFINITIONS,
    FEATURE_NAMES,
    FEATURE_PIPELINE_ID,
    FEATURE_PIPELINE_VERSION,
)
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
from material_workbench.modeling.runtime import INPUT_SCHEMA_VERSION, TARGETS, TASK_ID, ModelRuntime
from material_workbench.tasks.task_registry import load_task_contracts


SEED = 20260723
HETERO_PACKAGE_ID = "annealed-heteroscedastic-gp-process-v2"
HIERARCHICAL_PACKAGE_ID = "annealed-hierarchical-bayes-process-v2"
FEATURE_GROUP_INDICES = feature_index_families(
    FEATURE_DEFINITIONS,
    {
        "composition": ("composition",),
        "metallurgy": ("metallurgy",),
        "heat_pattern": ("heat_pattern",),
    },
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact(root: Path, path: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _digest(path),
        "bytes": path.stat().st_size,
    }


def _lengthscale(width: int, multiplier: float) -> np.ndarray:
    result = np.ones(width, dtype=np.float64)
    for columns in FEATURE_GROUP_INDICES.values():
        result[list(columns)] = multiplier * np.sqrt(len(columns))
    return result


def _groups(model: Any, target: str) -> dict[str, Any]:
    column = TARGETS[target][0]
    indexes_by_parent: dict[str, list[int]] = {}
    for index, row in enumerate(model.rows):
        indexes_by_parent.setdefault(training_context_key(row), []).append(index)
    parent_keys = sorted(indexes_by_parent)
    x_rows: list[np.ndarray] = []
    means: list[float] = []
    counts: list[int] = []
    sses: list[float] = []
    observation_ids: list[list[str]] = []
    for parent_key in parent_keys:
        indexes = indexes_by_parent[parent_key]
        values = np.asarray(
            [float(model.rows[index]["outputs"][column]) for index in indexes],
            dtype=np.float64,
        )
        x_rows.append(np.asarray(model.x_train[indexes], dtype=np.float64).mean(axis=0))
        means.append(float(values.mean()))
        counts.append(len(values))
        sses.append(float(np.sum((values - values.mean()) ** 2)))
        observation_ids.append([str(model.rows[index]["id"]) for index in indexes])
    return {
        "parent_keys": parent_keys,
        "x": np.vstack(x_rows),
        "mean": np.asarray(means),
        "count": np.asarray(counts, dtype=np.int64),
        "sse": np.asarray(sses),
        "observation_ids": observation_ids,
    }


def _fit_heteroscedastic(model: Any, target: str) -> tuple[dict[str, np.ndarray], TargetQualityMetric, dict[str, Any]]:
    grouped = _groups(model, target)
    x = grouped["x"]
    y = grouped["mean"]
    count = grouped["count"].astype(np.float64)
    sse = grouped["sse"]
    df = np.maximum(count - 1.0, 0.0)
    total_variance = max(float(np.var(y)), 1e-6)
    pooled = float(sse.sum() / df.sum()) if df.sum() else total_variance * 0.1
    pooled = max(pooled, total_variance * 1e-4, 1e-8)

    # Four prior degrees of freedom prevent n=2/3 conditions from declaring
    # their noisy sample variance to be a precisely known variance function.
    prior_df = 4.0
    shrunk_variance = (sse + prior_df * pooled) / (df + prior_df)
    noise_floor = max(float(np.quantile(shrunk_variance, 0.02)) * 0.25, 1e-8)
    noise_ceiling = max(float(np.quantile(shrunk_variance, 0.98)) * 4.0, noise_floor * 10)

    noise_lengthscale = _lengthscale(x.shape[1], 3.5)
    noise_scaled = (x[:, None, :] - x[None, :, :]) / noise_lengthscale
    noise_kernel = np.exp(-0.5 * np.sum(noise_scaled * noise_scaled, axis=2))
    log_noise = np.log(np.clip(shrunk_variance, noise_floor, noise_ceiling))
    noise_mean = float(np.average(log_noise, weights=np.maximum(df, 1.0)))
    noise_weights = np.maximum(df, 1.0)
    noise_regularizer = np.diag(1.0 / noise_weights) + np.eye(len(x)) * 0.35
    noise_alpha = np.linalg.solve(noise_kernel + noise_regularizer, log_noise - noise_mean)
    fitted_noise = np.clip(np.exp(noise_mean + noise_kernel @ noise_alpha), noise_floor, noise_ceiling)

    mean_lengthscale = _lengthscale(x.shape[1], 1.5)
    mean_scaled = (x[:, None, :] - x[None, :, :]) / mean_lengthscale
    mean_kernel_base = np.exp(-0.5 * np.sum(mean_scaled * mean_scaled, axis=2))
    outputscale = max(total_variance, 1e-6)
    covariance = outputscale * mean_kernel_base
    covariance.flat[:: len(x) + 1] += fitted_noise / count + total_variance * 1e-7
    cholesky = np.linalg.cholesky(covariance)
    precision = np.linalg.solve(cholesky.T, np.linalg.solve(cholesky, np.eye(len(x))))
    mean_value = float(y.mean())
    alpha = precision @ (y - mean_value)

    diagonal = np.diag(precision)
    loo_residual = alpha / diagonal
    loo_latent_variance = np.maximum(1.0 / diagonal - fitted_noise / count, 0.0)
    loo_total_variance = loo_latent_variance + fitted_noise
    z90 = 1.6448536269514722
    quality = TargetQualityMetric(
        target=target,
        parent_conditions=len(x),
        mae=float(np.mean(np.abs(loo_residual))),
        rmse=float(np.sqrt(np.mean(loo_residual**2))),
        interval_coverage_90=float(np.mean(np.abs(loo_residual) <= z90 * np.sqrt(loo_total_variance))),
    )
    arrays = {
        "train_x": x,
        "feature_mean": model.feature_mean,
        "feature_scale": model.feature_scale,
        "mean_lengthscale": mean_lengthscale,
        "mean_outputscale": np.asarray(outputscale),
        "mean_value": np.asarray(mean_value),
        "mean_precision": precision,
        "mean_alpha": alpha,
        "noise_lengthscale": noise_lengthscale,
        "noise_mean": np.asarray(noise_mean),
        "noise_alpha": noise_alpha,
        "noise_floor": np.asarray(noise_floor),
        "noise_ceiling": np.asarray(noise_ceiling),
    }
    reference = {
        "parent_keys": grouped["parent_keys"],
        "repeat_counts": grouped["count"].tolist(),
        "within_sse": grouped["sse"].tolist(),
        "observation_ids": grouped["observation_ids"],
        "pooled_within_parent_variance": pooled,
        "variance_prior_df": prior_df,
    }
    return arrays, quality, reference


def _inverse_gamma(rng: np.random.Generator, shape: float, scale: float) -> float:
    return float(1.0 / rng.gamma(shape, 1.0 / scale))


def _grouped_linear_quality(
    target: str,
    raw_x: np.ndarray,
    y: np.ndarray,
    group: np.ndarray,
    parent_count: int,
) -> TargetQualityMetric:
    predictions = np.zeros_like(y)
    predictive_variance = np.zeros_like(y)
    for fold in range(5):
        test = np.isin(group, np.arange(parent_count)[np.arange(parent_count) % 5 == fold])
        train = ~test
        mean = raw_x[train].mean(axis=0)
        scale = raw_x[train].std(axis=0)
        scale[scale < 1e-8] = 1.0
        train_design = np.column_stack([np.ones(train.sum()), (raw_x[train] - mean) / scale])
        test_design = np.column_stack([np.ones(test.sum()), (raw_x[test] - mean) / scale])
        penalty = np.diag(np.r_[0.01, np.full(raw_x.shape[1], 2.0)])
        coefficients = np.linalg.solve(train_design.T @ train_design + penalty, train_design.T @ y[train])
        predictions[test] = test_design @ coefficients
        residual = y[train] - train_design @ coefficients
        within_sse = 0.0
        within_df = 0
        group_means: list[float] = []
        for parent in np.unique(group[train]):
            values = residual[group[train] == parent]
            group_means.append(float(values.mean()))
            if len(values) > 1:
                within_sse += float(np.sum((values - values.mean()) ** 2))
                within_df += len(values) - 1
        within = within_sse / within_df if within_df else max(float(np.var(residual)) * 0.5, 1e-8)
        between = max(float(np.var(group_means)) - within / 2.0, 0.0)
        predictive_variance[test] = within + between
    residual = y - predictions
    z90 = 1.6448536269514722
    return TargetQualityMetric(
        target=target,
        parent_conditions=parent_count,
        mae=float(np.mean(np.abs(residual))),
        rmse=float(np.sqrt(np.mean(residual**2))),
        interval_coverage_90=float(
            np.mean(np.abs(residual) <= z90 * np.sqrt(np.maximum(predictive_variance, 1e-8)))
        ),
    )


def _fit_hierarchical(
    model: Any,
    target: str,
    *,
    draws: int = 384,
    burn: int = 256,
) -> tuple[dict[str, np.ndarray], TargetQualityMetric, dict[str, Any]]:
    column = TARGETS[target][0]
    x = np.asarray(model.x_train, dtype=np.float64)
    y = np.asarray([float(row["outputs"][column]) for row in model.rows], dtype=np.float64)
    parent_keys = sorted({training_context_key(row) for row in model.rows})
    parent_index = {key: index for index, key in enumerate(parent_keys)}
    group = np.asarray([parent_index[training_context_key(row)] for row in model.rows], dtype=np.int64)
    y_mean = float(y.mean())
    y_scale = max(float(y.std()), 1e-6)
    z = (y - y_mean) / y_scale
    design = np.column_stack([np.ones(len(x)), x])
    prior_precision = np.diag(np.r_[0.05, np.full(x.shape[1], 1.0)])
    rng = np.random.default_rng(SEED + sum(ord(char) for char in target))
    theta = np.zeros(design.shape[1])
    parent_effect = np.zeros(len(parent_keys))
    within_variance = 0.25
    between_variance = 0.25
    beta_draws: list[np.ndarray] = []
    intercept_draws: list[float] = []
    within_draws: list[float] = []
    between_draws: list[float] = []

    group_rows = [np.flatnonzero(group == index) for index in range(len(parent_keys))]
    for iteration in range(burn + draws):
        posterior_precision = design.T @ design / within_variance + prior_precision
        posterior_covariance = np.linalg.inv(posterior_precision)
        posterior_mean = posterior_covariance @ (
            design.T @ (z - parent_effect[group]) / within_variance
        )
        theta = rng.multivariate_normal(posterior_mean, posterior_covariance)
        residual_without_parent = z - design @ theta
        for index, rows in enumerate(group_rows):
            variance = 1.0 / (len(rows) / within_variance + 1.0 / between_variance)
            mean = variance * residual_without_parent[rows].sum() / within_variance
            parent_effect[index] = rng.normal(mean, np.sqrt(variance))
        residual = z - design @ theta - parent_effect[group]
        within_variance = _inverse_gamma(
            rng, 2.0 + len(z) / 2.0, 0.2 + float(residual @ residual) / 2.0
        )
        between_variance = _inverse_gamma(
            rng,
            2.0 + len(parent_effect) / 2.0,
            0.2 + float(parent_effect @ parent_effect) / 2.0,
        )
        if iteration >= burn:
            beta_raw = y_scale * theta[1:] / model.feature_scale
            intercept_raw = y_mean + y_scale * theta[0] - float(model.feature_mean @ beta_raw)
            beta_draws.append(beta_raw)
            intercept_draws.append(intercept_raw)
            within_draws.append(y_scale * np.sqrt(within_variance))
            between_draws.append(y_scale * np.sqrt(between_variance))

    beta_array = np.vstack(beta_draws)
    intercept_array = np.asarray(intercept_draws)
    raw_x = x * model.feature_scale + model.feature_mean
    quality = _grouped_linear_quality(
        target,
        raw_x,
        y,
        group,
        len(parent_keys),
    )
    arrays = {
        "beta_draws": beta_array,
        "intercept_draws": intercept_array,
        "noise_scale_draws": np.asarray(within_draws),
        "parent_scale_draws": np.asarray(between_draws),
    }
    reference = {
        "parent_keys": parent_keys,
        "repeat_counts": [len(rows) for rows in group_rows],
        "observation_ids": [[str(model.rows[index]["id"]) for index in rows] for rows in group_rows],
        "posterior_draws": draws,
        "burn_in": burn,
        "sampler": "conjugate Gibbs with normal shrinkage prior",
    }
    return arrays, quality, reference


def _pipeline_document() -> dict[str, Any]:
    return {
        "id": FEATURE_PIPELINE_ID,
        "version": FEATURE_PIPELINE_VERSION,
        "canonical_input_paths": list(CANONICAL_INPUT_PATHS),
        "features": [
            {
                "name": item.name,
                "unit": item.unit,
                "meaning": item.meaning,
                "group": item.group,
            }
            for item in FEATURE_DEFINITIONS
        ],
        "missing_composition": "training_median_from_source_workbook",
        "heat_interpolation": "piecewise_linear",
    }


def _build(source: Path, destination: Path, family: str, package_id: str) -> None:
    data = load_workbook_data(source)
    runtime = ModelRuntime(data, load_package=False)
    folders = {
        name: destination / name
        for name in ("model-artifacts", "feature-pipeline", "reference", "smoke", "reports")
    }
    for folder in folders.values():
        folder.mkdir(parents=True, exist_ok=True)
    pipeline_path = folders["feature-pipeline"] / "pipeline.json"
    pipeline_path.write_text(
        json.dumps(_pipeline_document(), ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    files = [pipeline_path]
    predictors: list[dict[str, Any]] = []
    quality_metrics = []
    group_references: dict[str, Any] = {}
    smoke_points: dict[str, float] = {}
    for target, model in sorted(runtime.models.items()):
        if family == "heteroscedastic-gp":
            arrays, quality, reference = _fit_heteroscedastic(model, target)
            runtime_type = "builtin.heteroscedastic_exact_gp.v1"
            architecture_id = "heteroscedastic_rbf_individual_v1"
            config = {
                "training_unit": "individual_observation",
                "grouping": "parent_condition_sufficient_statistics",
                "variance_model": "shrunk_within_parent_log_variance_gp",
                "experimental": True,
            }
            smoke_points[target] = float(arrays["mean_value"])
        else:
            arrays, quality, reference = _fit_hierarchical(model, target)
            runtime_type = "builtin.posterior_linear.v1"
            architecture_id = "hierarchical_parent_random_intercept_v1"
            config = {
                "training_unit": "individual_observation",
                "grouping": "parent_random_intercept",
                "method": "hierarchical_gibbs",
                "output_representation": "moment_matched_normal",
                "experimental": True,
            }
            smoke_points[target] = float(arrays["intercept_draws"].mean())
        artifact_path = folders["model-artifacts"] / f"{target}.npz"
        np.savez(artifact_path, **arrays)
        files.append(artifact_path)
        quality_metrics.append(quality)
        group_references[target] = reference
        predictors.append({
            "id": f"{target.lower()}-{family}",
            "target": target,
            "unit": model.unit,
            "target_kind": "continuous_positive" if target == "lambda" else "continuous",
            "runtime_type": runtime_type,
            "architecture_id": architecture_id,
            "artifact": artifact_path.relative_to(destination).as_posix(),
            "predictive_family": "normal",
            "feature_names": list(FEATURE_NAMES),
            "config": config,
        })

    groups_path = folders["reference"] / "training-groups.json"
    groups_path.write_text(
        json.dumps(group_references, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    files.append(groups_path)
    stats_path = folders["reference"] / "training_stats.json"
    stats_path.write_text(
        json.dumps({
            "source_sha256": data.source_sha256,
            "composition_defaults": data.medians,
            "training_unit": "individual_observation",
            "evaluation_group": "parent_key",
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    files.append(stats_path)
    quality_path = folders["reports"] / "quality-report.json"
    quality_path.write_text(
        QualityReport(
            schema_version="model-quality-report/v1",
            split=(
                "leave-one-parent-condition-out"
                if family == "heteroscedastic-gp"
                else "grouped-parent-condition-k-fold"
            ),
            folds=None if family == "heteroscedastic-gp" else 5,
            targets=tuple(quality_metrics),
        ).model_dump_json(indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    files.append(quality_path)
    diagnostics_path = folders["reports"] / "training-diagnostics.json"
    diagnostics_path.write_text(
        json.dumps({
            "schema_version": "experimental-training-diagnostics/v1",
            "family": family,
            "note": "Synthetic demo data; use this package to inspect uncertainty handling, not model superiority.",
            "targets": {
                target: {
                    "individual_observations": len(model.rows),
                    "parent_conditions": len(group_references[target]["parent_keys"]),
                }
                for target, model in sorted(runtime.models.items())
            },
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    files.append(diagnostics_path)

    smoke_input = {
        "name": "package smoke",
        "inputs": {
            "composition": data.medians,
            "process": {"ls_mpm": 103.0},
            "heat_pattern": [
                {"time_s": 0, "temperature_c": 25},
                {"time_s": 300, "temperature_c": 800},
                {"time_s": 360, "temperature_c": 810},
                {"time_s": 650, "temperature_c": 120},
            ],
        },
    }
    smoke_input_path = folders["smoke"] / "input.json"
    smoke_input_path.write_text(
        json.dumps(smoke_input, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    smoke_candidate = CandidateInput.model_validate(smoke_input)
    smoke_features = runtime.vector_for_candidate(smoke_candidate)
    expected: dict[str, float] = {}
    for target in runtime.models:
        path = folders["model-artifacts"] / f"{target}.npz"
        with np.load(path, allow_pickle=False) as arrays:
            if family == "heteroscedastic-gp":
                point = (smoke_features - arrays["feature_mean"]) / arrays["feature_scale"]
                scaled = (arrays["train_x"] - point) / arrays["mean_lengthscale"]
                cross = float(arrays["mean_outputscale"]) * np.exp(
                    -0.5 * np.sum(scaled * scaled, axis=1)
                )
                value = float(arrays["mean_value"]) + float(cross @ arrays["mean_alpha"])
            else:
                value = float(arrays["beta_draws"].mean(axis=0) @ smoke_features) + float(
                    arrays["intercept_draws"].mean()
                )
        expected[target] = round(value, 8)
    smoke_expected_path = folders["smoke"] / "expected.json"
    smoke_expected_path.write_text(
        json.dumps(expected, indent=2), encoding="utf-8", newline="\n"
    )
    files.extend([smoke_input_path, smoke_expected_path])

    contract = load_task_contracts()[TASK_ID]
    canonical = canonical_training_dataset(TASK_ID, data, contract)
    manifest = {
        "schema_version": "model-package/v1",
        "package_id": package_id,
        "package_version": "1.1.0-experimental",
        "task_id": TASK_ID,
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "input_contract_digest": task_input_contract_digest(contract.task_definition),
        "runtime_capability_digest": runtime_capability_digest(contract.runtime_capability),
        "feature_pipeline": {
            "id": FEATURE_PIPELINE_ID,
            "version": FEATURE_PIPELINE_VERSION,
            "spec": pipeline_path.relative_to(destination).as_posix(),
            "canonical_input_paths": list(CANONICAL_INPUT_PATHS),
            "output_features": list(FEATURE_NAMES),
            "artifacts": [
                stats_path.relative_to(destination).as_posix(),
                groups_path.relative_to(destination).as_posix(),
            ],
        },
        "predictors": predictors,
        "provenance": {
            "training_data_id": f"sha256:{data.source_sha256}",
            "feature_dataset_id": canonical_training_dataset_digest(canonical),
            "training_code_revision": "build_annealed_individual_model_packages.py:v1",
            "dataset_profile_id": dataset_profile_digest(Path(data.profile_path)),
        },
        "artifacts": [_artifact(destination, path) for path in files],
        "smoke_test": {
            "input": smoke_input_path.relative_to(destination).as_posix(),
            "expected": smoke_expected_path.relative_to(destination).as_posix(),
        },
        "quality_report": quality_path.relative_to(destination).as_posix(),
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )


def build(
    source: Path,
    destination: Path,
    *,
    family: str,
    package_id: str,
    replace: bool = False,
) -> None:
    with staged_package_destination(destination, replace=replace) as staging:
        _build(source, staging, family, package_id)
        verify_model_package(staging, task_id=TASK_ID, source=source)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--family", choices=("heteroscedastic-gp", "hierarchical-bayes"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--package-id")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    default_id = HETERO_PACKAGE_ID if args.family == "heteroscedastic-gp" else HIERARCHICAL_PACKAGE_ID
    build(
        args.source,
        args.output,
        family=args.family,
        package_id=args.package_id or default_id,
        replace=args.replace,
    )
