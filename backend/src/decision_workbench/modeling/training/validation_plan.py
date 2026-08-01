from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Annotated, Any, Literal, Sequence

import numpy as np
from pydantic import Field, model_validator

from decision_workbench.contracts.task_contracts import ContractModel


ValidationStrategy = Literal[
    "kfold",
    "grouped_kfold",
    "stratified_kfold",
    "stratified_grouped_kfold",
    "temporal_holdout",
    "grouped_temporal",
]


class ValidationPlan(ContractModel):
    """Allow-listed split meaning recorded by a standard training recipe."""

    schema_version: Literal["validation-plan/v1"] = "validation-plan/v1"
    strategy: ValidationStrategy
    folds: Annotated[int, Field(ge=2, le=20)] | None = None
    holdout_fraction: Annotated[float, Field(gt=0, lt=0.5)] | None = None
    seed: Annotated[int, Field(ge=0, le=2**32 - 1)] = 20260730
    group_key: Literal["parent_key", "replicate_context", "observation_id"] | None = None
    time_key: Annotated[str, Field(min_length=1)] | None = None
    gap: Annotated[int, Field(ge=0)] = 0
    minimum_train_size: Annotated[int, Field(ge=2)] = 2
    class_balance_policy: Literal["require_each_training_fold"] | None = None

    @model_validator(mode="after")
    def strategy_fields_match(self) -> "ValidationPlan":
        kfold = self.strategy.endswith("kfold")
        temporal = self.strategy in {"temporal_holdout", "grouped_temporal"}
        grouped = self.strategy in {
            "grouped_kfold",
            "stratified_grouped_kfold",
            "grouped_temporal",
        }
        stratified = self.strategy in {
            "stratified_kfold",
            "stratified_grouped_kfold",
        }
        if kfold != (self.folds is not None):
            raise ValueError("k-fold plans require folds; temporal plans must omit folds")
        if temporal != (self.holdout_fraction is not None):
            raise ValueError(
                "temporal plans require holdout_fraction; k-fold plans must omit it"
            )
        if grouped != (self.group_key is not None):
            raise ValueError(
                "grouped plans require group_key; non-grouped plans must omit it"
            )
        if temporal != (self.time_key is not None):
            raise ValueError(
                "temporal plans require time_key; non-temporal plans must omit it"
            )
        if stratified != (self.class_balance_policy is not None):
            raise ValueError(
                "stratified plans require class_balance_policy; other plans must omit it"
            )
        if not temporal and (self.gap != 0 or self.minimum_train_size != 2):
            raise ValueError("gap and minimum_train_size belong to temporal plans")
        return self


def grouped_kfold_plan(*, folds: int, seed: int) -> ValidationPlan:
    return ValidationPlan(
        strategy="grouped_kfold",
        folds=folds,
        seed=seed,
        group_key="parent_key",
    )


def semantic_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _order(value: str, *, seed: int) -> bytes:
    return hashlib.sha256(f"{seed}:{value}".encode("utf-8")).digest()


@dataclass(frozen=True)
class ValidationAssignment:
    fold_assignments: tuple[tuple[str, int], ...]
    fold_ids: np.ndarray
    folds: int
    plan_digest: str
    diagnostics: dict[str, Any]


def _require_binary(target: str, labels: np.ndarray) -> None:
    if set(np.unique(labels)) != {0.0, 1.0}:
        raise ValueError(f"{target}: stratified validation requires binary labels 0 and 1")


def _assert_training_classes(
    target: str,
    labels: np.ndarray,
    fold_ids: np.ndarray,
    folds: int,
) -> None:
    for fold in range(folds):
        if set(np.unique(labels[fold_ids != fold])) != {0.0, 1.0}:
            raise ValueError(
                f"{target}: every stratified training fold must contain both classes"
            )


