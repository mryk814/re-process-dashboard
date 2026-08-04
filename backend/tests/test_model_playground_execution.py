from __future__ import annotations

import json
from pathlib import Path

from decision_workbench.application.data_lifecycle import DataLifecycleService
from decision_workbench.application.model_playground import ModelPlaygroundUseCases
from decision_workbench.contracts.data_library_contracts import (
    ProfileRevisionCreateInput,
)
from decision_workbench.contracts.data_lifecycle_contracts import (
    CurationRecipeCreateInput,
    CurationRunCreateInput,
    DatasetApprovalInput,
    ObjectSelection,
    SourceConnectorCreateInput,
    SourceFetchRequest,
    TrainingSnapshotCreateInput,
)
from decision_workbench.contracts.model_playground_contracts import (
    ModelExplorationRunCreateRequest,
)
from decision_workbench.data.profile_family_registry import (
    restore_profile_document,
)
from decision_workbench.modeling.model_lifecycle import dataset_profile_digest


ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = (
    ROOT
    / "backend"
    / "src"
    / "decision_workbench"
    / "data"
    / "tabular-profile-mpea-room-tensile-v1.json"
)


def _rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(9):
        fe = 45 + index
        ni = 55 - index
        rows.append(
            {
                "Material": f"alloy-{index}",
                "File_Name": f"source-{index}",
                "Fe": fe,
                "Ni": ni,
                "Co": 0,
                "Mn": 0,
                "Cr": 0,
                "Al": 0,
                "Ti": 0,
                "Cu": 0,
                "Si": 0,
                "V": 0,
                "Nb": 0,
                "B": 0,
                "Mo": 0,
                "Ta": 0,
                "Homogenization? (Yes=1, No=0)": "no",
                "Homogenization temp (°C)": 0,
                "Homogenization time (hr)": 0,
                "Rolling? (Yes=1, No=0)": "no",
                "Rolling temp (°C)": 0,
                "Rolling %": 0,
                "Recrystallization (Y=1, N=0)": "no",
                "Recrystallization temp (°C)": 0,
                "Recrystallization Time (min)": 0,
                "Aging? (yes=1, No=0)": "no",
                "Aging temp (°C)": 0,
                "Aging time (hr)": 0,
                "Tensile Yield Strength (Mpa)": 300 + 10 * index,
                "Ultimate Tensile Strength (MPa)": 500 + 12 * index,
                "Tensile Ductility(%)": 30 - index,
            }
        )
    return rows


def _prepare_run_context(client, tmp_path):
    catalog = client.app.state.workspace_catalog
    document = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    profile = restore_profile_document(document)
    profile_digest = dataset_profile_digest(profile)
    revision = next(
        (
            item
            for item in catalog.list_profile_revisions(include_archived=True)
            if item.profile_digest == profile_digest
        ),
        None,
    )
    if revision is None:
        revision = catalog.upsert_profile_revision(
            ProfileRevisionCreateInput(
                profile_id=profile.profile_id,
                revision=99,
                name="Model Playground fixture",
                profile_digest=profile_digest,
                canonical_contract_digest="sha256:model-playground-fixture",
                effective_profile_json=document,
            )
        )
    lifecycle = DataLifecycleService(client.app.state.store.path)
    connector = lifecycle.create_connector(
        SourceConnectorCreateInput(
            name="Model Playground fixture",
            connector_type="object_storage_json_v1",
            source_locator="repository://model-playground-fixture.json",
            selection=ObjectSelection(
                format="json_array",
                primary_key="Material",
                source_adapter_id="model-playground-json-records",
                source_adapter_version="1.0.0",
            ),
        )
    )
    raw, _ = lifecycle.fetch(
        connector.id,
        SourceFetchRequest(
            object_content=json.dumps(_rows()),
            object_version="fixture-v1",
        ),
    )
    numeric_fields = [
        key
        for key, value in _rows()[0].items()
        if isinstance(value, (int, float))
    ]
    recipe = lifecycle.create_recipe(
        CurationRecipeCreateInput(
            recipe_id="model-playground-fixture",
            version=1,
            name="Model Playground fixture",
            steps=(
                {
                    "kind": "trim_strings_v1",
                    "fields": [
                        "Material",
                        "File_Name",
                        "Homogenization? (Yes=1, No=0)",
                        "Rolling? (Yes=1, No=0)",
                        "Recrystallization (Y=1, N=0)",
                        "Aging? (yes=1, No=0)",
                    ],
                },
                {"kind": "coerce_number_v1", "fields": numeric_fields},
                {
                    "kind": "required_fields_v1",
                    "fields": ["Material", "File_Name", *numeric_fields],
                },
                {
                    "kind": "target_eligibility_v1",
                    "fields": [
                        "Tensile Yield Strength (Mpa)",
                        "Ultimate Tensile Strength (MPa)",
                        "Tensile Ductility(%)",
                    ],
                },
            ),
        )
    )
    curation = lifecycle.curate(
        raw.id,
        CurationRunCreateInput(
            recipe_resource_id=recipe.id,
            profile_revision_id=revision.id,
            profile_digest=revision.profile_digest,
        ),
    )
    canonical = lifecycle.approve(
        curation.id,
        DatasetApprovalInput(actor="modeler", reason="fixture approved"),
    )
    snapshot = lifecycle.create_training_snapshot(
        canonical.id,
        TrainingSnapshotCreateInput(
            actor="modeler",
            purpose="Model Playground comparison",
            targets=(
                {
                    "target_key": "TYS",
                    "field": "Tensile Yield Strength (Mpa)",
                },
                {
                    "target_key": "UTS",
                    "field": "Ultimate Tensile Strength (MPa)",
                },
                {
                    "target_key": "EL",
                    "field": "Tensile Ductility(%)",
                },
            ),
            split={"group_field": "File_Name", "folds": 3},
            selection_policy={
                    "policy_id": "model-playground-all-approved",
                    "revision": 1,
                    "exclusions": (
                        {
                            "kind": "field_equals_any_v1",
                            "field": "Material",
                            "values": ("never-match",),
                        },
                    ),
                },
        ),
    )
    service = ModelPlaygroundUseCases(
        store=client.app.state.store,
        workspace_catalog=catalog,
        task_registry=client.app.state.task_registry,
        model_store_path=tmp_path / "models",
        task_store_path=tmp_path / "tasks",
        package_origins={},
        execution_instance_id="execution-test",
    )
    return service, revision, snapshot


