"""Fixed offline Design Prior replay on the bundled MPEA room-tensile Task.

This is promotion evidence, not a production generator registry.  It keeps the
active Model Package as the predictive authority and uses a group-held-out
slice of the public historical observations as the realized-outcome oracle.
"""
from __future__ import annotations

import csv
import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from types import SimpleNamespace
from typing import Any, Literal

import numpy as np
from scipy.stats import norm, rankdata

from decision_workbench.application.personal_task_packages import (
    build_standard_package,
)
from decision_workbench.contracts.candidate_project_contracts import CandidateInputs
from decision_workbench.contracts.task_contracts import (
    persisted_task_definition_payload,
)
from decision_workbench.domain.proposal_generation import (
    _latin_hypercube_unit,
    _sobol_unit,
)
from decision_workbench.modeling.tabular.data import load_tabular_data
from decision_workbench.modeling.tabular.profile import load_tabular_profile
from decision_workbench.modeling.tabular.runtime import TabularRegressionRuntime
from decision_workbench.modeling.packages.loader import ModelPackageLoader
from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.tasks.task_registry import load_task_contracts


ROOT = Path(__file__).resolve().parents[4]
TASK_ID = "mpea-room-tensile-v1"
TARGET = "TYS"
SOURCE = ROOT / "data/source/external/mpea_ground_truth_18021833.csv"
PROFILE = (
    ROOT
    / "backend/src/decision_workbench/data/"
    "tabular-profile-mpea-room-tensile-v1.json"
)
ACTIVE_PACKAGE = ROOT / "models/packages/mpea-room-tensile-ridge-v2"
SCHEMA_VERSION = "real-task-design-prior-replay/v1"
PROTOCOL_VERSION = "mpea-room-tensile-design-prior-replay/v1"
GENERATORS = (
    "latin_hypercube",
    "sobol",
    "empirical_rows",
    "knn_local",
    "gaussian_rank_copula",
)
POLICIES = ("direct_objective", "conservative_diverse")
SEEDS = (17, 41, 83)
KNN_ALPHA_RANGE = (0.05, 0.55)
SUPPORT_PENALTY = {"supported": 0.0, "caution": 250.0, "extrapolated": 750.0}


def _digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _file_digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _generator_parameters(generator: str, *, seed: int, budget: int) -> dict[str, Any]:
    common = {"generator_id": generator, "version": "1.0.0", "seed": seed, "budget": budget}
    if generator in {"latin_hypercube", "sobol"}:
        parameters = {
            "unit_sampler": generator,
            "composition_semantics": "declared-balance-remainder",
            "category_policy": "empirical-joint-tuple",
        }
    elif generator == "empirical_rows":
        parameters = {"selection": "uniform-with-replacement"}
    elif generator == "knn_local":
        parameters = {
            "anchor": "uniform-observation-in-category-mode-with-at-least-two-rows",
            "neighbor": "nearest-same-category-tuple",
            "alpha_range": KNN_ALPHA_RANGE,
            "singleton_mode_policy": "unavailable-for-knn",
        }
    elif generator == "gaussian_rank_copula":
        parameters = {
            "category_policy": "empirical-joint-tuple-conditioned",
            "minimum_mode_rows": 3,
            "sparse_mode_policy": "unavailable-for-copula",
            "rank_method": "average",
            "correlation_shrinkage": 0.9,
            "quantile_method": "linear",
        }
    else:
        raise ValueError(f"unknown generator: {generator}")
    return {**common, "parameters": parameters}


@dataclass(frozen=True)
class ReplayData:
    numeric_paths: tuple[str, ...]
    categorical_paths: tuple[str, ...]
    lower: np.ndarray
    span: np.ndarray
    train_numeric: np.ndarray
    train_categories: tuple[tuple[str, ...], ...]
    holdout_numeric: np.ndarray
    holdout_categories: tuple[tuple[str, ...], ...]
    holdout_outcomes: np.ndarray
    train_group_count: int
    holdout_group_count: int
    train_groups: tuple[str, ...]
    holdout_groups: tuple[str, ...]
    source_digest: str
    profile_digest: str


def _value(row: dict[str, Any], path: str) -> float | str:
    group, key = path.split(".", 1)
    if group == "composition":
        return row["composition"][key]
    if group == "process":
        return row["features"][key]
    return row["categorical"][key]


