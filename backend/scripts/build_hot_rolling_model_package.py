"""Train and package the demo hot-rolling regularized Horseshoe model."""
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

from material_workbench.modeling.hot_rolling_feature_pipeline import CANONICAL_INPUT_PATHS, FEATURE_DEFINITIONS, FEATURE_NAMES, INPUT_SCHEMA_VERSION, PIPELINE_ID, PIPELINE_VERSION, build_hot_rolling_features, build_hot_rolling_features_from_observation, candidate_from_observation
from material_workbench.data.importer import load_workbook_data
from material_workbench.contracts.model_example_contracts import SparseSelectionReport
from material_workbench.modeling.model_lifecycle import QualityReport, SamplingDiagnosticsReport, TargetQualityMetric, canonical_training_dataset, canonical_training_dataset_digest, dataset_profile_digest, runtime_capability_digest, staged_package_destination, task_input_contract_digest
from material_workbench.modeling.model_package_verify import verify_model_package
from material_workbench.tasks.task_registry import load_task_contracts


PACKAGE_ID = "hot-rolled-tutorial-v1"
PACKAGE_VERSION = "1.1.0-feature-design-v3"
TRAINING_CODE_REVISION = "1.1.0-feature-design-v3"
TASK_ID = "hot-rolled-properties-v1"
SEED = 20260722
POSTERIOR_DRAWS = 2048
CV_FOLDS = 5


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact(root: Path, path: Path) -> dict[str, object]:
    return {"path": path.relative_to(root).as_posix(), "sha256": _digest(path), "bytes": path.stat().st_size}


def _standardize(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float, float]:
    feature_mean, feature_scale = x.mean(axis=0), x.std(axis=0)
    feature_scale[feature_scale < 1e-9] = 1.0
    target_mean, target_scale = float(y.mean()), max(float(y.std()), 1e-9)
    return (x - feature_mean) / feature_scale, (y - target_mean) / target_scale, feature_mean, feature_scale, target_mean, target_scale


def _train_tiny_demo_posterior(
    x: np.ndarray,
    y: np.ndarray,
    *,
    seed: int,
    draws: int = 512,
) -> tuple[dict[str, np.ndarray], dict[str, float | int]]:
    """Stable approximate posterior for a deliberately tiny teaching dataset."""
    design = np.column_stack([np.ones(len(x)), x])
    penalty = np.eye(design.shape[1]) * 1.0
    penalty[0, 0] = 0.01
    precision = design.T @ design + penalty
    covariance = np.linalg.inv(precision)
    coefficients = covariance @ design.T @ y
    residual = y - design @ coefficients
    noise = max(float(np.sqrt(np.mean(residual**2))), 0.15)
    rng = np.random.default_rng(seed)
    sampled = rng.multivariate_normal(coefficients, covariance * noise**2, size=draws)
    return {
        "beta_draws": sampled[:, 1:],
        "intercept_draws": sampled[:, 0],
        "noise_scale_draws": np.full(draws, noise),
        "local_scale_draws": np.maximum(np.abs(sampled[:, 1:]), 0.05),
    }, {
        "chains": 1,
        "draws_per_chain": draws,
        "warmup_per_chain": 0,
        "divergences": 0,
        "minimum_effective_sample_size": float(draws),
        "maximum_r_hat": 1.0,
    }


def _tiny_demo_loo_quality(raw_x: np.ndarray, y: np.ndarray) -> TargetQualityMetric:
    predicted: list[float] = []
    uncertainty: list[float] = []
    for index in range(len(y)):
        mask = np.arange(len(y)) != index
        x_train, y_train, x_mean, x_scale, y_mean, y_scale = _standardize(raw_x[mask], y[mask])
        arrays, _ = _train_tiny_demo_posterior(x_train, y_train, seed=SEED + index + 1, draws=256)
        point = (raw_x[index] - x_mean) / x_scale
        latent = arrays["intercept_draws"] + arrays["beta_draws"] @ point
        predicted.append(float(y_mean + y_scale * latent.mean()))
        uncertainty.append(float(y_scale * np.sqrt(latent.var() + np.mean(arrays["noise_scale_draws"] ** 2))))
    errors = y - np.asarray(predicted)
    spread = np.asarray(uncertainty)
    return TargetQualityMetric(
        target="TS",
        parent_conditions=len(y),
        mae=float(np.mean(np.abs(errors))),
        rmse=float(np.sqrt(np.mean(errors**2))),
        interval_coverage_90=float(np.mean(np.abs(errors) <= 1.6448536269514722 * spread)),
    )