def test_ridge_and_additive_attempts_share_snapshot_evidence_and_identity(
    client,
    tmp_path,
) -> None:
    service, revision, snapshot = _prepare_run_context(client, tmp_path)
    preview = service.preview(
        task_id="mpea-room-tensile-v1",
        profile_revision_id=revision.id,
        training_snapshot_id=snapshot.id,
        compute_budget="quick",
    )
    recipes = {item.recipe_id: item for item in preview.recipes}
    assert recipes["ridge.v1"].availability == "ready"
    assert recipes["bayesian-additive-spline.v1"].availability == "ready"

    run = service.create_run(
        ModelExplorationRunCreateRequest(
            task_id="mpea-room-tensile-v1",
            profile_revision_id=revision.id,
            training_snapshot_id=snapshot.id,
            selected_recipe_ids=(
                "ridge.v1",
                "bayesian-additive-spline.v1",
            ),
            compute_budget="quick",
        )
    )
    run = service.execute_recipe(
        run.run_id,
        "ridge.v1",
        expected_revision=run.execution_revision,
    )
    run = service.execute_recipe(
        run.run_id,
        "bayesian-additive-spline.v1",
        expected_revision=run.execution_revision,
    )

    ridge, additive = run.attempts
    assert ridge.status == "completed"
    assert ridge.hypothesis is not None
    assert ridge.hypothesis.card_id == "ridge-linear-baseline"
    assert ridge.inference_identity is None
    assert additive.status == "completed"
    assert additive.hypothesis is not None
    assert additive.hypothesis.card_id == "bayesian-additive-spline"
    assert additive.inference_identity is not None
    assert additive.inference_identity.algorithm_id == "analytic-gaussian"
    assert ridge.result is not None and additive.result is not None
    assert all(
        item.inference_identity is None
        and item.inference_unavailable_reason
        for item in ridge.result.targets
    )
    assert all(
        item.inference_identity is not None
        and item.inference_identity.algorithm_id == "analytic-gaussian"
        for item in additive.result.targets
    )
    assert [
        (item.target_key, item.cohort_digest, item.fold_digest)
        for item in ridge.result.targets
    ] == [
        (item.target_key, item.cohort_digest, item.fold_digest)
        for item in additive.result.targets
    ]
    registered = service.register_attempt(
        run.run_id,
        additive.attempt_id,
        expected_revision=run.execution_revision,
    )
    receipt = registered.attempts[1].registration
    assert receipt is not None
    assert receipt.active_package_changed is False
    assert receipt.package_locator == str(
        (
            tmp_path
            / "models"
            / "packages"
            / additive.result.package_id
        ).resolve()
    )
    assert receipt.package_locator != additive.result.package_path
