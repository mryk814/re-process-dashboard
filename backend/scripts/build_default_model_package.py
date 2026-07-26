from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from material_workbench.modeling.feature_pipeline import CANONICAL_INPUT_PATHS, FEATURE_DEFINITIONS, FEATURE_NAMES, FEATURE_PIPELINE_ID, FEATURE_PIPELINE_VERSION
from material_workbench.data.importer import load_workbook_data, training_context_key
from material_workbench.modeling.model_lifecycle import QualityReport, canonical_training_dataset, canonical_training_dataset_digest, dataset_profile_digest, exact_gp_loo_quality, runtime_capability_digest, staged_package_destination, task_input_contract_digest
from material_workbench.modeling.model_package_verify import verify_model_package
from material_workbench.modeling.runtime import INPUT_SCHEMA_VERSION, TARGETS, TASK_ID, ModelRuntime
from material_workbench.contracts.schemas import CandidateInput
from material_workbench.tasks.task_registry import load_task_contracts


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact(root: Path, path: Path) -> dict[str, object]:
    return {"path": path.relative_to(root).as_posix(), "sha256": digest(path), "bytes": path.stat().st_size}


PACKAGE_ID = "annealed-gp-stable-ard-tutorial-v2"
PACKAGE_VERSION = "2.1.0-stable-ard"
TRAINING_CODE_REVISION = "stable-ard-multistart-v1"
def _grouped_training(model: object, target: str) -> tuple[np.ndarray, np.ndarray, float, float]:
    column = TARGETS[target][0]
    grouped: dict[str, list[int]] = {}
    rows = model.rows  # type: ignore[attr-defined]
    for index, row in enumerate(rows):
        grouped.setdefault(training_context_key(row), []).append(index)
    x_rows: list[np.ndarray] = []
    y_rows: list[float] = []
    within_sse = 0.0
    within_df = 0
    repeat_counts: list[int] = []
    normalized = model.x_train  # type: ignore[attr-defined]
    for indexes in grouped.values():
        values = np.asarray([float(rows[index]["outputs"][column]) for index in indexes])
        x_rows.append(normalized[indexes].mean(axis=0))
        y_rows.append(float(values.mean()))
        repeat_counts.append(len(values))
        if len(values) > 1:
            within_sse += float(np.sum((values - values.mean()) ** 2))
            within_df += len(values) - 1
    y = np.asarray(y_rows, dtype=np.float64)
    total_variance = max(float(np.var(y)), 1e-6)
    observation_variance = within_sse / within_df if within_df else total_variance * 0.1
    observation_variance = max(float(observation_variance), total_variance * 1e-4, 1e-8)
    median_repeats = max(float(np.median(repeat_counts)), 1.0)
    train_noise = max(observation_variance / median_repeats, total_variance * 1e-5, 1e-9)
    return np.vstack(x_rows), y, train_noise, observation_variance


