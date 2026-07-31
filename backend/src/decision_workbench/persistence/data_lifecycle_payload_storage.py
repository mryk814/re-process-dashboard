from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from decision_workbench.contracts.data_lifecycle_contracts import (
    CurationRun,
    RawSourceSnapshot,
)
from decision_workbench.persistence.row_payload_store import (
    RowPayloadError,
    RowPayloadReference,
    RowPayloadStore,
)


class LifecyclePayloadUnavailableError(RuntimeError):
    def __init__(self, resource_kind: str, resource_id: str, reason: str) -> None:
        super().__init__(f"{resource_kind} {resource_id} のrow payloadを確認できません: {reason}")
        self.resource_kind = resource_kind
        self.resource_id = resource_id
        self.reason = reason


class QuarantinedPayloadReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["quarantined-row-payload/v1"] = (
        "quarantined-row-payload/v1"
    )
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    size_bytes: Annotated[int, Field(ge=0)]
    path: Annotated[
        str,
        Field(pattern=r"^row-payloads/quarantine/[a-z_]+/[0-9a-f]{64}\.json$"),
    ]


class StoredLifecycleRowResource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["lifecycle-row-storage/v1"] = (
        "lifecycle-row-storage/v1"
    )
    resource_kind: Literal["raw_source_snapshot", "curation_run"]
    resource_id: str
    resource: dict[str, Any]
    row_payload: RowPayloadReference | None
    unavailable_reason: str | None = None
    quarantined_payload: QuarantinedPayloadReference | None = None


def store_raw_snapshot(
    snapshot: RawSourceSnapshot,
    store: RowPayloadStore,
) -> tuple[str, RowPayloadReference]:
    reference = store.write(snapshot.rows, record_kind="raw-json-record/v1")
    if reference.row_count != snapshot.row_count:
        raise RowPayloadError("Raw Snapshot row count changed during persistence")
    payload = StoredLifecycleRowResource(
        resource_kind="raw_source_snapshot",
        resource_id=snapshot.id,
        resource=snapshot.model_dump(mode="json", exclude={"rows"}),
        row_payload=reference,
    )
    return payload.model_dump_json(), reference


def store_curation_run(
    run: CurationRun,
    store: RowPayloadStore,
) -> tuple[str, RowPayloadReference]:
    reference = store.write(run.rows, record_kind="curated-row/v1")
    if reference.row_count != len(run.rows):
        raise RowPayloadError("Curation Run row count changed during persistence")
    payload = StoredLifecycleRowResource(
        resource_kind="curation_run",
        resource_id=run.id,
        resource=run.model_dump(mode="json", exclude={"rows"}),
        row_payload=reference,
    )
    return payload.model_dump_json(), reference


def _read_rows(
    payload: StoredLifecycleRowResource,
    store: RowPayloadStore,
    *,
    expected_reference: RowPayloadReference | None = None,
    expected_resource_id: str | None = None,
) -> tuple[dict[str, Any], ...]:
    if expected_resource_id is not None and payload.resource_id != expected_resource_id:
        raise LifecyclePayloadUnavailableError(
            payload.resource_kind,
            expected_resource_id,
            "stored resource ID does not match its indexed ID",
        )
    if payload.row_payload is None:
        raise LifecyclePayloadUnavailableError(
            payload.resource_kind,
            payload.resource_id,
            payload.unavailable_reason or "payload reference is missing",
        )
    if (
        expected_reference is not None
        and payload.row_payload != expected_reference
    ):
        raise LifecyclePayloadUnavailableError(
            payload.resource_kind,
            payload.resource_id,
            "stored payload reference does not match its indexed reference",
        )
    try:
        return store.read(payload.row_payload)
    except RowPayloadError as exc:
        raise LifecyclePayloadUnavailableError(
            payload.resource_kind,
            payload.resource_id,
            str(exc),
        ) from exc


def hydrate_raw_snapshot(
    stored: str,
    store: RowPayloadStore,
    *,
    expected_reference: RowPayloadReference | None = None,
    expected_resource_id: str | None = None,
) -> RawSourceSnapshot:
    payload = StoredLifecycleRowResource.model_validate_json(stored)
    if payload.resource_kind != "raw_source_snapshot":
        raise LifecyclePayloadUnavailableError(
            payload.resource_kind,
            payload.resource_id,
            "resource kind does not match Raw Snapshot",
        )
    if payload.row_payload is not None and payload.row_payload.record_kind != (
        "raw-json-record/v1"
    ):
        raise LifecyclePayloadUnavailableError(
            payload.resource_kind,
            payload.resource_id,
            "record kind does not match Raw Snapshot",
        )
    return RawSourceSnapshot.model_validate(
        {
            **payload.resource,
            "rows": _read_rows(
                payload,
                store,
                expected_reference=expected_reference,
                expected_resource_id=expected_resource_id,
            ),
        }
    )


def hydrate_curation_run(
    stored: str,
    store: RowPayloadStore,
    *,
    expected_reference: RowPayloadReference | None = None,
    expected_resource_id: str | None = None,
) -> CurationRun:
    payload = StoredLifecycleRowResource.model_validate_json(stored)
    if payload.resource_kind != "curation_run":
        raise LifecyclePayloadUnavailableError(
            payload.resource_kind,
            payload.resource_id,
            "resource kind does not match Curation Run",
        )
    if payload.row_payload is not None and payload.row_payload.record_kind != (
        "curated-row/v1"
    ):
        raise LifecyclePayloadUnavailableError(
            payload.resource_kind,
            payload.resource_id,
            "record kind does not match Curation Run",
        )
    return CurationRun.model_validate(
        {
            **payload.resource,
            "rows": _read_rows(
                payload,
                store,
                expected_reference=expected_reference,
                expected_resource_id=expected_resource_id,
            ),
        }
    )
