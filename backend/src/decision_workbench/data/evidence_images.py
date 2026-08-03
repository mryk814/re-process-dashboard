"""Resolve observation evidence images declared by a Dataset Input Profile.

An observation row may point at a micrograph. The column that holds the pointer
is declared per role in the Profile (`technical` mapping named ``evidence_image``),
never hard-coded here.

The pointer is untrusted source data, so resolution is deliberately narrow:

- the path is treated as relative to the source workbook's directory
- absolute paths, drive letters and anything escaping that directory are refused
- only allow-listed image suffixes are served
- a declared-but-missing file is reported as missing, never substituted

Nothing here reads the file's content beyond its bytes; no image library runs.
"""
from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping

EVIDENCE_IMAGE_FIELD = "evidence_image"
ALLOWED_SUFFIXES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


class EvidenceImageError(ValueError):
    """The declared image pointer cannot be served."""


def technical_field_is_optional(
    role: str,
    name: str,
    declared_optional_fields: Collection[str] = (),
) -> bool:
    """Apply one requiredness contract to Profile technical fields.

    A micrograph pointer is optional evidence by meaning. Existing Profiles can
    continue declaring other auxiliary fields through
    ``optional_technical_fields``.
    """

    return (
        name == EVIDENCE_IMAGE_FIELD
        or name in declared_optional_fields
        or f"{role}.{name}" in declared_optional_fields
    )


@dataclass(frozen=True)
class EvidenceImage:
    """A declared pointer, plus whether the bytes are actually available."""

    declared_path: str
    available: bool
    media_type: str | None = None
    resolved: Path | None = None
    reason: str | None = None


def _safe_relative(declared: str) -> PurePosixPath:
    text = declared.strip().replace("\\", "/")
    if not text:
        raise EvidenceImageError("画像パスが空です")
    if PureWindowsPath(text).is_absolute() or text.startswith("/"):
        raise EvidenceImageError("画像パスは絶対パスにできません")
    relative = PurePosixPath(text)
    if any(part in {"..", ""} for part in relative.parts):
        raise EvidenceImageError("画像パスに上位ディレクトリを含められません")
    return relative


def resolve_evidence_image(declared: str, source_path: str | Path) -> EvidenceImage:
    """Resolve a declared pointer inside the dataset's own directory."""

    relative = _safe_relative(declared)
    suffix = relative.suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise EvidenceImageError(f"対応していない画像形式です: {relative.suffix or '(拡張子なし)'}")
    root = Path(source_path).resolve().parent
    target = (root / relative).resolve()
    if root != target.parent and root not in target.parents:
        raise EvidenceImageError("画像パスがデータセットの配置場所の外を指しています")
    if not target.is_file():
        return EvidenceImage(
            declared_path=str(relative),
            available=False,
            reason="画像ファイルが見つかりません",
        )
    return EvidenceImage(
        declared_path=str(relative),
        available=True,
        media_type=ALLOWED_SUFFIXES[suffix],
        resolved=target,
    )


def declared_evidence_image(
    technical_columns: Mapping[tuple[str, str], str],
    role: str,
    source_row: Mapping[str, Any],
) -> str | None:
    """The raw pointer for a role, or None when the Profile declares no column."""

    column = technical_columns.get((role, EVIDENCE_IMAGE_FIELD))
    if column is None:
        return None
    value = source_row.get(column)
    if value is None:
        return None
    text = str(value).strip()
    return text or None