def _fit_gp_hyperparameters(
    train_x: np.ndarray,
    train_y: np.ndarray,
    train_noise: float,
    *,
    restarts: int = 3,
    seed: int = 20260723,
) -> tuple[np.ndarray, float, float, dict[str, object]]:
    """Fit a regularized ARD-RBF GP in standardized X/Y space.

    The exported covariance is converted back to the target unit, so the
    inference adapter remains simple and deterministic.
    """

    if train_x.ndim != 2 or train_y.shape != (len(train_x),) or len(train_x) < 3:
        raise ValueError("GP training arrays have incompatible shapes")
    target_mean = float(np.mean(train_y))
    target_scale = max(float(np.std(train_y)), 1e-8)
    target = (train_y - target_mean) / target_scale
    pairwise_sq = (train_x[:, None, :] - train_x[None, :, :]) ** 2
    noise_anchor = float(np.clip(train_noise / (target_scale * target_scale), 1e-5, 1.0))
    feature_count = train_x.shape[1]
    log_length_prior = np.log(2.0)
    length_bounds = (np.log(0.08), np.log(20.0))
    signal_bounds = (np.log(0.03), np.log(20.0))
    noise_bounds = (
        np.log(max(1e-6, noise_anchor / 8.0)),
        np.log(min(2.0, max(noise_anchor * 8.0, 2e-5))),
    )
    identity = np.eye(len(train_x))

    def objective(theta: np.ndarray) -> tuple[float, np.ndarray]:
        log_length = theta[:feature_count]
        signal = float(np.exp(theta[-2]))
        noise = float(np.exp(theta[-1]))
        scaled_sq = pairwise_sq / np.exp(2.0 * log_length)[None, None, :]
        signal_kernel = signal * np.exp(-0.5 * np.sum(scaled_sq, axis=2))
        covariance = signal_kernel + (noise + 1e-8) * identity
        try:
            cholesky = np.linalg.cholesky(covariance)
        except np.linalg.LinAlgError:
            return 1e30, np.zeros_like(theta)
        alpha = np.linalg.solve(cholesky.T, np.linalg.solve(cholesky, target))
        precision = np.linalg.solve(cholesky.T, np.linalg.solve(cholesky, identity))
        common = precision - np.outer(alpha, alpha)
        nll = float(
            0.5 * target @ alpha
            + np.log(np.diag(cholesky)).sum()
            + 0.5 * len(train_x) * np.log(2.0 * np.pi)
        )

        # ARD is useful here, but 42 weakly identified dimensions should not
        # drift independently. Shrink log-lengthscales toward their common
        # center and gently toward a two-standard-deviation prior scale.
        centered_length = log_length - float(np.mean(log_length))
        shrinkage = 0.04 * float(centered_length @ centered_length)
        prior = 0.01 * float(np.sum((log_length - log_length_prior) ** 2))
        noise_prior = 0.04 * float((theta[-1] - np.log(noise_anchor)) ** 2)
        nll += shrinkage + prior + noise_prior

        gradient = np.empty_like(theta)
        gradient[:feature_count] = 0.5 * np.einsum(
            "ij,ijk->k",
            common * signal_kernel,
            scaled_sq,
            optimize=True,
        )
        gradient[:feature_count] += (
            0.08 * centered_length
            + 0.02 * (log_length - log_length_prior)
        )
        gradient[-2] = 0.5 * np.sum(common * signal_kernel)
        gradient[-1] = (
            0.5 * noise * np.trace(common)
            + 0.08 * (theta[-1] - np.log(noise_anchor))
        )
        return nll, gradient

    base = np.r_[
        np.full(feature_count, log_length_prior),
        np.log(1.0),
        np.log(noise_anchor),
    ]
    rng = np.random.default_rng(seed)
    starts = [base]
    for restart in range(1, max(restarts, 1)):
        candidate = base.copy()
        candidate[:feature_count] += rng.normal(0.0, 0.55, feature_count)
        candidate[-2] += rng.normal(0.0, 0.45)
        candidate[-1] += rng.normal(0.0, 0.35)
        starts.append(candidate)
    bounds = [length_bounds] * feature_count + [signal_bounds, noise_bounds]
    results = [
        minimize(
            objective,
            np.clip(start, [item[0] for item in bounds], [item[1] for item in bounds]),
            method="L-BFGS-B",
            jac=True,
            bounds=bounds,
            options={"maxiter": 90, "ftol": 1e-6, "gtol": 1e-5, "maxls": 20},
        )
        for start in starts
    ]
    finite = [result for result in results if np.isfinite(result.fun)]
    if not finite:
        raise RuntimeError("Gaussian-process hyperparameter optimization found no finite solution")
    best = min(finite, key=lambda result: float(result.fun))
    lengthscale = np.exp(best.x[:feature_count])
    outputscale = float(np.exp(best.x[-2]) * target_scale * target_scale)
    fitted_noise = float(np.exp(best.x[-1]) * target_scale * target_scale)
    diagnostics: dict[str, object] = {
        "optimizer": "L-BFGS-B",
        "restarts": len(results),
        "converged_restarts": sum(bool(result.success) for result in results),
        "best_objective": float(best.fun),
        "input_standardization": "per_feature_training_mean_std",
        "output_standardization": {"mean": target_mean, "scale": target_scale},
        "kernel": "ARD-RBF",
        "ard_shrinkage": "log_lengthscale_common_center",
        "lengthscale": {
            "min": float(np.min(lengthscale)),
            "median": float(np.median(lengthscale)),
            "max": float(np.max(lengthscale)),
        },
        "outputscale": outputscale,
        "train_noise": fitted_noise,
        "replicate_noise_anchor": train_noise,
    }
    return lengthscale, outputscale, fitted_noise, diagnostics


