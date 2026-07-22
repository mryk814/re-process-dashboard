"""Train and export point-only and normal-approximation additive examples."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from material_workbench.adapters.builtin_additive_terms import bspline_basis
from material_workbench.model_example_contracts import ExampleQualityReport, ExampleSmokeExpected, ExampleSmokeInput
from material_workbench.model_lifecycle import staged_package_destination
from material_workbench.model_package_verify import verify_model_package_example
from material_workbench.model_packages import ModelPackageLoader
from material_workbench.task_contracts import TargetRuntimeCapability


KNOTS = np.asarray([0.0, 0.0, 0.0, 0.5, 1.0, 1.0, 1.0])
Z_KNOTS = np.asarray([-1.0, -1.0, -1.0, 0.0, 1.0, 1.0, 1.0])
DEGREE = 2
CATEGORIES = np.asarray([0.0, 1.0, 2.0])


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def _artifact(root: Path, path: Path) -> dict[str, object]:
    return {"path": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}


def _train() -> tuple[float, np.ndarray, np.ndarray, np.ndarray, float]:
    rows: list[np.ndarray] = []
    targets: list[float] = []
    truth_spline = np.asarray([0.0, 1.5, -0.5, 2.0])
    truth_categories = np.asarray([-0.5, 0.0, 0.8])
    for row_index, (x, category, z) in enumerate(
        (x, category, z)
        for x in np.linspace(0, 1, 9)
        for category in CATEGORIES
        for z in (-1.0, 0.0, 1.0)
    ):
        basis = bspline_basis(float(x), KNOTS, DEGREE)
        one_hot = (CATEGORIES == category).astype(float)
        z_basis = bspline_basis(float(z), Z_KNOTS, DEGREE)
        rows.append(np.concatenate(([1.0], basis, one_hot, z_basis)))
        targets.append(float(5.0 + basis @ truth_spline + truth_categories[int(category)] + 0.4 * z + 0.03 * np.sin(row_index)))
    design = np.vstack(rows)
    observed = np.asarray(targets)
    coefficients, *_ = np.linalg.lstsq(design, observed, rcond=None)
    residuals = observed - design @ coefficients
    intercept = float(coefficients[0])
    spline = coefficients[1:5]
    categories = coefficients[5:8]
    z_spline = coefficients[8:12]
    residual_scale = float(np.sqrt(np.mean(residuals**2)))
    return intercept, spline, categories, z_spline, residual_scale


def _build_one(staging: Path, *, normal: bool) -> None:
    intercept, spline, category_scores, z_spline, residual_scale = _train()
    pipeline = staging / "feature-pipeline" / "pipeline.json"
    model = staging / "model-artifacts" / "additive.npz"
    model.parent.mkdir(parents=True)
    _write_json(pipeline, {
        "id": "additive-three-feature-example",
        "version": "1.0.0",
        "canonical_input_paths": ["composition.x", "categorical.route_code", "process.z"],
        "features": [
            {"name": "x", "unit": "1", "meaning": "nonlinear synthetic input", "group": "composition"},
            {"name": "route_code", "unit": "1", "meaning": "encoded synthetic category", "group": "categorical"},
            {"name": "z", "unit": "1", "meaning": "linear synthetic process input", "group": "process"},
        ],
    })
    arrays = {
        "intercept": np.asarray(intercept),
        "term_0_knots": KNOTS,
        "term_0_coefficients": spline,
        "term_1_categories": CATEGORIES,
        "term_1_scores": category_scores,
        "term_2_knots": Z_KNOTS,
        "term_2_coefficients": z_spline,
    }
    if normal:
        arrays["residual_scale"] = np.asarray(residual_scale)
    np.savez(model, **arrays)
    family = "normal" if normal else "empirical_quantiles"
    package_id = f"additive-terms-{'normal' if normal else 'point'}-example"
    manifest = {
        "schema_version": "model-package/v1",
        "package_id": package_id,
        "package_version": "1.0.0",
        "task_id": "model-runtime-example",
        "input_schema_version": "example-input/v1",
        "feature_pipeline": {
            "id": "additive-three-feature-example",
            "version": "1.0.0",
            "spec": "feature-pipeline/pipeline.json",
            "canonical_input_paths": ["composition.x", "categorical.route_code", "process.z"],
            "output_features": ["x", "route_code", "z"],
            "artifacts": [],
        },
        "predictors": [{
            "id": "target",
            "target": "synthetic_response",
            "unit": "a.u.",
            "target_kind": "continuous",
            "runtime_type": "builtin.additive_terms.v1",
            "architecture_id": "additive_terms_v1",
            "artifact": "model-artifacts/additive.npz",
            "predictive_family": family,
            "feature_names": ["x", "route_code", "z"],
            "config": {
                "link_id": "identity",
                "extrapolation": "constant_boundary",
                "terms": [
                    {"id": "x_spline", "kind": "bspline_univariate", "feature_index": 0, "degree": DEGREE},
                    {"id": "route", "kind": "categorical_lookup", "feature_index": 1},
                    {"id": "z_spline", "kind": "bspline_univariate", "feature_index": 2, "degree": DEGREE},
                ],
            },
        }],
        "provenance": {
            "training_data_id": "synthetic:additive-known-terms",
            "feature_dataset_id": "synthetic:x-route-z-grid",
            "training_code_revision": "build_additive_model_examples.py:numpy-lstsq",
        },
        "artifacts": [_artifact(staging, pipeline), _artifact(staging, model)],
    }
    manifest_path = staging / "manifest.json"
    _write_json(manifest_path, manifest)
    smoke_input = ExampleSmokeInput(predictor_id="target", features={"x": 0.35, "route_code": 1.0, "z": 0.2}, seed=11)
    predictor = ModelPackageLoader().load(staging).load_predictor("target")
    summary = predictor.predict(smoke_input.features, seed=smoke_input.seed)
    explanation = predictor.explain(smoke_input.features)
    capability = TargetRuntimeCapability(
        target="synthetic_response",
        point_statistics=("mean",),
        standard_deviation=normal,
        quantiles=normal,
        samples=False,
        parametric_distribution=normal,
        uncertainty_components=False,
        support=True,
        warnings=True,
        goal_probability="normal_approximation" if normal else "unavailable",
    )
    smoke_input_path = staging / "smoke" / "input.json"
    smoke_expected_path = staging / "smoke" / "expected.json"
    quality_path = staging / "reports" / "quality-report.json"
    _write_json(smoke_input_path, smoke_input.model_dump(mode="json"))
    _write_json(smoke_expected_path, ExampleSmokeExpected(summary=summary, capability=capability).model_dump(mode="json"))
    curve_values = [predictor.predict({"x": float(x), "route_code": 1.0, "z": 0.2}).point_estimate for x in np.linspace(0, 1, 21)]
    quality = ExampleQualityReport(
        schema_version="model-example-quality/v1",
        evaluation_unit="synthetic x-route-z grid",
        metrics={
            "training_rmse": residual_scale,
            "explanation_reconstruction_error": abs(explanation.intercept + sum(item.contribution for item in explanation.terms) - explanation.link_score),
            "response_curve_span": float(max(curve_values) - min(curve_values)),
        },
        notes=("Local term contributions describe this additive score; they are not causal effects.",),
    )
    _write_json(quality_path, quality.model_dump(mode="json"))
    manifest["smoke_test"] = {"input": "smoke/input.json", "expected": "smoke/expected.json"}
    manifest["quality_report"] = "reports/quality-report.json"
    manifest["artifacts"].extend([_artifact(staging, smoke_input_path), _artifact(staging, smoke_expected_path), _artifact(staging, quality_path)])
    _write_json(manifest_path, manifest)


def build(destination: Path, *, replace: bool = False) -> None:
    for name, normal in (("point", False), ("normal", True)):
        target = destination / name
        with staged_package_destination(target, replace=replace) as staging:
            _build_one(staging, normal=normal)
        verify_model_package_example(target)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("examples/model-packages/additive-terms"))
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    build(args.output, replace=args.replace)


if __name__ == "__main__":
    main()
