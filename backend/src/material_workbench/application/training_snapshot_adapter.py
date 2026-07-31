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
from typing import Any

from material_workbench.modeling.model_package_contracts import SourceLifecycleProvenance
from material_workbench.modeling.tabular.data import load_tabular_data
from material_workbench.modeling.tabular.profile import load_tabular_profile
from material_workbench.persistence.data_lifecycle_repository import (
    DataLifecycleRepository,
)


BATTERY_TASK_ID = "battery-degradation-v1"
BATTERY_SOURCE_ROW_KEY = "_source_row_key"
BATTERY_SOURCE_ADAPTER_ID = "calce-battery-csv-to-source-records"
BATTERY_SOURCE_ADAPTER_VERSION = "1.0.0"
BATTERY_MATERIALIZATION_ADAPTER_ID = "battery-training-snapshot-csv"
BATTERY_MATERIALIZATION_ADAPTER_VERSION = "1.0.0"


@dataclass(frozen=True)
class TrainingSnapshotMaterialization:
    path: Path
    source_sha256: str
    row_count: int
    provenance: SourceLifecycleProvenance


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


class BatteryTrainingSnapshotAdapter:
    """Materialize one approved battery Training Snapshot as immutable CSV."""

    def __init__(
        self,
        repository: DataLifecycleRepository,
        profile_path: Path,
    ) -> None:
        self.repository = repository
        self.profile_path = profile_path.resolve()
        self.profile = load_tabular_profile(self.profile_path)
        if self.profile.task_id != BATTERY_TASK_ID:
            raise ValueError(
                f"Battery adapter cannot materialize task {self.profile.task_id}"
            )

    def materialize(
        self,
        snapshot_id: str,
        destination: Path,
    ) -> TrainingSnapshotMaterialization:
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
        )
        if (
            connector.selection.source_adapter_id
            != BATTERY_SOURCE_ADAPTER_ID
            or connector.selection.source_adapter_version
            != BATTERY_SOURCE_ADAPTER_VERSION
        ):
            raise ValueError(
                "Battery Training Snapshot requires the versioned CALCE "
                "source adapter"
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
        if snapshot.selection_policy_digest is None:
            raise ValueError(
                "Battery Training Snapshot requires a versioned selection policy"
            )

        columns = self._materialized_columns()
        encoded = self._encode_csv(records, columns)
        destination = destination.resolve()
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
        loaded = load_tabular_data(destination, self.profile_path)
        if loaded.source_sha256 != digest or loaded.row_count != snapshot.row_count:
            raise ValueError("materialized Training Snapshot did not round-trip")
        provenance = SourceLifecycleProvenance(
            connector_id=connector.id,
            connector_configuration_digest=connector.configuration_digest,
            source_adapter_id=BATTERY_SOURCE_ADAPTER_ID,
            source_adapter_version=BATTERY_SOURCE_ADAPTER_VERSION,
            raw_snapshot_id=raw.id,
            raw_snapshot_digest=raw.snapshot_digest,
            recipe_id=recipe.id,
            recipe_digest=recipe.recipe_digest,
            curation_run_id=run.id,
            curation_digest=run.curation_digest,
            profile_revision_id=run.profile_revision_id,
            profile_digest=run.profile_digest,
            canonical_dataset_revision_id=revision.id,
            canonical_dataset_digest=revision.dataset_digest,
            training_snapshot_id=snapshot.id,
            training_snapshot_digest=snapshot.snapshot_digest,
            training_selection_policy_digest=(
                snapshot.selection_policy_digest
            ),
            materialization_adapter_id=BATTERY_MATERIALIZATION_ADAPTER_ID,
            materialization_adapter_version=(
                BATTERY_MATERIALIZATION_ADAPTER_VERSION
            ),
            materialized_training_sha256=digest,
            row_count=snapshot.row_count,
        )
        return TrainingSnapshotMaterialization(
            path=destination,
            source_sha256=digest,
            row_count=snapshot.row_count,
            provenance=provenance,
        )

    def _materialized_columns(self) -> tuple[str, ...]:
        columns: list[str] = []
        if self.profile.id_column:
            columns.append(self.profile.id_column)
        if (
            self.profile.group_column
            and self.profile.group_column not in columns
        ):
            columns.append(self.profile.group_column)
        columns.extend(item.column for item in self.profile.inputs)
        columns.extend(item.column for item in self.profile.outputs)
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

    @staticmethod
    def _validate_chain(
        *,
        snapshot: Any,
        revision: Any,
        run: Any,
        raw: Any,
        recipe: Any,
        connector: Any,
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
            or run.raw_snapshot_digest != raw.snapshot_digest
            or run.recipe_digest != recipe.recipe_digest
            or raw.connector_configuration_digest
            != connector.configuration_digest
        ):
            raise ValueError("Source Lifecycle digest chain mismatch")
