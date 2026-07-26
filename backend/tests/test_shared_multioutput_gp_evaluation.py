from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from backend.scripts.evaluate_shared_multioutput_gp import (
    TARGET_ORDER,
    _checked_variance,
    _coregionalization,
    _fold_for_key,
)


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "docs/reports/shared-multi-output-gp-evaluation.json"
SOURCE = ROOT / "data/source/material_workbench_process_v1.xlsx"


def test_committed_evaluation_is_pinned_to_current_source_and_contract() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))

    assert report["schema_version"] == "shared-multi-output-gp-evaluation/v1"
    assert report["generated_from"]["source_sha256"] == hashlib.sha256(
        SOURCE.read_bytes()
    ).hexdigest()
    assert report["generated_from"]["task_id"] == "annealed-properties-v1"
    assert report["evaluation_policy"]["target_order"] == list(TARGET_ORDER)
    assert report["evaluation_policy"]["parent_conditions"] == 92
    assert report["sensitivity"]["excluded_parent_conditions"] == 1
    assert report["decision"]["adopt_runtime_and_package"] is False
    assert report["decision"]["primary"]["improved_target_count"] == 0
    assert report["decision"]["plausibility_clean_sensitivity"][
        "material_negative_transfer"
    ] is True
    assert all(
        report["sensitivity"]["negative_transfer"][target]["detected"]
        for target in TARGET_ORDER
    )


def test_evaluation_never_activates_or_claims_a_shared_runtime() -> None:
    active = json.loads((ROOT / "models/active-packages.json").read_text("utf-8"))
    assert "multitask" not in json.dumps(active).lower()
    assert "multi_output" not in json.dumps(active).lower()
    package_names = {
        path.name for path in (ROOT / "models/packages").iterdir() if path.is_dir()
    }
    assert not any(
        "multitask" in name or "multi-output" in name
        for name in package_names
    )


def test_coregionalization_is_positive_definite_and_negative_variance_fails() -> None:
    y = np.asarray(
        [
            [1.0, 2.0, 4.0, 7.0],
            [2.0, 2.5, 3.0, 6.0],
            [3.0, 4.0, 2.0, 4.0],
            [4.0, 5.0, 1.0, 2.0],
        ]
    )
    matrix = _coregionalization(y)
    assert np.allclose(matrix, matrix.T)
    assert np.linalg.eigvalsh(matrix).min() > 0

    with pytest.raises(ValueError, match="負の予測分散"):
        _checked_variance(np.asarray([0.2, -0.01]), "contract test")


def test_grouped_split_is_deterministic_without_target_specific_assignment() -> None:
    keys = ("AN-001", "AN-002", "AN-003", "AN-004")
    first = [_fold_for_key(key, 5, 20260726) for key in keys]
    second = [_fold_for_key(key, 5, 20260726) for key in keys]
    assert first == second
