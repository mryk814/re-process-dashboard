from __future__ import annotations

import hashlib
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlsplit


MAX_SOURCE_BYTES = 16 * 1024 * 1024
READ_CHUNK_BYTES = 1024 * 1024


class SourceIngressError(ValueError):
    pass


class SourceIntegrityError(SourceIngressError):
    pass


@dataclass(frozen=True)
class LoadedSourceObject:
    content: str
    byte_count: int
    content_sha256: str
    object_version: str


class SourceIngress(Protocol):
    def acquire(
        self,
        locator: str,
        *,
        credential: str | None = None,
    ) -> LoadedSourceObject: ...


def _file_path(locator: str) -> Path:
    parsed = urlsplit(locator)
    if parsed.scheme == "file":
        if parsed.netloc not in {"", "localhost"}:
            raise SourceIngressError("remote file host is not allowed")
        decoded = unquote(parsed.path)
        if len(decoded) >= 3 and decoded[0] == "/" and decoded[2] == ":":
            decoded = decoded[1:]
        return Path(decoded)
    if parsed.scheme:
        raise SourceIngressError("unsupported source locator scheme")
    return Path(locator)


def _read_chunks(chunks) -> tuple[str, int, str]:
    digest = hashlib.sha256()
    payload = bytearray()
    for chunk in chunks:
        if not chunk:
            continue
        payload.extend(chunk)
        if len(payload) > MAX_SOURCE_BYTES:
            raise SourceIngressError("source object exceeds the size limit")
        digest.update(chunk)
    if not payload:
        raise SourceIngressError("source object is empty")
    try:
        content = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceIngressError("source object is not UTF-8") from exc
    return content, len(payload), digest.hexdigest()


class LocalFileSourceIngress:
    def acquire(
        self,
        locator: str,
        *,
        credential: str | None = None,
    ) -> LoadedSourceObject:
        # A local file does not consume credentials. Keeping it inside this
        # boundary lets a future allow-listed remote adapter consume one
        # without persisting it in a request, attempt, or snapshot.
        del credential
        path = _file_path(locator).resolve()
        try:
            before = path.stat()
            if not stat.S_ISREG(before.st_mode):
                raise SourceIngressError("source locator is not a regular file")
            if before.st_size > MAX_SOURCE_BYTES:
                raise SourceIngressError("source object exceeds the size limit")
            with path.open("rb") as source:
                content, byte_count, digest = _read_chunks(
                    iter(lambda: source.read(READ_CHUNK_BYTES), b"")
                )
            after = path.stat()
        except OSError as exc:
            raise SourceIngressError("source file could not be read") from exc
        if (
            before.st_size,
            before.st_mtime_ns,
            before.st_ino,
        ) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ino,
        ):
            raise SourceIngressError("source file changed during acquisition")
        return LoadedSourceObject(
            content=content,
            byte_count=byte_count,
            content_sha256=digest,
            object_version=f"sha256:{digest}",
        )


def load_source_object(
    locator: str,
    *,
    credential: str | None = None,
    ingress: SourceIngress | None = None,
) -> LoadedSourceObject:
    return (ingress or LocalFileSourceIngress()).acquire(
        locator,
        credential=credential,
    )
