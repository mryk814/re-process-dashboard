from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from time import perf_counter, sleep

import pytest

from material_workbench.inference_work_graph import InferenceKey, InferenceWorkGraph


def _key(operation: str, *, canonical: object = None, package: str = "package-a", support: str = "") -> InferenceKey:
    return InferenceKey.build(
        task_id="task",
        runtime_type="spy.v1",
        canonical_input=canonical if canonical is not None else {"composition": {"C": 0.1}},
        package_digest=package,
        pipeline_digest="pipeline-a",
        support_digest=support,
        operation=operation,
        operation_parameters={"policy": "test"},
    )


def test_work_graph_coalesces_identical_in_flight_work_and_reports_operation_stats() -> None:
    graph = InferenceWorkGraph(max_entries=4)
    started = Event()
    release = Event()
    calls = 0
    calls_lock = Lock()

    def compute() -> dict[str, int]:
        nonlocal calls
        with calls_lock:
            calls += 1
        started.set()
        assert release.wait(2)
        return {"value": 7}

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(graph.execute, _key("preview"), compute)
        assert started.wait(1)
        second = pool.submit(graph.execute, _key("preview"), compute)
        sleep(0.02)
        release.set()
        assert first.result() == second.result() == {"value": 7}

    assert calls == 1
    stats = graph.diagnostics()["operations"]["preview"]
    assert stats["runtime_types"] == ["spy.v1"]
    assert (stats["hits"], stats["misses"], stats["coalesced"], stats["computations"]) == (1, 1, 1, 1)
    assert stats["computation_duration_ms"]["last"] > 0
    assert stats["total_duration_ms"]["max"] > 0


def test_lru_never_evicts_in_flight_identity_and_returns_to_bound_after_completion() -> None:
    graph = InferenceWorkGraph(max_entries=1)
    started = Event()
    release = Event()
    calls = 0
    calls_lock = Lock()

    def slow() -> str:
        nonlocal calls
        with calls_lock:
            calls += 1
        started.set()
        assert release.wait(2)
        return "slow"

    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(graph.execute, _key("preview"), slow)
        assert started.wait(1)
        assert graph.execute(_key("support", support="support-a"), lambda: "support") == "support"
        same = pool.submit(graph.execute, _key("preview"), slow)
        sleep(0.02)
        release.set()
        assert first.result() == same.result() == "slow"

    assert calls == 1
    assert graph.diagnostics()["cached_entries"] == 1


def test_failed_computation_is_not_cached_and_still_records_total_duration() -> None:
    graph = InferenceWorkGraph(max_entries=2)

    with pytest.raises(ValueError, match="failed"):
        graph.execute(_key("preview"), lambda: (_ for _ in ()).throw(ValueError("failed")))

    diagnostics = graph.diagnostics()
    assert diagnostics["cached_entries"] == 0
    stats = diagnostics["operations"]["preview"]
    assert stats["misses"] == stats["computations"] == 1
    assert stats["total_duration_ms"]["last"] >= 0


def test_identity_changes_only_for_operation_relevant_digests() -> None:
    canonical = {"composition": {"C": 0.1}}
    preview_a = _key("preview", canonical=canonical, package="package-a")
    preview_b = _key("preview", canonical=canonical, package="package-b")
    support_a = _key("support", canonical=canonical, package="", support="support-a")
    support_b = _key("support", canonical=canonical, package="", support="support-b")
    support_package_change = _key("support", canonical=canonical, package="", support="support-a")

    assert preview_a != preview_b
    assert support_a != support_b
    assert support_a == support_package_change
    assert preview_a.canonical_input_digest == support_a.canonical_input_digest


def test_registry_freezes_package_pipeline_and_support_reference_digests(client) -> None:
    registry = client.app.state.task_registry
    annealed = registry.entry_for("annealed-properties-v1")
    hot_rolled = registry.entry_for("hot-rolled-properties-v1")

    for entry in (annealed, hot_rolled):
        assert entry.package_digest.startswith("sha256:")
        assert entry.pipeline_digest.startswith("sha256:")
        assert entry.support_digest.startswith("sha256:")
        assert entry.predictor_runtime.support_policy_id
        assert entry.runtime_type
    assert annealed.pipeline_digest != hot_rolled.pipeline_digest
    assert annealed.support_digest != hot_rolled.support_digest