def _held_out(group: str) -> bool:
    return int(hashlib.sha256(group.encode("utf-8")).hexdigest()[:8], 16) % 5 == 0


def load_replay_data() -> ReplayData:
    profile = load_tabular_profile(PROFILE)
    data = load_tabular_data(SOURCE, profile, profile_locator=PROFILE)
    eligible = [
        row
        for row in data.observations
        if row["eligible"] and TARGET in row["outputs"]
    ]
    numeric_paths = tuple(item.path for item in profile.inputs if item.kind == "number")
    categorical_paths = tuple(
        item.path for item in profile.inputs if item.kind == "categorical"
    )

    numeric = np.asarray(
        [[float(_value(row, path)) for path in numeric_paths] for row in eligible],
        dtype=float,
    )
    categories = tuple(
        tuple(str(_value(row, path)) for path in categorical_paths)
        for row in eligible
    )
    groups = tuple(str(row["parent_key"]) for row in eligible)
    holdout_mask = np.asarray([_held_out(group) for group in groups], dtype=bool)
    if not holdout_mask.any() or holdout_mask.all():
        raise ValueError("fixed group holdout did not produce both train and holdout rows")
    train_raw = numeric[~holdout_mask]
    lower = train_raw.min(axis=0)
    span = train_raw.max(axis=0) - lower
    span[span == 0] = 1.0
    normalized = (numeric - lower) / span
    return ReplayData(
        numeric_paths=numeric_paths,
        categorical_paths=categorical_paths,
        lower=lower,
        span=span,
        train_numeric=normalized[~holdout_mask],
        train_categories=tuple(
            category
            for category, held_out in zip(categories, holdout_mask, strict=True)
            if not held_out
        ),
        holdout_numeric=normalized[holdout_mask],
        holdout_categories=tuple(
            category
            for category, held_out in zip(categories, holdout_mask, strict=True)
            if held_out
        ),
        holdout_outcomes=np.asarray(
            [
                float(row["outputs"][TARGET])
                for row, held_out in zip(eligible, holdout_mask, strict=True)
                if held_out
            ]
        ),
        train_group_count=len(
            {group for group, held_out in zip(groups, holdout_mask, strict=True) if not held_out}
        ),
        holdout_group_count=len(
            {group for group, held_out in zip(groups, holdout_mask, strict=True) if held_out}
        ),
        train_groups=tuple(
            group
            for group, held_out in zip(groups, holdout_mask, strict=True)
            if not held_out
        ),
        holdout_groups=tuple(
            group
            for group, held_out in zip(groups, holdout_mask, strict=True)
            if held_out
        ),
        source_digest=f"sha256:{data.source_sha256}",
        profile_digest=_file_digest(PROFILE),
    )


