from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from material_workbench.contracts.schemas import (
    DatasetViewRevisionCreateInput,
    Project,
    ProjectCreateInput,
    ProjectUpdateInput,
)


def test_single_dataset_view_requires_exactly_one_unique_member() -> None:
    member = {
        "dataset_revision_id": "dataset-revision-1",
        "ordinal": 0,
        "cohort_key": "primary",
        "cohort_label": "設備A",
    }
    created = DatasetViewRevisionCreateInput(
        view_id="view-1",
        revision=1,
        name="設備A",
        kind="single",
        members=[member],
    )
    assert created.members[0].cohort_key == "primary"

    with pytest.raises(ValidationError, match="1件だけ"):
        DatasetViewRevisionCreateInput(
            view_id="view-1",
            revision=1,
            name="invalid",
            kind="single",
            members=[member, {**member, "dataset_revision_id": "dataset-revision-2", "ordinal": 1, "cohort_key": "other"}],
        )


def test_existing_project_create_payload_remains_valid_without_binding() -> None:
    payload = ProjectCreateInput(name="既存形式", task_id="annealed-properties-v1")
    assert payload.dataset_view_revision_id is None
    assert payload.model_package_ref_id is None
    assert payload.task_contract_digest == ""


def test_project_response_accepts_unmigrated_nullable_binding() -> None:
    now = datetime.now(UTC)
    project = Project(
        id="project-1",
        created_at=now,
        updated_at=now,
        scientific_identity={
            "identity_kind": "single_task",
            "task_id": "annealed-properties-v1",
            "binding_provenance": "unbound_legacy",
        },
    )
    assert project.dataset_view_revision_id is None
    assert project.binding_provenance == "unbound_legacy"


def test_project_update_accepts_identity_fields_only_for_service_conflict_detection() -> None:
    payload = ProjectUpdateInput(task_id="other-task", dataset_view_revision_id="other-view")
    assert payload.task_id == "other-task"
    assert payload.dataset_view_revision_id == "other-view"