def _kfold_assignment(
    *,
    target: str,
    keys: Sequence[str],
    labels: np.ndarray,
    plan: ValidationPlan,
) -> tuple[tuple[tuple[str, int], ...], np.ndarray, int]:
    assert plan.folds is not None
    if plan.strategy in {"kfold", "stratified_kfold"}:
        assignment_keys = list(keys)
        if len(set(assignment_keys)) != len(assignment_keys):
            raise ValueError(f"{target}: row validation keys must be unique")
        if plan.strategy == "stratified_kfold":
            _require_binary(target, labels)
            by_class = {
                label: sorted(
                    (key for key, value in zip(keys, labels, strict=True) if value == label),
                    key=lambda key: (_order(key, seed=plan.seed), key),
                )
                for label in (0.0, 1.0)
            }
            folds = plan.folds
            if min(map(len, by_class.values())) < folds:
                raise ValueError(
                    f"{target}: each class needs at least {folds} rows for stratified k-fold"
                )
            assignments = tuple(
                sorted(
                    (
                        (key, index % folds)
                        for class_keys in by_class.values()
                        for index, key in enumerate(class_keys)
                    ),
                )
            )
        else:
            if len(assignment_keys) < plan.folds:
                raise ValueError(
                    f"{target}: requested {plan.folds} folds but only "
                    f"{len(assignment_keys)} rows are available"
                )
            folds = plan.folds
            assignments = tuple(
                (key, index % folds)
                for index, key in enumerate(
                    sorted(
                        assignment_keys,
                        key=lambda key: (_order(key, seed=plan.seed), key),
                    )
                )
            )
    else:
        unique_groups = sorted(
            set(keys),
            key=lambda key: (_order(key, seed=plan.seed), key),
        )
        if len(unique_groups) < plan.folds:
            raise ValueError(
                f"{target}: requested {plan.folds} folds but only "
                f"{len(unique_groups)} validation groups are available"
            )
        folds = plan.folds
        if plan.strategy == "stratified_grouped_kfold":
            _require_binary(target, labels)
            grouped_labels: dict[str, list[float]] = {}
            for key, label in zip(keys, labels, strict=True):
                grouped_labels.setdefault(key, []).append(float(label))
            ordered = sorted(
                unique_groups,
                key=lambda key: (
                    -abs(float(np.mean(grouped_labels[key])) - 0.5),
                    _order(key, seed=plan.seed),
                    key,
                ),
            )
            counts = np.zeros((folds, 2), dtype=int)
            sizes = np.zeros(folds, dtype=int)
            mutable: list[tuple[str, int]] = []
            for key in ordered:
                values = grouped_labels[key]
                class_counts = np.bincount(
                    np.asarray(values, dtype=int),
                    minlength=2,
                )
                fold = min(
                    range(folds),
                    key=lambda candidate: (
                        abs(
                            (counts[candidate, 1] + class_counts[1])
                            - (counts[candidate, 0] + class_counts[0])
                        ),
                        sizes[candidate],
                        candidate,
                    ),
                )
                counts[fold] += class_counts
                sizes[fold] += len(values)
                mutable.append((key, fold))
            assignments = tuple(sorted(mutable))
        else:
            assignments = tuple(
                (key, index % folds)
                for index, key in enumerate(unique_groups)
            )
    if folds < 2:
        raise ValueError(f"{target}: at least two validation folds are required")
    by_key = dict(assignments)
    fold_ids = np.asarray([by_key[key] for key in keys], dtype=int)
    if plan.strategy in {"stratified_kfold", "stratified_grouped_kfold"}:
        _assert_training_classes(target, labels, fold_ids, folds)
    return assignments, fold_ids, folds