def _train_numpyro(
    x: np.ndarray,
    y: np.ndarray,
    *,
    seed: int,
    warmup: int,
    draws: int,
    chains: int = 1,
) -> tuple[dict[str, np.ndarray], dict[str, float | int]]:
    try:
        import jax.numpy as jnp
        from jax import random
        import numpyro
        import numpyro.distributions as dist
        from numpyro.diagnostics import summary
        from numpyro.infer import MCMC, NUTS
    except ImportError as exc:
        raise RuntimeError("training requires the runtime-numpyro optional dependency") from exc

    expected_signals = min(6, x.shape[1] - 1)
    global_prior_scale = expected_signals / (x.shape[1] - expected_signals) / np.sqrt(len(x))

    def model(features: Any, observed: Any | None = None) -> None:
        global_scale = numpyro.sample("global_scale", dist.HalfCauchy(global_prior_scale))
        local_scale = numpyro.sample("local_scale", dist.HalfCauchy(jnp.ones(x.shape[1])))
        slab_aux = numpyro.sample("slab_aux", dist.InverseGamma(2.0, 2.0))
        slab_variance = 4.0 * slab_aux
        local_regularized = jnp.sqrt(
            slab_variance * local_scale**2 / (slab_variance + global_scale**2 * local_scale**2)
        )
        beta_raw = numpyro.sample("beta_raw", dist.Normal(jnp.zeros(x.shape[1]), jnp.ones(x.shape[1])))
        beta = numpyro.deterministic("beta", beta_raw * global_scale * local_regularized)
        intercept = numpyro.sample("intercept", dist.Normal(0, 2))
        noise_scale = numpyro.sample("noise_scale", dist.HalfNormal(1))
        numpyro.sample("observed", dist.Normal(intercept + jnp.asarray(features) @ beta, noise_scale), obs=observed)

    sampler = MCMC(
        NUTS(model, target_accept_prob=0.99, max_tree_depth=13),
        num_warmup=warmup,
        num_samples=draws,
        num_chains=chains,
        chain_method="sequential",
        progress_bar=False,
    )
    sampler.run(random.PRNGKey(seed), jnp.asarray(x), jnp.asarray(y))
    grouped = sampler.get_samples(group_by_chain=True)
    diagnostics = summary(grouped, group_by_chain=True)
    finite_ess = [float(value) for item in diagnostics.values() for value in np.asarray(item["n_eff"]).reshape(-1) if np.isfinite(value)]
    finite_rhat = [float(value) for item in diagnostics.values() for value in np.asarray(item["r_hat"]).reshape(-1) if np.isfinite(value)]
    flat = sampler.get_samples()
    arrays = {
        "beta_draws": np.asarray(flat["beta"], dtype=float),
        "intercept_draws": np.asarray(flat["intercept"], dtype=float),
        "noise_scale_draws": np.asarray(flat["noise_scale"], dtype=float),
        "local_scale_draws": np.asarray(flat["local_scale"], dtype=float),
    }
    extra = sampler.get_extra_fields()
    return arrays, {
        "chains": chains,
        "draws_per_chain": draws,
        "warmup_per_chain": warmup,
        "divergences": int(np.asarray(extra.get("diverging", [])).sum()),
        "minimum_effective_sample_size": min(finite_ess) if finite_ess else 0.0,
        "maximum_r_hat": max(finite_rhat) if finite_rhat else 0.0,
    }


def _raw_coordinate_draws(
    arrays: dict[str, np.ndarray],
    feature_mean: np.ndarray,
    feature_scale: np.ndarray,
    target_mean: float,
    target_scale: float,
) -> dict[str, np.ndarray]:
    beta = target_scale * arrays["beta_draws"] / feature_scale
    intercept = target_mean + target_scale * arrays["intercept_draws"] - beta @ feature_mean
    return {
        "beta_draws": beta,
        "intercept_draws": intercept,
        "noise_scale_draws": target_scale * arrays["noise_scale_draws"],
        "local_scale_draws": arrays["local_scale_draws"],
    }


