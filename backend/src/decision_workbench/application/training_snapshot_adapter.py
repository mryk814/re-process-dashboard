"""Task adapters that hand approved Training Snapshots to package builders.

The Source Lifecycle deliberately stores canonical rows and approval identities
separately from model-specific files.  This module is the narrow handoff: it
revalidates the immutable digest chain and materializes only approved,
target-eligible rows in a deterministic order.
"""
from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from decision_workbench.contracts.data_lifecycle_contracts import (
    TrainingSplitDefinition,
    TrainingTargetCohort,
)
from decision_workbench.data.profile_family_registry import (
    profile_output_columns,
    profile_registration_metadata,
    restore_profile_document,
)
from decision_workbench.modeling.model_lifecycle import dataset_profile_digest
from decision_workbench.modeling.packages.contracts import SourceLifecycleProvenance
from decision_workbench.modeling.tabular.data import load_tabular_data
from decision_workbench.modeling.tabular.profile import TabularDatasetProfile
from decision_workbench.persistence.data_lifecycle_repository import (
    DataLifecycleRepository,
)
from decision_workbench.persistence.workspace_catalog import WorkspaceCatalog


BATTERY_TASK_ID = "battery-degradation-v1"
BATTERY_SOURCE_ROW_KEY = "_source_row_key"
BATTERY_SOURCE_ADAPTER_ID = "calce-battery-csv-to-source-records"
BATTERY_SOURCE_ADAPTER_VERSION = "1.0.0"
BATTERY_MATERIALIZATION_ADAPTER_ID = "battery-training-snapshot-csv"
BATTERY_MATERIALIZATION_ADAPTER_VERSION = "1.0.0"
TABULAR_MATERIALIZATION_ADAPTER_ID = "tabular-training-snapshot-csv"
TABULAR_MATERIALIZATION_ADAPTER_VERSION = "1.0.0"
TABULAR_PROFILE_SCHEMA_VERSION = "tabular-dataset-profile/v1"
READ_ONLY_SOURCE_ROOT = Path(__file__).resolve().parents[4] / "data" / "source"


@dataclass(frozen=True)
class TrainingSnapshotBuilderInput:
    """Immutable standard-builder input resolved from one approved Snapshot."""

    task_id: str
    profile_revision_id: str
    profile_digest: str
    path: Path
    source_sha256: str
    row_count: int
    target_cohorts: tuple[TrainingTargetCohort, ...]
    split: TrainingSplitDefinition
    provenance: SourceLifecycleProvenance


@dataclass(frozen=True)
class TrainingSnapshotMaterializationRequest:
    task_id: str
    profile_revision_id: str
    training_snapshot_id: str
    destination: Path


@dataclass(frozen=True)
class TrainingSnapshotMaterializationUnavailable:
    reason_code: str
    reason: str
    status: Literal["unavailable"] = "unavailable"


@dataclass(frozen=True)
class TrainingSnapshotMaterializationAvailable:
    builder_input: TrainingSnapshotBuilderInput
    status: Literal["available"] = "available"


TrainingSnapshotMaterializationResult = (
    TrainingSnapshotMaterializationAvailable
    | TrainingSnapshotMaterializationUnavailable
)


@dataclass(frozen=True)
class TrainingSnapshotMaterializationContext:
    task_id: str
    profile_revision_id: str
    profile_digest: str
    profile: Any
    snapshot: Any
    revision: Any
    run: Any
    raw: Any
    recipe: Any
    connector: Any


class TrainingSnapshotMaterializerPort(Protocol):
    profile_schema_version: str
    profile_id: str | None
    materialization_adapter_id: str
    materialization_adapter_version: str

    def materialize(
        self,
        context: TrainingSnapshotMaterializationContext,
        destination: Path,
    ) -> TrainingSnapshotBuilderInput: ...


def battery_row_key(record: dict[str, Any]) -> str:
    """Return the stable upstream identity for one CALCE cell cycle."""

    cell_id = str(record.get("cell_id", "")).strip()
    source_file = str(record.get("source_file", "")).strip()
    local_cycle = str(record.get("source_local_cycle", "")).strip()
    if not cell_id or not source_file or not local_cycle:
        raise ValueError(
            "battery row identity requires cell_id, source_file and "
            "source_local_cycle"
        )
    try:
        numeric_cycle = float(local_cycle)
    except ValueError as exc:
        raise ValueError("battery source_local_cycle must be numeric") from exc
    if not numeric_cycle.is_integer():
        raise ValueError("battery source_local_cycle must be an integer")
    return f"{cell_id}|{source_file}|{int(numeric_cycle)}"


