"""Capacity probes for the production Data Lifecycle service and SQLite store.

The benchmark deliberately imports the real contracts, service, repository and
schema migration. It adds no benchmark-only table or persistence code.
"""

from __future__ import annotations

import ctypes
import gc
import hashlib
import json
import os
import sqlite3
import statistics
import threading
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, TypeVar

from pydantic import ValidationError

from material_workbench.application.data_lifecycle import DataLifecycleService
from material_workbench.contracts.data_lifecycle_contracts import (
    CurationRecipeCreateInput,
    CurationRunCreateInput,
    DatasetApprovalInput,
    ObjectSelection,
    SourceConnectorCreateInput,
    SourceFetchRequest,
    TrainingSnapshotCreateInput,
)
from material_workbench.persistence.store import Store


FixtureShape = Literal["narrow", "representative"]
SOURCE_FETCH_MAX_CHARACTERS = 5_000_000
NARROW_COLUMNS = ("id", "x", "target")
REPRESENTATIVE_COLUMNS = (
    "id",
    "x",
    "target",
    "temperature_c",
    "pressure_mpa",
    "speed_m_min",
    "hold_s",
    "carbon_pct",
    "manganese_pct",
    "silicon_pct",
    "chromium_pct",
    "nickel_pct",
    "aluminium_pct",
    "thickness_mm",
    "width_mm",
    "equipment",
    "route",
    "lot",
    "note_ja",
    "optional_value",
)
NUMERIC_COLUMNS = (
    "x",
    "target",
    "temperature_c",
    "pressure_mpa",
    "speed_m_min",
    "hold_s",
    "carbon_pct",
    "manganese_pct",
    "silicon_pct",
    "chromium_pct",
    "nickel_pct",
    "aluminium_pct",
    "thickness_mm",
    "width_mm",
    "optional_value",
)
T = TypeVar("T")


@dataclass(frozen=True)
class MeasuredOperation:
    value: Any
    elapsed_ms: float
    traced_peak_extra_bytes: int
    process_memory: dict[str, Any] | None

    def metrics(self) -> dict[str, Any]:
        return {
            "elapsed_ms": self.elapsed_ms,
            "traced_peak_extra_bytes": self.traced_peak_extra_bytes,
            "process_memory": self.process_memory,
        }


def fixture_columns(shape: FixtureShape) -> tuple[str, ...]:
    return NARROW_COLUMNS if shape == "narrow" else REPRESENTATIVE_COLUMNS


def _fixture_row(
    index: int,
    *,
    shape: FixtureShape,
    revision: int,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": f"r{index:06d}",
        "x": index % 101,
        "target": ((index * 7) + revision) % 113,
    }
    if shape == "representative":
        row.update(
            {
                "temperature_c": 650 + index % 251,
                "pressure_mpa": round(0.1 + (index % 90) / 10, 1),
                "speed_m_min": 20 + index % 181,
                "hold_s": index % 601,
                "carbon_pct": round(0.02 + (index % 80) / 100, 3),
                "manganese_pct": round(0.5 + (index % 180) / 100, 3),
                "silicon_pct": round((index % 90) / 100, 3),
                "chromium_pct": round((index % 120) / 100, 3),
                "nickel_pct": round((index % 70) / 100, 3),
                "aluminium_pct": round((index % 15) / 100, 3),
                "thickness_mm": round(0.6 + (index % 45) / 10, 2),
                "width_mm": 600 + index % 1_201,
                "equipment": f"EQ-{index % 12:02d}",
                "route": ("標準", "急冷", "再加熱")[index % 3],
                "lot": f"LOT-{index:08d}",
                "note_ja": f"測定条件 {index % 37}",
                "optional_value": None if index % 17 == 0 else index % 29,
            }
        )
    return row


