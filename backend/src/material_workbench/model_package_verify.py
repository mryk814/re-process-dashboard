from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from .importer import load_workbook_data
from .model_lifecycle import validate_lifecycle_metadata, validate_training_provenance
from .model_packages import MissingOptionalDependency, ModelPackageLoader, PackageContractError
from .task_registry import load_task_contracts


@dataclass(frozen=True)
class ModelPackageVerificationReport:
    package_root: str
    package_id: str
    package_version: str
    task_id: str
    manifest_sha256: str
    feature_pipeline_id: str
    feature_pipeline_version: str
    artifact_count: int
    quality_report: dict[str, Any]

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


class ModelPackageVerificationError(PackageContractError):
    """A package is loadable but is not safe to select as active."""


def verify_model_package(
    package_root: str | Path,
    *,
    task_id: str,
    source: str | Path = "data/source/process_dashboard_realistic_excel_v2.xlsx",
) -> ModelPackageVerificationReport:
    contracts = load_task_contracts()
    if task_id not in contracts:
        raise ModelPackageVerificationError(f"unknown task: {task_id}")
    package = ModelPackageLoader().load(package_root)
    if package.manifest.task_id != task_id:
        raise ModelPackageVerificationError(
            f"task_id mismatch: expected {task_id}, package declares {package.manifest.task_id}"
        )
    quality = validate_lifecycle_metadata(package, contracts[task_id])
    data = load_workbook_data(source)
    validate_training_provenance(package, data, contracts[task_id])

    if task_id == "annealed-properties-v1":
        from .runtime import ModelRuntime

        runtime = ModelRuntime(data, package_root=package.root)
    elif task_id == "hot-rolled-properties-v1":
        from .hot_rolling import HotRollingRuntime

        runtime = HotRollingRuntime(data, package_root=package.root)
    else:
        raise ModelPackageVerificationError(f"no task runtime verifier is registered for {task_id}")
    if runtime.output_keys != frozenset(output.key for output in contracts[task_id].task_definition.outputs):
        raise ModelPackageVerificationError("runtime outputs do not match TaskDefinition")

    manifest = package.manifest
    return ModelPackageVerificationReport(
        package_root=str(package.root),
        package_id=manifest.package_id,
        package_version=manifest.package_version,
        task_id=manifest.task_id,
        manifest_sha256=package.manifest_sha256,
        feature_pipeline_id=manifest.feature_pipeline.id,
        feature_pipeline_version=manifest.feature_pipeline.version,
        artifact_count=len(manifest.artifacts),
        quality_report=quality.model_dump(mode="json"),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify a Model Package through its production task runtime.")
    parser.add_argument("package_root", type=Path)
    parser.add_argument("--task", required=True, dest="task_id")
    parser.add_argument("--source", type=Path, default=Path("data/source/process_dashboard_realistic_excel_v2.xlsx"))
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = verify_model_package(args.package_root, task_id=args.task_id, source=args.source)
    except (MissingOptionalDependency, PackageContractError, OSError, ValueError) as exc:
        if args.json_output:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"Model Package verification: FAIL\n{exc}", file=sys.stderr)
        return 1
    if args.json_output:
        print(json.dumps({"ok": True, "report": report.model_dump()}, ensure_ascii=False, indent=2))
    else:
        print("\n".join([
            "Model Package verification: PASS",
            f"Package: {report.package_id} {report.package_version}",
            f"Task: {report.task_id}",
            f"Feature Pipeline: {report.feature_pipeline_id} {report.feature_pipeline_version}",
            f"Manifest SHA-256: {report.manifest_sha256}",
            f"Artifacts: {report.artifact_count}",
            "Production smoke: reproduced",
        ]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
