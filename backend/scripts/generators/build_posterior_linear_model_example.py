"""Train a sparse Bayesian linear model and export library-neutral draws."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from material_workbench.contracts.model_example_contracts import ExampleQualityReport, ExampleSmokeExpected, ExampleSmokeInput
from material_workbench.modeling.model_lifecycle import staged_package_destination
from material_workbench.modeling.model_package_verify import verify_model_package_example
from material_workbench.modeling.model_package_verification import ModelPackageLoader
from material_workbench.contracts.task_contracts import TargetRuntimeCapability


FEATURE_NAMES = tuple(f"x{index}" for index in range(8))


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def _artifact(root: Path, path: Path) -> dict[str, object]:
    return {"path": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}


def _synthetic_data() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(20260722)
    blocks = np.repeat(np.arange(8), 4)
    x = rng.normal(size=(len(blocks), len(FEATURE_NAMES)))
    x[:, 1] = 0.65 * x[:, 0] + rng.normal(0, 0.55, len(blocks))
    block_effect = rng.normal(0, 0.18, 8)[blocks]
    y = 1.2 + 2.0 * x[:, 0] - 1.1 * x[:, 1] + block_effect + rng.normal(0, 0.35, len(blocks))
    return x, y, blocks


def _train_numpyro(x: np.ndarray, y: np.ndarray, *, seed: int = 20260722, warmup: int = 160, draws: int = 192) -> dict[str, np.ndarray]:
    try:
        import jax.numpy as jnp
        from jax import random
        import numpyro
        import numpyro.distributions as dist
        from numpyro.infer import MCMC, NUTS
    except ImportError as exc:
        raise RuntimeError("training requires the runtime-numpyro optional dependency") from exc

    def model(features: object, observed: object | None = None) -> None:
        global_scale = numpyro.sample("global_scale", dist.HalfCauchy(0.35))
        local_scale = numpyro.sample("local_scale", dist.HalfCauchy(jnp.ones(x.shape[1])))
        beta = numpyro.sample("beta", dist.Normal(jnp.zeros(x.shape[1]), global_scale * local_scale))
        intercept = numpyro.sample("intercept", dist.Normal(0, 3))
        noise_scale = numpyro.sample("noise_scale", dist.HalfNormal(1))
        numpyro.sample("observed", dist.Normal(intercept + jnp.asarray(features) @ beta, noise_scale), obs=observed)

    sampler = MCMC(NUTS(model, target_accept_prob=0.85), num_warmup=warmup, num_samples=draws, num_chains=1, progress_bar=False)
    sampler.run(random.PRNGKey(seed), jnp.asarray(x), jnp.asarray(y))
    samples = sampler.get_samples()
    return {
        "beta_draws": np.asarray(samples["beta"], dtype=float),
        "intercept_draws": np.asarray(samples["intercept"], dtype=float),
        "noise_scale_draws": np.asarray(samples["noise_scale"], dtype=float),
        "local_scale_draws": np.asarray(samples["local_scale"], dtype=float),
    }


def _block_cv(x: np.ndarray, y: np.ndarray, blocks: np.ndarray, columns: np.ndarray) -> float:
    errors: list[float] = []
    for block in np.unique(blocks):
        train, test = blocks != block, blocks == block
        x_train = np.column_stack([np.ones(train.sum()), x[train][:, columns]])
        x_test = np.column_stack([np.ones(test.sum()), x[test][:, columns]])
        penalty = np.eye(x_train.shape[1]) * 0.1
        penalty[0, 0] = 0
        coefficients = np.linalg.solve(x_train.T @ x_train + penalty, x_train.T @ y[train])
        errors.extend((y[test] - x_test @ coefficients).tolist())
    return float(np.sqrt(np.mean(np.asarray(errors) ** 2)))


def _posterior_block_cv(x: np.ndarray, y: np.ndarray, blocks: np.ndarray) -> tuple[float, float, float]:
    full_errors: list[float] = []
    reduced_errors: list[float] = []
    selected_counts: list[int] = []
    for block in np.unique(blocks):
        train, test = blocks != block, blocks == block
        full = _train_numpyro(x[train], y[train], seed=20260800 + int(block), warmup=48, draws=64)
        full_mean = full["intercept_draws"].mean() + x[test] @ full["beta_draws"].mean(axis=0)
        full_errors.extend((y[test] - full_mean).tolist())
        inclusion = np.mean(np.abs(full["beta_draws"]) > 0.1, axis=0)
        selected = np.flatnonzero(inclusion >= 0.8)
        if not len(selected):
            selected = np.asarray([int(np.argmax(inclusion))])
        selected_counts.append(len(selected))
        reduced = _train_numpyro(x[train][:, selected], y[train], seed=20260900 + int(block), warmup=48, draws=64)
        reduced_mean = reduced["intercept_draws"].mean() + x[test][:, selected] @ reduced["beta_draws"].mean(axis=0)
        reduced_errors.extend((y[test] - reduced_mean).tolist())
    return (
        float(np.sqrt(np.mean(np.square(full_errors)))),
        float(np.sqrt(np.mean(np.square(reduced_errors)))),
        float(np.mean(selected_counts)),
    )


def _build(staging: Path, arrays: dict[str, np.ndarray], x: np.ndarray, y: np.ndarray, blocks: np.ndarray) -> None:
    pipeline = staging / "feature-pipeline" / "pipeline.json"
    model = staging / "model-artifacts" / "posterior-linear.npz"
    model.parent.mkdir(parents=True)
    _write_json(pipeline, {
        "id": "sparse-linear-eight-feature-example",
        "version": "1.0.0",
        "canonical_input_paths": [f"composition.{name}" for name in FEATURE_NAMES],
        "features": [{"name": name, "unit": "1", "meaning": f"synthetic feature {name}", "group": "composition"} for name in FEATURE_NAMES],
    })
    np.savez(model, **arrays)
    manifest = {
        "schema_version": "model-package/v1",
        "package_id": "posterior-linear-sparse-example",
        "package_version": "1.0.0",
        "task_id": "model-runtime-example",
        "input_schema_version": "example-input/v1",
        "feature_pipeline": {
            "id": "sparse-linear-eight-feature-example",
            "version": "1.0.0",
            "spec": "feature-pipeline/pipeline.json",
            "canonical_input_paths": [f"composition.{name}" for name in FEATURE_NAMES],
            "output_features": list(FEATURE_NAMES),
            "artifacts": [],
        },
        "predictors": [{
            "id": "target",
            "target": "synthetic_response",
            "unit": "a.u.",
            "target_kind": "continuous",
            "runtime_type": "builtin.posterior_linear.v1",
            "architecture_id": "posterior_linear_v1",
            "artifact": "model-artifacts/posterior-linear.npz",
            "predictive_family": "empirical_quantiles",
            "feature_names": list(FEATURE_NAMES),
            "config": {"draw_semantics": "joint_posterior", "noise_semantics": "observation_scale"},
        }],
        "provenance": {
            "training_data_id": "synthetic:sparse-correlated-small-data",
            "feature_dataset_id": "synthetic:eight-feature-parent-blocks",
            "training_code_revision": "build_posterior_linear_model_example.py:numpyro-horseshoe",
        },
        "artifacts": [_artifact(staging, pipeline), _artifact(staging, model)],
    }
    manifest_path = staging / "manifest.json"
    _write_json(manifest_path, manifest)
    smoke_input = ExampleSmokeInput(predictor_id="target", features={name: float(value) for name, value in zip(FEATURE_NAMES, x[0])}, seed=17)
    summary = ModelPackageLoader().load(staging).load_predictor("target").predict(smoke_input.features, seed=smoke_input.seed)
    capability = TargetRuntimeCapability(
        target="synthetic_response",
        point_statistics=("mean",),
        standard_deviation=True,
        quantiles=True,
        samples=False,
        parametric_distribution=False,
        uncertainty_components=True,
        support=True,
        warnings=True,
        goal_probability="unavailable",
    )
    beta = arrays["beta_draws"]
    local_scale = arrays["local_scale_draws"]
    selection_rows = []
    for index, name in enumerate(FEATURE_NAMES):
        draws = beta[:, index]
        selection_rows.append({
            "feature": name,
            "coefficient_mean": float(np.mean(draws)),
            "coefficient_sd": float(np.std(draws)),
            "q05": float(np.quantile(draws, 0.05)),
            "q95": float(np.quantile(draws, 0.95)),
            "sign_probability": float(max(np.mean(draws >= 0), np.mean(draws <= 0))),
            "rope_outside_probability": float(np.mean(np.abs(draws) > 0.1)),
            "local_scale_mean": float(np.mean(local_scale[:, index])),
        })
    smoke_input_path = staging / "smoke" / "input.json"
    smoke_expected_path = staging / "smoke" / "expected.json"
    quality_path = staging / "reports" / "quality-report.json"
    selection_path = staging / "reports" / "selection-report.json"
    _write_json(smoke_input_path, smoke_input.model_dump(mode="json"))
    _write_json(smoke_expected_path, ExampleSmokeExpected(summary=summary, capability=capability).model_dump(mode="json"))
    _write_json(selection_path, {
        "schema_version": "sparse-selection-report/v1",
        "method": "horseshoe",
        "features": selection_rows,
        "selected_subset_rule": "ROPE outside probability >= 0.8; report only, Feature Pipeline order is unchanged",
        "interpretation_warning": "Shrinkage and sign probabilities are not causal importance; correlated features may share evidence.",
    })
    full_cv_rmse, reduced_cv_rmse, selected_count = _posterior_block_cv(x, y, blocks)
    quality = ExampleQualityReport(
        schema_version="model-example-quality/v1",
        evaluation_unit="leave-one-parent-block-out",
        metrics={
            "full_horseshoe_block_cv_rmse": full_cv_rmse,
            "selected_reduced_horseshoe_block_cv_rmse": reduced_cv_rmse,
            "mean_selected_feature_count": selected_count,
            "full_ridge_baseline_rmse": _block_cv(x, y, blocks, np.arange(x.shape[1])),
            "posterior_draw_count": float(len(beta)),
        },
        notes=("Each held-out parent block trains a full horseshoe, selects features inside that training fold, then trains the reduced horseshoe. The ridge score is a separate full-feature baseline.",),
    )
    _write_json(quality_path, quality.model_dump(mode="json"))
    manifest["smoke_test"] = {"input": "smoke/input.json", "expected": "smoke/expected.json"}
    manifest["quality_report"] = "reports/quality-report.json"
    manifest["artifacts"].extend([_artifact(staging, path) for path in (smoke_input_path, smoke_expected_path, quality_path, selection_path)])
    _write_json(manifest_path, manifest)


def build(destination: Path, *, replace: bool = False) -> None:
    x, y, blocks = _synthetic_data()
    arrays = _train_numpyro(x, y)
    with staged_package_destination(destination, replace=replace) as staging:
        _build(staging, arrays, x, y, blocks)
    verify_model_package_example(destination)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("examples/model-packages/posterior-linear"))
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    build(args.output, replace=args.replace)


if __name__ == "__main__":
    main()