def _grouped_cv_quality(raw_x: np.ndarray, y: np.ndarray) -> TargetQualityMetric:
    rng = np.random.default_rng(SEED)
    folds = np.array_split(rng.permutation(len(y)), CV_FOLDS)
    predicted: list[float] = []
    lower: list[float] = []
    upper: list[float] = []
    actual: list[float] = []
    for fold, test_indices in enumerate(folds):
        train_mask = np.ones(len(y), dtype=bool)
        train_mask[test_indices] = False
        x_train, y_train, x_mean, x_scale, y_mean, y_scale = _standardize(raw_x[train_mask], y[train_mask])
        arrays, _ = _train_numpyro(x_train, y_train, seed=SEED + fold + 1, warmup=64, draws=64)
        latent = ((raw_x[test_indices] - x_mean) / x_scale) @ arrays["beta_draws"].T + arrays["intercept_draws"]
        mean = y_mean + y_scale * latent.mean(axis=1)
        std = y_scale * np.sqrt(latent.var(axis=1) + np.mean(arrays["noise_scale_draws"] ** 2))
        predicted.extend(mean.tolist())
        lower.extend((mean - 1.6448536269514722 * std).tolist())
        upper.extend((mean + 1.6448536269514722 * std).tolist())
        actual.extend(y[test_indices].tolist())
    errors = np.asarray(actual) - np.asarray(predicted)
    covered = (np.asarray(lower) <= actual) & (np.asarray(actual) <= upper)
    return TargetQualityMetric(
        target="TS",
        parent_conditions=len(y),
        mae=float(np.mean(np.abs(errors))),
        rmse=float(np.sqrt(np.mean(errors**2))),
        interval_coverage_90=float(np.mean(covered)),
    )


def _selection_report(arrays: dict[str, np.ndarray]) -> SparseSelectionReport:
    beta, local = arrays["beta_draws"], arrays["local_scale_draws"]
    return SparseSelectionReport.model_validate({
        "schema_version": "sparse-selection-report/v1",
        "method": "horseshoe",
        "features": [{
            "feature": name,
            "coefficient_mean": float(np.mean(beta[:, index])),
            "coefficient_sd": float(np.std(beta[:, index])),
            "q05": float(np.quantile(beta[:, index], 0.05)),
            "q95": float(np.quantile(beta[:, index], 0.95)),
            "sign_probability": float(max(np.mean(beta[:, index] >= 0), np.mean(beta[:, index] <= 0))),
            "rope_outside_probability": float(np.mean(np.abs(beta[:, index]) > 0.05)),
            "local_scale_mean": float(np.mean(local[:, index])),
        } for index, name in enumerate(FEATURE_NAMES)],
        "selected_subset_rule": "Standardized |coefficient| > 0.05 posterior probability; report only, no feature deletion.",
        "interpretation_warning": "Shrinkage and sign probabilities are not causal importance; correlated features may share evidence.",
    })