def synthetic_payload(
    row_count: int,
    *,
    shape: FixtureShape = "narrow",
    revision: int = 0,
) -> str:
    if row_count < 1:
        raise ValueError("row_count must be positive")
    return (
        "["
        + ",".join(
            json.dumps(
                _fixture_row(index, shape=shape, revision=revision),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            for index in range(row_count)
        )
        + "]"
    )


def fixture_metadata(content: str, shape: FixtureShape) -> dict[str, Any]:
    encoded = content.encode("utf-8")
    try:
        SourceFetchRequest(
            object_content=content,
            object_version="contract-feasibility",
        )
        contract_status = "accepted"
    except ValidationError:
        contract_status = "rejected"
    return {
        "shape": shape,
        "column_count": len(fixture_columns(shape)),
        "columns": list(fixture_columns(shape)),
        "characters": len(content),
        "utf8_bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "production_fetch_contract": contract_status,
        "source_fetch_max_characters": SOURCE_FETCH_MAX_CHARACTERS,
    }


def _measure(operation: Callable[[], T]) -> MeasuredOperation:
    gc.collect()
    current_before, _ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()
    process_before = _process_memory()
    samples: list[int] = []
    stop_sampling = threading.Event()

    def sample_working_set() -> None:
        while not stop_sampling.wait(0.01):
            memory = _process_memory()
            if memory is not None:
                samples.append(memory["working_set_bytes"])

    sampler = (
        threading.Thread(target=sample_working_set, daemon=True)
        if process_before is not None
        else None
    )
    if sampler is not None:
        sampler.start()
    started = time.perf_counter()
    try:
        value = operation()
    finally:
        elapsed_ms = round((time.perf_counter() - started) * 1_000, 3)
        stop_sampling.set()
        if sampler is not None:
            sampler.join(timeout=1)
    process_after = _process_memory()
    _, peak = tracemalloc.get_traced_memory()
    sampled_peak = max(
        [
            *(samples or []),
            *(
                [process_before["working_set_bytes"]]
                if process_before is not None
                else []
            ),
            *(
                [process_after["working_set_bytes"]]
                if process_after is not None
                else []
            ),
        ],
        default=0,
    )
    return MeasuredOperation(
        value=value,
        elapsed_ms=elapsed_ms,
        traced_peak_extra_bytes=max(0, peak - current_before),
        process_memory=(
            {
                "before": process_before,
                "after": process_after,
                "sampled_peak_working_set_bytes": sampled_peak,
                "sampled_peak_extra_over_before_bytes": max(
                    0,
                    sampled_peak - process_before["working_set_bytes"],
                ),
                "sample_count": len(samples),
            }
            if process_before is not None
            else None
        ),
    )


def _process_memory() -> dict[str, int] | None:
    if os.name != "nt":
        return None

    class ProcessMemoryCounters(ctypes.Structure):
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

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(ProcessMemoryCounters),
        ctypes.c_ulong,
    )
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    process = kernel32.GetCurrentProcess()
    if not psapi.GetProcessMemoryInfo(
        process,
        ctypes.byref(counters),
        counters.cb,
    ):
        return None
    return {
        "working_set_bytes": int(counters.WorkingSetSize),
        "peak_working_set_bytes": int(counters.PeakWorkingSetSize),
        "pagefile_bytes": int(counters.PagefileUsage),
        "peak_pagefile_bytes": int(counters.PeakPagefileUsage),
    }


def _storage_metrics(database: Path) -> dict[str, Any]:
    if not database.exists():
        return {
            "file_bytes": 0,
            "allocated_bytes": 0,
            "page_count": 0,
            "page_size": 0,
            "freelist_count": 0,
            "payload_bytes_total": 0,
            "payload_bytes_by_table": {},
        }
    tables = (
        "source_connectors",
        "source_fetch_attempts",
        "raw_source_snapshots",
        "curation_recipes",
        "source_curation_runs",
        "canonical_dataset_approvals",
        "approved_training_snapshots",
    )
    with sqlite3.connect(database) as connection:
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        freelist_count = int(
            connection.execute("PRAGMA freelist_count").fetchone()[0]
        )
        payload_by_table = {
            table: int(
                connection.execute(
                    f"SELECT COALESCE(SUM(length(CAST(payload AS BLOB))), 0) "
                    f"FROM {table}"  # table is an internal benchmark allow-list
                ).fetchone()[0]
            )
            for table in tables
        }
    return {
        "file_bytes": database.stat().st_size,
        "allocated_bytes": page_size * page_count,
        "page_count": page_count,
        "page_size": page_size,
        "freelist_count": freelist_count,
        "payload_bytes_total": sum(payload_by_table.values()),
        "payload_bytes_by_table": payload_by_table,
    }


def _connector_input(
    case_id: str,
    shape: FixtureShape,
) -> SourceConnectorCreateInput:
    return SourceConnectorCreateInput(
        name=f"Data Lifecycle benchmark {case_id}",
        connector_type="object_storage_json_v1",
        source_locator=f"s3://benchmark.local/{case_id}.json",
        selection=ObjectSelection(
            format="json_array",
            primary_key="id",
            included_fields=fixture_columns(shape),
        ),
    )


