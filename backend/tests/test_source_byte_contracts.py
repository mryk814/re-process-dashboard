"""Byte-addressed artifacts must survive checkout unchanged.

Package provenance is a digest over raw bytes. A checkout that rewrites line
endings therefore invalidates every active Package while `git status` still
reports a clean tree, so both the attribute rules and the tree itself are
contract surfaces.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
BYTE_ADDRESSED_PREFIXES = ("data/source/", "models/", "examples/model-packages/")


def _git(*arguments: str) -> str:
    if shutil.which("git") is None:
        pytest.skip("git is unavailable")
    completed = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip(f"git {' '.join(arguments)} failed: {completed.stderr.decode('utf-8', 'replace')}")
    return completed.stdout.decode("utf-8", "replace")


def _tracked_byte_addressed_files() -> list[str]:
    """Tracked artifacts whose bytes carry provenance; prose beside them does not."""
    files = [path for path in _git("ls-files", "-z").split("\0") if path]
    return [
        path
        for path in files
        if path.startswith(BYTE_ADDRESSED_PREFIXES) and not path.lower().endswith(".md")
    ]


def _checkout_rewrites_line_endings(attributes: dict[str, str]) -> bool:
    if attributes.get("text") in {"unset", "false"}:  # -text or binary
        return False
    if attributes.get("eol") == "lf":
        return False
    return True  # unspecified leaves the bytes to git's text auto-detection


def _attributes_by_path(paths: list[str]) -> dict[str, dict[str, str]]:
    output = _git("check-attr", "-z", "text", "eol", "--", *paths)
    fields = output.split("\0")
    resolved: dict[str, dict[str, str]] = {path: {} for path in paths}
    for index in range(0, len(fields) - 3 + 1, 3):
        path, attribute, value = fields[index], fields[index + 1], fields[index + 2]
        if path in resolved:
            resolved[path][attribute] = value
    return resolved


def test_byte_addressed_artifacts_are_never_line_ending_converted() -> None:
    paths = _tracked_byte_addressed_files()
    assert paths, "expected tracked data and model artifacts"
    attributes = _attributes_by_path(paths)
    unprotected = sorted(
        path for path in paths if _checkout_rewrites_line_endings(attributes[path])
    )
    assert not unprotected, (
        "these Package inputs could be rewritten on a CRLF checkout; "
        f"add a .gitattributes rule: {unprotected}"
    )


def _committed_bytes(paths: list[str]) -> dict[str, bytes]:
    """Read every committed blob through one `git cat-file` stream."""
    request = "".join(f"HEAD:{path}\n" for path in paths).encode("utf-8")
    completed = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=ROOT,
        input=request,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip("git cat-file --batch failed")
    blobs: dict[str, bytes] = {}
    cursor = 0
    stream = completed.stdout
    for path in paths:
        header_end = stream.index(b"\n", cursor)
        header = stream[cursor:header_end].decode("utf-8", "replace").split(" ")
        if len(header) != 3 or header[1] != "blob":
            pytest.skip(f"unexpected git cat-file record for {path}: {' '.join(header)}")
        size = int(header[2])
        start = header_end + 1
        blobs[path] = stream[start : start + size]
        cursor = start + size + 1  # skip the record's trailing newline
    return blobs


def test_unmodified_byte_addressed_artifacts_match_their_committed_bytes() -> None:
    modified = {
        entry[3:]
        for entry in _git("status", "--porcelain", "-z", "--", *BYTE_ADDRESSED_PREFIXES).split("\0")
        if entry
    }
    paths = [path for path in _tracked_byte_addressed_files() if path not in modified]
    committed = _committed_bytes(paths)
    corrupted = [path for path in paths if (ROOT / path).read_bytes() != committed[path]]
    assert not corrupted, (
        "the working tree silently differs from the committed bytes, so Package digests no longer "
        f"describe the files on disk: {corrupted}"
    )
