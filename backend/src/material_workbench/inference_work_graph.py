"""Process-local inference work cache and diagnostics.

The cache is deliberately bounded and non-persistent.  Its identity contains
every versioned input that can change the meaning of one inference operation;
candidate labels and UI selection state never participate in the key.
"""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from threading import RLock
from time import perf_counter
from typing import Any, Callable, Mapping


def semantic_digest(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class InferenceKey:
    task_id: str
    runtime_type: str
    canonical_input_digest: str
    package_digest: str
    pipeline_digest: str
    support_digest: str
    operation: str
    operation_parameters_digest: str

    @classmethod
    def build(
        cls,
        *,
        task_id: str,
        runtime_type: str,
        canonical_input: Any,
        package_digest: str,
        pipeline_digest: str,
        support_digest: str | None,
        operation: str,
        operation_parameters: Mapping[str, Any] | None = None,
    ) -> "InferenceKey":
        return cls(
            task_id=task_id,
            runtime_type=runtime_type,
            canonical_input_digest=semantic_digest(canonical_input),
            package_digest=package_digest,
            pipeline_digest=pipeline_digest,
            support_digest=support_digest or "",
            operation=operation,
            operation_parameters_digest=semantic_digest(operation_parameters or {}),
        )


@dataclass
class _OperationStats:
    runtime_types: set[str] | None = None
    hits: int = 0
    misses: int = 0
    coalesced: int = 0
    computations: int = 0
    computation_duration_ms_total: float = 0.0
    computation_duration_ms_last: float = 0.0
    computation_duration_ms_max: float = 0.0
    total_duration_ms_total: float = 0.0
    total_duration_ms_last: float = 0.0
    total_duration_ms_max: float = 0.0

    def record_computation(self, duration_ms: float) -> None:
        self.computations += 1
        self.computation_duration_ms_total += duration_ms
        self.computation_duration_ms_last = duration_ms
        self.computation_duration_ms_max = max(self.computation_duration_ms_max, duration_ms)

    def record_total(self, duration_ms: float) -> None:
        self.total_duration_ms_total += duration_ms
        self.total_duration_ms_last = duration_ms
        self.total_duration_ms_max = max(self.total_duration_ms_max, duration_ms)

    @staticmethod
    def _duration(total: float, last: float, maximum: float, count: int) -> dict[str, float]:
        return {
            "total": round(total, 3),
            "last": round(last, 3),
            "max": round(maximum, 3),
            "average": round(total / count, 3) if count else 0.0,
        }

    def snapshot(self) -> dict[str, Any]:
        requests = self.hits + self.misses
        return {
            "runtime_types": sorted(self.runtime_types or set()),
            "hits": self.hits,
            "misses": self.misses,
            "coalesced": self.coalesced,
            "computations": self.computations,
            "computation_duration_ms": self._duration(
                self.computation_duration_ms_total,
                self.computation_duration_ms_last,
                self.computation_duration_ms_max,
                self.computations,
            ),
            "total_duration_ms": self._duration(
                self.total_duration_ms_total,
                self.total_duration_ms_last,
                self.total_duration_ms_max,
                requests,
            ),
        }


@dataclass
class _Entry:
    future: Future[Any]


class InferenceWorkGraph:
    """Thread-safe bounded LRU with per-key request coalescing."""

    def __init__(self, max_entries: int = 256) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self.max_entries = max_entries
        self._entries: OrderedDict[InferenceKey, _Entry] = OrderedDict()
        self._stats: dict[str, _OperationStats] = {}
        self._lock = RLock()

    def execute(
        self,
        key: InferenceKey,
        compute: Callable[[], Any],
    ) -> Any:
        request_started = perf_counter()
        owner = False
        with self._lock:
            stats = self._stats.setdefault(key.operation, _OperationStats(runtime_types=set()))
            assert stats.runtime_types is not None
            stats.runtime_types.add(key.runtime_type)
            entry = self._entries.get(key)
            if entry is None:
                stats.misses += 1
                owner = True
                entry = _Entry(Future())
                self._entries[key] = entry
                self._trim_locked()
            else:
                stats.hits += 1
                if not entry.future.done():
                    stats.coalesced += 1
                self._entries.move_to_end(key)

        if owner:
            computation_started = perf_counter()
            try:
                result = compute()
            except BaseException as exc:
                entry.future.set_exception(exc)
                with self._lock:
                    if self._entries.get(key) is entry:
                        del self._entries[key]
            else:
                entry.future.set_result(deepcopy(result))
            finally:
                computation_ms = (perf_counter() - computation_started) * 1000.0
                with self._lock:
                    self._stats[key.operation].record_computation(computation_ms)
                    self._trim_locked()

        try:
            return deepcopy(entry.future.result())
        finally:
            total_ms = (perf_counter() - request_started) * 1000.0
            with self._lock:
                self._stats[key.operation].record_total(total_ms)

    def _trim_locked(self) -> None:
        while len(self._entries) > self.max_entries:
            completed = next(
                (key for key, entry in self._entries.items() if entry.future.done()),
                None,
            )
            if completed is None:
                # Never evict in-flight work: doing so would allow a duplicate
                # computation for the same identity. The map is trimmed as each
                # owner completes.
                return
            del self._entries[completed]

    def invalidate(self, predicate: Callable[[InferenceKey], bool]) -> int:
        with self._lock:
            keys = [
                key
                for key, entry in self._entries.items()
                if entry.future.done() and predicate(key)
            ]
            for key in keys:
                del self._entries[key]
            return len(keys)

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            return {
                "max_entries": self.max_entries,
                "cached_entries": len(self._entries),
                "operations": {
                    operation: stats.snapshot()
                    for operation, stats in sorted(self._stats.items())
                },
            }
