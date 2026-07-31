"""Train safe, code-free model packages for profile-driven CSV tasks."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

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
from material_workbench.modeling.model_packages import FEATURE_DATASET_DIGEST_FLOAT15
from material_workbench.modeling.model_package_verify import verify_model_package
from material_workbench.modeling.model_packages import (
    SourceLifecycleProvenance,
    validate_task_definition_canonical_inputs,
)
from material_workbench.modeling.tabular.data import TabularData, load_tabular_data
from material_workbench.modeling.tabular.features import (
    build_tabular_features,
    build_tabular_features_from_observation,
    candidate_from_observation,
    feature_definitions,
)
from material_workbench.tasks.task_registry import load_task_contracts


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tabular_training_code_revision(model_family: str) -> str:
    """Return the versioned trainer identity persisted in Package provenance."""

    if model_family == "lightgbm_binary":
        return "tabular-lightgbm-binary-crossfit-v2"
    if model_family == "lightgbm_monotone":
        return "tabular-lightgbm-monotone-fixed-round-v2"
    return "tabular-ridge-crossfit-coverage-v2"


def _value_digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _artifact(root: Path, path: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _digest(path),
        "bytes": path.stat().st_size,
    }


def _validated_training_contract(
    data: TabularData,
    rows: list[dict[str, Any]],
    training_contract: dict[str, object],
) -> dict[str, object]:
    """Recompute every externally supplied training identity before persisting it."""

    lifecycle_profile = data.lifecycle_profile
    if lifecycle_profile is None:
        raise ValueError("Training contract requires the effective lifecycle Profile")
    actual_profile_digest = dataset_profile_digest(lifecycle_profile)
    actual_transform_digest = getattr(lifecycle_profile, "transform_digest", None)
    actual_folds = getattr(lifecycle_profile, "folds", None)
    if not isinstance(actual_transform_digest, str):
        raise ValueError("Training contract requires a Profile transform digest")
    if not isinstance(actual_folds, int) or actual_folds < 2:
        raise ValueError("Training contract requires a valid Profile fold count")

    output_keys = tuple(output.key for output in data.profile.outputs)
    actual_cohorts: dict[str, str] = {}
    actual_assignments: dict[str, dict[str, int]] = {}
    actual_fold_digests: dict[str, str] = {}
    actual_missing: dict[str, int] = {}
    for output_key in output_keys:
        target_rows = [row for row in rows if output_key in row["outputs"]]
        cohort = [
            (str(row["id"]), str(row["parent_key"]))
            for row in target_rows
        ]
        groups = sorted({parent_key for _, parent_key in cohort})
        if len(groups) < actual_folds:
            raise ValueError(
                f"{output_key}: {actual_folds} grouped folds require at least "
                f"{actual_folds} non-empty groups"
            )
        assignment = {
            group: index % actual_folds
            for index, group in enumerate(groups)
        }
        actual_cohorts[output_key] = _value_digest(cohort)
        actual_assignments[output_key] = assignment
        actual_fold_digests[output_key] = _value_digest(assignment)
        actual_missing[output_key] = len(rows) - len(target_rows)

    expected = {
        "profile_digest": actual_profile_digest,
        "transform_digest": actual_transform_digest,
        "cohort_digests": actual_cohorts,
        "fold_digests": actual_fold_digests,
        "fold_assignments": actual_assignments,
        "folds": actual_folds,
        "missing_by_target": actual_missing,
    }
    for key, actual in expected.items():
        if training_contract.get(key) != actual:
            raise ValueError(f"Training contract {key} does not match compiled data")
    if set(training_contract) != set(expected):
        raise ValueError("Training contract contains unsupported or missing fields")
    return expected


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


def _grouped_oof(
    x: np.ndarray,
    y: np.ndarray,
    groups: list[str],
    ridge: float = 1.0,
    *,
    folds: int = 5,
    assignment: dict[str, int] | None = None,
) -> tuple[np.ndarray, np.ndarray, int]:
    unique = sorted(set(groups))
    if any(not group for group in groups):
        raise ValueError("Grouped validation requires a non-empty group for every row")
    if assignment is None:
        folds = min(folds, len(unique))
        assignment = {group: index % folds for index, group in enumerate(unique)}
    elif set(assignment) != set(unique):
        raise ValueError("Fold assignment groups do not match the target cohort")
    fold_values = sorted(set(assignment.values()))
    if fold_values != list(range(len(fold_values))):
        raise ValueError("Fold assignment IDs must be contiguous from zero")
    folds = len(fold_values)
    if folds < 2:
        raise ValueError("At least two independent groups are required")
    fold_ids = np.asarray([assignment[group] for group in groups])
    residuals = np.empty(len(y))
    for fold in range(folds):
        test = fold_ids == fold
        residuals[test] = y[test] - _predict(x[test], _fit(x[~test], y[~test], ridge))
    return residuals, fold_ids, folds


def _cross_fitted_quantile_coverage(
    residuals: np.ndarray,
    fold_ids: np.ndarray,
) -> float:
    covered = np.zeros(len(residuals), dtype=bool)
    for calibrate, evaluate in _cross_fit_masks(fold_ids):
        lower, upper = np.quantile(residuals[calibrate], (0.05, 0.95))
        covered[evaluate] = (
            (residuals[evaluate] >= lower) & (residuals[evaluate] <= upper)
        )
    return float(np.mean(covered))


def _cross_fitted_normal_coverage(
    residuals: np.ndarray,
    fold_ids: np.ndarray,
) -> float:
    z90 = 1.6448536269514722
    covered = np.zeros(len(residuals), dtype=bool)
    for calibrate, evaluate in _cross_fit_masks(fold_ids):
        residual_std = max(
            float(np.sqrt(np.mean(residuals[calibrate] ** 2))),
            1e-6,
        )
        covered[evaluate] = np.abs(residuals[evaluate]) <= z90 * residual_std
    return float(np.mean(covered))


def _cross_fit_masks(fold_ids: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
    masks = []
    for fold in np.unique(fold_ids):
        evaluate = fold_ids == fold
        masks.append((~evaluate, evaluate))
    return masks


def _lightgbm_parameters(monotone_constraints: list[int], seed: int) -> dict[str, object]:
    return {
        "objective": "regression_l2",
        "metric": "l2",
        "learning_rate": 0.035,
        "num_leaves": 15,
        "max_depth": 6,
        "min_data_in_leaf": 30,
        "feature_fraction": 0.9,
        "bagging_fraction": 0.9,
        "bagging_freq": 1,
        "lambda_l1": 0.05,
        "lambda_l2": 1.0,
        "verbosity": -1,
        "deterministic": True,
        "force_col_wise": True,
        "num_threads": 1,
        "seed": seed,
        "feature_fraction_seed": seed,
        "bagging_seed": seed,
        "data_random_seed": seed,
        "monotone_constraints": monotone_constraints,
        "monotone_constraints_method": "advanced",
    }


def _lightgbm_grouped_fit(
    x: np.ndarray,
    y: np.ndarray,
    groups: list[str],
    monotone_constraints: list[int],
    num_boost_round: int,
) -> tuple[object, np.ndarray, np.ndarray, int]:
    import lightgbm as lgb

    unique = sorted(set(groups))
    folds = min(5, len(unique))
    if folds < 2:
        raise ValueError("At least two independent groups are required")
    assignment = {group: index % folds for index, group in enumerate(unique)}
    fold_ids = np.asarray([assignment[group] for group in groups])
    oof = np.empty(len(y))
    for fold in range(folds):
        test = fold_ids == fold
        train = ~test
        booster = lgb.train(
            _lightgbm_parameters(monotone_constraints, 20260724 + fold),
            lgb.Dataset(x[train], label=y[train], free_raw_data=False),
            num_boost_round=num_boost_round,
            callbacks=[lgb.log_evaluation(0)],
        )
        oof[test] = booster.predict(x[test], num_iteration=num_boost_round)
    final = lgb.train(
        _lightgbm_parameters(monotone_constraints, 20260724),
        lgb.Dataset(x, label=y),
        num_boost_round=num_boost_round,
        callbacks=[lgb.log_evaluation(0)],
    )
    return final, y - oof, fold_ids, folds


def _binary_parameters(seed: int) -> dict[str, object]:
    return {
        "objective": "binary",
        "metric": "binary_logloss",
        "learning_rate": 0.03,
        "num_leaves": 15,
        "max_depth": 5,
        "min_data_in_leaf": 25,
        "feature_fraction": 0.85,
        "bagging_fraction": 0.9,
        "bagging_freq": 1,
        "lambda_l1": 0.1,
        "lambda_l2": 2.0,
        "verbosity": -1,
        "deterministic": True,
        "force_col_wise": True,
        "num_threads": 1,
        "seed": seed,
        "feature_fraction_seed": seed,
        "bagging_seed": seed,
        "data_random_seed": seed,
    }


def _stratified_folds(y: np.ndarray, folds: int) -> np.ndarray:
    assignment = np.empty(len(y), dtype=int)
    for label in (0.0, 1.0):
        indexes = np.flatnonzero(y == label)
        assignment[indexes] = np.arange(len(indexes)) % folds
    return assignment


def _fit_platt(probabilities: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    clipped = np.clip(probabilities, 1e-7, 1 - 1e-7)
    logits = np.log(clipped / (1 - clipped))
    design = np.column_stack((np.ones(len(y)), logits))
    weights = np.asarray([0.0, 1.0])
    penalty = np.diag([1e-6, 1e-3])
    for _ in range(30):
        fitted = 1 / (1 + np.exp(-np.clip(design @ weights, -30, 30)))
        curvature = np.maximum(fitted * (1 - fitted), 1e-8)
        hessian = design.T @ (curvature[:, None] * design) + penalty
        gradient = design.T @ (fitted - y) + penalty @ weights
        step = np.linalg.solve(hessian, gradient)
        weights -= step
        if float(np.max(np.abs(step))) < 1e-8:
            break
    return float(weights[0]), float(weights[1])


def _calibrate_binary(
    probabilities: np.ndarray,
    calibration: tuple[float, float],
) -> np.ndarray:
    intercept, slope = calibration
    clipped = np.clip(probabilities, 1e-7, 1 - 1e-7)
    logits = np.log(clipped / (1 - clipped))
    return 1 / (1 + np.exp(-np.clip(intercept + slope * logits, -30, 30)))


def _lightgbm_binary_fit(
    x: np.ndarray,
    y: np.ndarray,
    num_boost_round: int,
) -> tuple[object, np.ndarray, int, tuple[float, float]]:
    import lightgbm as lgb

    if set(np.unique(y)) != {0.0, 1.0}:
        raise ValueError("binary LightGBM target must contain both 0 and 1")
    folds = 5
    assignment = _stratified_folds(y, folds)
    oof = np.empty(len(y))
    for fold in range(folds):
        test = assignment == fold
        train = ~test
        booster = lgb.train(
            _binary_parameters(20260725 + fold),
            lgb.Dataset(x[train], label=y[train], free_raw_data=False),
            num_boost_round=num_boost_round,
            callbacks=[lgb.log_evaluation(0)],
        )
        oof[test] = booster.predict(x[test], num_iteration=num_boost_round)
    calibration = _fit_platt(oof, y)
    calibrated_oof = np.empty(len(y))
    for calibrate, evaluate in _cross_fit_masks(assignment):
        calibrator = _fit_platt(oof[calibrate], y[calibrate])
        calibrated_oof[evaluate] = _calibrate_binary(oof[evaluate], calibrator)
    final = lgb.train(
        _binary_parameters(20260725),
        lgb.Dataset(x, label=y),
        num_boost_round=num_boost_round,
        callbacks=[lgb.log_evaluation(0)],
    )
    return final, calibrated_oof, folds, calibration


def _binary_auc(y: np.ndarray, probabilities: np.ndarray) -> float:
    positives = y == 1
    negatives = ~positives
    order = np.argsort(probabilities, kind="stable")
    ranks = np.empty(len(y), dtype=float)
    ranks[order] = np.arange(1, len(y) + 1)
    positive_ranks = float(ranks[positives].sum())
    positive_count = int(positives.sum())
    negative_count = int(negatives.sum())
    return (
        positive_ranks - positive_count * (positive_count + 1) / 2
    ) / (positive_count * negative_count)


def build_tabular_package_from_data(
    data: TabularData,
    profile_path: Path,
    destination: Path,
    *,
    training_contract: dict[str, object] | None = None,
    package_id: str | None = None,
    package_version: str = "1.0.0",
    source_lifecycle: SourceLifecycleProvenance | None = None,
) -> None:
    profile = data.profile
    contract = load_task_contracts()[profile.task_id]
    rows = [row for row in data.observations if row["eligible"]]
    if not rows:
        raise ValueError("Curation後に学習可能な行がありません")
    if source_lifecycle is not None and (
        source_lifecycle.materialized_training_sha256 != data.source_sha256
        or source_lifecycle.row_count != data.row_count
    ):
        raise ValueError(
            "Source Lifecycle materialization does not match loaded tabular data"
        )
    if data.profile.group_column and any(not str(row["parent_key"]).strip() for row in rows):
        raise ValueError("Grouped training rows require a non-empty parent group")
    if training_contract is not None:
        training_contract = _validated_training_contract(
            data,
            rows,
            training_contract,
        )
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
    if profile.curation_recipe is not None:
        curation_path = reference_dir / "curation-recipe.json"
        curation_path.write_text(
            profile.curation_recipe.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        files.append(curation_path)
    predictors: list[dict[str, object]] = []
    metrics: list[TargetQualityMetric] = []
    classification_metrics: dict[str, dict[str, float | int]] = {}
    records: dict[str, int] = {}
    predict_by_target: dict[str, Callable[[np.ndarray], np.ndarray]] = {}
    used_fold_counts: list[int] = []
    for output in profile.outputs:
        target_rows = [row for row in rows if output.key in row["outputs"]]
        if not target_rows:
            raise ValueError(f"{output.key}の学習可能な行がありません")
        target_bundles = [
            build_tabular_features_from_observation(row, data.medians, profile)
            for row in target_rows
        ]
        x = np.vstack([bundle.values for bundle in target_bundles])
        groups = [str(row["parent_key"]) for row in target_rows]
        y = np.asarray([float(row["outputs"][output.key]) for row in target_rows])
        coverage_method = None
        coverage_observations = None
        if profile.model_family == "lightgbm_binary":
            assert profile.num_boost_round is not None
            fitted, oof_probability, folds, calibration = _lightgbm_binary_fit(
                x, y, profile.num_boost_round
            )
            residuals = y - oof_probability
            artifact_path = artifact_dir / f"{output.key}.txt"
            fitted.save_model(str(artifact_path))
            predict_by_target[output.key] = (
                lambda values, model=fitted, calibrator=calibration:
                _calibrate_binary(
                    np.asarray(model.predict(values), dtype=float),
                    calibrator,
                )
            )
            predictor = {
                "id": f"{output.key}-lightgbm",
                "target": output.key,
                "unit": output.unit,
                "target_kind": "binary",
                "runtime_type": "lightgbm.booster.v1",
                "architecture_id": "lightgbm_binary_calibrated_v1",
                "artifact": artifact_path.relative_to(destination).as_posix(),
                "predictive_family": "bernoulli_logit",
                "feature_names": list(feature_names),
                "config": {
                    "training_unit": "independent source row",
                    "validation": f"{folds}-fold stratified",
                    "source_profile": profile.profile_id,
                    "num_boost_round": profile.num_boost_round,
                    "calibration": {
                        "method": "out-of-fold Platt scaling",
                        "quality_evaluation": "cross-fitted out-of-fold Platt scaling",
                        "intercept": calibration[0],
                        "slope": calibration[1],
                    },
                },
            }
            predicted_class = oof_probability >= 0.5
            positives = y == 1
            negatives = ~positives
            classification_metrics[output.key] = {
                "records": len(y),
                "positive_records": int(positives.sum()),
                "negative_records": int(negatives.sum()),
                "roc_auc": _binary_auc(y, oof_probability),
                "brier_score": float(np.mean((oof_probability - y) ** 2)),
                "balanced_accuracy": float(
                    (
                        np.mean(predicted_class[positives])
                        + np.mean(~predicted_class[negatives])
                    ) / 2
                ),
            }
            lower, upper = -1.0, 1.0
        elif profile.model_family == "lightgbm_monotone":
            assert profile.num_boost_round is not None
            monotone_constraints = [
                -1 if name in profile.monotone_decreasing_paths else 0
                for name in feature_names
            ]
            fitted, residuals, fold_ids, folds = _lightgbm_grouped_fit(
                x,
                y,
                groups,
                monotone_constraints,
                profile.num_boost_round,
            )
            residual_std = max(float(np.sqrt(np.mean(residuals ** 2))), 1e-6)
            artifact_path = artifact_dir / f"{output.key}.txt"
            fitted.save_model(str(artifact_path))
            predict_by_target[output.key] = (
                lambda values, model=fitted: np.asarray(model.predict(values), dtype=float)
            )
            predictor = {
                "id": f"{output.key}-lightgbm",
                "target": output.key,
                "unit": output.unit,
                "target_kind": "continuous",
                "runtime_type": "lightgbm.booster.v1",
                "architecture_id": "lightgbm_monotone_regression_v1",
                "artifact": artifact_path.relative_to(destination).as_posix(),
                "predictive_family": "normal",
                "feature_names": list(feature_names),
                "config": {
                    "training_unit": "source_row_grouped_by_parent",
                    "validation": f"{folds}-fold grouped by {profile.group_column}",
                    "source_profile": profile.profile_id,
                    "num_boost_round": profile.num_boost_round,
                    "residual_std": residual_std,
                    "monotone_decreasing_paths": list(profile.monotone_decreasing_paths),
                },
            }
            z90 = 1.6448536269514722
            lower, upper = -z90 * residual_std, z90 * residual_std
            coverage = _cross_fitted_normal_coverage(residuals, fold_ids)
            coverage_method = "cross-fitted-oof-normal-scale"
            coverage_observations = len(residuals)
        else:
            contract_assignment = None
            requested_folds = 5
            if training_contract is not None:
                requested_folds = int(training_contract.get("folds", 5))
                assignments = training_contract.get("fold_assignments")
                if isinstance(assignments, dict):
                    selected_assignment = assignments.get(output.key)
                    if isinstance(selected_assignment, dict):
                        contract_assignment = {
                            str(group): int(fold)
                            for group, fold in selected_assignment.items()
                        }
                        expected_digest = training_contract["fold_digests"][output.key]  # type: ignore[index]
                        if _value_digest(contract_assignment) != expected_digest:
                            raise ValueError(
                                f"{output.key}: fold assignment digest does not match"
                            )
            residuals, fold_ids, folds = _grouped_oof(
                x,
                y,
                groups,
                profile.ridge_alpha,
                folds=requested_folds,
                assignment=contract_assignment,
            )
            fitted = _fit(x, y, profile.ridge_alpha)
            predict_by_target[output.key] = (
                lambda values, model=fitted: _predict(values, model)
            )
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
            predictor = {
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
                    "ridge_alpha": profile.ridge_alpha,
                    **(
                        {
                            "profile_digest": training_contract["profile_digest"],
                            "transform_digest": training_contract["transform_digest"],
                            "cohort_digest": training_contract["cohort_digests"][output.key],
                            "fold_digest": training_contract["fold_digests"][output.key],
                        }
                        if training_contract is not None
                        else {}
                    ),
                },
            }
            coverage = _cross_fitted_quantile_coverage(residuals, fold_ids)
            coverage_method = "cross-fitted-oof-residual-quantiles"
            coverage_observations = len(residuals)
        used_fold_counts.append(folds)
        files.append(artifact_path)
        records[output.key] = len(y)
        metrics.append(TargetQualityMetric(
            target=output.key,
            parent_conditions=len(set(groups)),
            mae=float(np.mean(np.abs(residuals))),
            rmse=float(np.sqrt(np.mean(residuals ** 2))),
            interval_coverage_90=(
                0.0
                if profile.model_family == "lightgbm_binary"
                else coverage
            ),
            interval_coverage_method=coverage_method,
            interval_coverage_observations=coverage_observations,
        ))
        predictors.append(predictor)

    stats_path = reference_dir / "training_stats.json"
    stats_path.write_text(json.dumps({
        "records": records,
        "groups": len({str(row["parent_key"]) for row in rows}),
        "rows": len(rows),
        "source_sha256": data.source_sha256,
        "curation": {
            status: sum(
                row["run_context"].get("curation", {}).get("status") == status
                for row in data.observations
            )
            for status in ("accepted", "warning", "quarantined", "blocked")
        },
        **({"training_contract": training_contract} if training_contract is not None else {}),
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
        folds=min(used_fold_counts),
        targets=tuple(metrics),
    ).model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    files.append(quality_path)
    if classification_metrics:
        classification_path = report_dir / "classification-diagnostics.json"
        classification_path.write_text(json.dumps({
            "schema_version": "classification-diagnostics/v1",
            "split": "stratified-k-fold",
            "calibration": "cross-fitted out-of-fold Platt scaling for quality evaluation",
            "targets": classification_metrics,
            "notes": [
                "The model uses 12 sensors selected for stability across stratified training folds.",
                "This fixed-field CV follows selection and is optimistic; use docs/reports/secom-sensor-selection.json for the nested selection estimate.",
                "SECOM feature names are anonymous; sensor importance is not a causal interpretation.",
            ],
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        files.append(classification_path)

    complete_rows = [
        row for row in rows
        if all(output.key in row["outputs"] for output in profile.outputs)
    ]
    sample_row = (complete_rows or rows)[len(complete_rows or rows) // 2]
    sample = candidate_from_observation(sample_row, profile).model_copy(
        update={"name": f"{profile.name} package smoke"}
    )
    smoke_input = smoke_dir / "input.json"
    smoke_input.write_text(sample.model_dump_json(indent=2), encoding="utf-8", newline="\n")
    sample_x = build_tabular_features(sample, profile).values.reshape(1, -1)
    smoke_expected = smoke_dir / "expected.json"
    smoke_expected.write_text(json.dumps({
        target: round(float(predict(sample_x)[0]), 8)
        for target, predict in predict_by_target.items()
    }, indent=2), encoding="utf-8", newline="\n")
    files.extend((smoke_input, smoke_expected))

    canonical = canonical_training_dataset(profile.task_id, data, contract)
    manifest = {
        "schema_version": "model-package/v1",
        "package_id": package_id or profile.package_id,
        "package_version": package_version,
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
            "feature_dataset_id": canonical_training_dataset_digest(
                canonical,
                algorithm=FEATURE_DATASET_DIGEST_FLOAT15,
            ),
            "feature_dataset_digest_algorithm": FEATURE_DATASET_DIGEST_FLOAT15,
            "training_code_revision": tabular_training_code_revision(
                profile.model_family
            ),
            "dataset_profile_id": (
                str(training_contract["profile_digest"])
                if training_contract is not None
                else dataset_profile_digest(profile)
            ),
            **(
                {
                    "source_lifecycle": source_lifecycle.model_dump(
                        mode="json"
                    )
                }
                if source_lifecycle is not None
                else {}
            ),
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
    package_id: str | None = None,
    package_version: str = "1.0.0",
    source_lifecycle: SourceLifecycleProvenance | None = None,
) -> None:
    with staged_package_destination(destination, replace=replace) as staging:
        build_tabular_package_from_data(
            load_tabular_data(source, profile_path),
            profile_path,
            staging,
            package_id=package_id,
            package_version=package_version,
            source_lifecycle=source_lifecycle,
        )
        verify_model_package(
            staging,
            task_id=load_tabular_profile(profile_path).task_id,
            source=source,
            profile=profile_path,
        )


# Kept local to avoid exporting profile parsing through the package builder API.
from material_workbench.modeling.tabular.profile import load_tabular_profile  # noqa: E402