def test_preview_similarity_curve_and_diagnostics_follow_independent_operation_contract(client) -> None:
    candidate = client.get("/api/projects/default/candidates").json()[0]
    base = f"/api/projects/default/candidates/{candidate['id']}"
    revision = candidate["revision"]

    first = client.post(f"{base}/preview", params={"expected_revision": revision})
    second = client.post(f"{base}/preview", params={"expected_revision": revision})
    assert first.status_code == second.status_code == 200
    preview = first.json()
    assert preview["similar"] == []
    prediction = preview["predictions"]["TS"]
    assert prediction["lower"] < prediction["value"] < prediction["upper"]
    assert preview["support"]["distance"] >= 0

    similar = client.get(f"{base}/similar", params={"expected_revision": revision, "limit": 2})
    assert similar.status_code == 200
    assert len(similar.json()) == 2

    curve = client.get(f"{base}/response-curve", params={
        "expected_revision": revision,
        "target": "TS",
        "variable": "composition.C",
        "points": 5,
    })
    assert curve.status_code == 200
    curve_payload = curve.json()
    assert curve_payload["target"] == "TS"
    assert curve_payload["variable"]["id"] == "composition.C"
    assert curve_payload["variable"]["unit"] == "%"
    assert curve_payload["point_count"] == len(curve_payload["points"]) == 5
    assert [point["x"] for point in curve_payload["points"]] == sorted(point["x"] for point in curve_payload["points"])
    assert all(point["lower"] < point["value"] < point["upper"] for point in curve_payload["points"])
    assert curve_payload["output_range"]["min"] < curve_payload["output_range"]["max"]
    assert curve_payload["policy_id"] == "fixed-grid-v2"

    diagnostics = client.get("/api/diagnostics/inference")
    assert diagnostics.status_code == 200
    operations = diagnostics.json()["operations"]
    assert operations["preview"]["misses"] == 1
    assert operations["preview"]["hits"] == 1
    assert operations["support"]["misses"] == 1
    assert operations["support"]["hits"] == 1
    assert operations["similarity"]["misses"] == 1
    assert operations["curve"]["misses"] == 1


def test_preview_support_does_not_materialize_similarity_rows(client, monkeypatch) -> None:
    candidate = client.get("/api/projects/default/candidates").json()[0]
    base = f"/api/projects/default/candidates/{candidate['id']}"
    runtime = client.app.state.task_registry.runtime_for("annealed-properties-v1")
    original = runtime._support
    modes: list[bool] = []

    def observed(*args, include_similarity=True, **kwargs):
        modes.append(include_similarity)
        return original(*args, include_similarity=include_similarity, **kwargs)

    monkeypatch.setattr(runtime, "_support", observed)
    preview = client.post(f"{base}/preview", params={"expected_revision": candidate["revision"]})
    assert preview.status_code == 200
    assert modes == [False]
    similar = client.get(f"{base}/similar", params={"expected_revision": candidate["revision"], "limit": 2})
    assert similar.status_code == 200
    assert modes == [False, True]


def test_non_inference_edit_reuses_preview_and_one_candidate_change_keeps_other_cache(client) -> None:
    candidates = client.get("/api/projects/default/candidates").json()
    first, second = candidates[:2]

    def preview(candidate: dict) -> None:
        response = client.post(
            f"/api/projects/default/candidates/{candidate['id']}/preview",
            params={"expected_revision": candidate["revision"]},
        )
        assert response.status_code == 200

    preview(first)
    preview(second)
    before = client.get("/api/diagnostics/inference").json()["operations"]["preview"]
    assert before["computations"] == 2

    renamed = client.put(
        f"/api/projects/default/candidates/{first['id']}",
        json={
            "name": "表示名だけ変更",
            "inputs": first["inputs"],
            "provenance": first["provenance"],
            "expected_revision": first["revision"],
        },
    ).json()
    preview(renamed)
    after_rename = client.get("/api/diagnostics/inference").json()["operations"]["preview"]
    assert after_rename["computations"] == 2
    assert after_rename["hits"] == 1

    changed_inputs = renamed["inputs"]
    changed_inputs["composition"]["C"] += 0.001
    changed = client.put(
        f"/api/projects/default/candidates/{first['id']}",
        json={
            "name": renamed["name"],
            "inputs": changed_inputs,
            "provenance": renamed["provenance"],
            "expected_revision": renamed["revision"],
        },
    ).json()
    preview(changed)
    preview(second)
    after_input = client.get("/api/diagnostics/inference").json()["operations"]["preview"]
    assert after_input["computations"] == 3
    assert after_input["hits"] == 2


def test_slow_curve_does_not_block_interactive_preview(client, monkeypatch) -> None:
    candidate = client.get("/api/projects/default/candidates").json()[0]
    base = f"/api/projects/default/candidates/{candidate['id']}"
    revision = candidate["revision"]
    runtime = client.app.state.task_registry.runtime_for("annealed-properties-v1")
    original = runtime.response_curve_result
    started = Event()

    def slow_curve(*args, **kwargs):
        started.set()
        sleep(0.25)
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime, "response_curve_result", slow_curve)
    with ThreadPoolExecutor(max_workers=2) as pool:
        curve = pool.submit(lambda: client.get(f"{base}/response-curve", params={
            "expected_revision": revision,
            "target": "TS",
            "variable": "composition.C",
            "points": 7,
        }))
        assert started.wait(1)
        before = perf_counter()
        preview = client.post(f"{base}/preview", params={"expected_revision": revision})
        preview_ms = (perf_counter() - before) * 1000
        assert preview.status_code == 200
        assert preview_ms < 150
        assert curve.result().status_code == 200
