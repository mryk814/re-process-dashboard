"""Generate fixed missingness-promotion evidence on MPEA room tensile."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
BACKEND_SRC = ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from decision_workbench.application.personal_task_packages import (  # noqa: E402
    build_standard_package,
)
from decision_workbench.contracts.candidate_project_contracts import (  # noqa: E402
    CandidateInput,
)
from decision_workbench.contracts.missingness_contracts import (  # noqa: E402
    MissingnessOperationCapability,
)
from decision_workbench.contracts.task_contracts import (  # noqa: E402
    persisted_task_definition_payload,
)
from decision_workbench.design_priors.builder import (  # noqa: E402
    build_design_prior_package,
)
from decision_workbench.design_priors.contracts import (  # noqa: E402
    DesignPriorObservation,
    DesignPriorSource,
)
from decision_workbench.design_priors.loader import (  # noqa: E402
    DesignPriorPackageLoader,
)
from decision_workbench.design_priors.sampling import (  # noqa: E402
    sample_conditional_completions,
)
from decision_workbench.execution.inference_work_graph import (  # noqa: E402
    semantic_digest,
)
from decision_workbench.modeling.missingness import (  # noqa: E402
    assess_input_missingness,
    classify_missingness_pattern_support,
    pattern_digest,
    pattern_support_policy_document,
    resolve_missingness_operation_capability,
)
from decision_workbench.modeling.model_package_verify import (  # noqa: E402
    verify_model_package,
)
from decision_workbench.modeling.packages.contracts import (  # noqa: E402
    FeaturePipelineDocument,
    predictive_interval,
)
from decision_workbench.modeling.packages.loader import (  # noqa: E402
    ModelPackageLoader,
)
from decision_workbench.modeling.tabular.data import (  # noqa: E402
    load_tabular_data,
)
from decision_workbench.modeling.tabular.features import (  # noqa: E402
    candidate_from_observation,
)
from decision_workbench.modeling.tabular.profile import (  # noqa: E402
    load_tabular_profile,
)
from decision_workbench.modeling.tabular.runtime import (  # noqa: E402
    TabularRegressionRuntime,
)
from decision_workbench.tasks.task_registry import (  # noqa: E402
    load_task_contracts,
)


TASK_ID = "mpea-room-tensile-v1"
TARGET = "TYS"
SOURCE = ROOT / "data/source/external/mpea_ground_truth_18021833.csv"
BASE_PROFILE = (
    ROOT
    / "backend"
    / "src"
    / "decision_workbench"
    / "data"
    / "tabular-profile-mpea-room-tensile-v1.json"
)
ACTIVE_PACKAGE = ROOT / "models/packages/mpea-room-tensile-ridge-v2"
DEFAULT_OUTPUT = ROOT / "docs/reports/mpea-missingness-promotion.json"
EVALUATION_PACKAGE_ID = "mpea-room-tensile-missingness-evaluation-v1"
EVALUATION_PACKAGE_VERSION = "1.0.0"
DESIGN_PRIOR_PACKAGE_ID = "mpea-room-tensile-completion-prior-v1"
SEED = 20260803
SAMPLE_COUNT = 16
HOLDOUT_MODULUS = 5
OPERATION_CAPABILITY = MissingnessOperationCapability(
    preview="block",
    comparison="block",
    snapshot="block",
    proposal="block",
    export="require_complete",
    completion_uncertainty="available",
)


@dataclass(frozen=True)
class Pattern:
    pattern_id: str
    paths: tuple[str, ...]
    masking_cohort_predicate: str
    masking_cohort_count: int
    expected_support: str
    masking_categories: tuple[tuple[str, str], ...] = ()


def _digest(value: object) -> str:
    return semantic_digest(value)


def _file_digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _held_out(group: str) -> bool:
    value = int(hashlib.sha256(group.encode("utf-8")).hexdigest()[:8], 16)
    return value % HOLDOUT_MODULUS == 0


def _value(row: dict[str, Any], path: str) -> float | str:
    group, key = path.split(".", 1)
    if group == "composition":
        return float(row["composition"][key])
    if group == "process":
        return float(row["features"][key])
    return str(row["categorical"][key])


def _evaluation_profile_document() -> dict[str, Any]:
    profile = json.loads(BASE_PROFILE.read_text(encoding="utf-8"))
    profile["profile_id"] = "mpea-room-tensile-missingness-evaluation-v1"
    profile["package_id"] = EVALUATION_PACKAGE_ID
    profile["missingness_operation_capability"] = (
        OPERATION_CAPABILITY.model_dump(mode="json")
    )
    for item in profile["inputs"]:
        if item["kind"] == "number":
            item["transform"] = "linear"
            item["numeric_missing"] = {
                "strategy": "training_median_with_indicator",
                "value": None,
                "reason": None,
            }
        else:
            item["categorical_missing"] = {
                "strategy": "map_to_missing_category",
                "category": None,
            }
            item["unknown_category"] = {
                "strategy": "reject",
                "other_choice": None,
            }
    return profile


def _write_train_source(
    destination: Path,
    allowed_rows: Counter[tuple[str, str]],
) -> None:
    with SOURCE.open("r", encoding="utf-8-sig", newline="") as stream:
        preamble = stream.readline()
        reader = csv.DictReader(stream)
        rows: list[dict[str, str]] = []
        remaining = allowed_rows.copy()
        for row in reader:
            identity = (str(row["File_Name"]), str(row["Material"]))
            if remaining[identity] <= 0:
                continue
            rows.append(row)
            remaining[identity] -= 1
        if any(remaining.values()):
            raise ValueError("fixed training cohort could not be materialized")
        fieldnames = reader.fieldnames
    if not fieldnames:
        raise ValueError("MPEA source header is unavailable")
    with destination.open("w", encoding="utf-8", newline="") as stream:
        stream.write(preamble)
        writer = csv.DictWriter(
            stream,
            fieldnames=fieldnames,
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _candidate_with(
    candidate: CandidateInput,
    *,
    removed: tuple[str, ...] = (),
    completed: dict[str, float | str] | None = None,
    missing_kind: str = "not_measured",
) -> CandidateInput:
    payload = candidate.model_dump(mode="json")
    for path in removed:
        group, key = path.split(".", 1)
        payload["inputs"][group].pop(key, None)
        payload["input_missing_kinds"][path] = missing_kind
    for path, value in (completed or {}).items():
        group, key = path.split(".", 1)
        payload["inputs"][group][key] = value
        payload["input_missing_kinds"].pop(path, None)
    return CandidateInput.model_validate(payload)


def _prediction(
    runtime: TabularRegressionRuntime,
    candidate: CandidateInput,
) -> tuple[float, float, float]:
    values = runtime._feature_bundle(candidate).as_dict()
    summary = runtime.predictors[TARGET].predict(values)
    lower, upper = predictive_interval(summary)
    return float(summary.point_estimate), float(lower), float(upper)


def _complete_metrics(
    runtime: TabularRegressionRuntime,
    holdout: list[dict[str, Any]],
    profile: Any,
) -> tuple[dict[str, Any], dict[str, tuple[float, float, float]]]:
    predictions: dict[str, tuple[float, float, float]] = {}
    observed: list[float] = []
    point: list[float] = []
    covered: list[bool] = []
    for row in holdout:
        result = _prediction(
            runtime,
            candidate_from_observation(row, profile),
        )
        predictions[str(row["id"])] = result
        truth = float(row["outputs"][TARGET])
        observed.append(truth)
        point.append(result[0])
        covered.append(result[1] <= truth <= result[2])
    errors = np.asarray(point) - np.asarray(observed)
    return (
        {
            "evaluation_count": len(observed),
            "failure_rate": 0.0,
            "rmse": float(np.sqrt(np.mean(errors**2))),
            "calibration_bias": float(np.mean(errors)),
            "interval_coverage_90": float(np.mean(covered)),
        },
        predictions,
    )


def _completion_metrics(
    *,
    generator_id: str,
    prior: Any,
    runtime: TabularRegressionRuntime,
    holdout: list[dict[str, Any]],
    profile: Any,
    paths: tuple[str, ...],
    complete_predictions: dict[str, tuple[float, float, float]],
    numeric_scales: dict[str, float],
) -> dict[str, Any]:
    truths: list[float] = []
    points: list[float] = []
    model_covered: list[bool] = []
    combined_covered: list[bool] = []
    input_std: list[float] = []
    model_sigma: list[float] = []
    reconstruction_errors: list[float] = []
    reconstruction_covered: list[bool] = []
    point_shifts: list[float] = []
    overvalued: list[bool] = []
    identities: set[str] = set()
    failed = 0

    for row in holdout:
        base = candidate_from_observation(row, profile)
        observed = {
            item.path: _value(row, item.path)
            for item in profile.inputs
            if item.path not in paths
        }
        try:
            samples = sample_conditional_completions(
                prior,
                generator_id=generator_id,
                count=SAMPLE_COUNT,
                seed=SEED,
                observed=observed,
                missing_paths=paths,
            )
            predicted = [
                _prediction(
                    runtime,
                    _candidate_with(
                        base,
                        removed=paths,
                        completed=sample.values,
                    ),
                )
                for sample in samples
            ]
        except (ValueError, KeyError):
            failed += 1
            continue

        point_values = np.asarray([item[0] for item in predicted])
        lower_values = np.asarray([item[1] for item in predicted])
        upper_values = np.asarray([item[2] for item in predicted])
        truth = float(row["outputs"][TARGET])
        mean_point = float(point_values.mean())
        mean_lower = float(lower_values.mean())
        mean_upper = float(upper_values.mean())
        combined_lower = float(np.quantile(lower_values, 0.05))
        combined_upper = float(np.quantile(upper_values, 0.95))
        truths.append(truth)
        points.append(mean_point)
        model_covered.append(mean_lower <= truth <= mean_upper)
        combined_covered.append(combined_lower <= truth <= combined_upper)
        input_std.append(float(np.std(point_values, ddof=1)))
        model_sigma.append(
            float(np.mean((upper_values - lower_values) / (2 * 1.6448536)))
        )
        baseline = complete_predictions[str(row["id"])][0]
        point_shifts.append(mean_point - baseline)
        overvalued.append(mean_point > baseline)

        for path in paths:
            sampled = [sample.values[path] for sample in samples]
            actual = _value(row, path)
            if path in numeric_scales:
                numeric = np.asarray(sampled, dtype=float)
                reconstruction_errors.append(
                    abs(float(numeric.mean()) - float(actual))
                    / numeric_scales[path]
                )
                reconstruction_covered.append(
                    float(numeric.min())
                    <= float(actual)
                    <= float(numeric.max())
                )
            else:
                reconstruction_errors.append(
                    float(np.mean([str(item) != str(actual) for item in sampled]))
                )
                reconstruction_covered.append(
                    any(str(item) == str(actual) for item in sampled)
                )
        identities.update(
            _digest(sample.evidence.model_dump(mode="json"))
            for sample in samples
        )

    evaluated = len(holdout)
    if not points:
        return {
            "generator_id": generator_id,
            "generator_version": "1.0.0",
            "seed": SEED,
            "sample_count": SAMPLE_COUNT,
            "evaluation_count": evaluated,
            "failure_rate": 1.0,
            "rmse": None,
            "calibration_bias": None,
            "interval_coverage_90": None,
        }
    errors = np.asarray(points) - np.asarray(truths)
    mean_model_sigma = float(np.mean(model_sigma))
    mean_input_std = float(np.mean(input_std))
    return {
        "generator_id": generator_id,
        "generator_version": "1.0.0",
        "generator_parameter_digest": (
            samples[0].evidence.lane_parameter_digest
        ),
        "seed": SEED,
        "sample_count": SAMPLE_COUNT,
        "evaluation_count": evaluated,
        "successful_count": len(points),
        "failure_rate": failed / evaluated,
        "rmse": float(np.sqrt(np.mean(errors**2))),
        "calibration_bias": float(np.mean(errors)),
        "model_interval_coverage_90": float(np.mean(model_covered)),
        "combined_interval_coverage_90": float(
            np.mean(combined_covered)
        ),
        "normalized_reconstruction_error": float(
            np.mean(reconstruction_errors)
        ),
        "conditional_sample_coverage": float(
            np.mean(reconstruction_covered)
        ),
        "model_uncertainty": {
            "available": True,
            "method": "verified Package grouped-OOF residual interval",
            "mean_sigma_approximation": mean_model_sigma,
        },
        "input_missingness_uncertainty": {
            "available": True,
            "method": "between-completion point standard deviation",
            "mean_standard_deviation": mean_input_std,
        },
        "combined_uncertainty": {
            "method": "root-sum-of-squares diagnostic",
            "mean_standard_deviation": float(
                np.sqrt(mean_model_sigma**2 + mean_input_std**2)
            ),
        },
        "point_shift_vs_complete": float(np.mean(point_shifts)),
        "provisional_overvaluation_rate": float(np.mean(overvalued)),
        "completion_sample_identity_count": len(identities),
        "completion_sample_identity_digest": _digest(sorted(identities)),
    }


def _package_imputation_metrics(
    *,
    runtime: TabularRegressionRuntime,
    holdout: list[dict[str, Any]],
    profile: Any,
    paths: tuple[str, ...],
    complete_predictions: dict[str, tuple[float, float, float]],
) -> dict[str, Any]:
    truths: list[float] = []
    points: list[float] = []
    covered: list[bool] = []
    shifts: list[float] = []
    failed = 0
    for row in holdout:
        try:
            result = _prediction(
                runtime,
                _candidate_with(
                    candidate_from_observation(row, profile),
                    removed=paths,
                ),
            )
        except (ValueError, KeyError):
            failed += 1
            continue
        truth = float(row["outputs"][TARGET])
        truths.append(truth)
        points.append(result[0])
        covered.append(result[1] <= truth <= result[2])
        shifts.append(result[0] - complete_predictions[str(row["id"])][0])
    if not points:
        return {
            "evaluation_count": len(holdout),
            "failure_rate": 1.0,
            "rmse": None,
            "calibration_bias": None,
            "interval_coverage_90": None,
        }
    errors = np.asarray(points) - np.asarray(truths)
    return {
        "evaluation_count": len(holdout),
        "successful_count": len(points),
        "failure_rate": failed / len(holdout),
        "rmse": float(np.sqrt(np.mean(errors**2))),
        "calibration_bias": float(np.mean(errors)),
        "interval_coverage_90": float(np.mean(covered)),
        "point_shift_vs_complete": float(np.mean(shifts)),
        "mean_absolute_point_shift_vs_complete": float(
            np.mean(np.abs(shifts))
        ),
        "uncertainty_limit": (
            "Package interval excludes between-completion uncertainty"
        ),
    }


def _patterns(train: list[dict[str, Any]]) -> tuple[Pattern, ...]:
    categories = Counter(
        tuple(
            str(row["categorical"][key])
            for key in ("homogenized", "rolled", "recrystallized", "aged")
        )
        for row in train
    )
    return (
        Pattern(
            "low-impact-numeric-single",
            ("composition.Fe",),
            "all TYS training rows",
            len(train),
            "unseen",
        ),
        Pattern(
            "high-impact-numeric-single",
            ("process.aging_time_h",),
            "categorical.aged=yes",
            sum(row["categorical"]["aged"] == "yes" for row in train),
            "unseen",
            (("aged", "yes"),),
        ),
        Pattern(
            "correlated-process-pair",
            (
                "process.rolling_temp_c",
                "process.rolling_reduction_pct",
            ),
            "categorical.rolled=yes",
            sum(row["categorical"]["rolled"] == "yes" for row in train),
            "unseen",
            (("rolled", "yes"),),
        ),
        Pattern(
            "frequent-process-multi",
            ("process.aging_temp_c", "process.aging_time_h"),
            "all TYS training rows",
            len(train),
            "supported",
        ),
        Pattern(
            "sparse-process-multi",
            (
                "process.aging_temp_c",
                "process.aging_time_h",
                "process.recrystallization_temp_c",
                "process.recrystallization_time_min",
            ),
            "all TYS training rows",
            len(train),
            "sparse",
        ),
        Pattern(
            "unseen-mixed-mode",
            (
                "categorical.homogenized",
                "process.homogenization_temp_c",
                "process.homogenization_time_h",
            ),
            "category tuple=(no,yes,no,yes) is a mask trigger only",
            categories[("no", "yes", "no", "yes")],
            "unseen",
            (
                ("homogenized", "no"),
                ("rolled", "yes"),
                ("recrystallized", "no"),
                ("aged", "yes"),
            ),
        ),
    )


def _masking_rows(
    rows: list[dict[str, Any]],
    pattern: Pattern,
) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if all(
            row["categorical"][key] == value
            for key, value in pattern.masking_categories
        )
    ]


def _observed_pattern_counts(
    rows: list[dict[str, Any]],
    profile: Any,
) -> Counter[tuple[tuple[str, str], ...]]:
    counts: Counter[tuple[tuple[str, str], ...]] = Counter()
    for row in rows:
        policies = row["run_context"]["curation"]["predictor_policies"]
        pattern: list[tuple[str, str]] = []
        for item in profile.inputs:
            source_state = policies[item.column]["source_state"]
            if source_state == "unknown_category":
                rule = (
                    None
                    if profile.curation_recipe is None
                    else profile.curation_recipe.columns.get(item.column)
                )
                _, key = item.path.split(".", 1)
                if (
                    rule is not None
                    and rule.parser == "reported_flag"
                    and row["categorical"].get(key) in item.choices
                ):
                    continue
            if source_state not in {
                "missing",
                "unknown_category",
                "structural_not_applicable",
                "redacted",
            }:
                continue
            kind = (
                source_state
                if source_state != "missing"
                else "not_measured"
            )
            pattern.append((item.path, kind))
        counts[tuple(sorted(pattern))] += 1
    return counts


def _alias_evidence(
    train_groups: set[str],
    train: list[dict[str, Any]],
) -> dict[str, Any]:
    columns = {
        "categorical.homogenized": "Homogenization? (Yes=1, No=0)",
        "categorical.rolled": "Rolling? (Yes=1, No=0)",
        "categorical.recrystallized": "Recrystallization (Y=1, N=0)",
        "categorical.aged": "Aging? (yes=1, No=0)",
    }
    aliases: dict[str, Counter[str]] = {
        path: Counter() for path in columns
    }
    with SOURCE.open("r", encoding="utf-8-sig", newline="") as stream:
        next(stream)
        for row in csv.DictReader(stream):
            if str(row["File_Name"]) not in train_groups:
                continue
            for path, column in columns.items():
                raw = str(row[column]).strip()
                if raw.casefold() not in {"0", "1", "no", "yes"}:
                    aliases[path][raw] += 1
    return {
        "source_alias_count": sum(
            sum(counter.values()) for counter in aliases.values()
        ),
        "examples_by_path": {
            path: [value for value, _ in counter.most_common(3)]
            for path, counter in aliases.items()
        },
        "canonical_values_after_curation": {
            path: sorted({
                str(row["categorical"][path.split(".", 1)[1]])
                for row in train
            })
            for path in columns
        },
        "status": "alias_resolved",
        "observed_training_missing_pattern_count": 0,
        "semantics": "raw category spelling normalization, not missingness",
        "unknown_category_coercion": False,
    }


def build_report() -> dict[str, Any]:
    base_profile = load_tabular_profile(BASE_PROFILE)
    full_data = load_tabular_data(
        SOURCE,
        base_profile,
        profile_locator=BASE_PROFILE,
    )
    cohort = [
        row
        for row in full_data.observations
        if row["eligible"] and TARGET in row["outputs"]
    ]
    train = [
        row for row in cohort if not _held_out(str(row["parent_key"]))
    ]
    holdout = [
        row for row in cohort if _held_out(str(row["parent_key"]))
    ]
    train_groups = {str(row["parent_key"]) for row in train}
    holdout_groups = {str(row["parent_key"]) for row in holdout}
    if train_groups & holdout_groups:
        raise ValueError("fixed group holdout leaked a source paper")

    with TemporaryDirectory(prefix="mpea-missingness-promotion-") as directory:
        work = Path(directory)
        profile_path = work / "evaluation-profile.json"
        profile_document = _evaluation_profile_document()
        profile_path.write_text(
            json.dumps(profile_document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        train_source = work / "mpea-room-tensile-train.csv"
        _write_train_source(
            train_source,
            Counter(
                (
                    str(row["parent_key"]),
                    str(row["id"]).rsplit(":", 1)[0],
                )
                for row in train
            ),
        )
        package_path = work / "evaluation-package"
        build_standard_package(
            TASK_ID,
            train_source,
            package_path,
            work / "canonical-training-dataset.json",
            package_id=EVALUATION_PACKAGE_ID,
            package_version=EVALUATION_PACKAGE_VERSION,
            replace=False,
            estimator="ridge.v1",
            estimator_options={"alpha": 1000.0},
            profile=profile_path,
        )
        verification = verify_model_package(
            package_path,
            task_id=TASK_ID,
            source=train_source,
            profile=profile_path,
        )
        verified = ModelPackageLoader().load(package_path)
        evaluation_profile = load_tabular_profile(profile_path)
        train_data = load_tabular_data(
            train_source,
            evaluation_profile,
            profile_locator=profile_path,
        )
        runtime = TabularRegressionRuntime(
            train_data,
            verified,
            missing_policy_inputs=evaluation_profile.inputs,
        )
        pipeline = FeaturePipelineDocument.model_validate_json(
            verified.artifact_path(
                verified.manifest.feature_pipeline.spec
            ).read_text(encoding="utf-8")
        )
        capability = resolve_missingness_operation_capability(
            pipeline.missing_policy
        )
        if capability != OPERATION_CAPABILITY:
            raise ValueError("evaluation Package capability was not preserved")

        canonical_paths = tuple(
            item.path for item in evaluation_profile.inputs
        )
        dataset_view = {
            "source_sha256": _file_digest(train_source),
            "profile_digest": _file_digest(profile_path),
            "observation_ids": [str(row["id"]) for row in train],
            "group_ids": sorted(train_groups),
            "target_columns": [],
        }
        prior_path = work / "design-prior"
        build_design_prior_package(
            prior_path,
            package_id=DESIGN_PRIOR_PACKAGE_ID,
            package_version="1.0.0",
            task_id=TASK_ID,
            task_contract_digest=_digest(
                persisted_task_definition_payload(
                    load_task_contracts()[TASK_ID].task_definition
                )
            ),
            canonical_input_schema_version="canonical-candidate/v1",
            canonical_input_paths=canonical_paths,
            source=DesignPriorSource(
                dataset_view_digest=_digest(dataset_view)
            ),
            observations=(
                DesignPriorObservation(
                    sample_id=str(row["id"]),
                    inputs={
                        path: _value(row, path)
                        for path in canonical_paths
                    },
                )
                for row in train
            ),
            training_code_revision=(
                "mpea-missingness-completion-train-only-v1"
            ),
        )
        prior = DesignPriorPackageLoader().load(prior_path)

        complete_metrics, complete_predictions = _complete_metrics(
            runtime,
            holdout,
            evaluation_profile,
        )
        numeric_scales = {
            item.path: max(
                max(float(_value(row, item.path)) for row in train)
                - min(float(_value(row, item.path)) for row in train),
                1e-12,
            )
            for item in evaluation_profile.inputs
            if item.kind == "number"
        }
        support_policy = pattern_support_policy_document()
        complete_digest = pattern_digest(())
        observed_pattern_counts = _observed_pattern_counts(
            train,
            base_profile,
        )
        pattern_reports: list[dict[str, Any]] = [{
            "pattern_id": "complete",
            "paths": [],
            "pattern_digest": complete_digest,
            "masking_cohort_count": len(train),
            "masking_holdout_evaluation_count": len(holdout),
            "observed_training_pattern_count": observed_pattern_counts[()],
            "observed_pattern_evaluation_count": 0,
            "support": "supported",
            "metrics": {"complete": complete_metrics},
        }]
        for pattern in _patterns(train):
            masking_train = _masking_rows(train, pattern)
            masking_holdout = _masking_rows(holdout, pattern)
            if len(masking_train) != pattern.masking_cohort_count:
                raise ValueError(
                    f"{pattern.pattern_id}: masking cohort count drifted"
                )
            if not masking_holdout:
                raise ValueError(
                    f"{pattern.pattern_id}: fixed holdout has no maskable rows"
                )
            digest = pattern_digest(
                tuple((path, "not_measured") for path in pattern.paths)
            )
            observed_evidence = next(
                (
                    item
                    for item in runtime.training_stats["missing_policy"][
                        "pattern_evidence"
                    ]
                    if item["pattern_digest"] == digest
                ),
                None,
            )
            observed_training_count = observed_pattern_counts[
                tuple((path, "not_measured") for path in sorted(pattern.paths))
            ]
            target_metrics = (
                {}
                if observed_evidence is None
                else observed_evidence["metrics_by_target"].get(TARGET, {})
            )
            support_evidence = (
                None
                if observed_training_count == 0
                else {
                    "training_count": observed_training_count,
                    "evaluation_count": int(
                        target_metrics.get("evaluation_count", 0)
                    ),
                    "metrics_by_target": {
                        TARGET: target_metrics
                    },
                }
            )
            metrics = {
                "package_imputation": _package_imputation_metrics(
                    runtime=runtime,
                    holdout=masking_holdout,
                    profile=evaluation_profile,
                    paths=pattern.paths,
                    complete_predictions=complete_predictions,
                ),
                "conditional_completion": {
                    generator: _completion_metrics(
                        generator_id=generator,
                        prior=prior,
                        runtime=runtime,
                        holdout=masking_holdout,
                        profile=evaluation_profile,
                        paths=pattern.paths,
                        complete_predictions=complete_predictions,
                        numeric_scales=numeric_scales,
                    )
                    for generator in ("empirical_rows", "knn_local")
                },
            }
            prediction_failure = float(
                metrics["package_imputation"]["failure_rate"]
            )
            support = classify_missingness_pattern_support(
                support_evidence,
                support_policy=support_policy,
            )
            if support != pattern.expected_support:
                raise ValueError(
                    f"{pattern.pattern_id}: support drifted to {support}"
                )
            pattern_reports.append({
                "pattern_id": pattern.pattern_id,
                "paths": list(pattern.paths),
                "missing_kind": "not_measured",
                "pattern_digest": digest,
                "generation_policy": {
                    "version": "mpea-realistic-mask/v1",
                    "predicate": pattern.masking_cohort_predicate,
                    "category_predicate": dict(
                        pattern.masking_categories
                    ),
                    "digest": _digest({
                        "paths": pattern.paths,
                        "predicate": pattern.masking_cohort_predicate,
                        "categories": pattern.masking_categories,
                    }),
                },
                "masking_cohort_count": pattern.masking_cohort_count,
                "masking_holdout_evaluation_count": len(masking_holdout),
                "observed_training_pattern_count": observed_training_count,
                "observed_pattern_evaluation_count": (
                    0
                    if not target_metrics
                    else int(target_metrics.get("evaluation_count", 0))
                ),
                "support": support,
                "support_evidence_source": (
                    "verified evaluation Package training_stats.pattern_evidence"
                ),
                "simulation_prediction_failure_rate": prediction_failure,
                "metrics": metrics,
            })

        structural_candidate = _candidate_with(
            candidate_from_observation(
                next(
                    row
                    for row in holdout
                    if row["categorical"]["aged"] == "no"
                ),
                evaluation_profile,
            ),
            removed=(
                "process.aging_temp_c",
                "process.aging_time_h",
            ),
            missing_kind="structural_not_applicable",
        )
        structural = assess_input_missingness(
            structural_candidate,
            evaluation_profile.inputs,
            runtime.training_stats,
            operation="preview",
            operation_capability=capability,
        )
        unknown_candidate = _candidate_with(
            candidate_from_observation(holdout[0], evaluation_profile),
            completed={"categorical.aged": "sometimes"},
        )
        unknown = assess_input_missingness(
            unknown_candidate,
            evaluation_profile.inputs,
            runtime.training_stats,
            operation="preview",
            operation_capability=capability,
        )

        active = ModelPackageLoader().load(ACTIVE_PACKAGE)
        return {
            "schema_version": "missingness-promotion-report/v2",
            "task": {
                "task_id": TASK_ID,
                "target": TARGET,
                "selection_reason": [
                    "public actual tensile outcomes support a source-paper group holdout",
                    "22 numeric and 4 categorical inputs exercise mixed completion",
                    "source aliases are curated to canonical category values",
                ],
            },
            "fixed_protocol": {
                "protocol_version": "mpea-missingness-promotion/v1",
                "source_digest": _file_digest(SOURCE),
                "base_profile_digest": _file_digest(BASE_PROFILE),
                "evaluation_profile_digest": _file_digest(profile_path),
                "holdout_policy": (
                    "sha256(File_Name) prefix modulo 5 equals 0"
                ),
                "train_rows": len(train),
                "train_groups": len(train_groups),
                "holdout_rows": len(holdout),
                "holdout_groups": len(holdout_groups),
                "train_group_digest": _digest(sorted(train_groups)),
                "holdout_group_digest": _digest(sorted(holdout_groups)),
                "same_holdout_for_masked_patterns": True,
                "target_excluded_from_completion": True,
                "seed": SEED,
                "completion_sample_count": SAMPLE_COUNT,
                "support_policy": support_policy,
            },
            "package_authority": {
                "active_recipe_reference_only": {
                    "package_id": active.manifest.package_id,
                    "package_version": active.manifest.package_version,
                    "manifest_digest": (
                        f"sha256:{active.manifest_sha256}"
                    ),
                    "reason_not_used_for_holdout": (
                        "active Package was trained on the full public source"
                    ),
                },
                "evaluation_package": {
                    "package_id": verified.manifest.package_id,
                    "package_version": verified.manifest.package_version,
                    "manifest_digest": (
                        f"sha256:{verified.manifest_sha256}"
                    ),
                    "training_data_id": (
                        verified.manifest.provenance.training_data_id
                    ),
                    "feature_dataset_id": (
                        verified.manifest.provenance.feature_dataset_id
                    ),
                    "training_code_revision": (
                        verified.manifest.provenance.training_code_revision
                    ),
                    "verified": True,
                    "verification_task_id": verification.task_id,
                    "runtime_class": type(runtime).__name__,
                    "capability_source": (
                        "feature-pipeline/pipeline.json#missing_policy."
                        "operation_capability"
                    ),
                },
            },
            "design_prior_authority": {
                "package_id": prior.manifest.package_id,
                "package_version": prior.manifest.package_version,
                "manifest_digest": f"sha256:{prior.manifest_sha256}",
                "dataset_view_digest": (
                    prior.manifest.source.dataset_view_digest
                ),
                "training_code_revision": (
                    prior.manifest.training_code_revision
                ),
                "rows": prior.quality_report.rows,
                "generators": [
                    {
                        "id": item.generator_id,
                        "version": item.version,
                        "max_neighbors": item.max_neighbors,
                    }
                    for item in prior.manifest.generators
                ],
            },
            "patterns": pattern_reports,
            "category_semantics": {
                "alias": _alias_evidence(train_groups, train),
                "structural_inactive": {
                    "paths": [
                        "process.aging_temp_c",
                        "process.aging_time_h",
                    ],
                    "masking_cohort_count": sum(
                        row["categorical"]["aged"] == "no"
                        for row in train
                    ),
                    "observed_training_pattern_count": 0,
                    "observed_pattern_kind": "structural_not_applicable",
                    "source_semantics": (
                        "inactive raw blanks are normalized to canonical zero; "
                        "they are not evidence for a not_measured prediction"
                    ),
                    "support": structural.missingness_support,
                    "prediction_status": structural.prediction_status,
                },
                "true_unknown": {
                    "path": "categorical.aged",
                    "value": "sometimes",
                    "masking_cohort_count": len(train),
                    "observed_training_pattern_count": 0,
                    "support": unknown.missingness_support,
                    "prediction_status": unknown.prediction_status,
                    "applied_policy": unknown.fields[0].applied_policy,
                    "coerced_to_missing": False,
                },
            },
            "operation_capability": {
                **capability.model_dump(mode="json"),
                "authority_digest": _digest(
                    capability.model_dump(mode="json")
                ),
            },
            "decision": {
                "production_promotion": "not_promoted",
                "reasons": [
                    "evaluation Profile and Package are not the active recipe",
                    (
                        "single-numeric and selected mixed masks have zero "
                        "observed matching training patterns"
                    ),
                    "sparse, unseen, structural, and unknown patterns remain blocked",
                ],
                "proposal_rejects_unsafe_missingness": True,
                "export_requires_complete_input": True,
            },
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate or verify fixed MPEA missingness evidence."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = json.dumps(
        build_report(),
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    ) + "\n"
    if args.check:
        if (
            not args.output.is_file()
            or args.output.read_text(encoding="utf-8") != expected
        ):
            print(f"Missingness promotion report is stale: {args.output}")
            return 1
        print(f"Missingness promotion report is current: {args.output}")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(expected, encoding="utf-8", newline="\n")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
