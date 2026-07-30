"""Run the CALCE reference loop from Source Snapshot to prediction vs Actual.

This is an acceptance runner, not an application training subsystem.  It
creates an isolated workspace, builds one immutable data-only Package from an
approved Training Snapshot, restarts the API between lifecycle stages, and
reuses every semantic checkpoint when run again.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

from material_workbench.app import _AppResources, _prepare_app_resources, create_app
from material_workbench.application.training_snapshot_adapter import (
    BATTERY_MATERIALIZATION_ADAPTER_VERSION,
    BATTERY_SOURCE_ADAPTER_ID,
    BATTERY_SOURCE_ADAPTER_VERSION,
    BATTERY_SOURCE_ROW_KEY,
    BatteryTrainingSnapshotAdapter,
    battery_source_json,
    battery_source_records,
)
from material_workbench.contracts.design_space_contracts import (
    DesignSpaceDefinition,
)
from material_workbench.contracts.objective_contracts import ObjectiveDefinition
from material_workbench.contracts.schemas import ModelPackageRefCreateInput
from material_workbench.data.dataset_registration import register_dataset_records
from material_workbench.modeling.model_lifecycle import (
    dataset_profile_digest,
    ensure_available_packages_config,
    register_available_package,
)
from material_workbench.modeling.model_package_verify import verify_model_package
from material_workbench.modeling.model_packages import ModelPackageLoader
from material_workbench.modeling.tabular_model_builder import (
    build,
    tabular_training_code_revision,
)
from material_workbench.modeling.tabular_regression import load_tabular_profile
from material_workbench.persistence.data_lifecycle_repository import (
    DataLifecycleRepository,
)
from material_workbench.persistence.workspace_catalog import WorkspaceCatalog
from material_workbench.persistence.workspace_catalog_bootstrap import (
    task_definition_digest,
)


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data/source/external/battery_calce_cs2_cycles.csv"
PROFILE = (
    ROOT
    / "backend/src/material_workbench/data/tabular-profile-battery-degradation-v1.json"
)
TASK_ID = "battery-degradation-v1"
HELD_OUT_CELL_ID = "CS2_35"
ACTUAL_ROW_KEY = "CS2_35|CS2_35_10_22_10.xlsx|46"
PROJECT_NAME = "CALCE Source Lifecycle受入"
BASE_CANDIDATE_NAME = "CS2_35 cycle 400 基準条件"
EXPERIMENT_NO = "CALCE-CS2_35-300"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_ok(response: Any) -> dict[str, Any]:
    if response.status_code not in {200, 201}:
        raise RuntimeError(
            f"{response.request.method} {response.request.url}: "
            f"{response.status_code} {response.text}"
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("expected an object response")
    return payload


def _profile_revision(catalog: WorkspaceCatalog) -> Any:
    expected = dataset_profile_digest(PROFILE)
    matches = [
        item
        for item in catalog.list_profile_revisions(include_archived=True)
        if item.profile_id == "calce-cs2-battery-capacity-v1"
        and item.profile_digest == expected
    ]
    if len(matches) != 1:
        raise RuntimeError("CALCE Dataset Profile Revision was not registered once")
    return matches[0]


def _source_truth() -> dict[str, Any]:
    records = battery_source_records(SOURCE)
    return next(
        record
        for record in records
        if record[BATTERY_SOURCE_ROW_KEY] == ACTUAL_ROW_KEY
    )


def _lifecycle(client: TestClient) -> dict[str, Any]:
    catalog: WorkspaceCatalog = client.app.state.workspace_catalog
    profile = _profile_revision(catalog)
    source_content = battery_source_json(SOURCE)
    connector = _require_ok(
        client.post(
            "/api/data-lifecycle/connectors",
            json={
                "name": "CALCE CS2 battery source",
                "connector_type": "object_storage_json_v1",
                "source_locator": (
                    "repository://data/source/external/"
                    "battery_calce_cs2_cycles.csv"
                ),
                "selection": {
                    "format": "json_array",
                    "primary_key": BATTERY_SOURCE_ROW_KEY,
                    "source_adapter_id": BATTERY_SOURCE_ADAPTER_ID,
                    "source_adapter_version": BATTERY_SOURCE_ADAPTER_VERSION,
                },
            },
        )
    )
    recipe = _require_ok(
        client.post(
            "/api/data-lifecycle/recipes",
            json={
                "recipe_id": "battery-degradation-curation-v1",
                "version": 1,
                "name": "CALCE battery canonical rows",
                "steps": [
                    {
                        "kind": "trim_strings_v1",
                        "fields": [
                            "cell_id",
                            BATTERY_SOURCE_ROW_KEY,
                        ],
                    },
                    {
                        "kind": "coerce_number_v1",
                        "fields": [
                            "cycle_index",
                            "discharge_rate_c",
                            "capacity_percent",
                        ],
                    },
                    {
                        "kind": "required_fields_v1",
                        "fields": [
                            "cell_id",
                            "cycle_index",
                            "discharge_rate_c",
                            "capacity_percent",
                            BATTERY_SOURCE_ROW_KEY,
                        ],
                    },
                    {
                        "kind": "target_eligibility_v1",
                        "fields": ["capacity_percent"],
                    },
                ],
            },
        )
    )
    fetched = _require_ok(
        client.post(
            f"/api/data-lifecycle/connectors/{connector['id']}/fetch",
            json={
                "object_content": source_content,
                "object_version": f"sha256:{_sha256(SOURCE)}",
                "trigger_kind": "manual",
            },
        )
    )
    raw = fetched["snapshot"]
    curation = _require_ok(
        client.post(
            f"/api/data-lifecycle/raw-snapshots/{raw['id']}/curation-runs",
            json={
                "recipe_resource_id": recipe["id"],
                "profile_revision_id": profile.id,
                "profile_digest": profile.profile_digest,
            },
        )
    )
    revision = _require_ok(
        client.post(
            f"/api/data-lifecycle/curation-runs/{curation['id']}/approve",
            json={
                "reason": "CALCE reference loop acceptance",
            },
        )
    )
    training = _require_ok(
        client.post(
            (
                "/api/data-lifecycle/canonical-dataset-revisions/"
                f"{revision['id']}/training-snapshots"
            ),
            json={
                "purpose": "battery-degradation-v1 acceptance Package",
                "targets": [
                    {
                        "target_key": "capacity_percent",
                        "field": "capacity_percent",
                    }
                ],
                "split": {
                    "strategy_id": "sorted-group-round-robin-v1",
                    "group_field": "cell_id",
                    "folds": 3,
                },
                "selection_policy": {
                    "schema_version": "training-snapshot-selection/v1",
                    "policy_id": "battery-cell-holdout-v1",
                    "revision": 1,
                    "exclusions": [
                        {
                            "kind": "field_equals_any_v1",
                            "field": "cell_id",
                            "values": [HELD_OUT_CELL_ID],
                        }
                    ],
                },
            },
        )
    )
    if any(
        row_key.startswith(f"{HELD_OUT_CELL_ID}|")
        for row_key in training["included_row_keys"]
    ):
        raise RuntimeError("held-out cell leaked into the Training Snapshot")
    if raw["row_count"] != 3_131:
        raise RuntimeError("Raw Snapshot did not preserve every source row")
    if (
        curation["quality"]["quarantined"] != 0
        or len(revision["approved_row_keys"]) != 3_131
        or training["row_count"] != 2_302
    ):
        raise RuntimeError("cell-level holdout boundary differs from the reference")
    return {
        "connector": connector,
        "raw": raw,
        "recipe": recipe,
        "curation": curation,
        "revision": revision,
        "training": training,
    }


def _build_and_register(
    *,
    workspace: Path,
    database: Path,
    resources: _AppResources,
    lifecycle: dict[str, Any],
) -> dict[str, Any]:
    snapshot = lifecycle["training"]
    snapshot_suffix = snapshot["snapshot_digest"].removeprefix("sha256:")[:16]
    adapter_suffix = BATTERY_MATERIALIZATION_ADAPTER_VERSION.replace(".", "_")
    materialized = (
        workspace
        / "training-snapshots"
        / f"battery-{snapshot_suffix}-adapter-{adapter_suffix}.csv"
    )
    adapter = BatteryTrainingSnapshotAdapter(
        DataLifecycleRepository(database),
        PROFILE,
    )
    artifact = adapter.materialize(snapshot["id"], materialized)
    data_suffix = artifact.source_sha256[:12]
    builder_revision = tabular_training_code_revision(
        load_tabular_profile(PROFILE).model_family
    )
    builder_suffix = hashlib.sha256(
        builder_revision.encode("utf-8")
    ).hexdigest()[:12]
    identity_suffix = (
        f"{snapshot_suffix}-{data_suffix}-adapter-{adapter_suffix}"
        f"-builder-{builder_suffix}"
    )
    package_id = f"battery-degradation-lifecycle-{identity_suffix}"
    package_version = (
        f"1.0.0+snapshot.{snapshot_suffix}.data.{data_suffix}"
        f".adapter.{adapter_suffix}.builder.{builder_suffix}"
    )
    package_root = workspace / "model-store" / "packages" / package_id
    if package_root.exists():
        package = ModelPackageLoader().load(package_root)
        verify_model_package(package_root, task_id=TASK_ID, source=artifact.path)
        if (
            package.manifest.package_id != package_id
            or package.manifest.package_version != package_version
            or package.manifest.provenance.source_lifecycle
            != artifact.provenance
            or package.manifest.provenance.training_code_revision
            != builder_revision
        ):
            raise RuntimeError("existing immutable Package checkpoint differs")
    else:
        build(
            artifact.path,
            PROFILE,
            package_root,
            package_id=package_id,
            package_version=package_version,
            source_lifecycle=artifact.provenance,
        )
        package = ModelPackageLoader().load(package_root)

    catalog = WorkspaceCatalog(database)
    member_provenance = {
        "source_lifecycle": artifact.provenance.model_dump(mode="json")
    }
    dataset = register_dataset_records(
        catalog=catalog,
        source_path=artifact.path,
        source_sha256=artifact.source_sha256,
        profile_path=PROFILE,
        locator_kind="managed",
        locator=artifact.path,
        name="CALCE approved Training Snapshot",
        member_provenance=member_provenance,
    )
    package_ref = catalog.upsert_model_package_ref(
        ModelPackageRefCreateInput(
            package_id=package.manifest.package_id,
            task_id=TASK_ID,
            task_contract_digest=task_definition_digest(
                resources.task_registry,
                TASK_ID,
            ),
            manifest_digest=package.manifest_sha256,
            locator=str(package.root),
            manifest_json=package.manifest.model_dump(mode="json"),
        )
    )
    return {
        "artifact": artifact,
        "dataset": dataset,
        "package": package,
        "package_ref": package_ref,
    }


def _find_or_create_project(
    client: TestClient,
    *,
    dataset_view_revision_id: str,
    package_ref_id: str,
    task_contract_digest: str,
) -> dict[str, Any]:
    design_space = {
        "schema_version": "design-space-definition/v1",
        "design_space_id": "battery-reference-loop-v1",
        "revision": 1,
        "name": "同一cellの300／400 cycle比較",
        "task_id": TASK_ID,
        "task_contract_digest": task_contract_digest,
        "fixed_values": {"process.discharge_rate_c": 1.0},
        "numeric_domains": [
            {
                "path": "process.cycle_index",
                "mode": "values",
                "values": [300.0, 400.0],
            }
        ],
    }
    objective = {
        "schema_version": "objective-definition/v1",
        "objective_id": "battery-reference-capacity-max-v1",
        "revision": 1,
        "name": "容量維持率を高める",
        "task_id": TASK_ID,
        "task_contract_digest": task_contract_digest,
        "optimization_kind": "single_objective",
        "terms": [
            {
                "output_key": "capacity_percent",
                "unit": "%",
                "role": "primary_objective",
                "direction": "maximize",
            }
        ],
        "incumbent": {"source": "none"},
    }
    matches = [
        item
        for item in client.get("/api/projects").json()
        if item["name"] == PROJECT_NAME
        and item["task_id"] == TASK_ID
        and item["dataset_view_revision_id"] == dataset_view_revision_id
        and item["model_package_ref_id"] == package_ref_id
    ]
    if len(matches) > 1:
        raise RuntimeError("reference loop Project checkpoint is ambiguous")
    expected_design_space = DesignSpaceDefinition.model_validate(design_space)
    expected_objective = ObjectiveDefinition.model_validate(objective)
    if matches:
        project = matches[0]
        if (
            project["design_space_digest"] != expected_design_space.digest
            or project["objective_definition_digest"]
            != expected_objective.digest
        ):
            raise RuntimeError("existing Project checkpoint differs")
        return project
    return _require_ok(
        client.post(
            "/api/projects",
            json={
                "name": PROJECT_NAME,
                "purpose": "Sourceから実測評価までの責任境界を検証する",
                "task_id": TASK_ID,
                "dataset_view_revision_id": dataset_view_revision_id,
                "model_package_ref_id": package_ref_id,
                "design_space": design_space,
                "objective_definition": objective,
            },
        )
    )


def _find_or_create_base_candidate(
    client: TestClient,
    *,
    project_id: str,
) -> dict[str, Any]:
    candidates = client.get(
        f"/api/projects/{project_id}/candidates",
        params={"include_archived": True},
    ).json()
    matches = [
        item for item in candidates if item["name"] == BASE_CANDIDATE_NAME
    ]
    if len(matches) > 1:
        raise RuntimeError("reference loop Candidate checkpoint is ambiguous")
    if matches:
        candidate = matches[0]
    else:
        candidate = _require_ok(
            client.post(
                f"/api/projects/{project_id}/candidates",
                json={
                    "name": BASE_CANDIDATE_NAME,
                    "inputs": {
                        "composition": {},
                        "process": {
                            "discharge_rate_c": 1.0,
                            "cycle_index": 400.0,
                        },
                        "categorical": {},
                    },
                },
            )
        )
    expected = {
        "discharge_rate_c": 1.0,
        "cycle_index": 400.0,
    }
    if (
        candidate["inputs"]["process"] != expected
        or candidate["archived_at"] is not None
        or candidate["revision"] != 1
        or candidate["provenance"]["source_kind"] != "direct"
        or candidate["provenance"]["source_ref"] is not None
    ):
        raise RuntimeError("existing base Candidate checkpoint differs")
    return candidate


def _promote_experiment_candidate(
    client: TestClient,
    *,
    project: dict[str, Any],
    base_candidate: dict[str, Any],
    truth: dict[str, Any],
) -> dict[str, Any]:
    activity = _require_ok(
        client.post(
            (
                f"/api/projects/{project['id']}/candidates/"
                f"{base_candidate['id']}"
                "/decision-activities/counterfactual-target-reach-v1/runs"
            ),
            json={
                "expected_revision": base_candidate["revision"],
                "parameters": {
                    "schema_version": "counterfactual-parameters/v1",
                    "sample_count": 48,
                    "result_count": 1,
                    "seed": 254,
                    "max_changed_fields": 1,
                    "immutable_paths": ["process.discharge_rate_c"],
                },
            },
        )
    )
    proposals = activity["result"]["proposals"]
    if len(proposals) != 1:
        raise RuntimeError("counterfactual Activity did not return one proposal")
    proposal = proposals[0]
    expected_inputs = {
        "discharge_rate_c": float(truth["discharge_rate_c"]),
        "cycle_index": float(truth["cycle_index"]),
    }
    if proposal["inputs"]["process"] != expected_inputs:
        raise RuntimeError(
            "counterfactual proposal does not match the held-out source condition"
        )
    candidate = _require_ok(
        client.post(
            (
                f"/api/projects/{project['id']}/decision-activity-runs/"
                f"{activity['id']}/proposals/{proposal['proposal_id']}/candidate"
            )
        )
    )
    if (
        candidate["inputs"]["process"] != expected_inputs
        or candidate["provenance"]["source_kind"] != "decision_activity"
        or candidate["provenance"]["source_ref"]["run_id"] != activity["id"]
        or candidate["provenance"]["source_ref"]["proposal_id"]
        != proposal["proposal_id"]
    ):
        raise RuntimeError("promoted experiment Candidate lost Activity provenance")
    return {
        "activity": activity,
        "proposal": proposal,
        "candidate": candidate,
    }


def _register_actual(
    client: TestClient,
    *,
    project: dict[str, Any],
    candidate: dict[str, Any],
    truth: dict[str, Any],
) -> dict[str, Any]:
    expected_actual = {
        "property": "capacity_percent",
        "mean": float(truth["capacity_percent"]),
        "std": 0.0,
        "replicates": 1,
        "unit": "%",
        "experiment_no": EXPERIMENT_NO,
        "measured_at": None,
        "note": f"source row {ACTUAL_ROW_KEY}",
    }
    actuals = client.get(
        f"/api/projects/{project['id']}/candidates/{candidate['id']}/actuals"
    ).json()
    matching = [
        item
        for item in actuals
        if item["experiment_no"] == EXPERIMENT_NO
        and item["property"] == "capacity_percent"
    ]
    if len(matching) > 1:
        raise RuntimeError("reference loop Actual checkpoint is ambiguous")
    if matching:
        actual = matching[0]
        if any(
            actual.get(key) != value
            for key, value in expected_actual.items()
        ):
            raise RuntimeError("existing Actual checkpoint differs from source truth")
    else:
        actual = _require_ok(
            client.post(
                (
                    f"/api/projects/{project['id']}/candidates/"
                    f"{candidate['id']}/actuals"
                ),
                params={"expected_revision": candidate["revision"]},
                json=expected_actual,
            )
        )
    comparison = _require_ok(
        client.get(
            (
                f"/api/projects/{project['id']}/candidates/"
                f"{candidate['id']}/prediction-vs-actual"
            )
        )
    )
    matches = [
        item
        for item in comparison["comparisons"]
        if item["actual"]["id"] == actual["id"]
    ]
    if len(matches) != 1:
        raise RuntimeError("prediction vs Actual comparison is not unique")
    comparison = matches[0]
    if (
        comparison["snapshot_id"] != actual["snapshot_id"]
        or comparison["candidate_revision"] != candidate["revision"]
    ):
        raise RuntimeError("Actual checkpoint points to a different Candidate revision")
    return {
        "actual": actual,
        "comparison": comparison,
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    encoded = (
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.partial")
    try:
        staging.write_bytes(encoded)
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)


def run_reference_data_loop(
    workspace: Path,
    *,
    resources: _AppResources | None = None,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    model_store = workspace / "model-store"
    available_packages = ensure_available_packages_config(model_store)
    source_before = _sha256(SOURCE)
    database = workspace / "workbench.db"
    prepared = resources or _prepare_app_resources()

    # First process lifetime: acquire, curate, approve and freeze training rows.
    with TestClient(
        create_app(
            db_path=database,
            data_library_path=workspace / "data-library",
            model_store_path=model_store,
            _resources=prepared,
        )
    ) as client:
        lifecycle = _lifecycle(client)

    registered = _build_and_register(
        workspace=workspace,
        database=database,
        resources=prepared,
        lifecycle=lifecycle,
    )
    register_available_package(
        registered["package"].root,
        config_path=available_packages,
    )

    # Second process lifetime: resolve the registered Package from fixed Project
    # bindings, persist an Activity, and register the held-out measured truth.
    truth = _source_truth()
    with TestClient(
        create_app(
            db_path=database,
            data_library_path=workspace / "data-library",
            model_store_path=model_store,
            _resources=prepared,
        )
    ) as client:
        task_digest = task_definition_digest(
            prepared.task_registry,
            TASK_ID,
        )
        project = _find_or_create_project(
            client,
            dataset_view_revision_id=(
                registered["dataset"].dataset_view_revision_id
            ),
            package_ref_id=registered["package_ref"].id,
            task_contract_digest=task_digest,
        )
        base_candidate = _find_or_create_base_candidate(
            client,
            project_id=project["id"],
        )
        promotion = _promote_experiment_candidate(
            client,
            project=project,
            base_candidate=base_candidate,
            truth=truth,
        )
        candidate = promotion["candidate"]
        evidence = _register_actual(
            client,
            project=project,
            candidate=candidate,
            truth=truth,
        )

    source_after = _sha256(SOURCE)
    if source_after != source_before:
        raise RuntimeError("read-only source changed during reference loop")
    provenance = (
        registered["artifact"].provenance.model_dump(mode="json")
    )
    observed_provenance = evidence["comparison"]["provenance"].get(
        "source_lifecycle"
    )
    if observed_provenance != provenance:
        raise RuntimeError(
            "prediction Snapshot did not preserve Source Lifecycle provenance"
        )
    model_identity = evidence["comparison"]["provenance"]
    manifest = registered["package"].manifest
    manifest_provenance = manifest.provenance
    expected_model_package = {
        "id": manifest.package_id,
        "version": manifest.package_version,
        "manifest_sha256": registered["package"].manifest_sha256,
    }
    if any(
        model_identity["package"].get(key) != value
        for key, value in expected_model_package.items()
    ):
        raise RuntimeError("prediction Snapshot Model Package identity differs")
    pipeline_identity = model_identity["feature_pipeline"]
    if (
        pipeline_identity["id"] != manifest.feature_pipeline.id
        or pipeline_identity["version"] != manifest.feature_pipeline.version
        or pipeline_identity["digest"]
        != promotion["activity"]["provenance"]["feature_pipeline_digest"]
    ):
        raise RuntimeError("prediction Snapshot Feature Pipeline identity differs")
    training_identity = model_identity["training_data"]
    artifact_sha = registered["artifact"].source_sha256
    expected_training_identity = {
        "source_sha256": artifact_sha,
        "training_data_id": manifest_provenance.training_data_id,
        "feature_dataset_id": manifest_provenance.feature_dataset_id,
        "training_code_revision": manifest_provenance.training_code_revision,
    }
    if any(
        training_identity.get(key) != value
        for key, value in expected_training_identity.items()
    ) or manifest_provenance.training_data_id != f"sha256:{artifact_sha}":
        raise RuntimeError("prediction Snapshot Training Data identity differs")
    report = {
        "schema_version": "reference-data-loop-acceptance/v1",
        "task_id": TASK_ID,
        "responsibility": {
            "source_owner": "CALCE Battery Research Group, University of Maryland",
            "derived_asset_owner": "repository prepare_calce_battery_dataset.py",
            "approval_actor": "reference-loop-owner",
            "model_actor": "reference-loop-model-developer",
            "source_adapter": (
                f"{BATTERY_SOURCE_ADAPTER_ID}@"
                f"{BATTERY_SOURCE_ADAPTER_VERSION}"
            ),
            "row_identity": (
                "cell_id|source_file|source_local_cycle"
            ),
            "actual_return": (
                "the complete CS2_35 cell is excluded by Curation; one held-out "
                "capacity_percent is registered on the Activity-derived Candidate"
            ),
        },
        "source_sha256": source_before,
        "source_lifecycle": provenance,
        "materialized_dataset": {
            **registered["dataset"].as_dict(),
            "row_count": registered["artifact"].row_count,
        },
        "model_package": {
            "id": registered["package"].manifest.package_id,
            "version": registered["package"].manifest.package_version,
            "manifest_digest": registered["package"].manifest_sha256,
            "reference_id": registered["package_ref"].id,
            "training_code_revision": (
                registered["package"].manifest.provenance.training_code_revision
            ),
        },
        "project": {
            "id": project["id"],
            "dataset_view_revision_id": project["dataset_view_revision_id"],
            "model_package_ref_id": project["model_package_ref_id"],
            "model_package_manifest_digest": (
                project["model_package_manifest_digest"]
            ),
        },
        "candidate": {
            "id": candidate["id"],
            "revision": candidate["revision"],
            "source_row_key": ACTUAL_ROW_KEY,
            "base_candidate_id": base_candidate["id"],
            "source_kind": candidate["provenance"]["source_kind"],
        },
        "activity": {
            "id": promotion["activity"]["id"],
            "semantic_identity": promotion["activity"]["semantic_identity"],
            "proposal_id": promotion["proposal"]["proposal_id"],
        },
        "actual": {
            "id": evidence["actual"]["id"],
            "snapshot_id": evidence["actual"]["snapshot_id"],
            "experiment_no": evidence["actual"]["experiment_no"],
            "value": evidence["actual"]["mean"],
            "unit": evidence["actual"]["unit"],
        },
        "comparison": {
            "snapshot_id": evidence["comparison"]["snapshot_id"],
            "predicted": evidence["comparison"]["prediction"]["predictions"][
                "capacity_percent"
            ]["value"],
            "actual": evidence["actual"]["mean"],
            "model_identity": {
                "package": model_identity["package"],
                "model": model_identity["model"],
                "feature_pipeline": model_identity["feature_pipeline"],
                "training_data": {
                    key: training_identity[key]
                    for key in (
                        "source_sha256",
                        "training_data_id",
                        "feature_dataset_id",
                        "training_code_revision",
                        "records",
                    )
                },
                "source_lifecycle": model_identity["source_lifecycle"],
            },
        },
        "resume_points": [
            "Raw Snapshot",
            "Curation Run",
            "Canonical Dataset Revision",
            "Training Snapshot",
            "materialized training asset",
            "Model Package",
            "Project",
            "Candidate",
            "Decision Activity Run",
            "Prediction Snapshot and Actual",
        ],
    }
    _write_report(workspace / "acceptance-report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("artifacts/reference-data-loop"),
        help="Isolated mutable workspace; data/source is never modified.",
    )
    args = parser.parse_args()
    report = run_reference_data_loop(args.workspace)
    print(
        json.dumps(
            {
                "report": str(
                    (args.workspace / "acceptance-report.json").resolve()
                ),
                "project_id": report["project"]["id"],
                "package_id": report["model_package"]["id"],
                "actual_id": report["actual"]["id"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