def _build(source: Path, destination: Path) -> None:
    data = load_workbook_data(source)
    contract = load_task_contracts()[TASK_ID]
    output = contract.task_definition.outputs[0]
    column = f"{output.key}[{output.unit}]"
    rows = [row for row in data.observations if row["task_id"] == TASK_ID and row["eligible"] and row["features"] and row["composition"] and column in row["outputs"]]
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row["parent_key"]), []).append(row)
    raw_x: list[np.ndarray] = []
    target_y: list[float] = []
    for group_rows in grouped.values():
        bundle = build_hot_rolling_features_from_observation(group_rows[0], data.medians)
        if bundle is None:
            raise ValueError("eligible hot-rolling observation did not convert to a candidate")
        raw_x.append(bundle.values)
        target_y.append(float(np.mean([float(row["outputs"][column]) for row in group_rows])))
    raw, y = np.vstack(raw_x), np.asarray(target_y)
    x, y_scaled, feature_mean, feature_scale, target_mean, target_scale = _standardize(raw, y)
    tiny_demo = len(y) < 12
    if tiny_demo:
        standardized_draws, diagnostics = _train_tiny_demo_posterior(x, y_scaled, seed=SEED)
    else:
        standardized_draws, diagnostics = _train_numpyro(x, y_scaled, seed=SEED, warmup=1536, draws=POSTERIOR_DRAWS // 2, chains=2)
    exported_draws = _raw_coordinate_draws(standardized_draws, feature_mean, feature_scale, target_mean, target_scale)
    if not all(np.isfinite(value).all() for value in exported_draws.values()):
        raise ValueError("Horseshoe export contains non-finite posterior draws")

    artifact_dir, feature_dir, reference_dir, smoke_dir, report_dir = (destination / name for name in ("model-artifacts", "feature-pipeline", "reference", "smoke", "reports"))
    for folder in (artifact_dir, feature_dir, reference_dir, smoke_dir, report_dir):
        folder.mkdir(parents=True, exist_ok=True)
    pipeline_path = feature_dir / "pipeline.json"
    _write_json(pipeline_path, {"id": PIPELINE_ID, "version": PIPELINE_VERSION, "canonical_input_paths": list(CANONICAL_INPUT_PATHS), "features": [{"name": item.name, "unit": item.unit, "meaning": item.meaning, "group": item.group} for item in FEATURE_DEFINITIONS]})
    model_path = artifact_dir / "TS.npz"
    np.savez(model_path, **exported_draws)
    stats_path = reference_dir / "training_stats.json"
    _write_json(stats_path, {"records": {"TS": len(y)}, "source_sha256": data.source_sha256, "composition_defaults": data.medians})
    quality_path = report_dir / "quality-report.json"
    quality = QualityReport(
        schema_version="model-quality-report/v1",
        split="leave-one-parent-condition-out" if tiny_demo else "grouped-parent-condition-k-fold",
        **({} if tiny_demo else {"folds": CV_FOLDS}),
        targets=(_tiny_demo_loo_quality(raw, y) if tiny_demo else _grouped_cv_quality(raw, y),),
    )
    _write_json(quality_path, quality.model_dump(mode="json"))

    sample_candidate = candidate_from_observation(rows[0])
    if sample_candidate is None:
        raise ValueError("smoke observation did not convert to a hot-rolling candidate")
    sample = sample_candidate.model_copy(update={"name": "hot rolling Horseshoe package smoke"})
    smoke_input = smoke_dir / "input.json"
    _write_json(smoke_input, sample.model_dump(mode="json"))
    smoke_raw = build_hot_rolling_features(sample, data.medians).values
    point = float(exported_draws["beta_draws"].mean(axis=0) @ smoke_raw + exported_draws["intercept_draws"].mean())
    smoke_expected = smoke_dir / "expected.json"
    _write_json(smoke_expected, {"TS": round(point, 8)})

    files = [pipeline_path, model_path, stats_path, quality_path, smoke_input, smoke_expected]
    if not tiny_demo:
        diagnostics_path = report_dir / "training-diagnostics.json"
        sampling_diagnostics = SamplingDiagnosticsReport.model_validate({"schema_version": "sampling-diagnostics/v1", **diagnostics, "finite_export": True})
        _write_json(diagnostics_path, sampling_diagnostics.model_dump(mode="json"))
        selection_path = report_dir / "selection-report.json"
        _write_json(selection_path, _selection_report(standardized_draws).model_dump(mode="json"))
        files.extend([diagnostics_path, selection_path])
    canonical_dataset = canonical_training_dataset(TASK_ID, data, contract)
    manifest = {
        "schema_version": "model-package/v1",
        "package_id": PACKAGE_ID,
        "package_version": PACKAGE_VERSION,
        "task_id": TASK_ID,
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "input_contract_digest": task_input_contract_digest(contract.task_definition),
        "runtime_capability_digest": runtime_capability_digest(contract.runtime_capability),
        "feature_pipeline": {"id": PIPELINE_ID, "version": PIPELINE_VERSION, "spec": pipeline_path.relative_to(destination).as_posix(), "canonical_input_paths": list(CANONICAL_INPUT_PATHS), "output_features": list(FEATURE_NAMES), "artifacts": [stats_path.relative_to(destination).as_posix()]},
        "predictors": [{"id": "ts-horseshoe", "target": "TS", "unit": output.unit, "target_kind": "continuous", "runtime_type": "builtin.posterior_linear.v1", "architecture_id": "posterior_linear_v1", "artifact": model_path.relative_to(destination).as_posix(), "predictive_family": "normal", "feature_names": list(FEATURE_NAMES), "config": {"training_unit": "parent_condition_mean", "method": "tiny_demo_ridge_posterior" if tiny_demo else "regularized_horseshoe", "output_representation": "moment_matched_normal"}}],
        "provenance": {"training_data_id": f"sha256:{data.source_sha256}", "feature_dataset_id": canonical_training_dataset_digest(canonical_dataset), "training_code_revision": TRAINING_CODE_REVISION, "dataset_profile_id": dataset_profile_digest(Path(data.profile_path))},
        "artifacts": [_artifact(destination, path) for path in files],
        "smoke_test": {"input": smoke_input.relative_to(destination).as_posix(), "expected": smoke_expected.relative_to(destination).as_posix()},
        "quality_report": quality_path.relative_to(destination).as_posix(),
    }
    _write_json(destination / "manifest.json", manifest)


def build(source: Path, destination: Path, *, replace: bool = False, package_id: str = PACKAGE_ID) -> None:
    with staged_package_destination(destination, replace=replace) as staging:
        _build(source, staging)
        if package_id != PACKAGE_ID:
            manifest_path = staging / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["package_id"] = package_id
            _write_json(manifest_path, manifest)
        verify_model_package(staging, task_id=TASK_ID, source=source)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("data/source/material_workbench_tutorial_v1.xlsx"))
    parser.add_argument("--output", type=Path, default=Path("models/packages/hot-rolled-tutorial-v1"))
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--package-id", default=PACKAGE_ID)
    arguments = parser.parse_args()
    build(arguments.source, arguments.output, replace=arguments.replace, package_id=arguments.package_id)