def _recipe_input(
    case_id: str,
    shape: FixtureShape,
) -> CurationRecipeCreateInput:
    numeric = tuple(
        column for column in fixture_columns(shape) if column in NUMERIC_COLUMNS
    )
    return CurationRecipeCreateInput(
        recipe_id=f"data-lifecycle-benchmark-{case_id}",
        version=1,
        name=f"Data Lifecycle benchmark {case_id}",
        steps=(
            {"kind": "coerce_number_v1", "fields": numeric},
            {"kind": "required_fields_v1", "fields": ["id", "x"]},
            {"kind": "target_eligibility_v1", "fields": ["target"]},
        ),
    )


def _read_fixture(
    *,
    fixture_path: Path | None,
    row_count: int,
    shape: FixtureShape,
) -> str:
    return (
        fixture_path.read_text(encoding="utf-8")
        if fixture_path is not None
        else synthetic_payload(row_count, shape=shape)
    )


def run_benchmark_case(
    *,
    row_count: int,
    shape: FixtureShape,
    workspace: Path,
    environment_label: str,
    fixture_path: Path | None = None,
) -> dict[str, Any]:
    """Run a complete immutable lifecycle in one fresh worker/database."""

    workspace.mkdir(parents=True, exist_ok=True)
    database = workspace / "workbench.db"
    if database.exists():
        raise FileExistsError(f"benchmark database already exists: {database}")
    case_id = f"{environment_label}-{shape}-{row_count}".replace(" ", "-")
    memory_before_fixture = _process_memory()
    tracemalloc.start()
    content = _read_fixture(
        fixture_path=fixture_path,
        row_count=row_count,
        shape=shape,
    )
    metadata = fixture_metadata(content, shape)
    memory_after_fixture = _process_memory()
    if metadata["production_fetch_contract"] == "rejected":
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return {
            "schema_version": "data-lifecycle-benchmark-case/v2",
            "status": "skipped_source_fetch_contract",
            "environment_label": environment_label,
            "row_count": row_count,
            "fixture": metadata,
            "memory": {
                "before_fixture": memory_before_fixture,
                "after_fixture": memory_after_fixture,
                "python_traced_current_bytes": current,
                "python_traced_peak_bytes": peak,
            },
        }

    storage: dict[str, Any] = {"initial": _storage_metrics(database)}
    timings: dict[str, dict[str, int | float]] = {}

    startup = _measure(lambda: Store(database))
    timings["fresh_store_startup"] = startup.metrics()
    del startup
    storage["after_store"] = _storage_metrics(database)

    service = DataLifecycleService(database)
    connector = service.create_connector(_connector_input(case_id, shape))
    recipe = service.create_recipe(_recipe_input(case_id, shape))
    storage["after_setup"] = _storage_metrics(database)

    fetched = _measure(
        lambda: service.fetch(
            connector.id,
            SourceFetchRequest(
                object_content=content,
                object_version=f"fixture-{row_count}",
            ),
        )
    )
    raw_snapshot_id = fetched.value[0].id
    reused = fetched.value[1].reused_existing_snapshot
    timings["fetch_snapshot_save"] = fetched.metrics()
    del fetched
    gc.collect()
    storage["after_fetch"] = _storage_metrics(database)

    curated = _measure(
        lambda: service.curate(
            raw_snapshot_id,
            CurationRunCreateInput(
                recipe_resource_id=recipe.id,
                profile_revision_id="benchmark-profile@1",
                profile_digest="sha256:benchmark-profile",
            ),
        )
    )
    curation_run_id = curated.value.id
    quality = curated.value.quality.model_dump(mode="json")
    timings["curation_save"] = curated.metrics()
    del curated
    gc.collect()
    storage["after_curation"] = _storage_metrics(database)

    approved = _measure(
        lambda: service.approve(
            curation_run_id,
            DatasetApprovalInput(
                actor="benchmark",
                reason="capacity benchmark",
            ),
        )
    )
    canonical_revision_id = approved.value.id
    timings["canonical_approval_save"] = approved.metrics()
    del approved
    gc.collect()
    storage["after_approval"] = _storage_metrics(database)

    training = _measure(
        lambda: service.create_training_snapshot(
            canonical_revision_id,
            TrainingSnapshotCreateInput(
                actor="benchmark",
                purpose="capacity benchmark",
            ),
        )
    )
    training_row_count = training.value.row_count
    timings["training_snapshot_save"] = training.metrics()
    del training
    gc.collect()
    storage["after_training_snapshot"] = _storage_metrics(database)

    detail = _measure(lambda: service.detail(connector.id))
    counts = {
        "raw_snapshots": len(detail.value.raw_snapshots),
        "curation_runs": len(detail.value.curation_runs),
        "canonical_revisions": len(detail.value.canonical_revisions),
        "training_snapshots": len(detail.value.training_snapshots),
        "training_rows": training_row_count,
    }
    timings["detail_load"] = detail.metrics()
    serialized = _measure(
        lambda: detail.value.model_dump_json().encode("utf-8")
    )
    detail_payload_bytes = len(serialized.value)
    timings["detail_json_serialization"] = serialized.metrics()
    del serialized, detail
    gc.collect()
    storage["final"] = _storage_metrics(database)

    memory_final = _process_memory()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    source_bytes = metadata["utf8_bytes"]
    lifecycle_file_increment = (
        storage["final"]["file_bytes"] - storage["after_setup"]["file_bytes"]
    )
    lifecycle_payload_increment = (
        storage["final"]["payload_bytes_total"]
        - storage["after_setup"]["payload_bytes_total"]
    )

    with sqlite3.connect(database) as connection:
        journal_mode = str(
            connection.execute("PRAGMA journal_mode").fetchone()[0]
        ).lower()
        foreign_key_violations = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

    return {
        "schema_version": "data-lifecycle-benchmark-case/v2",
        "status": "measured",
        "environment_label": environment_label,
        "row_count": row_count,
        "fixture": metadata,
        "quality": quality,
        "counts": counts,
        "correctness": {
            "fetch_reused_existing_snapshot": reused,
            "foreign_key_violations": len(foreign_key_violations),
            "row_count_matches": training_row_count == row_count,
        },
        "timings": timings,
        "detail_payload_bytes": detail_payload_bytes,
        "database": {
            "phases": storage,
            "journal_mode": journal_mode,
            "lifecycle_file_increment_bytes": lifecycle_file_increment,
            "lifecycle_payload_increment_bytes": lifecycle_payload_increment,
            "file_amplification_over_source": round(
                lifecycle_file_increment / source_bytes,
                3,
            ),
            "payload_amplification_over_source": round(
                lifecycle_payload_increment / source_bytes,
                3,
            ),
        },
        "memory": {
            "before_fixture": memory_before_fixture,
            "after_fixture": memory_after_fixture,
            "final": memory_final,
            "python_traced_current_bytes": current,
            "python_traced_peak_bytes": peak,
        },
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(
        ordered[lower] + (ordered[upper] - ordered[lower]) * fraction,
        3,
    )


def run_concurrency_probe(
    *,
    workspace: Path,
    row_count: int = 250,
    iterations: int = 10,
    include_forced_lock: bool = True,
) -> dict[str, Any]:
    """Overlap four real reads and one changed Snapshot write per iteration."""

    workspace.mkdir(parents=True, exist_ok=True)
    database = workspace / "workbench.db"
    if database.exists():
        raise FileExistsError(f"benchmark database already exists: {database}")
    Store(database)
    service = DataLifecycleService(database)
    connector = service.create_connector(_connector_input("concurrency", "narrow"))
    first = synthetic_payload(row_count, revision=0)
    service.fetch(
        connector.id,
        SourceFetchRequest(object_content=first, object_version="initial"),
    )

    operations: list[dict[str, Any]] = []
    successful_writes = 0
    for revision in range(1, iterations + 1):
        barrier = threading.Barrier(5)
        changed = synthetic_payload(row_count, revision=revision)

        def reader(reader_index: int) -> dict[str, Any]:
            barrier.wait()
            started = time.perf_counter()
            try:
                DataLifecycleService(database).detail(connector.id)
                return {
                    "kind": "read",
                    "index": reader_index,
                    "status": "succeeded",
                    "elapsed_ms": round(
                        (time.perf_counter() - started) * 1_000,
                        3,
                    ),
                }
            except Exception as exc:  # result must retain all reliability failures
                return {
                    "kind": "read",
                    "index": reader_index,
                    "status": "failed",
                    "elapsed_ms": round(
                        (time.perf_counter() - started) * 1_000,
                        3,
                    ),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }

        def writer() -> dict[str, Any]:
            barrier.wait()
            started = time.perf_counter()
            try:
                DataLifecycleService(database).fetch(
                    connector.id,
                    SourceFetchRequest(
                        object_content=changed,
                        object_version=f"revision-{revision}",
                    ),
                )
                return {
                    "kind": "write",
                    "status": "succeeded",
                    "elapsed_ms": round(
                        (time.perf_counter() - started) * 1_000,
                        3,
                    ),
                }
            except Exception as exc:
                return {
                    "kind": "write",
                    "status": "failed",
                    "elapsed_ms": round(
                        (time.perf_counter() - started) * 1_000,
                        3,
                    ),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [
                *(executor.submit(reader, index) for index in range(4)),
                executor.submit(writer),
            ]
            batch = [future.result() for future in futures]
        operations.extend(batch)
        successful_writes += sum(
            item["kind"] == "write" and item["status"] == "succeeded"
            for item in batch
        )

    forced_lock: dict[str, Any] | None = None
    if include_forced_lock:
        lock = sqlite3.connect(database, timeout=1)
        lock.execute("BEGIN EXCLUSIVE")
        lock_started = time.perf_counter()
        blocked_write_started = threading.Event()

        def blocked_write() -> dict[str, Any]:
            started = time.perf_counter()
            blocked_write_started.set()
            connection: sqlite3.Connection | None = None
            try:
                connection = sqlite3.connect(database, timeout=5)
                connection.execute("PRAGMA busy_timeout=5000")
                connection.execute("BEGIN IMMEDIATE")
                return {
                    "status": "unexpected_success",
                    "elapsed_ms": round(
                        (time.perf_counter() - started) * 1_000,
                        3,
                    ),
                }
            except Exception as exc:
                return {
                    "status": "expected_failure",
                    "elapsed_ms": round(
                        (time.perf_counter() - started) * 1_000,
                        3,
                    ),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            finally:
                if connection is not None:
                    connection.close()

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(blocked_write)
            if not blocked_write_started.wait(timeout=2):
                raise RuntimeError("forced-lock writer did not start")
            # Keep the calibration lock well beyond the configured 5 second
            # busy timeout. Windows scheduling can otherwise release a
            # 6-second lock just as SQLite completes its retry loop.
            time.sleep(12.5)
            lock.rollback()
            forced_lock = future.result()
        lock.close()
        forced_lock["lock_held_ms"] = round(
            (time.perf_counter() - lock_started) * 1_000,
            3,
        )

    read_latencies = [
        item["elapsed_ms"] for item in operations if item["kind"] == "read"
    ]
    write_latencies = [
        item["elapsed_ms"] for item in operations if item["kind"] == "write"
    ]
    with sqlite3.connect(database) as connection:
        foreign_key_violations = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
    detail = service.detail(connector.id)
    return {
        "schema_version": "data-lifecycle-concurrency-probe/v1",
        "row_count": row_count,
        "iterations": iterations,
        "read_operations": len(read_latencies),
        "write_operations": len(write_latencies),
        "failed_operations": sum(
            item["status"] == "failed" for item in operations
        ),
        "sqlite_busy_operations": sum(
            "locked" in item.get("error", "").lower()
            or "busy" in item.get("error", "").lower()
            for item in operations
        ),
        "read_latency_ms": {
            "median": round(statistics.median(read_latencies), 3),
            "p95": _percentile(read_latencies, 0.95),
            "max": max(read_latencies),
        },
        "write_latency_ms": {
            "median": round(statistics.median(write_latencies), 3),
            "p95": _percentile(write_latencies, 0.95),
            "max": max(write_latencies),
        },
        "correctness": {
            "successful_writes": successful_writes,
            "expected_raw_snapshots": 1 + successful_writes,
            "actual_raw_snapshots": len(detail.raw_snapshots),
            "foreign_key_violations": len(foreign_key_violations),
        },
        "forced_lock_calibration": forced_lock,
        "operations": operations,
    }


def run_history_probe(
    *,
    workspace: Path,
    row_count: int = 250,
    history_depth: int = 10,
    unrelated_connectors: int = 10,
) -> dict[str, Any]:
    """Expose per-connector history and global-table Python filtering costs."""

    workspace.mkdir(parents=True, exist_ok=True)
    database = workspace / "workbench.db"
    if database.exists():
        raise FileExistsError(f"benchmark database already exists: {database}")
    Store(database)
    service = DataLifecycleService(database)
    recipe = service.create_recipe(_recipe_input("history", "narrow"))
    target = service.create_connector(_connector_input("history-target", "narrow"))
    fetch_ms: list[float] = []
    curate_ms: list[float] = []

    def add_revision(connector_id: str, revision: int) -> None:
        payload = synthetic_payload(row_count, revision=revision)
        fetched = _measure(
            lambda: service.fetch(
                connector_id,
                SourceFetchRequest(
                    object_content=payload,
                    object_version=f"revision-{revision}",
                ),
            )
        )
        fetch_ms.append(fetched.elapsed_ms)
        raw_id = fetched.value[0].id
        curated = _measure(
            lambda: service.curate(
                raw_id,
                CurationRunCreateInput(
                    recipe_resource_id=recipe.id,
                    profile_revision_id="benchmark-profile@1",
                    profile_digest="sha256:benchmark-profile",
                ),
            )
        )
        curate_ms.append(curated.elapsed_ms)
        approved = service.approve(
            curated.value.id,
            DatasetApprovalInput(actor="benchmark", reason="history probe"),
        )
        service.create_training_snapshot(
            approved.id,
            TrainingSnapshotCreateInput(
                actor="benchmark",
                purpose="history probe",
            ),
        )

    tracemalloc.start()
    for revision in range(history_depth):
        add_revision(target.id, revision)
    def detail_samples() -> tuple[list[float], dict[str, int]]:
        service.detail(target.id)  # warm-up is not part of the distribution
        measurements = [
            _measure(lambda: service.detail(target.id)) for _ in range(5)
        ]
        counts = {
            "raw_snapshots": len(measurements[-1].value.raw_snapshots),
            "curation_runs": len(measurements[-1].value.curation_runs),
        }
        elapsed = [measurement.elapsed_ms for measurement in measurements]
        del measurements
        gc.collect()
        return elapsed, counts

    before_elapsed, before_counts = detail_samples()

    for index in range(unrelated_connectors):
        unrelated = service.create_connector(
            _connector_input(f"unrelated-{index}", "narrow")
        )
        add_revision(unrelated.id, history_depth + index)
    after_elapsed, after_counts = detail_samples()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "schema_version": "data-lifecycle-history-probe/v1",
        "row_count": row_count,
        "history_depth": history_depth,
        "unrelated_connectors": unrelated_connectors,
        "fetch_latency_ms": {
            "first": fetch_ms[0],
            "target_last": fetch_ms[history_depth - 1],
            "all_max": max(fetch_ms),
        },
        "curation_latency_ms": {
            "first": curate_ms[0],
            "target_last": curate_ms[history_depth - 1],
            "all_max": max(curate_ms),
        },
        "target_detail_before_unrelated": {
            "median_ms": round(statistics.median(before_elapsed), 3),
            "max_ms": max(before_elapsed),
            "samples_ms": before_elapsed,
            "counts": before_counts,
        },
        "target_detail_after_unrelated": {
            "median_ms": round(statistics.median(after_elapsed), 3),
            "max_ms": max(after_elapsed),
            "samples_ms": after_elapsed,
            "counts": after_counts,
        },
        "unrelated_detail_slowdown_ratio": round(
            statistics.median(after_elapsed)
            / max(statistics.median(before_elapsed), 0.001),
            3,
        ),
        "database": _storage_metrics(database),
        "memory": {
            "python_traced_current_bytes": current,
            "python_traced_peak_bytes": peak,
            "process": _process_memory(),
        },
    }


def summarize_report(
    *,
    results: list[dict[str, Any]],
    history_probe: dict[str, Any] | None,
    concurrency_probe: dict[str, Any] | None,
    packaged_results: dict[str, Any] | None = None,
) -> dict[str, Any]:
    groups: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for result in results:
        key = (result["fixture"]["shape"], result["row_count"])
        groups.setdefault(key, []).append(result)
    aggregated = []
    for (shape, row_count), cases in sorted(
        groups.items(),
        key=lambda item: (item[0][1], item[0][0]),
    ):
        measured = [case for case in cases if case["status"] == "measured"]
        item: dict[str, Any] = {
            "shape": shape,
            "row_count": row_count,
            "repeats": len(cases),
            "status": "measured" if measured else cases[0]["status"],
            "fixture": cases[0]["fixture"],
        }
        if measured:
            item["median_ms"] = {
                operation: round(
                    statistics.median(
                        case["timings"][operation]["elapsed_ms"]
                        for case in measured
                    ),
                    3,
                )
                for operation in measured[0]["timings"]
            }
            item["max_ms"] = {
                operation: max(
                    case["timings"][operation]["elapsed_ms"]
                    for case in measured
                )
                for operation in measured[0]["timings"]
            }
            item["median_database"] = {
                "lifecycle_file_increment_bytes": round(
                    statistics.median(
                        case["database"]["lifecycle_file_increment_bytes"]
                        for case in measured
                    )
                ),
                "file_amplification_over_source": round(
                    statistics.median(
                        case["database"]["file_amplification_over_source"]
                        for case in measured
                    ),
                    3,
                ),
            }
            item["max_process_peak_working_set_bytes"] = max(
                (
                    case["memory"]["final"] or {}
                ).get("peak_working_set_bytes", 0)
                for case in measured
            )
            item["detail_payload_bytes"] = max(
                case["detail_payload_bytes"] for case in measured
            )
        aggregated.append(item)

    representative_100k = next(
        (
            item
            for item in aggregated
            if item["shape"] == "representative"
            and item["row_count"] == 100_000
        ),
        None,
    )
    narrow_100k = next(
        (
            item
            for item in aggregated
            if item["shape"] == "narrow" and item["row_count"] == 100_000
        ),
        None,
    )
    measured_cases = [
        case for case in results if case["status"] == "measured"
    ]
    max_phase_rss_delta = max(
        (
            timing.get("process_memory", {}).get(
                "sampled_peak_extra_over_before_bytes",
                0,
            )
            for case in measured_cases
            for timing in case["timings"].values()
            if timing.get("process_memory") is not None
        ),
        default=0,
    )
    max_process_peak = max(
        (
            (case["memory"]["final"] or {}).get(
                "peak_working_set_bytes",
                0,
            )
            for case in measured_cases
        ),
        default=0,
    )
    max_file_increment = max(
        (
            case["database"]["lifecycle_file_increment_bytes"]
            for case in measured_cases
        ),
        default=0,
    )
    max_file_amplification = max(
        (
            case["database"]["file_amplification_over_source"]
            for case in measured_cases
        ),
        default=0,
    )
    max_fetch_or_curation_median = max(
        (
            max(
                item.get("median_ms", {}).get("fetch_snapshot_save", 0),
                item.get("median_ms", {}).get("curation_save", 0),
            )
            for item in aggregated
            if item["status"] == "measured"
        ),
        default=0,
    )
    max_detail_payload = max(
        (
            item.get("detail_payload_bytes", 0)
            for item in aggregated
            if item["status"] == "measured"
        ),
        default=0,
    )
    max_detail_median = max(
        (
            item.get("median_ms", {}).get("detail_load", 0)
            for item in aggregated
            if item["status"] == "measured"
        ),
        default=0,
    )
    ingress_met = bool(
        representative_100k
        and representative_100k["status"]
        == "skipped_source_fetch_contract"
    )
    persistence_hard_met = (
        max_process_peak > 2 * 1024**3
        or max_file_increment > 1024**3
    )
    persistence_soft = {
        "database_size_and_amplification": (
            max_file_amplification > 4
            and max_file_increment > 250 * 1024**2
        ),
        "phase_peak_working_set": max_phase_rss_delta > 512 * 1024**2,
        "operation_latency": max_fetch_or_curation_median > 10_000,
    }
    persistence_soft_count = sum(persistence_soft.values())
    persistence_externalize = (
        persistence_hard_met or persistence_soft_count >= 2
    )
    concurrency_failures = (
        0
        if concurrency_probe is None
        else concurrency_probe["failed_operations"]
        + concurrency_probe["sqlite_busy_operations"]
    )
    read_ui_met = (
        max_detail_payload > 10 * 1024**2 or max_detail_median > 2_000
    )
    history_ratio = (
        None
        if history_probe is None
        else history_probe["unrelated_detail_slowdown_ratio"]
    )
    startup_status = "inconclusive"
    startup_evidence: dict[str, Any] = {
        "reason": "packaged results have not been attached"
    }
    if packaged_results is not None:
        modes = [
            value
            for value in packaged_results.values()
            if isinstance(value, dict)
            and "launchToFirstUsableMs" in value
        ]
        if modes:
            startup_status = (
                "met"
                if any(
                    value["launchToFirstUsableMs"] > 10_000
                    for value in modes
                )
                else "not_met"
            )
            startup_evidence = {
                "modes": {
                    value["mode"]: {
                        "launchToFirstUsableMs": value[
                            "launchToFirstUsableMs"
                        ],
                        "restartToFirstUsableMs": value.get(
                            "restartToFirstUsableMs"
                        ),
                    }
                    for value in modes
                }
            }

    return {
        "schema_version": "data-lifecycle-benchmark-report/v2",
        "thresholds_fixed_before_measurement": {
            "hard": {
                "representative_ingress_rejected": True,
                "normal_concurrency_busy_or_error_count": 1,
                "peak_working_set_bytes": 2 * 1024**3,
                "three_revision_database_increment_bytes": 1024**3,
            },
            "soft_persistence": {
                "database_amplification_ratio": 4,
                "single_lifecycle_database_increment_bytes": 250 * 1024**2,
                "phase_peak_working_set_delta_bytes": 512 * 1024**2,
                "fetch_or_curation_median_ms": 10_000,
            },
            "soft_read_ui": {
                "detail_p95_ms": 2_000,
                "detail_response_bytes": 10 * 1024**2,
                "unrelated_history_slowdown_ratio": 2,
            },
            "startup": {
                "populated_vs_empty_health_delta_ms": 2_000,
                "packaged_first_usable_observed_ms": 10_000,
            },
        },
        "decision_rule": {
            "ingress_hard_trigger": "file/object connector ingressを分離する",
            "read_ui_trigger": "summary・pagination・filter-before-parseを実装する",
            "persistence_trigger": (
                "hard trigger 1件、またはsoft persistence 2件以上で"
                "content-addressed payload化Issueを作る"
            ),
            "excluded_as_single_reason": (
                "portable path差、forced lock、narrow 100万行だけでは"
                "外部payload化しない"
            ),
        },
        "results": results,
        "aggregated_results": aggregated,
        "history_probe": history_probe,
        "concurrency_probe": concurrency_probe,
        "packaged_results": packaged_results,
        "million_row_feasibility": {
            "status": "skipped_source_fetch_contract",
            "reason": (
                "100万行は100k narrowの約10倍で現行5,000,000文字契約を"
                "明確に超える。production契約を迂回して測定しない"
            ),
            "projected_narrow_characters_from_100k": (
                None
                if narrow_100k is None
                else narrow_100k["fixture"]["characters"] * 10
            ),
            "projected_representative_characters_from_100k": (
                None
                if representative_100k is None
                else representative_100k["fixture"]["characters"] * 10
            ),
        },
        "trigger_evaluation": {
            "ingress_contract": {
                "status": "met" if ingress_met else "not_met",
                "evidence": (
                    representative_100k["fixture"]
                    if representative_100k is not None
                    else None
                ),
            },
            "persistence_hard": {
                "status": "met" if persistence_hard_met else "not_met",
                "evidence": {
                    "max_process_peak_working_set_bytes": max_process_peak,
                    "max_lifecycle_file_increment_bytes": max_file_increment,
                },
            },
            "persistence_soft": {
                "status": (
                    "met"
                    if persistence_soft_count >= 2
                    else "not_met"
                ),
                "evidence": {
                    **persistence_soft,
                    "trigger_count": persistence_soft_count,
                    "max_file_amplification": max_file_amplification,
                    "max_phase_rss_delta_bytes": max_phase_rss_delta,
                    "max_fetch_or_curation_median_ms": (
                        max_fetch_or_curation_median
                    ),
                },
            },
            "read_ui": {
                "status": "met" if read_ui_met else "not_met",
                "evidence": {
                    "max_detail_payload_bytes": max_detail_payload,
                    "max_detail_median_ms": max_detail_median,
                    "unrelated_history_slowdown_ratio": history_ratio,
                },
            },
            "normal_concurrency": {
                "status": (
                    "inconclusive"
                    if concurrency_probe is None
                    else ("met" if concurrency_failures else "not_met")
                ),
                "evidence": concurrency_probe,
            },
            "packaged_startup": {
                "status": startup_status,
                "evidence": startup_evidence,
            },
        },
        "recommended_decisions": {
            "source_ingress": (
                "create_file_or_object_ingress_followup"
                if ingress_met
                else "keep_current_request_contract"
            ),
            "row_payload_persistence": (
                "create_content_addressed_payload_followup"
                if persistence_externalize
                else "keep_sqlite_text_for_current_supported_envelope"
            ),
            "detail_api": (
                "create_summary_pagination_filter_before_parse_followup"
                if read_ui_met
                else "keep_current_detail_response"
            ),
            "sqlite_concurrency": (
                "investigate_normal_busy_failures"
                if concurrency_failures
                else "keep_current_delete_full_policy"
            ),
            "limitation": (
                "representative 100k persistence is not measured because "
                "the production ingress contract rejects it"
            ),
        },
    }