def battery_source_records(
    source: Path,
) -> tuple[dict[str, Any], ...]:
    """Read the immutable CALCE CSV into Source Lifecycle JSON records.

    The single-key Source Connector contract remains unchanged.  A composite
    task identity is made explicit before ingestion.  Training selection stays
    outside Raw acquisition and is recorded by the versioned Curation Recipe.
    """

    records: list[dict[str, Any]] = []
    with source.open("r", encoding="utf-8-sig", newline="") as stream:
        for raw in csv.DictReader(stream):
            record: dict[str, Any] = dict(raw)
            row_key = battery_row_key(record)
            record[BATTERY_SOURCE_ROW_KEY] = row_key
            records.append(record)
    if not records:
        raise ValueError("battery source has no rows")
    return tuple(records)


def battery_source_json(
    source: Path,
) -> str:
    return json.dumps(
        battery_source_records(source),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True)
class TabularTrainingSnapshotMaterializer:
    """Materialize an approved tabular Snapshot without changing its selection."""

    profile_schema_version: str = TABULAR_PROFILE_SCHEMA_VERSION
    profile_id: str | None = None
    materialization_adapter_id: str = TABULAR_MATERIALIZATION_ADAPTER_ID
    materialization_adapter_version: str = (
        TABULAR_MATERIALIZATION_ADAPTER_VERSION
    )
    required_source_adapter_id: str | None = None
    required_source_adapter_version: str | None = None

    def materialize(
        self,
        context: TrainingSnapshotMaterializationContext,
        destination: Path,
    ) -> TrainingSnapshotBuilderInput:
        snapshot = context.snapshot
        revision = context.revision
        run = context.run
        raw = context.raw
        recipe = context.recipe
        connector = context.connector
        profile = context.profile
        if not isinstance(profile, TabularDatasetProfile):
            raise TypeError("Tabular materializer requires a Tabular Dataset Profile")
        source_adapter_id = connector.selection.source_adapter_id
        source_adapter_version = connector.selection.source_adapter_version
        if not source_adapter_id or not source_adapter_version:
            raise ValueError(
                "Training Snapshot materialization requires a versioned source adapter"
            )
        if (
            self.required_source_adapter_id is not None
            and (
                source_adapter_id != self.required_source_adapter_id
                or source_adapter_version != self.required_source_adapter_version
            )
        ):
            raise ValueError(
                "Battery Training Snapshot requires the versioned CALCE "
                "source adapter"
            )
        if snapshot.schema_version != "approved-training-snapshot/v2":
            raise ValueError(
                "standard builder materialization requires a v2 Training Snapshot"
            )
        if snapshot.split is None:
            raise ValueError("Training Snapshot split identity is missing")
        builder_group_field = profile.group_column or profile.id_column
        if snapshot.split.group_field != builder_group_field:
            raise ValueError(
                "Training Snapshot split group field does not match the "
                "resolved Profile Revision"
            )
        if snapshot.selection_policy_digest is None:
            raise ValueError(
                "Training Snapshot requires a versioned selection policy"
            )
        outputs = profile_output_columns(profile, context.task_id)
        for cohort in snapshot.target_cohorts:
            expected_fields = outputs.get(cohort.target_key)
            if (
                expected_fields is None
                or cohort.target_field not in expected_fields
            ):
                raise ValueError(
                    "Training Snapshot target does not match the resolved "
                    f"Profile Revision: {cohort.target_key}/{cohort.target_field}"
                )
        rows_by_key = {row.row_key: row for row in run.rows}
        if len(rows_by_key) != len(run.rows):
            raise ValueError("Curation Run contains duplicate row keys")
        missing = [
            row_key
            for row_key in snapshot.included_row_keys
            if row_key not in rows_by_key
        ]
        if missing:
            raise ValueError(
                "Training Snapshot references missing rows: " + ", ".join(missing[:5])
            )
        approved = set(revision.approved_row_keys)
        cohort_rows = {
            row_key
            for cohort in snapshot.target_cohorts
            for row_key in cohort.row_keys
        }
        records: list[dict[str, Any]] = []
        for row_key in snapshot.included_row_keys:
            row = rows_by_key[row_key]
            if (
                row_key not in approved
                or row.status not in {"accepted", "warning"}
                or (
                    snapshot.schema_version == "approved-training-snapshot/v1"
                    and not row.target_eligible
                )
                or (
                    snapshot.schema_version == "approved-training-snapshot/v2"
                    and row_key not in cohort_rows
                )
            ):
                raise ValueError(
                    f"Training Snapshot row is not approved and eligible: {row_key}"
                )
            records.append(row.canonical_record)

        columns = self._materialized_columns(profile)
        encoded = self._encode_csv(records, columns)
        destination = destination.resolve()
        source_root = READ_ONLY_SOURCE_ROOT.resolve()
        if destination == source_root or source_root in destination.parents:
            raise ValueError(
                "materialized Training Snapshot cannot be written beneath "
                "the read-only source of truth"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.read_bytes() != encoded:
                raise FileExistsError(
                    f"immutable Training Snapshot artifact already differs: {destination}"
                )
        else:
            staging = destination.with_name(f".{destination.name}.partial")
            try:
                staging.write_bytes(encoded)
                staging.replace(destination)
            finally:
                staging.unlink(missing_ok=True)

        digest = hashlib.sha256(encoded).hexdigest()
        loaded = load_tabular_data(
            destination,
            profile,
            profile_locator=f"catalog:{context.profile_revision_id}",
        )
        if loaded.source_sha256 != digest or loaded.row_count != snapshot.row_count:
            raise ValueError("materialized Training Snapshot did not round-trip")
        provenance = SourceLifecycleProvenance(
            connector_id=connector.id,
            connector_configuration_digest=connector.configuration_digest,
            source_adapter_id=source_adapter_id,
            source_adapter_version=source_adapter_version,
            raw_snapshot_id=raw.id,
            raw_snapshot_digest=raw.snapshot_digest,
            recipe_id=recipe.id,
            recipe_digest=recipe.recipe_digest,
            curation_run_id=run.id,
            curation_digest=run.curation_digest,
            profile_revision_id=context.profile_revision_id,
            profile_digest=context.profile_digest,
            canonical_dataset_revision_id=revision.id,
            canonical_dataset_digest=revision.dataset_digest,
            training_snapshot_id=snapshot.id,
            training_snapshot_digest=snapshot.snapshot_digest,
            training_selection_policy_digest=(
                snapshot.selection_policy_digest
            ),
            materialization_adapter_id=self.materialization_adapter_id,
            materialization_adapter_version=self.materialization_adapter_version,
            materialized_training_sha256=digest,
            row_count=snapshot.row_count,
        )
        return TrainingSnapshotBuilderInput(
            task_id=context.task_id,
            profile_revision_id=context.profile_revision_id,
            profile_digest=context.profile_digest,
            path=destination,
            source_sha256=digest,
            row_count=snapshot.row_count,
            target_cohorts=snapshot.target_cohorts,
            split=snapshot.split,
            provenance=provenance,
        )

    @staticmethod
    def _materialized_columns(
        profile: TabularDatasetProfile,
    ) -> tuple[str, ...]:
        columns: list[str] = []
        if profile.id_column:
            columns.append(profile.id_column)
        if (
            profile.group_column
            and profile.group_column not in columns
        ):
            columns.append(profile.group_column)
        columns.extend(item.column for item in profile.inputs)
        columns.extend(item.column for item in profile.outputs)
        return tuple(dict.fromkeys(columns))

    @staticmethod
    def _encode_csv(
        records: list[dict[str, Any]],
        columns: tuple[str, ...],
    ) -> bytes:
        from io import StringIO

        stream = StringIO(newline="")
        writer = csv.DictWriter(
            stream,
            fieldnames=columns,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        for record in records:
            writer.writerow(record)
        return stream.getvalue().encode("utf-8")


class TrainingSnapshotMaterializerRegistry:
    """Resolve one exact allow-listed materializer; never try another source."""

    def __init__(
        self,
        repository: DataLifecycleRepository,
        catalog: WorkspaceCatalog,
        adapters: tuple[TrainingSnapshotMaterializerPort, ...],
    ) -> None:
        self.repository = repository
        self.catalog = catalog
        exact: dict[tuple[str, str], TrainingSnapshotMaterializerPort] = {}
        families: dict[str, TrainingSnapshotMaterializerPort] = {}
        for adapter in adapters:
            if adapter.profile_id is None:
                if adapter.profile_schema_version in families:
                    raise ValueError("Profile family materializer must be unique")
                families[adapter.profile_schema_version] = adapter
            else:
                key = (adapter.profile_schema_version, adapter.profile_id)
                if key in exact:
                    raise ValueError("Exact Profile materializer must be unique")
                exact[key] = adapter
        self._exact = exact
        self._families = families

    def materialize(
        self,
        request: TrainingSnapshotMaterializationRequest,
    ) -> TrainingSnapshotMaterializationResult:
        profile_revision = self.catalog.get_profile_revision(
            request.profile_revision_id,
            include_archived=True,
        )
        if profile_revision is None:
            return TrainingSnapshotMaterializationUnavailable(
                reason_code="profile_revision_not_found",
                reason="指定したProfile Revisionを解決できません",
            )
        schema_version = str(
            profile_revision.effective_profile_json.get("schema_version")
        )
        adapter = self._exact.get(
            (schema_version, profile_revision.profile_id)
        )
        if adapter is None:
            adapter = self._families.get(schema_version)
        if adapter is None:
            return TrainingSnapshotMaterializationUnavailable(
                reason_code="profile_family_unsupported",
                reason=(
                    "このProfile familyのTraining Snapshot materializerは"
                    "allow-listされていません"
                ),
            )
        profile = restore_profile_document(profile_revision.effective_profile_json)
        metadata = profile_registration_metadata(profile)
        if request.task_id not in metadata.task_ids:
            return TrainingSnapshotMaterializationUnavailable(
                reason_code="profile_task_mismatch",
                reason="Profile Revisionは指定したPrediction Taskに対応していません",
            )
        if dataset_profile_digest(profile) != profile_revision.profile_digest:
            raise ValueError(
                "Profile Revision digest does not match its effective Profile"
            )
        context = self._context(
            task_id=request.task_id,
            profile_revision_id=profile_revision.id,
            profile_digest=profile_revision.profile_digest,
            profile=profile,
            snapshot_id=request.training_snapshot_id,
        )
        return TrainingSnapshotMaterializationAvailable(
            builder_input=adapter.materialize(context, request.destination)
        )

    def _context(
        self,
        *,
        task_id: str,
        profile_revision_id: str,
        profile_digest: str,
        profile: Any,
        snapshot_id: str,
    ) -> TrainingSnapshotMaterializationContext:
        snapshot = self.repository.get_training_snapshot(snapshot_id)
        revision = self.repository.get_canonical_revision(
            snapshot.canonical_dataset_revision_id
        )
        run = self.repository.get_curation_run(revision.curation_run_id)
        raw = self.repository.get_raw_snapshot(run.raw_snapshot_id)
        recipe = self.repository.get_recipe(run.recipe_id)
        connector = self.repository.get_connector(raw.connector_id)
        self._validate_chain(
            snapshot=snapshot,
            revision=revision,
            run=run,
            raw=raw,
            recipe=recipe,
            connector=connector,
            profile_revision_id=profile_revision_id,
            profile_digest=profile_digest,
        )
        return TrainingSnapshotMaterializationContext(
            task_id=task_id,
            profile_revision_id=profile_revision_id,
            profile_digest=profile_digest,
            profile=profile,
            snapshot=snapshot,
            revision=revision,
            run=run,
            raw=raw,
            recipe=recipe,
            connector=connector,
        )

    @staticmethod
    def _validate_chain(
        *,
        snapshot: Any,
        revision: Any,
        run: Any,
        raw: Any,
        recipe: Any,
        connector: Any,
        profile_revision_id: str,
        profile_digest: str,
    ) -> None:
        if (
            snapshot.canonical_dataset_revision_id != revision.id
            or revision.curation_run_id != run.id
            or run.raw_snapshot_id != raw.id
            or run.recipe_id != recipe.id
            or raw.connector_id != connector.id
        ):
            raise ValueError("Source Lifecycle reference chain mismatch")
        if snapshot.dataset_digest != revision.dataset_digest:
            raise ValueError("Training Snapshot dataset digest mismatch")
        if (
            revision.curation_digest != run.curation_digest
            or revision.raw_snapshot_digest != raw.snapshot_digest
            or revision.recipe_digest != recipe.recipe_digest
            or revision.profile_revision_id != run.profile_revision_id
            or revision.profile_digest != run.profile_digest
            or run.profile_revision_id != profile_revision_id
            or run.profile_digest != profile_digest
            or run.raw_snapshot_digest != raw.snapshot_digest
            or run.recipe_digest != recipe.recipe_digest
            or raw.connector_configuration_digest
            != connector.configuration_digest
        ):
            raise ValueError("Source Lifecycle digest chain mismatch")


BATTERY_TRAINING_SNAPSHOT_MATERIALIZER = TabularTrainingSnapshotMaterializer(
    profile_id="calce-cs2-battery-capacity-v1",
    materialization_adapter_id=BATTERY_MATERIALIZATION_ADAPTER_ID,
    materialization_adapter_version=BATTERY_MATERIALIZATION_ADAPTER_VERSION,
    required_source_adapter_id=BATTERY_SOURCE_ADAPTER_ID,
    required_source_adapter_version=BATTERY_SOURCE_ADAPTER_VERSION,
)
TABULAR_TRAINING_SNAPSHOT_MATERIALIZER = TabularTrainingSnapshotMaterializer()


def training_snapshot_materializer_registry(
    repository: DataLifecycleRepository,
    catalog: WorkspaceCatalog,
) -> TrainingSnapshotMaterializerRegistry:
    return TrainingSnapshotMaterializerRegistry(
        repository,
        catalog,
        (
            BATTERY_TRAINING_SNAPSHOT_MATERIALIZER,
            TABULAR_TRAINING_SNAPSHOT_MATERIALIZER,
        ),
    )
