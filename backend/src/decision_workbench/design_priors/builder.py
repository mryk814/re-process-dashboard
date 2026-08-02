"""Build a staged data-only Design Prior Package from canonical observations."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from tempfile import mkdtemp
from typing import Iterable

from decision_workbench.design_priors.contracts import (
    DESIGN_PRIOR_OBSERVATIONS_SCHEMA_VERSION,
    DESIGN_PRIOR_PACKAGE_SCHEMA_VERSION,
    DESIGN_PRIOR_QUALITY_SCHEMA_VERSION,
    DesignPriorArtifact,
    DesignPriorManifest,
    DesignPriorObservation,
    DesignPriorObservations,
    DesignPriorQualityReport,
    DesignPriorSource,
)
from decision_workbench.design_priors.loader import DesignPriorPackageLoader


def build_design_prior_package(
    destination: str | Path,
    *,
    package_id: str,
    package_version: str,
    task_id: str,
    task_contract_digest: str,
    canonical_input_schema_version: str,
    canonical_input_paths: tuple[str, ...],
    source: DesignPriorSource,
    observations: Iterable[DesignPriorObservation],
    training_code_revision: str,
    feature_recipe_digest: str | None = None,
) -> Path:
    """Stage, verify, then atomically place a P0 empirical/kNN package."""

    target = Path(destination)
    if target.exists():
        raise FileExistsError(f"Design Prior Package destination already exists: {target}")
    staging = Path(mkdtemp(prefix="design-prior-package-", dir=target.parent))
    try:
        observations_payload = DesignPriorObservations(
            schema_version=DESIGN_PRIOR_OBSERVATIONS_SCHEMA_VERSION,
            rows=tuple(observations),
        ).model_dump(mode="json")
        observation_path = staging / "observations.json"
        observation_path.write_text(
            json.dumps(
                observations_payload,
                allow_nan=False,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        numeric_columns = [
            path
            for path in canonical_input_paths
            if all(
                isinstance(row["inputs"][path], (int, float))
                and not isinstance(row["inputs"][path], bool)
                for row in observations_payload["rows"]
            )
        ]
        quality = DesignPriorQualityReport(
            schema_version=DESIGN_PRIOR_QUALITY_SCHEMA_VERSION,
            rows=len(observations_payload["rows"]),
            canonical_input_paths=canonical_input_paths,
            numeric_paths=tuple(numeric_columns),
            generator_comparison=("empirical_rows@1.0.0", "knn_local@1.0.0"),
            limitations=(
                "quality report does not certify hard feasibility",
                "no privacy guarantee",
            ),
        ).model_dump(mode="json")
        quality_path = staging / "quality-report.json"
        quality_path.write_text(
            json.dumps(
                quality,
                allow_nan=False,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        artifacts = tuple(
            DesignPriorArtifact(
                path=path.name,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                bytes=path.stat().st_size,
            )
            for path in (observation_path, quality_path)
        )
        manifest = DesignPriorManifest(
            schema_version=DESIGN_PRIOR_PACKAGE_SCHEMA_VERSION,
            package_id=package_id,
            package_version=package_version,
            task_id=task_id,
            task_contract_digest=task_contract_digest,
            canonical_input_schema_version=canonical_input_schema_version,
            canonical_input_paths=canonical_input_paths,
            feature_recipe_digest=feature_recipe_digest,
            source=source,
            generators=(
                {"generator_id": "empirical_rows", "observations_artifact": "observations.json"},
                {"generator_id": "knn_local", "observations_artifact": "observations.json", "max_neighbors": 8},
            ),
            artifacts=artifacts,
            training_code_revision=training_code_revision,
            quality_report="quality-report.json",
        )
        (staging / "manifest.json").write_text(
            json.dumps(
                manifest.model_dump(mode="json"),
                allow_nan=False,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        DesignPriorPackageLoader().load(staging)
        staging.replace(target)
        return target
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
