"""Generate bounded, machine-readable Issue #780 capacity evidence."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
import scipy


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
BACKEND_SRC = REPOSITORY_ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from decision_workbench.modeling.training.approximate_gp_spike import (  # noqa: E402
    SPIKE_DEFAULT_BASIS_COUNT,
    SPIKE_ID,
    evaluate_same_cohort,
)
from decision_workbench.modeling.training.capacity import (  # noqa: E402
    CAPACITY_EVIDENCE_SCHEMA_VERSION,
    CAPACITY_POLICY_ID,
    CAPACITY_POLICY_VERSION,
    ExactGpCapacityContext,
    capacity_context_from_training_set,
    estimate_exact_gp_capacity,
    resolve_exact_gp_capacity,
)
from decision_workbench.modeling.training.estimators import exact_gp  # noqa: E402
from decision_workbench.modeling.training.feature_dataset import (  # noqa: E402
    compile_target_training_set,
)
from decision_workbench.modeling.training.recipe import ExactGPEstimatorRecipe  # noqa: E402
from decision_workbench.modeling.training.validation_plan import (  # noqa: E402
    grouped_kfold_plan,
)


DEFAULT_OUTPUT = (
    REPOSITORY_ROOT / "docs" / "benchmarks" / "exact-gp-capacity-v1.json"
)
BENCHMARK_SEED = 20260808
BASELINE = (100, 8, 3, 1)
MEMORY_MEASUREMENT = {
    "metric": "process_peak_working_set_bytes",
    "scope": "benchmark process high-water mark; not an isolated per-case process",
    "source": "Windows psapi.GetProcessMemoryInfo.PeakWorkingSetSize",
    "includes_native_allocations": True,
    "policy_use": "evidence only; capacity policy decisions use estimated_peak_memory_bytes",
}
MEASUREMENT_DESIGN = (
    ("baseline", BASELINE),
    ("effective_rows=100", (100, 8, 3, 1)),
    ("effective_rows=250", (250, 8, 3, 1)),
    ("effective_rows=500", (500, 8, 3, 1)),
    ("effective_rows=750", (750, 8, 3, 1)),
    ("effective_rows=1000", (1000, 8, 3, 1)),
    ("features=8", (100, 8, 3, 1)),
    ("features=32", (100, 32, 3, 1)),
    ("features=64", (100, 64, 3, 1)),
    ("folds=3", (100, 8, 3, 1)),
    ("folds=5", (100, 8, 5, 1)),
    ("restarts=1", (100, 8, 3, 1)),
    ("restarts=3", (100, 8, 3, 3)),
)


def _commit_identity(override: str | None) -> str:
    if override:
        return override
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _canonical_fixture(*, effective_rows: int, feature_count: int) -> dict[str, Any]:
    rng = np.random.default_rng(BENCHMARK_SEED + feature_count)
    weights = np.linspace(0.2, 0.8, feature_count)
    rows: list[dict[str, Any]] = []
    for index in range(effective_rows):
        values = rng.normal(size=feature_count)
        target = float(10.0 + values @ weights + rng.normal(0.0, 0.12))
        parent = f"cohort-{index % 5}"
        row = {
            "observation_id": f"observation-{index}",
            "parent_key": parent,
            "condition_context_id": f"context-{index}",
            "features": {
                f"feature_{column}": float(values[column])
                for column in range(feature_count)
            },
            "outputs": {"target": target},
        }
        rows.append(row)
        if index % 10 == 0:
            duplicate = dict(row)
            duplicate["observation_id"] = f"observation-{index}-replicate"
            duplicate["outputs"] = {"target": target + 0.05}
            rows.append(duplicate)
    return {
        "schema_version": "canonical-training-dataset/v1",
        "task_id": "exact-gp-capacity-benchmark",
        "source_data_digest": "sha256:benchmark-source",
        "dataset_profile_digest": "sha256:benchmark-profile",
        "feature_pipeline": {
            "id": "exact-gp-capacity-benchmark-features",
            "version": "1.0.0",
            "features": [
                {
                    "name": f"feature_{column}",
                    "unit": "1",
                    "group": "numeric",
                }
                for column in range(feature_count)
            ],
        },
        "rows": rows,
    }


def _fixture_training_set(
    *,
    effective_rows: int,
    feature_count: int,
    folds: int,
):
    canonical = _canonical_fixture(
        effective_rows=effective_rows,
        feature_count=feature_count,
    )
    return compile_target_training_set(
        canonical,
        target="target",
        unit="1",
        folds=folds,
        seed=BENCHMARK_SEED,
        validation_plan=grouped_kfold_plan(
            folds=folds,
            seed=BENCHMARK_SEED,
        ),
    )


def _capacity_context(
    *,
    effective_rows: int,
    feature_count: int,
    folds: int,
    restarts: int,
) -> ExactGpCapacityContext:
    return ExactGpCapacityContext(
        raw_observation_count=effective_rows + (effective_rows + 9) // 10,
        effective_replicate_context_count=effective_rows,
        effective_training_rows=effective_rows,
        independent_validation_group_count=5,
        feature_count=feature_count,
        validation_strategy="grouped_kfold",
        requested_folds=folds,
        planned_quality_fit_count=folds,
        final_fit_count=1,
        total_fit_count=folds + 1,
        optimizer_restarts=restarts,
        recipe_max_rows=500,
        seed=BENCHMARK_SEED,
    )


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _process_peak_working_set_bytes() -> int:
    """Read process peak working set, including native NumPy allocations."""

    if os.name == "nt":
        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
        psapi = ctypes.WinDLL("psapi.dll", use_last_error=True)
        get_current_process = kernel32.GetCurrentProcess
        get_current_process.argtypes = []
        get_current_process.restype = wintypes.HANDLE
        get_process_memory_info = psapi.GetProcessMemoryInfo
        get_process_memory_info.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        get_process_memory_info.restype = wintypes.BOOL
        success = get_process_memory_info(
            get_current_process(),
            ctypes.byref(counters),
            counters.cb,
        )
        if not success:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(counters.PeakWorkingSetSize)
    import resource

    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value * (1024 if sys.platform != "darwin" else 1))


def _measure_case(
    case: tuple[int, int, int, int],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    effective_rows, feature_count, folds, restarts = case
    preflight_context = _capacity_context(
        effective_rows=effective_rows,
        feature_count=feature_count,
        folds=folds,
        restarts=restarts,
    )
    preflight = resolve_exact_gp_capacity(preflight_context)
    if preflight.decision == "approximate_required":
        started = time.perf_counter()
        preflight = resolve_exact_gp_capacity(preflight_context)
        wall = time.perf_counter() - started
        peak = _process_peak_working_set_bytes()
        return (
            {
                "capacity_resolution": preflight.model_dump(mode="json"),
                "metrics": {
                    "measurement_status": "measured_preflight",
                    "build_wall_seconds": round(wall, 6),
                    "peak_working_set_bytes": int(peak),
                    "memory_measurement": MEMORY_MEASUREMENT,
                    "convergence": {
                        "status": "not_started",
                        "reason": "hard capacity boundary stopped the build before an exact fit",
                    },
                    "artifact_size_bytes": 0,
                    "artifact_status": "not_created_due_to_preflight",
                    "prediction_latency_ms": None,
                    "total_fit_count": preflight_context.total_fit_count,
                    "capacity_decision": preflight.decision,
                },
            },
            None,
        )

    data = _fixture_training_set(
        effective_rows=effective_rows,
        feature_count=feature_count,
        folds=folds,
    )
    recipe = ExactGPEstimatorRecipe(
        restarts=restarts,
        folds=folds,
        seed=BENCHMARK_SEED,
        max_rows=500,
    )
    context = capacity_context_from_training_set(data, recipe)
    resolution = resolve_exact_gp_capacity(context)
    with tempfile.TemporaryDirectory(prefix="exact-gp-capacity-") as temporary:
        artifact = Path(temporary) / "target.npz"
        started = time.perf_counter()
        trained = exact_gp.train(data, recipe, artifact)
        wall = time.perf_counter() - started
        peak = _process_peak_working_set_bytes()
        latency_started = time.perf_counter()
        for _ in range(20):
            trained.predict(data.x[0])
        latency_ms = (time.perf_counter() - latency_started) * 1000.0 / 20.0
        measured = {
            "capacity_resolution": resolution.model_dump(mode="json"),
            "metrics": {
                "measurement_status": "measured_full_fit",
                "build_wall_seconds": round(wall, 6),
                "peak_working_set_bytes": int(peak),
                "memory_measurement": MEMORY_MEASUREMENT,
                "convergence": {
                    "converged_restarts": trained.diagnostics.get("converged_restarts"),
                    "restarts": trained.diagnostics.get("restarts"),
                },
                "artifact_size_bytes": artifact.stat().st_size,
                "prediction_latency_ms": round(latency_ms, 6),
                "total_fit_count": context.total_fit_count,
                "capacity_decision": resolution.decision,
            },
        }
        comparison = None
        if case == BASELINE:
            spike = evaluate_same_cohort(
                data,
                basis_count=SPIKE_DEFAULT_BASIS_COUNT,
                seed=BENCHMARK_SEED,
            )
            comparison = {
                "schema_version": CAPACITY_EVIDENCE_SCHEMA_VERSION,
                "cohort_digest": data.cohort_digest,
                "fold_digest": data.fold_digest,
                "validation_plan_digest": data.validation_plan_digest,
                "raw_observation_count": data.raw_observation_count,
                "effective_replicate_context_count": data.effective_replicate_context_count,
                "exact": {
                    "estimator_id": "exact-gp-rbf.v1",
                    "restarts": restarts,
                    "folds": folds,
                    "cohort_digest": data.cohort_digest,
                    "fold_digest": data.fold_digest,
                    "mae": trained.quality.mae,
                    "rmse": trained.quality.rmse,
                    "interval_coverage_90": trained.quality.interval_coverage_90,
                    "uncertainty": "normal predictive distribution",
                },
                "approximate": {
                    "estimator_id": SPIKE_ID,
                    "basis_count": spike.basis_count,
                    "seed": spike.seed,
                    "cohort_digest": spike.cohort_digest,
                    "fold_digest": spike.fold_digest,
                    "mae": spike.mae,
                    "rmse": spike.rmse,
                    "interval_coverage_90": spike.interval_coverage_90,
                    "uncertainty": spike.uncertainty_label,
                },
                "adoption_decision": "no_adopt",
                "adoption_rationale": (
                    "The fixed random-feature spike ran on the same cohort and folds, "
                    "but it has no allow-listed production Package/runtime adapter and "
                    "one bounded cohort is insufficient evidence for production uncertainty calibration."
                ),
                "alternative_path": {
                    "estimator_id": "ridge.v1",
                    "reason": "Use the compatible production baseline when exact GP capacity is exceeded; do not auto-switch."
                },
            }
        return measured, comparison


def _case(
    *,
    rows: int,
    features: int,
    folds: int,
    restarts: int,
    measured: dict[str, Any] | None,
) -> dict[str, Any]:
    context = _capacity_context(
        effective_rows=rows,
        feature_count=features,
        folds=folds,
        restarts=restarts,
    )
    resolution = resolve_exact_gp_capacity(context)
    estimate = estimate_exact_gp_capacity(context)
    if measured is not None:
        metrics = measured["metrics"]
        resolution_document = measured["capacity_resolution"]
        context_document = resolution_document["context"]
    else:
        metrics = {
            "measurement_status": "projected_from_versioned_policy",
            "build_wall_seconds": estimate.estimated_wall_seconds,
            "estimated_peak_memory_bytes": estimate.estimated_peak_memory_bytes,
            "memory_measurement": {
                **MEMORY_MEASUREMENT,
                "metric": "estimated_peak_memory_bytes",
                "scope": "versioned policy estimate; no native allocation measurement for projected case",
                "includes_native_allocations": "modeled, not measured",
            },
            "convergence": {
                "status": "not_measured",
                "reason": "this cross-combination was not in the bounded one-factor-at-a-time design",
            },
            "artifact_size_bytes": estimate.estimated_artifact_bytes,
            "prediction_latency_ms": estimate.estimated_prediction_latency_ms,
            "total_fit_count": context.total_fit_count,
            "capacity_decision": resolution.decision,
        }
        resolution_document = resolution.model_dump(mode="json")
        context_document = resolution_document["context"]
    return {
        "effective_rows": rows,
        "features": features,
        "folds": folds,
        "restarts": restarts,
        "raw_observation_count": context_document["raw_observation_count"],
        "effective_replicate_context_count": context_document[
            "effective_replicate_context_count"
        ],
        "planned_quality_fit_count": context_document["planned_quality_fit_count"],
        "final_fit_count": context_document["final_fit_count"],
        "total_fit_count": context_document["total_fit_count"],
        "optimizer_max_iterations": context_document["optimizer_max_iterations"],
        "capacity_resolution": resolution_document,
        "metrics": metrics,
    }


def build_report(*, commit_identity: str) -> dict[str, Any]:
    unique_actual_cases = tuple(dict.fromkeys(point for _role, point in MEASUREMENT_DESIGN))
    measured_cases: dict[tuple[int, int, int, int], dict[str, Any]] = {}
    comparison = None
    for point in unique_actual_cases:
        measured, candidate_comparison = _measure_case(point)
        measured_cases[point] = measured
        if candidate_comparison is not None:
            comparison = candidate_comparison
    assert comparison is not None
    cases = [
        _case(
            rows=rows,
            features=features,
            folds=folds,
            restarts=restarts,
            measured=measured_cases.get((rows, features, folds, restarts)),
        )
        for rows in (100, 250, 500, 750, 1000)
        for features in (8, 32, 64)
        for folds in (3, 5)
        for restarts in (1, 3)
    ]
    return {
        "schema_version": CAPACITY_EVIDENCE_SCHEMA_VERSION,
        "benchmark_id": "exact-gp-capacity-v1",
        "policy": {
            "policy_id": CAPACITY_POLICY_ID,
            "policy_version": CAPACITY_POLICY_VERSION,
            "effective_rows": [100, 250, 500, 750, 1000],
            "features": [8, 32, 64],
            "folds": [3, 5],
            "restarts": [1, 3],
            "bounded_actual_design": [
                {"role": role, "case": list(case)}
                for role, case in MEASUREMENT_DESIGN
            ],
            "actual_measurement_limit": {
                "design_points": len(MEASUREMENT_DESIGN),
                "unique_case_points": len(unique_actual_cases),
                "full_fit_points": sum(
                    item["metrics"]["measurement_status"] == "measured_full_fit"
                    for item in measured_cases.values()
                ),
                "preflight_points": sum(
                    item["metrics"]["measurement_status"] == "measured_preflight"
                    for item in measured_cases.values()
                ),
            },
            "memory_measurement": MEMORY_MEASUREMENT,
        },
        "environment": {
            "hardware": {
                "platform": platform.platform(),
                "machine": platform.machine(),
                "processor": platform.processor(),
                "cpu_count": os.cpu_count(),
            },
            "libraries": {
                "python": platform.python_version(),
                "numpy": np.__version__,
                "scipy": scipy.__version__,
            },
            "commit_identity": commit_identity,
            "seed": BENCHMARK_SEED,
        },
        "measurement_design": [
            {
                "role": role,
                "case": list(case),
                "measurement_status": measured_cases[case]["metrics"][
                    "measurement_status"
                ],
                "shared_with_case": list(BASELINE) if case == BASELINE and role != "baseline" else None,
            }
            for role, case in MEASUREMENT_DESIGN
        ],
        "cases": cases,
        "same_cohort_comparison": comparison,
        "adoption_memo": {
            "decision": "no_adopt",
            "recorded_by": "exact-gp-capacity-benchmark-v1",
            "rationale": comparison["adoption_rationale"],
            "next_safe_path": comparison["alternative_path"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--commit", default=None)
    args = parser.parse_args()
    report = build_report(commit_identity=_commit_identity(args.commit))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
