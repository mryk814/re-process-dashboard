from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from decision_workbench.contracts.feature_recipe_contracts import (
    FeatureRecipe,
    FeatureRecipeState,
)
from decision_workbench.modeling.training.feature_recipe import (
    fit_feature_recipe,
    transform_feature_recipe,
)
from decision_workbench.modeling.training.validation_plan import (
    ValidationPlan,
    ValidationRowRole,
    build_validation_assignment,
    grouped_kfold_plan,
    temporal_role_rows,
)


@dataclass(frozen=True)
class TargetTrainingSet:
    target: str
    unit: str
    target_kind: str
    feature_names: tuple[str, ...]
    x: np.ndarray
    y: np.ndarray
    replicate_contexts: tuple[str, ...]
    validation_groups: tuple[str, ...]
    observation_ids: tuple[tuple[str, ...], ...]
    repeat_counts: tuple[int, ...]
    within_context_sse: np.ndarray
    within_context_df: np.ndarray
    observation_variance: float
    cohort_digest: str
    fold_assignments: tuple[tuple[str, int], ...]
    fold_ids: np.ndarray
    fold_digest: str
    folds: int
    validation_plan: ValidationPlan
    validation_plan_digest: str
    validation_diagnostics: dict[str, Any]
    imputed_feature_indices: tuple[int, ...] = ()
    final_imputation_values: tuple[tuple[int, float], ...] = ()
    feature_recipe: FeatureRecipe | None = None
    feature_recipe_state: FeatureRecipeState | None = None
    recipe_rows: tuple[Mapping[str, Any], ...] = ()

    @property
    def raw_observation_count(self) -> int:
        """Number of source observations before replicate aggregation."""

        return sum(self.repeat_counts)

    @property
    def effective_replicate_context_count(self) -> int:
        """Number of rows actually presented to the estimator."""

        return len(self.y)

    @property
    def is_temporal_validation(self) -> bool:
        return self.validation_plan.strategy in {
            "temporal_holdout",
            "grouped_temporal",
        }

    @property
    def quality_rows(self) -> np.ndarray:
        if self.is_temporal_validation:
            return temporal_role_rows(
                self.fold_ids,
                ValidationRowRole.EVALUATION,
            )
        return np.ones(len(self.y), dtype=bool)

    def training_rows_for_fold(self, fold: int) -> np.ndarray:
        if self.is_temporal_validation:
            return temporal_role_rows(
                self.fold_ids,
                ValidationRowRole.MODEL_TRAIN,
            )
        return self.fold_ids != fold

    @property
    def temporal_calibration_rows(self) -> np.ndarray:
        return temporal_role_rows(
            self.fold_ids,
            ValidationRowRole.CALIBRATION,
        )


def _replicate_context(row: Mapping[str, Any]) -> str:
    value = row.get("condition_context_id") or row.get("observation_id")
    if value is None or not str(value).strip():
        raise ValueError("canonical training row has no replicate context")
    return str(value)


def _semantic_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _fold_order(group: str, *, seed: int) -> bytes:
    return hashlib.sha256(f"{seed}:{group}".encode("utf-8")).digest()


def _evaluation_identity(
    *,
    target: str,
    replicate_contexts: Sequence[str],
    validation_groups: Sequence[str],
    observation_ids: Sequence[Sequence[str]],
    requested_folds: int,
    seed: int,
) -> tuple[str, tuple[tuple[str, int], ...], np.ndarray, str, int]:
    unique_groups = sorted(
        set(validation_groups),
        key=lambda group: (_fold_order(group, seed=seed), group),
    )
    if requested_folds > len(unique_groups):
        raise ValueError(
            f"{target}: requested {requested_folds} folds but only "
            f"{len(unique_groups)} independent validation groups are available; "
            "fold count is never reduced implicitly"
        )
    folds = requested_folds
    if folds < 2:
        raise ValueError(
            f"{target}: at least two independent validation groups are required"
        )
    assignments = tuple(
        (group, index % folds)
        for index, group in enumerate(unique_groups)
    )
    assignment_by_group = dict(assignments)
    fold_ids = np.asarray(
        [assignment_by_group[group] for group in validation_groups],
        dtype=int,
    )
    cohort_digest = _semantic_digest({
        "target": target,
        "rows": [
            {
                "replicate_context": context,
                "validation_group": group,
                "observation_ids": list(ids),
            }
            for context, group, ids in zip(
                replicate_contexts,
                validation_groups,
                observation_ids,
                strict=True,
            )
        ],
    })
    fold_digest = _semantic_digest({
        "target": target,
        "folds": folds,
        "seed": seed,
        "assignments": [
            {"validation_group": group, "fold": fold}
            for group, fold in assignments
        ],
    })
    fold_ids.setflags(write=False)
    return cohort_digest, assignments, fold_ids, fold_digest, folds