def _gp_point(artifact_path: Path, raw_features: np.ndarray) -> float:
    with np.load(artifact_path, allow_pickle=False) as item:
        train_x = item["train_x"]
        train_y = item["train_y"]
        mean = float(item["mean"])
        point = (raw_features - item["feature_mean"]) / item["feature_scale"]
        cross_scaled = (train_x - point) / item["lengthscale"]
        cross = float(item["outputscale"]) * np.exp(-0.5 * np.sum(cross_scaled * cross_scaled, axis=1))
        return mean + float(cross @ item["alpha"])


def _build(source: Path, destination: Path) -> None:
    data = load_workbook_data(source)
    runtime = ModelRuntime(data, load_package=False)
    artifacts_dir = destination / "model-artifacts"
    feature_dir = destination / "feature-pipeline"
    reference_dir = destination / "reference"
    smoke_dir = destination / "smoke"
    report_dir = destination / "reports"
    for folder in (artifacts_dir, feature_dir, reference_dir, smoke_dir, report_dir):
        folder.mkdir(parents=True, exist_ok=True)

    pipeline_path = feature_dir / "pipeline.json"
    pipeline_path.write_text(json.dumps({
        "id": FEATURE_PIPELINE_ID,
        "version": FEATURE_PIPELINE_VERSION,
        "canonical_input_paths": list(CANONICAL_INPUT_PATHS),
        "features": [{"name": item.name, "unit": item.unit, "meaning": item.meaning, "group": item.group} for item in FEATURE_DEFINITIONS],
        "missing_composition": "training_median_from_source_workbook",
        "heat_interpolation": "piecewise_linear",
    }, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")

    predictors: list[dict[str, object]] = []
    files = [pipeline_path]
    training_counts: dict[str, int] = {}
    quality_metrics = []
    training_diagnostics: dict[str, object] = {}
    for target, model in sorted(runtime.models.items()):
        train_x, train_y, train_noise, observation_noise = _grouped_training(model, target)
        lengthscale, outputscale, train_noise, diagnostics = _fit_gp_hyperparameters(
            train_x, train_y, train_noise
        )
        scaled = (train_x[:, None, :] - train_x[None, :, :]) / lengthscale
        covariance = outputscale * np.exp(-0.5 * np.sum(scaled * scaled, axis=2))
        covariance.flat[:: len(train_x) + 1] += train_noise
        cholesky = np.linalg.cholesky(covariance)
        precision = np.linalg.solve(cholesky.T, np.linalg.solve(cholesky, np.eye(len(train_x))))
        mean_value = float(train_y.mean())
        alpha = precision @ (train_y - mean_value)
        quality_metrics.append(exact_gp_loo_quality(target, train_y, alpha, precision))
        path = artifacts_dir / f"{target}.npz"
        np.savez(
            path,
            train_x=train_x,
            train_y=train_y,
            feature_mean=model.feature_mean,
            feature_scale=model.feature_scale,
            lengthscale=lengthscale,
            outputscale=np.asarray(outputscale),
            train_noise=np.asarray(train_noise),
            observation_noise=np.asarray(observation_noise),
            mean=np.asarray(mean_value),
            precision=precision,
            alpha=alpha,
        )
        files.append(path)
        training_counts[target] = len(train_y)
        training_diagnostics[target] = diagnostics
        unit = model.unit
        predictors.append({
            "id": f"{target.lower()}-gp", "target": target, "unit": unit,
            "target_kind": "continuous_positive" if target == "lambda" else "continuous",
            "runtime_type": "builtin.exact_gp.v1", "architecture_id": "exact_rbf_ard_v1",
            "artifact": path.relative_to(destination).as_posix(),
            "predictive_family": "normal", "feature_names": list(FEATURE_NAMES),
            "config": {
                "training_unit": "parent_condition_mean",
                "replicate_noise": "pooled_within_parent",
                "input_standardization": "per_feature_training_mean_std",
                "output_standardization": True,
                "kernel": "ARD-RBF",
                "hyperparameter_optimizer": "L-BFGS-B",
                "optimizer_restarts": 3,
                "ard_shrinkage": "weak_common_log_lengthscale",
            },
        })

    stats_path = reference_dir / "training_stats.json"
    stats_path.write_text(json.dumps({
        "records": training_counts,
        "source_sha256": data.source_sha256,
        "composition_defaults": data.medians,
    }, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    files.append(stats_path)

    quality_path = report_dir / "quality-report.json"
    quality = QualityReport(
        schema_version="model-quality-report/v1",
        split="leave-one-parent-condition-out",
        targets=tuple(quality_metrics),
    )
    quality_path.write_text(quality.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    files.append(quality_path)
    diagnostics_path = report_dir / "training-diagnostics.json"
    diagnostics_path.write_text(json.dumps({
        "schema_version": "gp-training-diagnostics/v1",
        "training_policy": "standardized-ard-multistart-v1",
        "note": "Synthetic demo data; diagnostics describe numerical fitting, not scientific validity.",
        "targets": training_diagnostics,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    files.append(diagnostics_path)

    smoke_input = {
        "name": "package smoke",
        "inputs": {
            "composition": data.medians,
            "process": {"ls_mpm": 103.0},
            "heat_pattern": [{"time_s": 0, "temperature_c": 25}, {"time_s": 300, "temperature_c": 800}, {"time_s": 360, "temperature_c": 810}, {"time_s": 650, "temperature_c": 120}],
        },
    }
    smoke_input_path = smoke_dir / "input.json"
    smoke_input_path.write_text(json.dumps(smoke_input, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    smoke_candidate = CandidateInput.model_validate(smoke_input)
    smoke_values = runtime.vector_for_candidate(smoke_candidate)
    expected = {target: round(_gp_point(artifacts_dir / f"{target}.npz", smoke_values), 8) for target in runtime.models}
    smoke_expected_path = smoke_dir / "expected.json"
    smoke_expected_path.write_text(json.dumps(expected, indent=2), encoding="utf-8", newline="\n")
    files.extend([smoke_input_path, smoke_expected_path])

    contract = load_task_contracts()[TASK_ID]
    canonical_dataset = canonical_training_dataset(TASK_ID, data, contract)
    manifest = {
        "schema_version": "model-package/v1", "package_id": PACKAGE_ID, "package_version": PACKAGE_VERSION,
        "task_id": TASK_ID, "input_schema_version": INPUT_SCHEMA_VERSION,
        "input_contract_digest": task_input_contract_digest(contract.task_definition),
        "runtime_capability_digest": runtime_capability_digest(contract.runtime_capability),
        "feature_pipeline": {"id": FEATURE_PIPELINE_ID, "version": FEATURE_PIPELINE_VERSION, "spec": pipeline_path.relative_to(destination).as_posix(), "canonical_input_paths": list(CANONICAL_INPUT_PATHS), "output_features": list(FEATURE_NAMES), "artifacts": [stats_path.relative_to(destination).as_posix()]},
        "predictors": predictors,
        "provenance": {"training_data_id": f"sha256:{data.source_sha256}", "feature_dataset_id": canonical_training_dataset_digest(canonical_dataset), "training_code_revision": TRAINING_CODE_REVISION, "dataset_profile_id": dataset_profile_digest(Path(data.profile_path))},
        "artifacts": [artifact(destination, path) for path in files],
        "smoke_test": {"input": smoke_input_path.relative_to(destination).as_posix(), "expected": smoke_expected_path.relative_to(destination).as_posix()},
        "quality_report": quality_path.relative_to(destination).as_posix(),
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )


def build(source: Path, destination: Path, *, replace: bool = False, package_id: str = PACKAGE_ID) -> None:
    with staged_package_destination(destination, replace=replace) as staging:
        _build(source, staging)
        if package_id != PACKAGE_ID:
            manifest_path = staging / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["package_id"] = package_id
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        verify_model_package(staging, task_id=TASK_ID, source=source)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("data/source/material_workbench_tutorial_v2.xlsx"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/model-package-candidates/annealed-gp-stable-ard-tutorial-v2"),
    )
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--package-id", default=PACKAGE_ID)
    args = parser.parse_args()
    build(args.source, args.output, replace=args.replace, package_id=args.package_id)
