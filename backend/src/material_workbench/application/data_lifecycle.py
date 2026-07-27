from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from material_workbench.contracts.data_lifecycle_contracts import (
    ApprovedTrainingSnapshot,
    CanonicalDatasetRevision,
    ConnectorLifecycleDetail,
    CurationRecipe,
    CurationRecipeCreateInput,
    CurationRun,
    CurationRunCreateInput,
    DatasetApprovalInput,
    FetchAttempt,
    RawSourceSnapshot,
    SourceConnector,
    SourceConnectorCreateInput,
    SourceFetchRequest,
    TrainingSnapshotCreateInput,
)
from material_workbench.domain.data_lifecycle import (
    LifecycleConflictError,
    SourceObjectError,
    approve_curation_run,
    build_raw_snapshot,
    build_training_snapshot,
    curate_snapshot,
)
from material_workbench.persistence.data_lifecycle_repository import (
    DataLifecycleRepository,
)
from material_workbench.application.source_ingress import (
    SourceIngressError,
    SourceIntegrityError,
    load_source_object,
)


class SourceFetchFailedError(ValueError):
    def __init__(self, attempt: FetchAttempt) -> None:
        super().__init__(attempt.error_message)
        self.attempt = attempt


class DataLifecycleService:
    def __init__(self, database: str | Path) -> None:
        self.repository = DataLifecycleRepository(database)

    def create_connector(
        self, payload: SourceConnectorCreateInput
    ) -> SourceConnector:
        return self.repository.create_connector(payload)

    def list_connectors(self) -> tuple[SourceConnector, ...]:
        return self.repository.list_connectors()

    def detail(self, connector_id: str) -> ConnectorLifecycleDetail:
        return self.repository.detail(connector_id)

    def fetch(
        self,
        connector_id: str,
        request: SourceFetchRequest,
        *,
        source_credential: str | None = None,
    ) -> tuple[RawSourceSnapshot, FetchAttempt]:
        connector = self.repository.get_connector(connector_id)
        started = datetime.now(UTC)
        attempt_object_version = request.object_version or "connector-locator"
        if request.trigger_kind == "scheduled" and (
            connector.trigger_policy != "schedulable"
            or connector.schedule is None
            or not connector.schedule.enabled
        ):
            attempt = FetchAttempt(
                id=f"fetch-attempt-{uuid.uuid4()}",
                connector_id=connector.id,
                trigger_kind=request.trigger_kind,
                object_version=attempt_object_version,
                status="failed",
                error_code="scheduled_trigger_not_allowed",
                error_message="このConnectorのscheduled取得は有効ではありません",
                retry_of=request.retry_of,
                started_at=started,
                finished_at=datetime.now(UTC),
            )
            self.repository.save_attempt(attempt)
            raise SourceFetchFailedError(attempt)
        previous_items = self.repository.list_raw_snapshots(connector_id)
        previous = previous_items[-1] if previous_items else None
        source_byte_count: int | None = None
        try:
            if request.ingress == "source_locator":
                loaded = load_source_object(
                    connector.source_locator,
                    credential=source_credential,
                )
                object_content = loaded.content
                source_byte_count = loaded.byte_count
                attempt_object_version = loaded.object_version
                if (
                    request.expected_content_sha256 is not None
                    and loaded.content_sha256 != request.expected_content_sha256
                ):
                    raise SourceIntegrityError("source digest does not match")
            else:
                assert request.object_content is not None
                object_content = request.object_content
            candidate = build_raw_snapshot(
                connector,
                object_content,
                object_version=attempt_object_version,
                source_byte_count=source_byte_count,
                trigger_kind=request.trigger_kind,
                previous=previous,
            )
            if (
                request.expected_content_sha256 is not None
                and candidate.content_sha256 != request.expected_content_sha256
            ) or (
                request.expected_row_count is not None
                and candidate.row_count != request.expected_row_count
            ):
                raise SourceIntegrityError(
                    "source integrity expectation does not match"
                )
        except SourceIntegrityError:
            attempt = FetchAttempt(
                id=f"fetch-attempt-{uuid.uuid4()}",
                connector_id=connector.id,
                trigger_kind=request.trigger_kind,
                object_version=attempt_object_version,
                status="failed",
                error_code="source_integrity_mismatch",
                error_message="取得objectの完全性を確認できません",
                retry_of=request.retry_of,
                started_at=started,
                finished_at=datetime.now(UTC),
            )
            self.repository.save_attempt(attempt)
            raise SourceFetchFailedError(attempt) from None
        except SourceIngressError:
            attempt = FetchAttempt(
                id=f"fetch-attempt-{uuid.uuid4()}",
                connector_id=connector.id,
                trigger_kind=request.trigger_kind,
                object_version=attempt_object_version,
                status="failed",
                error_code="source_unavailable",
                error_message="取得objectをlocatorから読み込めません",
                retry_of=request.retry_of,
                started_at=started,
                finished_at=datetime.now(UTC),
            )
            self.repository.save_attempt(attempt)
            raise SourceFetchFailedError(attempt) from None
        except SourceObjectError:
            attempt = FetchAttempt(
                id=f"fetch-attempt-{uuid.uuid4()}",
                connector_id=connector.id,
                trigger_kind=request.trigger_kind,
                object_version=attempt_object_version,
                status="failed",
                error_code="invalid_object",
                error_message="取得objectを許可されたJSON形式として解釈できません",
                retry_of=request.retry_of,
                started_at=started,
                finished_at=datetime.now(UTC),
            )
            self.repository.save_attempt(attempt)
            raise SourceFetchFailedError(attempt) from None
        snapshot, reused = self.repository.save_raw_snapshot(candidate)
        attempt = FetchAttempt(
            id=f"fetch-attempt-{uuid.uuid4()}",
            connector_id=connector.id,
            trigger_kind=request.trigger_kind,
            object_version=attempt_object_version,
            status="succeeded",
            retry_of=request.retry_of,
            snapshot_id=snapshot.id,
            reused_existing_snapshot=reused,
            started_at=started,
            finished_at=datetime.now(UTC),
        )
        self.repository.save_attempt(attempt)
        return snapshot, attempt

    def create_recipe(
        self, payload: CurationRecipeCreateInput
    ) -> CurationRecipe:
        return self.repository.create_recipe(payload)

    def list_recipes(self) -> tuple[CurationRecipe, ...]:
        return self.repository.list_recipes()

    def curate(
        self,
        snapshot_id: str,
        payload: CurationRunCreateInput,
    ) -> CurationRun:
        snapshot = self.repository.get_raw_snapshot(snapshot_id)
        recipe = self.repository.get_recipe(payload.recipe_resource_id)
        connector = self.repository.get_connector(snapshot.connector_id)
        previous = self.repository.previous_curation_run(
            connector_id=connector.id,
            recipe_id=recipe.id,
            excluding_snapshot_id=snapshot.id,
        )
        run = curate_snapshot(
            snapshot,
            recipe,
            profile_revision_id=payload.profile_revision_id,
            profile_digest=payload.profile_digest,
            primary_key=connector.selection.primary_key,
            previous=previous,
        )
        return self.repository.save_curation_run(
            run,
            raw=snapshot,
            recipe=recipe,
        )

    def approve(
        self,
        run_id: str,
        payload: DatasetApprovalInput,
    ) -> CanonicalDatasetRevision:
        run = self.repository.get_curation_run(run_id)
        revision = approve_curation_run(run, payload)
        return self.repository.save_canonical_revision(revision, run=run)

    def create_training_snapshot(
        self,
        revision_id: str,
        payload: TrainingSnapshotCreateInput,
    ) -> ApprovedTrainingSnapshot:
        revision = self.repository.get_canonical_revision(revision_id)
        run = self.repository.get_curation_run(revision.curation_run_id)
        recipe = self.repository.get_recipe(run.recipe_id)
        declared_target_fields = {
            field
            for step in recipe.steps
            if step.kind == "target_eligibility_v1"
            for field in step.fields
        }
        requested_target_fields = {item.field for item in payload.targets}
        if requested_target_fields != declared_target_fields:
            raise LifecycleConflictError(
                "Training Snapshotのtarget fieldsがCuration Recipeと一致しません"
            )
        snapshot = build_training_snapshot(revision, run, payload)
        return self.repository.save_training_snapshot(
            snapshot,
            revision=revision,
            run=run,
        )