def compile_target_training_set(
    canonical_dataset: Mapping[str, Any],
    *,
    target: str,
    unit: str,
    target_kind: str = "continuous",
    folds: int = 5,
    seed: int = 20260730,
    validation_plan: ValidationPlan | None = None,
    feature_recipe: FeatureRecipe | None = None,
    feature_recipe_state: FeatureRecipeState | None = None,
) -> TargetTrainingSet:
    if (feature_recipe is None) != (feature_recipe_state is None):
        raise ValueError("feature recipe and final state must be supplied together")
    feature_names = (
        tuple(item.name for item in feature_recipe.features)
        if feature_recipe is not None
        else tuple(
            str(item["name"])
            for item in canonical_dataset["feature_pipeline"]["features"]
        )
    )
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in canonical_dataset["rows"]:
        if target in row["outputs"]:
            grouped.setdefault(_replicate_context(row), []).append(row)
    if len(grouped) < 3:
        raise ValueError(f"{target}: at least three training contexts are required")

    x_rows: list[np.ndarray] = []
    y_rows: list[float] = []
    replicate_contexts: list[str] = []
    validation_groups: list[str] = []
    observation_ids: list[tuple[str, ...]] = []
    repeat_counts: list[int] = []
    within_context_sse: list[float] = []
    within_context_df: list[int] = []
    validation_times: list[float] = []
    recipe_rows: list[Mapping[str, Any]] = []
    within_sse = 0.0
    within_df = 0
    for replicate_context, rows in sorted(grouped.items()):
        parent_keys = {
            str(row["parent_key"])
            for row in rows
            if str(row.get("parent_key", "")).strip()
        }
        if len(parent_keys) != 1:
            raise ValueError(
                f"{target}/{replicate_context}: replicate context must belong "
                "to one validation group"
            )
        if feature_recipe is not None:
            raw_rows = [row.get("canonical_inputs") for row in rows]
            if any(not isinstance(item, Mapping) for item in raw_rows):
                raise ValueError(
                    f"{target}/{replicate_context}: recipe requires canonical_inputs"
                )
            normalized = [
                {
                    path: item.get(path)
                    for path in feature_recipe.canonical_input_paths
                }
                for item in raw_rows
                if isinstance(item, Mapping)
            ]
            if any(item != normalized[0] for item in normalized[1:]):
                raise ValueError(
                    f"{target}/{replicate_context}: one replicate context has "
                    "different canonical inputs"
                )
            recipe_rows.append(normalized[0])
        else:
            feature_rows = np.asarray(
                [
                    [float(row["features"][name]) for name in feature_names]
                    for row in rows
                ],
                dtype=float,
            )
            if np.isinf(feature_rows).any():
                raise ValueError(
                    f"{target}/{replicate_context}: features must not be infinite"
                )
            if not np.allclose(
                feature_rows,
                feature_rows[0],
                rtol=1e-10,
                atol=1e-12,
                equal_nan=True,
            ):
                raise ValueError(
                    f"{target}/{replicate_context}: one replicate context has "
                    "different feature rows"
                )
            x_rows.append(feature_rows[0])
        values = np.asarray(
            [float(row["outputs"][target]) for row in rows],
            dtype=float,
        )
        if not np.isfinite(values).all():
            raise ValueError(
                f"{target}/{replicate_context}: outputs must be finite"
            )
        y_rows.append(float(values.mean()))
        replicate_contexts.append(replicate_context)
        validation_groups.append(next(iter(parent_keys)))
        observation_ids.append(tuple(str(row["observation_id"]) for row in rows))
        repeat_counts.append(len(values))
        if len(values) > 1:
            context_sse = float(np.sum((values - values.mean()) ** 2))
            context_df = len(values) - 1
            within_sse += context_sse
            within_df += context_df
        else:
            context_sse = 0.0
            context_df = 0
        within_context_sse.append(context_sse)
        within_context_df.append(context_df)
        if validation_plan is not None and validation_plan.time_key is not None:
            time_values = {
                float(row["features"][validation_plan.time_key])
                for row in rows
                if validation_plan.time_key in row["features"]
            }
            if len(time_values) != 1:
                raise ValueError(
                    f"{target}/{replicate_context}: time role "
                    f"{validation_plan.time_key} must resolve to one value"
                )
            validation_times.append(next(iter(time_values)))

    y = np.asarray(y_rows, dtype=float)
    fallback = max(float(np.var(y)) * 0.1, 1e-8)
    observation_variance = (
        max(within_sse / within_df, 1e-8)
        if within_df
        else fallback
    )
    plan = validation_plan or grouped_kfold_plan(folds=folds, seed=seed)
    cohort_digest = _semantic_digest({
        "target": target,
        "rows": [
            {
                "replicate_context": context,
                "validation_group": group,
                "observation_ids": list(ids),
            }
            for context, group, ids in zip(
                replicate_contexts,
                validation_groups,
                observation_ids,
                strict=True,
            )
        ],
    })
    if validation_plan is None:
        (
            _,
            fold_assignments,
            fold_ids,
            fold_digest,
            resolved_folds,
        ) = _evaluation_identity(
            target=target,
            replicate_contexts=replicate_contexts,
            validation_groups=validation_groups,
            observation_ids=observation_ids,
            requested_folds=folds,
            seed=seed,
        )
        plan = grouped_kfold_plan(folds=resolved_folds, seed=seed)
        assignment = build_validation_assignment(
            target=target,
            keys=validation_groups,
            labels=y,
            plan=plan,
        )
    else:
        keys = (
            validation_groups
            if plan.group_key == "parent_key"
            else replicate_contexts
            if plan.group_key == "replicate_context"
            else [ids[0] for ids in observation_ids]
        )
        assignment = build_validation_assignment(
            target=target,
            keys=keys,
            labels=y,
            plan=plan,
            times=validation_times or None,
        )
        fold_assignments = assignment.fold_assignments
        fold_ids = assignment.fold_ids
        resolved_folds = assignment.folds
        fold_digest = _semantic_digest({
            "target": target,
            "validation_plan_digest": assignment.plan_digest,
            "assignments": [
                {"validation_key": key, "fold": fold}
                for key, fold in fold_assignments
            ],
        })
    if target_kind == "binary":
        if plan.strategy in {"temporal_holdout", "grouped_temporal"}:
            binary_cohorts = {
                "training": temporal_role_rows(
                    fold_ids, ValidationRowRole.MODEL_TRAIN
                ),
                "calibration": temporal_role_rows(
                    fold_ids, ValidationRowRole.CALIBRATION
                ),
                "holdout": temporal_role_rows(
                    fold_ids, ValidationRowRole.EVALUATION
                ),
            }
            for cohort_name, rows in binary_cohorts.items():
                if set(np.unique(y[rows])) != {0.0, 1.0}:
                    raise ValueError(
                        f"{target}: temporal binary {cohort_name} cohort "
                        "must contain both classes"
                    )
        else:
            for fold in range(resolved_folds):
                if set(np.unique(y[fold_ids != fold])) != {0.0, 1.0}:
                    raise ValueError(
                        f"{target}: binary training fold {fold} "
                        "must contain both classes"
                    )
    if feature_recipe is not None:
        assert feature_recipe_state is not None
        x = transform_feature_recipe(
            feature_recipe,
            feature_recipe_state,
            recipe_rows,
        )
    else:
        x = np.vstack(x_rows)
    imputed_feature_indices = tuple(
        int(index)
        for index in np.flatnonzero(np.isnan(x).any(axis=0))
    )
    fitted_final_imputation_values = tuple(
        (
            index,
            float(np.nanmedian(x[:, index])),
        )
        for index in imputed_feature_indices
        if not np.isnan(x[:, index]).all()
    )
    if len(fitted_final_imputation_values) != len(imputed_feature_indices):
        raise ValueError(f"{target}: imputed feature has no observed training values")
    declared_imputation = canonical_dataset["feature_pipeline"].get(
        "missing_policy",
        {},
    ).get("imputation_values", {})
    final_imputation_values = tuple(
        (
            index,
            float(
                declared_imputation.get(
                    feature_names[index],
                    dict(fitted_final_imputation_values)[index],
                )
            ),
        )
        for index in imputed_feature_indices
    )
    return TargetTrainingSet(
        target=target,
        unit=unit,
        target_kind=target_kind,
        feature_names=feature_names,
        x=x,
        y=y,
        replicate_contexts=tuple(replicate_contexts),
        validation_groups=tuple(validation_groups),
        observation_ids=tuple(observation_ids),
        repeat_counts=tuple(repeat_counts),
        within_context_sse=np.asarray(within_context_sse, dtype=float),
        within_context_df=np.asarray(within_context_df, dtype=int),
        observation_variance=observation_variance,
        cohort_digest=cohort_digest,
        fold_assignments=fold_assignments,
        fold_ids=fold_ids,
        fold_digest=fold_digest,
        folds=resolved_folds,
        validation_plan=plan,
        validation_plan_digest=assignment.plan_digest,
        validation_diagnostics=assignment.diagnostics,
        imputed_feature_indices=imputed_feature_indices,
        final_imputation_values=final_imputation_values,
        feature_recipe=feature_recipe,
        feature_recipe_state=feature_recipe_state,
        recipe_rows=tuple(recipe_rows),
    )


