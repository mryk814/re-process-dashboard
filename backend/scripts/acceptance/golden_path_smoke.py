from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[3]
BACKEND_SRC = ROOT / "backend" / "src"
OPERATIONS = ROOT / "backend" / "scripts" / "operations"
GENERATORS = ROOT / "backend" / "scripts" / "generators"
for entry in (BACKEND_SRC, OPERATIONS, GENERATORS):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from model_workflow import (  # noqa: E402
    build_package,
    diagnose_source,
    promote_package,
)
from material_workbench.contracts.candidate_project_contracts import Candidate
from material_workbench.modeling.model_package_verification import ModelPackageLoader  # noqa: E402
from material_workbench.task_composition.catalog import task_module  # noqa: E402
from material_workbench.tasks.task_registry import load_task_contracts  # noqa: E402


TASK_ID = "heat-treatment-tradeoff-v1"
SOURCE = ROOT / "data" / "source" / "external" / "heat_treatment_tradeoff_samples.csv"
PACKAGE_ID = "golden-path-smoke-heat-treatment"
PACKAGE_VERSION = "0.0.0-smoke"


def main() -> int:
    diagnostic = diagnose_source(SOURCE, task_id=TASK_ID, profile=None)
    if diagnostic["route"] != "existing_task_replacement":
        raise RuntimeError(f"golden-path diagnosis branched unexpectedly: {diagnostic}")

    with TemporaryDirectory(prefix="material-workbench-golden-path-") as raw:
        temporary = Path(raw)
        candidate_root = temporary / "candidate" / PACKAGE_ID
        dataset_output = temporary / "canonical-data.json"
        built = build_package(
            TASK_ID,
            SOURCE,
            candidate_root,
            dataset_output,
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
            replace=False,
        )

        models_root = temporary / "models"
        promoted = promote_package(
            TASK_ID,
            candidate_root,
            SOURCE,
            models_root,
        )

        module = task_module(TASK_ID)
        data = module.data_loader(SOURCE)
        package = ModelPackageLoader().load(Path(promoted["trusted_package"]))
        runtime = module.runtime_factory(data, package)
        raw_candidate = load_task_contracts()[TASK_ID].canonical_candidate
        now = datetime.now(UTC)
        candidate = Candidate(
            id="golden-path-smoke",
            project_id="golden-path-smoke",
            revision=1,
            created_at=now,
            updated_at=now,
            name="golden path smoke",
            inputs={
                "composition": dict(raw_candidate.composition),
                "process": dict(raw_candidate.process),
                "categorical": dict(raw_candidate.categorical),
                "heat_pattern": raw_candidate.heat_pattern,
            },
            provenance=raw_candidate.provenance,
        )
        prediction = runtime.predict(candidate)
        if not prediction.get("predictions"):
            raise RuntimeError("promoted package produced no application prediction")

        print(json.dumps({
            "ok": True,
            "route": diagnostic["route"],
            "package_id": package.manifest.package_id,
            "package_version": package.manifest.package_version,
            "canonical_rows": built["dataset"]["rows"],
            "promoted": promoted["promoted"],
            "store": promoted["store"],
            "prediction_targets": sorted(prediction["predictions"]),
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