def _temporal_assignment(
    *,
    target: str,
    keys: Sequence[str],
    times: Sequence[float] | None,
    plan: ValidationPlan,
) -> tuple[tuple[tuple[str, int], ...], np.ndarray, int]:
    if times is None or len(times) != len(keys):
        raise ValueError(f"{target}: temporal validation requires one explicit time per row")
    numeric_times = np.asarray(times, dtype=float)
    if not np.isfinite(numeric_times).all():
        raise ValueError(f"{target}: temporal validation times must be finite")
    assert plan.holdout_fraction is not None
    ordered = sorted(
        range(len(keys)),
        key=lambda index: (numeric_times[index], keys[index]),
    )
    holdout_rows = max(1, int(np.ceil(len(keys) * plan.holdout_fraction)))
    split_at = len(keys) - holdout_rows
    train_end = split_at - plan.gap
    calibration_rows = max(1, int(np.ceil(train_end * plan.holdout_fraction)))
    model_train_end = train_end - calibration_rows
    if model_train_end < plan.minimum_train_size:
        raise ValueError(
            f"{target}: temporal plan has {model_train_end} training rows; "
            f"minimum_train_size is {plan.minimum_train_size}"
        )
    held_out = set(ordered[split_at:])
    if plan.strategy == "grouped_temporal":
        train_groups = {keys[index] for index in ordered[:train_end]}
        held_out_groups = {keys[index] for index in held_out}
        overlap = train_groups & held_out_groups
        if overlap:
            raise ValueError(
                f"{target}: grouped temporal holdout crosses groups: "
                + ", ".join(sorted(overlap))
            )
    fold_ids = np.full(len(keys), -2, dtype=int)
    fold_ids[ordered[:model_train_end]] = -1
    fold_ids[ordered[model_train_end:train_end]] = -3
    fold_ids[list(held_out)] = 0
    assignments = tuple(
        (keys[index], int(fold_ids[index]))
        for index in range(len(keys))
    )
    return assignments, fold_ids, 1


def build_validation_assignment(
    *,
    target: str,
    keys: Sequence[str],
    labels: np.ndarray,
    plan: ValidationPlan,
    times: Sequence[float] | None = None,
) -> ValidationAssignment:
    if len(keys) != len(labels):
        raise ValueError(f"{target}: validation keys and labels have different lengths")
    if plan.strategy in {"temporal_holdout", "grouped_temporal"}:
        assignments, fold_ids, folds = _temporal_assignment(
            target=target,
            keys=keys,
            times=times,
            plan=plan,
        )
    else:
        assignments, fold_ids, folds = _kfold_assignment(
            target=target,
            keys=keys,
            labels=labels,
            plan=plan,
        )
    fold_ids.setflags(write=False)
    binary_labels = set(np.unique(labels)) == {0.0, 1.0}
    fold_rows = [
        {
            "fold": fold,
            "evaluation_rows": int(np.sum(fold_ids == fold)),
            "training_rows": int(
                np.sum(fold_ids != fold)
                if plan.strategy not in {"temporal_holdout", "grouped_temporal"}
                else np.sum(fold_ids == -1)
            ),
            "calibration_rows": int(np.sum(fold_ids == -3))
            if plan.strategy in {"temporal_holdout", "grouped_temporal"}
            else None,
            "class_ratio": (
                float(np.mean(labels[fold_ids == fold]))
                if binary_labels and np.any(fold_ids == fold)
                else None
            ),
        }
        for fold in range(folds)
    ]
    time_ranges = None
    if times is not None:
        numeric_times = np.asarray(times, dtype=float)
        time_ranges = {
            name: {
                "min": float(np.min(numeric_times[rows])),
                "max": float(np.max(numeric_times[rows])),
            }
            for name, rows in {
                "training": fold_ids == -1,
                "calibration": fold_ids == -3,
                "holdout": fold_ids == 0,
            }.items()
            if np.any(rows)
        }
    return ValidationAssignment(
        fold_assignments=assignments,
        fold_ids=fold_ids,
        folds=folds,
        plan_digest=semantic_digest(plan.model_dump(mode="json")),
        diagnostics={
            "schema_version": "validation-diagnostics/v1",
            "strategy": plan.strategy,
            "folds": fold_rows,
            "group_overlap": False
            if plan.group_key is not None
            else None,
            "temporal_order_verified": plan.time_key is not None,
            "time_ranges": time_ranges,
            "validation_groups": len(set(keys))
            if plan.group_key is not None
            else None,
            "preprocessing": {
                "fit_scope": "training_fold_only",
                "policy": (
                    "all data-derived preprocessing and calibration are fit "
                    "without evaluation rows"
                ),
                "not_applicable": [
                    "imputation:canonical_features_require_finite_values",
                    "feature_selection:feature_pipeline_is_fixed_before_training",
                ],
                "final_model_fit": "full_target_cohort_after_honest_evaluation",
            },
        },
    )
