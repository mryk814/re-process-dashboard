from __future__ import annotations

import json
from pathlib import Path

import pytest

from decision_workbench.design_priors.builder import build_design_prior_package
from decision_workbench.design_priors.contracts import (
    DesignPriorObservation,
    DesignPriorPackageError,
    DesignPriorSource,
)
from decision_workbench.design_priors.loader import DesignPriorPackageLoader
from decision_workbench.design_priors.sampling import sample_prior


TASK_DIGEST = "sha256:" + "a" * 64
SOURCE = DesignPriorSource(dataset_view_digest="sha256:" + "b" * 64)


def _build(tmp_path: Path, *, paths: tuple[str, ...] = ("process.temperature", "process.time", "categorical.route")) -> Path:
    rows = (
        DesignPriorObservation(sample_id="a-1", inputs={"process.temperature": 700.0, "process.time": 10.0, "categorical.route": "A"}),
        DesignPriorObservation(sample_id="a-2", inputs={"process.temperature": 710.0, "process.time": 12.0, "categorical.route": "A"}),
        DesignPriorObservation(sample_id="b-1", inputs={"process.temperature": 900.0, "process.time": 45.0, "categorical.route": "B"}),
        DesignPriorObservation(sample_id="b-2", inputs={"process.temperature": 910.0, "process.time": 48.0, "categorical.route": "B"}),
    )
    return build_design_prior_package(
        tmp_path / "prior",
        package_id="correlated-mixed-v1",
        package_version="1.0.0",
        task_id="fixture-task-v1",
        task_contract_digest=TASK_DIGEST,
        canonical_input_schema_version="candidate-v1",
        canonical_input_paths=paths,
        source=SOURCE,
        observations=rows,
        training_code_revision="git:test",
    )


def test_build_verify_and_sample_empirical_and_knn_without_cross_mode_categories(tmp_path: Path) -> None:
    package = DesignPriorPackageLoader().load(_build(tmp_path))
    empirical = sample_prior(
        package,
        generator_id="empirical_rows",
        lane="conservative",
        count=8,
        seed=7,
        fixed_context={},
    )
    knn = sample_prior(
        package,
        generator_id="knn_local",
        lane="balanced",
        count=8,
        seed=7,
        fixed_context={},
    )
    assert [item.values for item in empirical] == [item.values for item in sample_prior(package, generator_id="empirical_rows", lane="conservative", count=8, seed=7, fixed_context={})]
    assert {item.values["categorical.route"] for item in knn} <= {"A", "B"}
    assert all(
        item.evidence.neighbor_sample_id is not None
        and item.evidence.raw_sample_id.split("-")[0]
        == item.evidence.neighbor_sample_id.split("-")[0]
        for item in knn
    )
    assert all(item.evidence.manifest_digest == f"sha256:{package.manifest_sha256}" for item in knn)
    assert all(item.evidence.nearest_neighbor_distance is not None for item in knn)


def test_loader_rejects_tampered_data_artifact(tmp_path: Path) -> None:
    root = _build(tmp_path)
    (root / "observations.json").write_text("{}", encoding="utf-8")
    with pytest.raises(DesignPriorPackageError, match="(size|hash) mismatch"):
        DesignPriorPackageLoader().load(root)


def test_api_pins_explicit_prior_reference_and_sample_evidence(client, tmp_path: Path) -> None:
    candidate = client.get("/api/projects/default/candidates").json()[0]
    project = client.get("/api/projects/default").json()
    inputs = candidate["inputs"]
    path_values = {
        **{f"composition.{key}": value for key, value in inputs["composition"].items()},
        **{f"process.{key}": value for key, value in inputs["process"].items()},
        **{f"categorical.{key}": value for key, value in inputs["categorical"].items()},
    }
    rows = []
    for index, (carbon, speed) in enumerate(((0.05, 90.0), (0.06, 95.0), (0.11, 125.0), (0.12, 130.0)), start=1):
        row = dict(path_values)
        row["composition.C"] = carbon
        row["process.ls_mpm"] = speed
        rows.append(DesignPriorObservation(sample_id=f"row-{index}", inputs=row))
    root = build_design_prior_package(
        tmp_path / "api-prior",
        package_id="annealed-prior-fixture-v1",
        package_version="1.0.0",
        task_id=project["task_id"],
        task_contract_digest=project["task_contract_digest"],
        canonical_input_schema_version="candidate-v1",
        canonical_input_paths=tuple(path_values),
        source=SOURCE,
        observations=rows,
        training_code_revision="git:test",
    )
    package = DesignPriorPackageLoader().load(root)
    body = {
        "purpose": "goal_search",
        "base_candidate_id": candidate["id"],
        "base_inputs": inputs,
        "samples": 48,
        "seed": 309,
        "target": "TS",
        "target_goal": {"direction": "at_least", "lower": 500},
        "variables": {
            "composition.C": {"mode": "range", "min": 0.04, "max": 0.12},
            "process.ls_mpm": {"mode": "range", "min": 80, "max": 130},
        },
        "proposal": {
            "strategy_id": "design_prior_empirical_v1",
            "pool_multiplier": 2,
            "support_policy": "supported_first",
            "fallback_policy": "reject",
            "design_prior": {
                "locator": str(root),
                "package_id": package.manifest.package_id,
                "package_version": package.manifest.package_version,
                "manifest_digest": f"sha256:{package.manifest_sha256}",
                "generator_id": "knn_local",
                "lane": "balanced",
            },
        },
    }
    response = client.post("/api/screening", json=body)
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["proposal_strategy"]["generator_id"] == "design_prior"
    assert run["design_prior"] == body["proposal"]["design_prior"]
    assert all(item["design_prior_evidence"]["raw_sample_id"] for item in run["proposal_pool"])
    assert all(item["design_prior_evidence"]["generator_id"] == "knn_local" for item in run["points"])
    restored = client.get(f"/api/screening/{run['id']}").json()
    assert restored["design_prior"] == run["design_prior"]
