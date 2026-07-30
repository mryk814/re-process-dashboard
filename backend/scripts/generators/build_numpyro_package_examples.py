"""Build small, loadable NumPyro posterior Package examples for every likelihood."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import shutil
from pathlib import Path
import sys
from uuid import uuid4

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from material_workbench.contracts.model_example_contracts import ExampleQualityReport, ExampleSmokeExpected, ExampleSmokeInput
from material_workbench.modeling.model_package_verify import verify_model_package_example
from material_workbench.modeling.model_packages import ModelPackageLoader
from material_workbench.contracts.task_contracts import TargetRuntimeCapability


EXAMPLES = {
    "normal": ("continuous", 1, {"obs_scale": 0.25}),
    "student_t": ("continuous", 1, {"obs_scale": 0.25, "df": 6.0}),
    "lognormal": ("continuous_positive", 1, {"obs_scale": 0.15}),
    "bernoulli_logit": ("binary", 1, {}),
    "poisson_log": ("count", 1, {}),
    "negative_binomial_log": ("count", 1, {"dispersion": 4.0}),
    "zero_inflated_poisson_log": ("count", 2, {}),
    "ordinal_logit": ("ordinal", 1, {}),
}
CANONICAL_INPUT_PATHS = ("composition.C", "composition.Mn")


def _artifact(root: Path, path: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size,
    }


def _build_contents(destination: Path) -> None:
    destination.mkdir(parents=True)
    readme_rows: list[str] = []
    for family, (target_kind, output_width, extras) in EXAMPLES.items():
        root = destination / family
        pipeline_dir, model_dir = root / "feature-pipeline", root / "model-artifacts"
        pipeline_dir.mkdir(parents=True)
        model_dir.mkdir()
        pipeline = pipeline_dir / "pipeline.json"
        pipeline.write_text(json.dumps({"id": "two-feature-example", "version": "1.0.0", "canonical_input_paths": list(CANONICAL_INPUT_PATHS), "features": [{"name": "C", "unit": "mass%", "meaning": "C composition", "group": "composition"}, {"name": "Mn", "unit": "mass%", "meaning": "Mn composition", "group": "composition"}]}, indent=2), encoding="utf-8", newline="\n")
        draws = 64
        rng = np.random.default_rng(20260720)
        arrays: dict[str, np.ndarray] = {
            "w0": rng.normal(0, 0.12, (draws, 2, 4)),
            "b0": rng.normal(0, 0.08, (draws, 4)),
            "w1": rng.normal(0, 0.12, (draws, 4, output_width)),
            "b1": rng.normal(0, 0.08, (draws, output_width)),
        }
        arrays.update({name: np.full(draws, value) for name, value in extras.items()})
        posterior = model_dir / "posterior.npz"
        np.savez(posterior, **arrays)
        config: dict[str, object] = {"activation": "tanh"}
        if family == "ordinal_logit":
            config["thresholds"] = [-0.7, 0.2, 1.0]
            config["categories"] = ["low", "medium", "high", "very_high"]
        manifest = {
            "schema_version": "model-package/v1",
            "package_id": f"numpyro-{family}-example",
            "package_version": "1.0.0",
            "task_id": "model-package-example",
            "input_schema_version": "candidate-v2",
            "feature_pipeline": {"id": "two-feature-example", "version": "1.0.0", "spec": "feature-pipeline/pipeline.json", "canonical_input_paths": list(CANONICAL_INPUT_PATHS), "output_features": ["C", "Mn"], "artifacts": []},
            "predictors": [{"id": "target", "target": "example", "unit": "1", "target_kind": target_kind, "runtime_type": "numpyro.dense_posterior.v1", "architecture_id": "dense_mlp_v1", "artifact": "model-artifacts/posterior.npz", "predictive_family": family, "feature_names": ["C", "Mn"], "config": config}],
            "provenance": {"training_data_id": "synthetic:documented-example", "feature_dataset_id": "synthetic:C-Mn", "training_code_revision": "build_numpyro_package_examples.py"},
            "artifacts": [_artifact(root, pipeline), _artifact(root, posterior)],
        }
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        smoke_input_path = root / "smoke" / "input.json"
        smoke_expected_path = root / "smoke" / "expected.json"
        quality_path = root / "reports" / "quality-report.json"
        smoke_input = ExampleSmokeInput(predictor_id="target", features={"C": 0.08, "Mn": 1.5}, seed=7)
        summary = ModelPackageLoader().load(root).load_predictor("target").predict(smoke_input.features, seed=smoke_input.seed)
        capability = TargetRuntimeCapability(
            target="example",
            point_statistics=(summary.point_statistic,),
            standard_deviation="std" in summary.distribution,
            quantiles=bool(summary.quantiles),
            samples=False,
            parametric_distribution=True,
            uncertainty_components=False,
            support=True,
            warnings=True,
            goal_probability="native" if family == "bernoulli_logit" else "unavailable",
        )
        smoke_input_path.parent.mkdir()
        quality_path.parent.mkdir()
        smoke_input_path.write_text(json.dumps(smoke_input.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        smoke_expected_path.write_text(json.dumps(ExampleSmokeExpected(summary=summary, capability=capability).model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        quality = ExampleQualityReport(
            schema_version="model-example-quality/v1",
            evaluation_unit="synthetic posterior contract fixture",
            metrics={
                "posterior_draw_count": float(draws),
                "smoke_quantile_count": float(len(summary.quantiles)),
                "contract_fixture_count": 1.0,
            },
            notes=("Contract checks only; accuracy metrics require observations and are intentionally absent.",),
        )
        quality_path.write_text(json.dumps(quality.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        manifest["smoke_test"] = {"input": "smoke/input.json", "expected": "smoke/expected.json"}
        manifest["quality_report"] = "reports/quality-report.json"
        manifest["artifacts"].extend([_artifact(root, smoke_input_path), _artifact(root, smoke_expected_path), _artifact(root, quality_path)])
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        verify_model_package_example(root)
        readme_rows.append(f"- `{family}/`: `{target_kind}` 出力の `{family}` 尤度")
    (destination / "README.md").write_text(
        "# NumPyro posterior Package examples\n\n"
        "学習済みposteriorを安全な固定BNN構造へexportした、production loaderとadapterで検証できる8例です。"
        "学習コード自体はPackageへ含めません。各例は同じ2層BNN、異なる尤度・出力supportを示し、"
        "deterministic smoke、RuntimeCapability、意味に合う最小quality reportを同梱します。\n\n"
        + "\n".join(readme_rows)
        + "\n\n検証例: `uv run python backend/scripts/operations/verify_model_package.py examples/model-packages/numpyro/bernoulli_logit --example`\n",
        encoding="utf-8",
    )


@contextmanager
def _staged_collection(destination: Path, *, replace: bool):
    destination = destination.resolve()
    if destination.exists() and not replace:
        raise FileExistsError(f"refusing to replace existing example collection: {destination}")
    if destination.exists() and (
        not (destination / "README.md").is_file()
        or any(not (destination / family / "manifest.json").is_file() for family in EXAMPLES)
    ):
        raise FileExistsError(f"refusing to replace a directory that is not the NumPyro example collection: {destination}")
    staging = destination.with_name(f".{destination.name}.staging-{uuid4().hex}")
    backup = destination.with_name(f".{destination.name}.backup-{uuid4().hex}")
    try:
        yield staging
        if destination.exists():
            destination.rename(backup)
        staging.rename(destination)
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        if backup.exists() and not destination.exists():
            backup.rename(destination)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def build(destination: Path, *, replace: bool = False) -> None:
    with _staged_collection(destination, replace=replace) as staging:
        _build_contents(staging)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("examples/model-packages/numpyro"))
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    build(args.output, replace=args.replace)
