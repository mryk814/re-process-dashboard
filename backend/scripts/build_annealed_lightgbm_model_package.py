from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    import lightgbm as lgb
except ModuleNotFoundError as exc:  # pragma: no cover - exercised by CLI users.
    raise SystemExit(
        "LightGBM builder requires: uv run --extra runtime-lightgbm python "
        "backend/scripts/build_annealed_lightgbm_model_package.py"
    ) from exc

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
from material_workbench.modeling.runtime import (
    INPUT_SCHEMA_VERSION,
    TARGETS,
    TASK_ID,
    ModelRuntime,
)
from material_workbench.tasks.task_registry import load_task_contracts


PACKAGE_ID = "annealed-lightgbm-standard-tutorial-v2"
PACKAGE_VERSION = "1.1.0-standard"
TRAINING_CODE_REVISION = "lightgbm-grouped-fixed-round-crossfit-v2"
NUM_BOOST_ROUND = 50


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact(root: Path, path: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _digest(path),
        "bytes": path.stat().st_size,
    }


def _grouped_training(
    model: object, target: str
) -> tuple[np.ndarray, np.ndarray]:
    column = TARGETS[target][0]
    rows = model.rows  # type: ignore[attr-defined]
    grouped: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        grouped.setdefault(training_context_key(row), []).append(index)
    normalized = model.x_train  # type: ignore[attr-defined]
    x_rows: list[np.ndarray] = []
    y_rows: list[float] = []
    for indexes in grouped.values():
        values = np.asarray(
            [float(rows[index]["outputs"][column]) for index in indexes],
            dtype=np.float64,
        )
        normalized_x = normalized[indexes].mean(axis=0)
        raw_x = (
            normalized_x * model.feature_scale  # type: ignore[attr-defined]
            + model.feature_mean  # type: ignore[attr-defined]
        )
        x_rows.append(raw_x)
        y_rows.append(float(np.mean(values)))
    return np.vstack(x_rows), np.asarray(y_rows, dtype=np.float64)


def _parameters(seed: int) -> dict[str, object]:
    return {
        "objective": "regression_l2",
        "metric": "l2",
        "learning_rate": 0.03,
        "num_leaves": 15,
        "max_depth": -1,
        "min_data_in_leaf": 8,
        "min_sum_hessian_in_leaf": 1e-3,
        "feature_fraction": 0.85,
        "bagging_fraction": 0.85,
        "bagging_freq": 1,
        "lambda_l1": 0.05,
        "lambda_l2": 1.0,
        "min_gain_to_split": 1e-4,
        "max_bin": 127,
        "verbosity": -1,
        "deterministic": True,
        "force_col_wise": True,
        "num_threads": 1,
        "seed": seed,
        "feature_fraction_seed": seed,
        "bagging_seed": seed,
        "data_random_seed": seed,
    }


def _fit(
    x: np.ndarray,
    y: np.ndarray,
    *,
    seed: int = 20260723,
) -> tuple[lgb.Booster, float, TargetQualityMetric, dict[str, object]]:
    folds = min(5, len(y))
    if folds < 2:
        raise ValueError("LightGBM training requires at least two parent conditions")
    fold_ids = np.arange(len(y)) % folds
    oof = np.empty_like(y)
    for fold in range(folds):
        test = fold_ids == fold
        train = ~test
        booster = lgb.train(
            _parameters(seed + fold),
            lgb.Dataset(x[train], label=y[train], free_raw_data=False),
            num_boost_round=NUM_BOOST_ROUND,
            callbacks=[lgb.log_evaluation(0)],
        )
        oof[test] = booster.predict(x[test], num_iteration=NUM_BOOST_ROUND)
    final = lgb.train(
        _parameters(seed),
        lgb.Dataset(x, label=y),
        num_boost_round=NUM_BOOST_ROUND,
        callbacks=[lgb.log_evaluation(0)],
    )
    residuals = y - oof
    residual_std = max(
        float(np.sqrt(np.mean(residuals * residuals))),
        float(np.std(y)) * 0.05,
        1e-6,
    )
    z90 = 1.6448536269514722
    covered = np.zeros(len(y), dtype=bool)
    for fold in range(folds):
        evaluate = fold_ids == fold
        calibrate = ~evaluate
        calibration_std = max(
            float(np.sqrt(np.mean(residuals[calibrate] ** 2))),
            float(np.std(y[calibrate])) * 0.05,
            1e-6,
        )
        covered[evaluate] = (
            np.abs(residuals[evaluate]) <= z90 * calibration_std
        )
    metric = TargetQualityMetric(
        target="placeholder",
        parent_conditions=len(y),
        mae=float(np.mean(np.abs(residuals))),
        rmse=float(np.sqrt(np.mean(residuals * residuals))),
        interval_coverage_90=float(np.mean(covered)),
        interval_coverage_method="cross-fitted-oof-normal-scale",
        interval_coverage_observations=len(y),
    )
    diagnostics = {
        "folds": folds,
        "num_boost_round": NUM_BOOST_ROUND,
        "residual_std": residual_std,
        "calibration": "cross-fitted_grouped_parent_condition_oof",
        "parameters": _parameters(seed),
    }
    return final, residual_std, metric, diagnostics