@contextmanager
def _replay_runtime() -> Any:
    """Build the fixed train-only Package used by predictive replay.

    The checked-in active Package is trained on the whole public source and is
    therefore not valid holdout evidence.  This bounded temporary Package uses
    the same allow-listed ridge estimator, feature contract, and alpha in the
    standard Package builder, while excluding fixed holdout groups from both
    fitting and support reference construction.
    """

    with TemporaryDirectory(prefix="mpea-design-prior-replay-") as directory:
        root = Path(directory)
        train_source = root / "mpea-room-tensile-train.csv"
        with SOURCE.open("r", encoding="utf-8-sig", newline="") as stream:
            preamble = stream.readline()
            reader = csv.DictReader(stream)
            rows = [
                row
                for row in reader
                if not _held_out(str(row["File_Name"]))
            ]
            fieldnames = reader.fieldnames
        if not fieldnames:
            raise ValueError("MPEA replay source header is unavailable")
        with train_source.open("w", encoding="utf-8", newline="") as stream:
            stream.write(preamble)
            writer = csv.DictWriter(
                stream,
                fieldnames=fieldnames,
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(rows)
        package = root / "model-package"
        build_standard_package(
            TASK_ID,
            train_source,
            package,
            root / "canonical-training-dataset.json",
            package_id="mpea-room-tensile-replay-ridge-v1",
            package_version="1.0.0",
            replace=False,
            estimator="ridge.v1",
            estimator_options={"alpha": 1000.0},
            profile=PROFILE,
        )
        verified = ModelPackageLoader().load(package)
        train_data = load_tabular_data(
            train_source,
            PROFILE,
            profile_locator=PROFILE,
        )
        yield (
            TabularRegressionRuntime(
                train_data,
                verified,
                missing_policy_inputs=train_data.profile.inputs,
            ),
            verified.manifest.model_dump(mode="json"),
            verified.manifest_sha256,
            _file_digest(train_source),
        )


def _category_sample(
    categories: tuple[tuple[str, ...], ...],
    *,
    budget: int,
    rng: np.random.Generator,
) -> tuple[tuple[str, ...], ...]:
    indexes = rng.integers(0, len(categories), size=budget)
    return tuple(categories[int(index)] for index in indexes)


def _generate(
    data: ReplayData,
    generator: str,
    *,
    budget: int,
    seed: int,
) -> tuple[np.ndarray, tuple[tuple[str, ...], ...], dict[str, Any]]:
    rng = np.random.default_rng(seed)
    dimensions = len(data.numeric_paths)
    if generator in {"latin_hypercube", "sobol"}:
        points = (
            _latin_hypercube_unit(budget, dimensions, seed)
            if generator == "latin_hypercube"
            else _sobol_unit(budget, dimensions, seed)
        )
        # Existing LHS/Sobol Proposal semantics resolve the declared balance
        # component after sampling the other dimensions.  Preserve that
        # baseline contract instead of evaluating a deliberately broken box
        # sampler against composition-aware priors.
        raw = data.lower + points * data.span
        balance_index = data.numeric_paths.index("composition.Fe")
        component_indexes = [
            index
            for index, path in enumerate(data.numeric_paths)
            if path.startswith("composition.") and index != balance_index
        ]
        raw[:, balance_index] = 100.0 - raw[:, component_indexes].sum(axis=1)
        points[:, balance_index] = (
            raw[:, balance_index] - data.lower[balance_index]
        ) / data.span[balance_index]
        categories = _category_sample(data.train_categories, budget=budget, rng=rng)
        operation = {
            "training_required": False,
            "artifact_bytes": 0,
            "composition_semantics": "declared-balance-remainder",
        }
    elif generator == "empirical_rows":
        indexes = rng.integers(0, len(data.train_numeric), size=budget)
        points = data.train_numeric[indexes].copy()
        categories = tuple(data.train_categories[int(index)] for index in indexes)
        operation = {"training_required": False, "artifact_bytes": 0}
    elif generator == "knn_local":
        points = np.empty((budget, dimensions), dtype=float)
        sampled_categories: list[tuple[str, ...]] = []
        train_categories = np.asarray(data.train_categories, dtype=object)
        mode_counts = {
            mode: data.train_categories.count(mode)
            for mode in set(data.train_categories)
        }
        eligible_anchors = np.asarray(
            [
                index
                for index, mode in enumerate(data.train_categories)
                if mode_counts[mode] >= 2
            ],
            dtype=int,
        )
        if not len(eligible_anchors):
            raise ValueError("kNN unavailable: no category mode has at least two rows")
        for output_index in range(budget):
            anchor = int(eligible_anchors[int(rng.integers(len(eligible_anchors)))])
            same_mode = np.flatnonzero(
                np.all(train_categories == train_categories[anchor], axis=1)
            )
            neighbors = same_mode[same_mode != anchor]
            distances = np.linalg.norm(
                data.train_numeric[neighbors] - data.train_numeric[anchor],
                axis=1,
            )
            neighbor = int(neighbors[np.argmin(distances)])
            alpha = float(rng.uniform(*KNN_ALPHA_RANGE))
            points[output_index] = (
                (1 - alpha) * data.train_numeric[anchor]
                + alpha * data.train_numeric[neighbor]
            )
            sampled_categories.append(data.train_categories[anchor])
        categories = tuple(sampled_categories)
        operation = {
            "training_required": False,
            "artifact_bytes": 0,
            "excluded_singleton_category_modes": [
                list(mode)
                for mode, count in sorted(mode_counts.items())
                if count < 2
            ],
        }
    elif generator == "gaussian_rank_copula":
        mode_counts = {
            mode: data.train_categories.count(mode)
            for mode in set(data.train_categories)
        }
        eligible_modes = tuple(
            mode for mode, count in sorted(mode_counts.items()) if count >= 3
        )
        eligible_rows = tuple(
            mode for mode in data.train_categories if mode in eligible_modes
        )
        if not eligible_rows:
            raise ValueError("copula unavailable: no category mode has at least three rows")
        categories = _category_sample(eligible_rows, budget=budget, rng=rng)
        points = np.empty((budget, dimensions), dtype=float)
        train_categories = np.asarray(data.train_categories, dtype=object)
        for mode in sorted(set(categories)):
            output_indexes = [index for index, item in enumerate(categories) if item == mode]
            mode_mask = np.all(train_categories == np.asarray(mode, dtype=object), axis=1)
            mode_rows = data.train_numeric[mode_mask]
            uniforms = np.column_stack(
                [
                    rankdata(mode_rows[:, axis], method="average") / (len(mode_rows) + 1)
                    for axis in range(dimensions)
                ]
            )
            gaussian = norm.ppf(np.clip(uniforms, 1e-6, 1 - 1e-6))
            with np.errstate(divide="ignore", invalid="ignore"):
                correlation = np.corrcoef(gaussian, rowvar=False)
            correlation = np.nan_to_num(correlation, nan=0.0)
            correlation = 0.9 * correlation + 0.1 * np.eye(dimensions)
            eigenvalues, eigenvectors = np.linalg.eigh(correlation)
            correlation = eigenvectors @ np.diag(np.maximum(eigenvalues, 1e-6)) @ eigenvectors.T
            latent = rng.multivariate_normal(
                np.zeros(dimensions),
                correlation,
                size=len(output_indexes),
            )
            quantiles = norm.cdf(latent)
            for axis in range(dimensions):
                points[output_indexes, axis] = np.quantile(
                    mode_rows[:, axis],
                    quantiles[:, axis],
                    method="linear",
                )
        operation = {
            "training_required": True,
            "artifact_bytes": dimensions * dimensions * 8,
            "excluded_sparse_category_modes": [
                list(mode)
                for mode, count in sorted(mode_counts.items())
                if count < 3
            ],
        }
    else:
        raise ValueError(f"unknown generator: {generator}")
    return points, categories, operation


def _candidate(
    data: ReplayData,
    point: np.ndarray,
    category: tuple[str, ...],
    *,
    index: int,
) -> SimpleNamespace:
    values = data.lower + point * data.span
    composition: dict[str, float] = {}
    process: dict[str, float] = {}
    for path, value in zip(data.numeric_paths, values, strict=True):
        group, key = path.split(".", 1)
        (composition if group == "composition" else process)[key] = float(value)
    categorical = {
        path.split(".", 1)[1]: value
        for path, value in zip(data.categorical_paths, category, strict=True)
    }
    return SimpleNamespace(
        id=f"replay-{index}",
        inputs=CandidateInputs(
            composition=composition,
            process=process,
            categorical=categorical,
        ),
    )


def _hard_feasible(data: ReplayData, point: np.ndarray) -> bool:
    raw = data.lower + point * data.span
    in_design_space = bool(np.all(point >= 0) and np.all(point <= 1))
    composition_total = sum(
        float(value)
        for path, value in zip(data.numeric_paths, raw, strict=True)
        if path.startswith("composition.")
    )
    return in_design_space and 99.0 <= composition_total <= 101.0


def _mixed_distances(
    left_numeric: np.ndarray,
    left_categories: tuple[tuple[str, ...], ...],
    right_numeric: np.ndarray,
    right_categories: tuple[tuple[str, ...], ...],
) -> np.ndarray:
    numeric = np.mean((left_numeric[:, None, :] - right_numeric[None, :, :]) ** 2, axis=2)
    left_cat = np.asarray(left_categories, dtype=object)
    right_cat = np.asarray(right_categories, dtype=object)
    categorical = np.mean(
        left_cat[:, None, :] != right_cat[None, :, :],
        axis=2,
    )
    return np.sqrt((numeric + categorical) / 2)


def _evaluate_pool(
    data: ReplayData,
    runtime: Any,
    generator: str,
    policy: Literal["direct_objective", "conservative_diverse"],
    *,
    budget: int,
    batch_size: int,
    seed: int,
) -> dict[str, Any]:
    started = perf_counter()
    points, categories, operation = _generate(
        data,
        generator,
        budget=budget,
        seed=seed,
    )
    feasible = np.asarray([_hard_feasible(data, point) for point in points])
    feasible_indexes = np.flatnonzero(feasible)
    predictions: list[float] = []
    support_statuses: list[str] = []
    for index in feasible_indexes:
        prediction = runtime.predict(
            _candidate(data, points[index], categories[index], index=int(index))
        )
        predictions.append(float(prediction["predictions"][TARGET].value))
        support_statuses.append(str(prediction["support"].status))
    distances_to_train = _mixed_distances(
        points,
        categories,
        data.train_numeric,
        data.train_categories,
    ).min(axis=1)
    holdout_distances = _mixed_distances(
        points,
        categories,
        data.holdout_numeric,
        data.holdout_categories,
    )
    nearest_holdout = holdout_distances.argmin(axis=1)
    realized = data.holdout_outcomes[nearest_holdout]
    score = np.asarray(predictions, dtype=float)
    if policy == "conservative_diverse":
        score = score - np.asarray(
            [SUPPORT_PENALTY.get(status, 1000.0) for status in support_statuses]
        )
        score = score - 150.0 * distances_to_train[feasible_indexes]
    ranked = feasible_indexes[np.argsort(score, kind="stable")[::-1]]
    selected: list[int] = []
    for index in ranked:
        if len(selected) >= batch_size:
            break
        if policy == "conservative_diverse" and selected:
            diversity = _mixed_distances(
                points[[index]],
                (categories[index],),
                points[selected],
                tuple(categories[item] for item in selected),
            ).min()
            if diversity < 0.02:
                continue
        selected.append(int(index))
    selected_predictions = np.asarray(
        [predictions[list(feasible_indexes).index(index)] for index in selected]
    )
    selected_realized = realized[selected]
    selected_support = [
        support_statuses[list(feasible_indexes).index(index)] for index in selected
    ]
    pairwise = (
        _mixed_distances(
            points[selected],
            tuple(categories[index] for index in selected),
            points[selected],
            tuple(categories[index] for index in selected),
        )[np.triu_indices(len(selected), 1)]
        if len(selected) > 1
        else np.asarray([])
    )
    category_modes = len(set(categories))
    training_category_modes = len(set(data.train_categories))
    selected_modes = len({categories[index] for index in selected})
    return {
        "generator_id": generator,
        "selection_policy": policy,
        "identity": {
            "generator_parameters": _generator_parameters(
                generator,
                seed=seed,
                budget=budget,
            ),
            "generator_parameter_digest": _digest(
                _generator_parameters(generator, seed=seed, budget=budget)
            ),
            "selection_parameters": (
                {"support_penalty_mpa": SUPPORT_PENALTY, "distance_penalty_mpa": 150.0, "minimum_pair_distance": 0.02}
                if policy == "conservative_diverse"
                else {"ranking": f"predicted_{TARGET}_descending"}
            ),
        },
        "seed": seed,
        "pool_budget": budget,
        "batch_size": batch_size,
        "feasibility": {
            "hard_violation_rate": round(float(1 - feasible.mean()), 8),
            "rejection_count": int((~feasible).sum()),
            "repair_count": 0,
            "selection_shortfall": batch_size - len(selected),
        },
        "plausibility": {
            "mean_nearest_training_distance": round(float(distances_to_train.mean()), 8),
            "observed_duplicate_rate": round(
                float(np.mean(distances_to_train <= 1e-12)),
                8,
            ),
            "training_category_mode_coverage": round(
                selected_modes / training_category_modes
                if training_category_modes
                else 0.0,
                8,
            ),
            "eligible_category_mode_coverage": round(
                selected_modes / category_modes if category_modes else 0.0,
                8,
            ),
        },
        "predictive_safety": {
            "support_counts": {
                status: selected_support.count(status)
                for status in ("supported", "caution", "extrapolated")
            },
            "mean_prediction_holdout_gap": (
                round(float(np.mean(np.abs(selected_predictions - selected_realized))), 8)
                if len(selected)
                else None
            ),
            "exploitation_rate": (
                round(
                    float(
                        np.mean(
                            (selected_predictions - selected_realized > 300)
                            & np.asarray(
                                [status != "supported" for status in selected_support]
                            )
                        )
                    ),
                    8,
                )
                if len(selected)
                else None
            ),
        },
        "decision_value": {
            "best_realized_outcome": (
                round(float(selected_realized.max()), 8) if len(selected) else None
            ),
            "median_realized_outcome": (
                round(float(np.median(selected_realized)), 8) if len(selected) else None
            ),
            "holdout_median": round(float(np.median(data.holdout_outcomes)), 8),
            "mean_pairwise_diversity": (
                round(float(pairwise.mean()), 8) if len(pairwise) else None
            ),
            "selected_count": len(selected),
        },
        "operation": {
            **operation,
            "observed_runtime_ms": round((perf_counter() - started) * 1000, 3),
            "failure_reason": None,
        },
    }


def _summary(runs: list[dict[str, Any]], generator: str, policy: str) -> dict[str, Any]:
    matching = [
        run
        for run in runs
        if run["generator_id"] == generator
        and run["selection_policy"] == policy
    ]

    def mean(section: str, field: str) -> float | None:
        values = [
            run[section][field]
            for run in matching
            if run[section][field] is not None
        ]
        return round(float(np.mean(values)), 8) if values else None

    return {
        "generator_id": generator,
        "selection_policy": policy,
        "mean_hard_violation_rate": mean("feasibility", "hard_violation_rate"),
        "mean_selection_shortfall": mean("feasibility", "selection_shortfall"),
        "mean_nearest_training_distance": mean(
            "plausibility", "mean_nearest_training_distance"
        ),
        "mean_prediction_holdout_gap": mean(
            "predictive_safety", "mean_prediction_holdout_gap"
        ),
        "mean_exploitation_rate": mean(
            "predictive_safety", "exploitation_rate"
        ),
        "mean_best_realized_outcome": mean(
            "decision_value", "best_realized_outcome"
        ),
        "mean_batch_diversity": mean(
            "decision_value", "mean_pairwise_diversity"
        ),
        "runtime_ms_range": [
            min(run["operation"]["observed_runtime_ms"] for run in matching),
            max(run["operation"]["observed_runtime_ms"] for run in matching),
        ],
    }


def build_report(
    *,
    seeds: tuple[int, ...] = SEEDS,
    budget: int = 96,
    batch_size: int = 8,
) -> dict[str, Any]:
    data = load_replay_data()
    active_manifest = json.loads(
        (ACTIVE_PACKAGE / "manifest.json").read_text(encoding="utf-8")
    )
    task_contract_digest = semantic_digest(
        persisted_task_definition_payload(
            load_task_contracts()[TASK_ID].task_definition
        )
    )
    design_prior_payload = {
        "schema_version": "design-prior-replay-observations/v1",
        "numeric_paths": data.numeric_paths,
        "categorical_paths": data.categorical_paths,
        "numeric_rows": data.train_numeric.tolist(),
        "category_rows": data.train_categories,
        "group_rows": data.train_groups,
    }
    design_space_payload = {
        "numeric_paths": data.numeric_paths,
        "lower": data.lower.tolist(),
        "upper": (data.lower + data.span).tolist(),
        "categorical_values": sorted(set(data.train_categories)),
        "composition_total": {"minimum": 99.0, "maximum": 101.0},
        "balance_path": "composition.Fe",
    }
    distance_contract = {
        "id": "normalized-numeric-category-rms/v1",
        "numeric": "mean squared distance after train-min/max normalization",
        "categorical": "mean unequal-field indicator",
        "combination": "sqrt((numeric + categorical) / 2)",
        "tie_break": "first held-out source order",
    }
    holdout_payload = {
        "schema_version": "historical-holdout-oracle/v1",
        "split": "sha256-group-mod5/v1",
        "numeric_rows": data.holdout_numeric.tolist(),
        "category_rows": data.holdout_categories,
        "group_rows": data.holdout_groups,
        "outcomes": data.holdout_outcomes.tolist(),
        "outcome_key": TARGET,
        "candidate_outcome_mapping": "nearest held-out mixed-distance observation",
        "distance_contract": distance_contract,
    }
    with _replay_runtime() as (
        runtime,
        manifest,
        manifest_digest,
        train_source_digest,
    ):
        runs = [
            _evaluate_pool(
                data,
                runtime,
                generator,
                policy,
                budget=budget,
                batch_size=batch_size,
                seed=seed,
            )
            for generator in GENERATORS
            for policy in POLICIES
            for seed in seeds
        ]
    summaries = [
        _summary(runs, generator, policy)
        for generator in GENERATORS
        for policy in POLICIES
    ]
    protocol = {
        "protocol_version": PROTOCOL_VERSION,
        "task_id": TASK_ID,
        "task_selection_reason": (
            "public MPEA observations provide correlated composition, process "
            "categories, a composition total constraint, grouped literature "
            "holdout, and an allow-listed ridge Package fitted only on replay training groups"
        ),
        "source": {"path": SOURCE.relative_to(ROOT).as_posix(), "digest": data.source_digest},
        "profile": {"path": PROFILE.relative_to(ROOT).as_posix(), "digest": data.profile_digest},
        "training_snapshot": {
            "training_data_id": manifest["provenance"]["training_data_id"],
            "feature_dataset_id": manifest["provenance"]["feature_dataset_id"],
            "train_source_digest": train_source_digest,
            "holdout_groups_excluded_from_fit_and_support": True,
        },
        "task_contract_digest": task_contract_digest,
        "input_contract_digest": manifest["input_contract_digest"],
        "feature_recipe": manifest["feature_pipeline"],
        "validation_plan": [
            {
                "target": predictor["target"],
                "plan": predictor["config"]["training"]["validation"]["plan"],
                "digest": predictor["config"]["training"]["validation"][
                    "plan_digest"
                ],
            }
            for predictor in manifest["predictors"]
        ],
        "predictive_model_package": {
            "package_id": manifest["package_id"],
            "version": manifest["package_version"],
            "manifest_digest": f"sha256:{manifest_digest}",
            "training_scope": "fixed non-holdout literature groups only",
            "active_recipe_reference": {
                "package_id": active_manifest["package_id"],
                "version": active_manifest["package_version"],
                "manifest_digest": _file_digest(ACTIVE_PACKAGE / "manifest.json"),
                "ridge_alpha": active_manifest["predictors"][0]["config"][
                    "ridge_alpha"
                ],
            },
        },
        "design_prior": {
            "identity": "mpea-room-tensile-replay-observations/v1",
            "manifest_digest": _digest(design_prior_payload),
            "artifact_kind": "data-only replay projection",
            "train_rows": len(data.train_numeric),
            "holdout_rows": len(data.holdout_numeric),
            "train_groups": data.train_group_count,
            "holdout_groups": data.holdout_group_count,
        },
        "design_space": {
            "numeric_bounds": "training min/max for every canonical numeric input",
            "numeric_domains": [
                {
                    "path": path,
                    "minimum": float(lower),
                    "maximum": float(lower + span),
                }
                for path, lower, span in zip(
                    data.numeric_paths,
                    data.lower,
                    data.span,
                    strict=True,
                )
            ],
            "categorical_paths": data.categorical_paths,
            "categorical_tuples": sorted(set(data.train_categories)),
            "composition_total": {"minimum": 99.0, "maximum": 101.0},
            "balance_path": "composition.Fe",
            "digest": _digest(design_space_payload),
        },
        "holdout_oracle": {
            "identity": "sha256-group-mod5/v1",
            "digest": _digest(holdout_payload),
            "outcome": TARGET,
            "candidate_outcome_mapping": "nearest held-out mixed-distance observation",
            "distance_contract": distance_contract,
        },
        "generators": GENERATORS,
        "selection_policies": POLICIES,
        "seeds": seeds,
        "candidate_budget": budget,
        "batch_size": batch_size,
        "production_registry_changed": False,
    }
    decisions = {
        "knn_local": {
            "status": "experimental",
            "reason": (
                "composition-preserving local interpolation is feasible and plausible, "
                "but one public Task does not establish cross-Task production value"
            ),
        },
        "gaussian_rank_copula": {
            "status": "no_adopt",
            "reason": (
                "rank dependence alone does not preserve the 14-component composition "
                "constraint; rejection/shortfall is material"
            ),
        },
        "production_promotion": False,
        "proposal_registry_changed": False,
        "ui_changed": False,
    }
    reproducible = {
        "protocol": protocol,
        "runs": [
            {
                **run,
                "operation": {
                    key: value
                    for key, value in run["operation"].items()
                    if key != "observed_runtime_ms"
                },
            }
            for run in runs
        ],
        "summaries": [
            {key: value for key, value in summary.items() if key != "runtime_ms_range"}
            for summary in summaries
        ],
        "decisions": decisions,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "result_digest": _digest(reproducible),
        "protocol": protocol,
        "runs": runs,
        "summaries": summaries,
        "decisions": decisions,
        "limitations": (
            "nearest held-out outcome is an offline historical proxy, not a new experiment",
            "one public Task cannot establish cross-domain generator safety",
            "wall-clock measurements are environment-specific and excluded from result_digest",
            "no saved Proposal Run or production registry was changed",
        ),
    }


def render_memo(report: dict[str, Any]) -> str:
    decisions = report["decisions"]
    summaries = report["summaries"]

    def row(generator: str, policy: str) -> dict[str, Any]:
        return next(
            item
            for item in summaries
            if item["generator_id"] == generator
            and item["selection_policy"] == policy
        )

    table = "\n".join(
        "| {generator} | {policy} | {violation:.3f} | {shortfall:.2f} | {gap} | {best} |".format(
            generator=generator,
            policy=policy,
            violation=row(generator, policy)["mean_hard_violation_rate"],
            shortfall=row(generator, policy)["mean_selection_shortfall"],
            gap=(
                "—"
                if row(generator, policy)["mean_prediction_holdout_gap"] is None
                else f'{row(generator, policy)["mean_prediction_holdout_gap"]:.1f}'
            ),
            best=(
                "—"
                if row(generator, policy)["mean_best_realized_outcome"] is None
                else f'{row(generator, policy)["mean_best_realized_outcome"]:.1f}'
            ),
        )
        for generator in GENERATORS
        for policy in POLICIES
    )
    return f"""# MPEA room-tensile Design Prior replay

<!-- generated from {report["schema_version"]}; result-digest: {report["result_digest"]} -->

## 判断

- `kNN local`: **{decisions["knn_local"]["status"]}**。{decisions["knn_local"]["reason"]}
- `Gaussian rank copula`: **{decisions["gaussian_rank_copula"]["status"]}**。{decisions["gaussian_rank_copula"]["reason"]}
- production昇格は行わず、Proposal registry、UI、保存済みRunを変更しない。

## Taskと固定protocol

`mpea-room-tensile-v1`を選んだ。公開MPEA文献データに相関した14元素組成、工程category、
組成合計constraint、論文group holdout、同梱active Packageが揃い、機密データを外部送信しない。

- 再生成: `uv run python backend/scripts/experiments/run_real_task_design_prior_replay.py`
- 数値正本: [`real-task-design-prior-replay-report.json`](real-task-design-prior-replay-report.json)
- seed: `{", ".join(str(seed) for seed in report["protocol"]["seeds"])}`
- candidate budget: `{report["protocol"]["candidate_budget"]}` / batch: `{report["protocol"]["batch_size"]}`
- holdout: SHA-256で固定した文献groupの20% bucket。候補のrealized outcomeは最近傍holdout実測TYS。
- predictive replay Packageとsupport referenceもnon-holdout groupだけで構築し、
  active Packageと同じallow-list済みridge estimator、feature contract、alphaを
  standard Package builderで固定（training unitはstandard builderの契約）。
- source、Profile、Training Snapshot、Task contract、Feature Recipe、Validation Plan、
  Model Package、Design Space、generator parameter、selection policy、holdout identityをreportへ固定。

## 比較

| generator | policy | hard violation | mean shortfall | prediction-holdout gap (MPa) | best realized TYS (MPa) |
| --- | --- | ---: | ---: | ---: | ---: |
{table}

feasibility、plausibility、predictive support、objective gap、diversityは別々に保存し、
一つのscoreへ畳んでいない。LHS／Sobolの独立samplingとcopulaは組成合計を自動的には
守らない。hard validatorによるrejectを、generator likelihoodやclipで置き換えていない。

## 限界

- 最近傍holdout実測はhistorical replay用proxyであり、新しい材料実験ではない。
- 単一の公開Taskはcross-domainのproduction安全性を証明しない。
- wall-clockは環境依存のためreportへ残すがresult digestから除外する。
- deep generator、online active learning、自動generator選択は評価していない。
"""
