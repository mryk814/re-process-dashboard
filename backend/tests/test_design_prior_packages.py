from __future__ import annotations

import json
import hashlib
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


def test_loader_rejects_symlink_root_and_undeclared_executable_artifact(tmp_path: Path) -> None:
    root = _build(tmp_path)
    link = tmp_path / "prior-link"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(DesignPriorPackageError, match="symlink"):
        DesignPriorPackageLoader().load(link)

    observations = root / "observations.json"
    observations_payload = observations.read_bytes()
    outside = tmp_path / "outside-observations.json"
    outside.write_bytes(observations_payload)
    observations.unlink()
    observations.symlink_to(outside)
    with pytest.raises(DesignPriorPackageError, match="symlink"):
        DesignPriorPackageLoader().load(root)
    observations.unlink()
    observations.write_bytes(observations_payload)

    (root / "callback.py").write_text("raise RuntimeError('must never load')", encoding="utf-8")
    with pytest.raises(DesignPriorPackageError, match="undeclared|unsafe"):
        DesignPriorPackageLoader().load(root)

    (root / "unsafe.pkl").write_bytes(b"\x80\x04N.")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for filename in ("callback.py", "unsafe.pkl"):
        payload = (root / filename).read_bytes()
        manifest["artifacts"].append(
            {
                "path": filename,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
                "media_type": "application/json",
            }
        )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(DesignPriorPackageError, match="manifest|JSON"):
        DesignPriorPackageLoader().load(root)


def test_loader_parses_quality_report_as_json_instead_of_trusting_hash_only(tmp_path: Path) -> None:
    root = _build(tmp_path)
    quality = root / "quality-report.json"
    quality.write_bytes(b"not-json")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = next(item for item in manifest["artifacts"] if item["path"] == quality.name)
    artifact["bytes"] = quality.stat().st_size
    artifact["sha256"] = hashlib.sha256(quality.read_bytes()).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(DesignPriorPackageError, match="JSON|json|quality"):
        DesignPriorPackageLoader().load(root)


def test_loader_rejects_mixed_numeric_and_categorical_roles(tmp_path: Path) -> None:
    root = _build(tmp_path)
    observations_path = root / "observations.json"
    observations = json.loads(observations_path.read_text(encoding="utf-8"))
    observations["rows"][1]["inputs"]["process.temperature"] = "mixed-category"
    observations_path.write_text(
        json.dumps(
            observations,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = next(
        item
        for item in manifest["artifacts"]
        if item["path"] == observations_path.name
    )
    payload = observations_path.read_bytes()
    artifact["bytes"] = len(payload)
    artifact["sha256"] = hashlib.sha256(payload).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(DesignPriorPackageError, match="uniformly numeric or categorical"):
        DesignPriorPackageLoader().load(root)


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_design_prior_build_and_load_reject_non_finite_numeric_values(
    tmp_path: Path,
    non_finite: float,
) -> None:
    with pytest.raises(ValueError, match="finite"):
        build_design_prior_package(
            tmp_path / "invalid-prior",
            package_id="invalid-prior-v1",
            package_version="1.0.0",
            task_id="fixture-task-v1",
            task_contract_digest=TASK_DIGEST,
            canonical_input_schema_version="candidate-v1",
            canonical_input_paths=("process.x",),
            source=SOURCE,
            observations=(
                DesignPriorObservation.model_construct(
                    sample_id="invalid",
                    inputs={"process.x": non_finite},
                ),
                DesignPriorObservation(sample_id="valid", inputs={"process.x": 1.0}),
            ),
            training_code_revision="git:test",
        )

    root = _build(tmp_path)
    observations_path = root / "observations.json"
    observations = json.loads(observations_path.read_text(encoding="utf-8"))
    observations["rows"][0]["inputs"]["process.temperature"] = non_finite
    observations_path.write_text(
        json.dumps(
            observations,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = next(
        item
        for item in manifest["artifacts"]
        if item["path"] == observations_path.name
    )
    payload = observations_path.read_bytes()
    artifact["bytes"] = len(payload)
    artifact["sha256"] = hashlib.sha256(payload).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(DesignPriorPackageError, match="finite"):
        DesignPriorPackageLoader().load(root)


def test_knn_distance_and_typicality_are_measured_from_generated_point_to_all_rows(tmp_path: Path) -> None:
    root = build_design_prior_package(
        tmp_path / "distance-prior",
        package_id="distance-prior-v1",
        package_version="1.0.0",
        task_id="fixture-task-v1",
        task_contract_digest=TASK_DIGEST,
        canonical_input_schema_version="candidate-v1",
        canonical_input_paths=("process.x",),
        source=SOURCE,
        observations=(
            DesignPriorObservation(sample_id="left", inputs={"process.x": 0.0}),
            DesignPriorObservation(sample_id="right", inputs={"process.x": 10.0}),
        ),
        training_code_revision="git:test",
    )
    package = DesignPriorPackageLoader().load(root)
    sample = sample_prior(
        package,
        generator_id="knn_local",
        lane="balanced",
        count=1,
        seed=17,
        fixed_context={},
    )[0]
    normalized_distance = min(
        abs(float(sample.values["process.x"]) - endpoint) / 10.0
        for endpoint in (0.0, 10.0)
    )
    assert sample.evidence.nearest_neighbor_distance == pytest.approx(normalized_distance)
    assert sample.evidence.typicality_band == (
        "typical" if normalized_distance <= 0.05
        else "near_edge" if normalized_distance <= 0.2
        else "low_density"
    )
    assert sample.evidence.lane_parameter_digest.startswith("sha256:")
    frontier = sample_prior(
        package,
        generator_id="knn_local",
        lane="frontier",
        count=1,
        seed=17,
        fixed_context={},
    )[0]
    assert frontier.evidence.lane_parameter_digest != sample.evidence.lane_parameter_digest


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
    assert {
        key: value
        for key, value in run["design_prior"].items()
        if key != "lane_parameter_digest"
    } == body["proposal"]["design_prior"]
    assert run["design_prior"]["lane_parameter_digest"].startswith("sha256:")
    assert all(item["design_prior_evidence"]["raw_sample_id"] for item in run["proposal_pool"])
    assert all(item["design_prior_evidence"]["generator_id"] == "knn_local" for item in run["points"])
    restored = client.get(f"/api/screening/{run['id']}").json()
    assert restored["design_prior"] == run["design_prior"]


def test_api_rejects_prior_reference_for_non_prior_strategy_before_persistence(client, tmp_path: Path) -> None:
    candidate = client.get("/api/projects/default/candidates").json()[0]
    before = client.get("/api/screening").json()
    body = {
        "purpose": "goal_search",
        "base_candidate_id": candidate["id"],
        "base_inputs": candidate["inputs"],
        "samples": 48,
        "seed": 310,
        "target": "TS",
        "target_goal": {"direction": "at_least", "lower": 500},
        "variables": {
            "composition.C": {"mode": "range", "min": 0.04, "max": 0.12},
        },
        "proposal": {
            "strategy_id": "latin_hypercube_v1",
            "pool_multiplier": 2,
            "support_policy": "supported_first",
            "fallback_policy": "reject",
            "design_prior": {
                "locator": str(tmp_path / "must-not-be-loaded"),
                "package_id": "unexpected-prior",
                "package_version": "1.0.0",
                "manifest_digest": "sha256:" + "c" * 64,
                "generator_id": "empirical_rows",
                "lane": "conservative",
            },
        },
    }
    response = client.post("/api/screening", json=body)
    assert response.status_code == 422, response.text
    after = client.get("/api/screening").json()
    assert [item["id"] for item in after] == [item["id"] for item in before]