def prepared_feature_matrix(
    data: TargetTrainingSet,
    *,
    fit_rows: np.ndarray | None = None,
    transform_rows: np.ndarray | None = None,
) -> np.ndarray:
    """Apply training-only medians to one selected matrix without fold leakage."""

    fit = (
        np.ones(len(data.x), dtype=bool)
        if fit_rows is None
        else np.asarray(fit_rows, dtype=bool)
    )
    transform = (
        np.ones(len(data.x), dtype=bool)
        if transform_rows is None
        else np.asarray(transform_rows, dtype=bool)
    )
    if data.feature_recipe is not None:
        fitted_state = data.feature_recipe_state
        if fit_rows is not None:
            fitted_state = fit_feature_recipe(
                data.feature_recipe,
                [
                    row
                    for row, included in zip(data.recipe_rows, fit, strict=True)
                    if included
                ],
            )
        assert fitted_state is not None
        return transform_feature_recipe(
            data.feature_recipe,
            fitted_state,
            [
                row
                for row, included in zip(data.recipe_rows, transform, strict=True)
                if included
            ],
        )
    values = np.asarray(data.x[transform], dtype=float).copy()
    final_values = dict(data.final_imputation_values)
    for index in data.imputed_feature_indices:
        if fit_rows is None:
            replacement = final_values[index]
        else:
            observed = data.x[fit, index]
            observed = observed[np.isfinite(observed)]
            if not len(observed):
                raise ValueError(
                    f"{data.target}/{data.feature_names[index]}: "
                    "fold has no observed values for training median"
                )
            replacement = float(np.median(observed))
        values[np.isnan(values[:, index]), index] = replacement
    if not np.isfinite(values).all():
        raise ValueError(f"{data.target}: prepared features must be finite")
    return values


def observation_variance_for_rows(
    data: TargetTrainingSet,
    rows: np.ndarray,
) -> float:
    """Estimate repeat noise from only the rows available to one fit."""

    selected_y = data.y[rows]
    if len(selected_y) < 2:
        raise ValueError(f"{data.target}: noise estimation needs at least two rows")
    degrees = int(np.sum(data.within_context_df[rows]))
    if degrees:
        return max(
            float(np.sum(data.within_context_sse[rows])) / degrees,
            1e-8,
        )
    return max(float(np.var(selected_y)) * 0.1, 1e-8)


def feature_vector(
    feature_names: Sequence[str],
    row: Mapping[str, Any],
) -> np.ndarray:
    values = np.asarray([float(row[name]) for name in feature_names], dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("smoke feature row must be finite")
    return values
