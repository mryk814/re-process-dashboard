from __future__ import annotations

from material_workbench.developer_experience.data_lifecycle_benchmark import (
    fixture_metadata,
    run_benchmark_case,
    run_concurrency_probe,
    run_history_probe,
    summarize_report,
    synthetic_payload,
)


def test_fixtures_are_deterministic_and_report_exact_shape() -> None:
    narrow = synthetic_payload(3, shape="narrow")
    representative = synthetic_payload(3, shape="representative")

    assert narrow == synthetic_payload(3, shape="narrow")
    assert fixture_metadata(narrow, "narrow") == fixture_metadata(
        narrow,
        "narrow",
    )
    assert fixture_metadata(narrow, "narrow")["column_count"] == 3
    assert fixture_metadata(representative, "representative")["column_count"] == 20
    assert fixture_metadata(representative, "representative")["utf8_bytes"] > len(
        representative
    )


def test_benchmark_runs_the_complete_production_lifecycle(tmp_path) -> None:
    result = run_benchmark_case(
        row_count=25,
        shape="representative",
        workspace=tmp_path / "benchmark",
        environment_label="test",
    )

    assert result["status"] == "measured"
    assert result["counts"] == {
        "raw_snapshots": 1,
        "curation_runs": 1,
        "canonical_revisions": 1,
        "training_snapshots": 1,
        "training_rows": 25,
    }
    assert result["correctness"] == {
        "fetch_reused_existing_snapshot": False,
        "foreign_key_violations": 0,
        "row_count_matches": True,
    }
    assert result["quality"]["accepted"] == 25
    assert result["database"]["lifecycle_payload_increment_bytes"] > 0
    assert result["database"]["journal_mode"] == "delete"
    assert result["detail_payload_bytes"] > result["fixture"]["utf8_bytes"]


def test_history_probe_exposes_global_filtering_without_changing_target_counts(
    tmp_path,
) -> None:
    result = run_history_probe(
        workspace=tmp_path / "history",
        row_count=5,
        history_depth=2,
        unrelated_connectors=2,
    )

    assert result["target_detail_before_unrelated"]["counts"] == {
        "raw_snapshots": 2,
        "curation_runs": 2,
    }
    assert result["target_detail_after_unrelated"]["counts"] == {
        "raw_snapshots": 2,
        "curation_runs": 2,
    }
    assert result["unrelated_detail_slowdown_ratio"] > 0


def test_concurrency_probe_overlaps_real_writes_and_checks_integrity(
    tmp_path,
) -> None:
    result = run_concurrency_probe(
        workspace=tmp_path / "concurrency",
        row_count=5,
        iterations=2,
        include_forced_lock=False,
    )

    assert result["read_operations"] == 8
    assert result["write_operations"] == 2
    assert result["failed_operations"] == 0
    assert result["correctness"] == {
        "successful_writes": 2,
        "expected_raw_snapshots": 3,
        "actual_raw_snapshots": 3,
        "foreign_key_violations": 0,
    }


def test_report_aggregates_repeats_per_scale_and_keeps_thresholds_explicit(
    tmp_path,
) -> None:
    cases = [
        run_benchmark_case(
            row_count=5,
            shape="narrow",
            workspace=tmp_path / f"case-{index}",
            environment_label=f"test-{index}",
        )
        for index in range(2)
    ]
    report = summarize_report(
        results=cases,
        history_probe=None,
        concurrency_probe=None,
    )

    assert report["aggregated_results"][0]["repeats"] == 2
    assert report["aggregated_results"][0]["row_count"] == 5
    assert (
        report["thresholds_fixed_before_measurement"]["soft_read_ui"][
            "detail_p95_ms"
        ]
        == 2_000
    )
