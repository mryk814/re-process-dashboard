from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RowPayloadError(RuntimeError):
    pass


class RowPayloadReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["row-payload-ref/v1"] = "row-payload-ref/v1"
    record_kind: Literal["raw-json-record/v1", "curated-row/v1"]
    media_type: Literal["application/x-ndjson"] = "application/x-ndjson"
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    size_bytes: Annotated[int, Field(ge=0)]
    row_count: Annotated[int, Field(ge=0)]


def _canonical_line(row: Mapping[str, Any] | BaseModel) -> bytes:
    if isinstance(row, BaseModel):
        return (row.model_dump_json() + "\n").encode("utf-8")
    payload = dict(row)
    try:
        return (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RowPayloadError("row payload is not canonical JSON") from exc


class RowPayloadStore:
    def __init__(self, database: str | Path) -> None:
        self.database = Path(database).resolve()
        self.root = self.database.parent / "row-payloads" / "sha256"

    def path_for(self, reference: RowPayloadReference) -> Path:
        digest = reference.sha256
        return self.root / digest[:2] / f"{digest}.jsonl"

    def write(
        self,
        rows: Iterable[Mapping[str, Any] | BaseModel],
        *,
        record_kind: Literal["raw-json-record/v1", "curated-row/v1"],
    ) -> RowPayloadReference:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary_root = self.root / ".tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        temporary = temporary_root / f"{uuid.uuid4().hex}.jsonl"
        digest = hashlib.sha256()
        size_bytes = 0
        row_count = 0
        try:
            with temporary.open("xb") as output:
                for row in rows:
                    encoded = _canonical_line(row)
                    output.write(encoded)
                    digest.update(encoded)
                    size_bytes += len(encoded)
                    row_count += 1
                output.flush()
                os.fsync(output.fileno())
            reference = RowPayloadReference(
                record_kind=record_kind,
                sha256=digest.hexdigest(),
                size_bytes=size_bytes,
                row_count=row_count,
            )
            destination = self.path_for(reference)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                self.verify(reference)
                return reference
            os.replace(temporary, destination)
            return reference
        finally:
            temporary.unlink(missing_ok=True)

    def verify(self, reference: RowPayloadReference) -> None:
        self._scan(reference, collect=False)

    def read(self, reference: RowPayloadReference) -> tuple[dict[str, Any], ...]:
        return self._scan(reference, collect=True)

    def _scan(
        self,
        reference: RowPayloadReference,
        *,
        collect: bool,
    ) -> tuple[dict[str, Any], ...]:
        path = self.path_for(reference)
        try:
            root = self.root.resolve(strict=True)
            resolved = path.resolve(strict=True)
            if resolved.parent.parent != root or resolved.parent.name != reference.sha256[:2]:
                raise RowPayloadError("row payload path escapes its content root")
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise RowPayloadError("row payload is not a regular file")
            if metadata.st_size != reference.size_bytes:
                raise RowPayloadError("row payload size does not match its reference")
            digest = hashlib.sha256()
            rows: list[dict[str, Any]] = []
            observed_count = 0
            with path.open("rb") as source:
                for raw_line in source:
                    observed_count += 1
                    digest.update(raw_line)
                    if not raw_line.endswith(b"\n"):
                        raise RowPayloadError("row payload line is not LF terminated")
                    try:
                        parsed = json.loads(raw_line)
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise RowPayloadError("row payload contains invalid JSON") from exc
                    if not isinstance(parsed, dict):
                        raise RowPayloadError("row payload record is not an object")
                    if collect:
                        rows.append(parsed)
            if digest.hexdigest() != reference.sha256:
                raise RowPayloadError("row payload digest does not match its reference")
            if observed_count != reference.row_count:
                raise RowPayloadError("row payload count does not match its reference")
            return tuple(rows)
        except OSError as exc:
            raise RowPayloadError("row payload file is unavailable") from exc
