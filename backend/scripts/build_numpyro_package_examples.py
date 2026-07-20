"""Build small, loadable NumPyro posterior Package examples for every likelihood."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import numpy as np


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


def build(destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
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
        manifest = {
            "schema_version": "model-package/v1",
            "package_id": f"numpyro-{family}-example",
            "package_version": "1.0.0",
            "task_id": "model-package-example",
            "input_schema_version": "candidate-v1",
            "feature_pipeline": {"id": "two-feature-example", "version": "1.0.0", "spec": "feature-pipeline/pipeline.json", "canonical_input_paths": list(CANONICAL_INPUT_PATHS), "output_features": ["C", "Mn"], "artifacts": []},
            "predictors": [{"id": "target", "target": "example", "unit": "1", "target_kind": target_kind, "runtime_type": "numpyro.dense_posterior.v1", "architecture_id": "dense_mlp_v1", "artifact": "model-artifacts/posterior.npz", "predictive_family": family, "feature_names": ["C", "Mn"], "config": config}],
            "provenance": {"training_data_id": "synthetic:documented-example", "feature_dataset_id": "synthetic:C-Mn", "training_code_revision": "build_numpyro_package_examples.py"},
            "artifacts": [_artifact(root, pipeline), _artifact(root, posterior)],
        }
        (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        readme_rows.append(f"- `{family}/`: `{target_kind}` 出力の `{family}` 尤度")
    (destination / "README.md").write_text(
        "# NumPyro posterior Package examples\n\n"
        "学習済みposteriorを安全な固定BNN構造へexportした、loaderで実際に読める8例です。"
        "学習コード自体はPackageへ含めません。各例は同じ2層BNN、異なる尤度・出力supportを示します。\n\n"
        + "\n".join(readme_rows) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    build(Path("examples/model-packages/numpyro"))
