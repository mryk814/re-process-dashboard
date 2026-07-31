from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[3]
BACKEND_SRC = ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from material_workbench.bootstrap.resources import prepare_app_resources
from material_workbench.contracts.candidate_project_contracts import Candidate
from material_workbench.task_composition.ports import BatchPredictionRuntime
from material_workbench.tasks.task_registry import load_task_contracts


DEFAULT_TASKS = (
    "annealed-properties-v1",
    "flank-wear-v1",
    "battery-degradation-v1",
    "mpea-room-tensile-v1",
    "welding-stage-c-properties-v1",
)


def _candidate(task_id: str, index: int) -> Candidate:
    source = load_task_contracts()[task_id].canonical_candidate
    now = datetime.now(UTC)
    return Candidate(
        id=f"benchmark-{task_id}-{index:05d}",
        project_id="proposal-pool-benchmark",
        revision=1,
        created_at=now,
        updated_at=now,
        name=f"benchmark {index}",
        inputs={
            "composition": dict(source.composition),
            "process": dict(source.process),
            "categorical": dict(source.categorical),
            "heat_pattern": (
                None
                if source.heat_pattern is None
                else [item.model_dump() for item in source.heat_pattern]
            ),
            "heat_time_basis": "line_speed",
        },
        provenance=source.provenance,
    )


def _median_ms(operation: Callable[[], Any], repeats: int) -> float:
    elapsed = []
    for _ in range(repeats):
        started = time.perf_counter()
        operation()
        elapsed.append((time.perf_counter() - started) * 1000)
    return statistics.median(elapsed)


def _assert_batch_equivalence(runtime: Any, candidates: list[Candidate]) -> None:
    batch = runtime.predict_batch(candidates)
    scalar = [runtime.predict(candidate) for candidate in candidates]
    if [item["candidate_id"] for item in batch] != [
        candidate.id for candidate in candidates
    ]:
        raise RuntimeError("native batch changed candidate order")
    for batch_item, scalar_item in zip(batch, scalar, strict=True):
        if batch_item["predictions"] != scalar_item["predictions"]:
            raise RuntimeError("native batch changed predictive summaries")
        if batch_item["support"] != scalar_item["support"]:
            raise RuntimeError("native batch changed support evidence")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure proposal-pool runtime paths without persistence."
    )
    parser.add_argument(
        "--task",
        action="append",
        dest="tasks",
        help="Task ID. Repeat to benchmark more than one task.",
    )
    parser.add_argument("--count", type=int, default=512)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.count < 1 or args.repeats < 1:
        parser.error("--count and --repeats must be positive")

    resources = prepare_app_resources()
    results = []
    for task_id in args.tasks or DEFAULT_TASKS:
        runtime = resources.task_registry.runtime_for(task_id)
        candidates = [_candidate(task_id, index) for index in range(args.count)]
        sample = candidates[: min(3, len(candidates))]
        native_batch = (
            isinstance(runtime, BatchPredictionRuntime)
            and runtime.supports_batch_prediction
        )
        if native_batch:
            _assert_batch_equivalence(runtime, sample)

        full_scalar_ms = _median_ms(
            lambda: [runtime.predict(candidate) for candidate in candidates],
            args.repeats,
        )
        pool_scalar_ms = _median_ms(
            lambda: [
                (
                    runtime.predict_core(candidate),
                    runtime.support_summary(candidate),
                )
                for candidate in candidates
            ],
            args.repeats,
        )
        native_batch_ms = (
            _median_ms(
                lambda: runtime.predict_batch(candidates),
                args.repeats,
            )
            if native_batch
            else None
        )
        results.append(
            {
                "task_id": task_id,
                "count": args.count,
                "full_scalar_ms": round(full_scalar_ms, 3),
                "pool_scalar_ms": round(pool_scalar_ms, 3),
                "native_batch_ms": (
                    None
                    if native_batch_ms is None
                    else round(native_batch_ms, 3)
                ),
                "native_batch": native_batch,
            }
        )

    print(
        json.dumps(
            {
                "schema_version": "proposal-pool-benchmark/v1",
                "repeats": args.repeats,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