def _build(source: Path, destination: Path, package_id: str) -> None:
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
        json.dumps(
            {
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
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    files = [pipeline_path]
    predictors: list[dict[str, object]] = []
    quality_metrics: list[TargetQualityMetric] = []
    training_counts: dict[str, int] = {}
    training_diagnostics: dict[str, object] = {}
    boosters: dict[str, lgb.Booster] = {}
    for target, model in sorted(runtime.models.items()):
        x, y = _grouped_training(model, target)
        booster, residual_std, metric, diagnostics = _fit(x, y)
        metric = metric.model_copy(update={"target": target})
        path = folders["model-artifacts"] / f"{target}.txt"
        booster.save_model(str(path))
        files.append(path)
        boosters[target] = booster
        training_counts[target] = len(y)
        training_diagnostics[target] = diagnostics
        quality_metrics.append(metric)
        predictors.append(
            {
                "id": f"{target.lower()}-lightgbm",
                "target": target,
                "unit": model.unit,
                "target_kind": "continuous_positive"
                if target == "lambda"
                else "continuous",
                "runtime_type": "lightgbm.booster.v1",
                "architecture_id": "lightgbm_regression_standard_v1",
                "artifact": path.relative_to(destination).as_posix(),
                "predictive_family": "normal",
                "feature_names": list(FEATURE_NAMES),
                "config": {
                    "training_unit": "parent_condition_mean",
                    "residual_std": residual_std,
                    "uncertainty_calibration": "grouped_parent_condition_oof",
                    "parameter_policy": "regularized_small_data_v1",
                    "num_boost_round": diagnostics["num_boost_round"],
                },
            }
        )

    stats_path = folders["reference"] / "training_stats.json"
    stats_path.write_text(
        json.dumps(
            {
                "records": training_counts,
                "source_sha256": data.source_sha256,
                "composition_defaults": data.medians,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    files.append(stats_path)
    quality_path = folders["reports"] / "quality-report.json"
    quality_path.write_text(
        QualityReport(
            schema_version="model-quality-report/v1",
            split="grouped-parent-condition-k-fold",
            folds=5,
            targets=tuple(quality_metrics),
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    files.append(quality_path)
    diagnostics_path = folders["reports"] / "training-diagnostics.json"
    diagnostics_path.write_text(
        json.dumps(
            {
                "schema_version": "lightgbm-training-diagnostics/v1",
                "training_policy": "regularized-small-data-grouped-cv-v1",
                "note": "Synthetic demo data; diagnostics describe numerical fitting, not scientific validity.",
                "targets": training_diagnostics,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
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
        json.dumps(smoke_input, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    smoke_values = runtime.vector_for_candidate(
        CandidateInput.model_validate(smoke_input)
    )
    smoke_expected_path = folders["smoke"] / "expected.json"
    smoke_expected_path.write_text(
        json.dumps(
            {
                target: round(
                    float(booster.predict(smoke_values.reshape(1, -1))[0]), 8
                )
                for target, booster in boosters.items()
            },
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    files.extend([smoke_input_path, smoke_expected_path])

    contract = load_task_contracts()[TASK_ID]
    canonical_dataset = canonical_training_dataset(TASK_ID, data, contract)
    manifest = {
        "schema_version": "model-package/v1",
        "package_id": package_id,
        "package_version": PACKAGE_VERSION,
        "task_id": TASK_ID,
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "input_contract_digest": task_input_contract_digest(contract.task_definition),
        "runtime_capability_digest": runtime_capability_digest(
            contract.runtime_capability
        ),
        "feature_pipeline": {
            "id": FEATURE_PIPELINE_ID,
            "version": FEATURE_PIPELINE_VERSION,
            "spec": pipeline_path.relative_to(destination).as_posix(),
            "canonical_input_paths": list(CANONICAL_INPUT_PATHS),
            "output_features": list(FEATURE_NAMES),
            "artifacts": [stats_path.relative_to(destination).as_posix()],
        },
        "predictors": predictors,
        "provenance": {
            "training_data_id": f"sha256:{data.source_sha256}",
            "feature_dataset_id": canonical_training_dataset_digest(
                canonical_dataset
            ),
            "training_code_revision": TRAINING_CODE_REVISION,
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
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )


def build(
    source: Path,
    destination: Path,
    *,
    replace: bool = False,
    package_id: str = PACKAGE_ID,
) -> None:
    with staged_package_destination(destination, replace=replace) as staging:
        _build(source, staging, package_id)
        verify_model_package(staging, task_id=TASK_ID, source=source)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("data/source/material_workbench_tutorial_v2.xlsx"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "artifacts/model-package-candidates/annealed-lightgbm-standard-tutorial-v2"
        ),
    )
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--package-id", default=PACKAGE_ID)
    arguments = parser.parse_args()
    build(
        arguments.source,
        arguments.output,
        replace=arguments.replace,
        package_id=arguments.package_id,
    )
