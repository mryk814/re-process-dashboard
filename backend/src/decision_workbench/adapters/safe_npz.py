"""Bounded, data-only loading for NumPy ``.npz`` model artifacts."""
from __future__ import annotations

from pathlib import Path
from zipfile import BadZipFile, ZipFile

import numpy as np

from decision_workbench.modeling.packages.contracts import PackageContractError


MAX_NPZ_ENTRIES = 64
MAX_NPZ_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_NPZ_COMPRESSION_RATIO = 100
MAX_TENSOR_ELEMENTS = 4_000_000


def safe_npz_arrays(
    path: Path,
    *,
    max_entries: int = MAX_NPZ_ENTRIES,
    max_uncompressed_bytes: int = MAX_NPZ_UNCOMPRESSED_BYTES,
    max_tensor_elements: int = MAX_TENSOR_ELEMENTS,
) -> dict[str, np.ndarray]:
    """Load finite numeric arrays after rejecting archive bombs and odd entries."""

    try:
        with ZipFile(path) as archive:
            infos = archive.infolist()
            if not 1 <= len(infos) <= max_entries:
                raise PackageContractError("model npz has too many entries")
            if sum(info.file_size for info in infos) > max_uncompressed_bytes:
                raise PackageContractError("model npz expands beyond the allowed size")
            for info in infos:
                if (
                    info.is_dir()
                    or not info.filename.endswith(".npy")
                    or "/" in info.filename
                    or "\\" in info.filename
                ):
                    raise PackageContractError("model npz has an invalid entry name")
                if info.file_size and (
                    not info.compress_size
                    or info.file_size / info.compress_size > MAX_NPZ_COMPRESSION_RATIO
                ):
                    raise PackageContractError("model npz compression ratio is too high")
        with np.load(path, allow_pickle=False) as archive:
            arrays = {name: np.asarray(archive[name]) for name in archive.files}
    except (BadZipFile, OSError, ValueError) as exc:
        if isinstance(exc, PackageContractError):
            raise
        raise PackageContractError("model artifact is not a safe npz archive") from exc

    total_elements = sum(array.size for array in arrays.values())
    if total_elements > max_tensor_elements or any(array.size > max_tensor_elements for array in arrays.values()):
        raise PackageContractError("model tensors exceed the allowed element count")
    if any(array.dtype.kind not in "biuf" for array in arrays.values()):
        raise PackageContractError("model tensors must be numeric")
    if any(not np.isfinite(np.asarray(array, dtype=float)).all() for array in arrays.values()):
        raise PackageContractError("model tensors must be finite")
    return arrays
