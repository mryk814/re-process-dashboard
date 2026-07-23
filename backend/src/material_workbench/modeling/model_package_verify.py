from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from material_workbench.modeling.model_lifecycle import validate_lifecycle_metadata, validate_training_provenance
from material_workbench.contracts.model_example_contracts import ExampleQualityReport, ExampleSmokeExpected, ExampleSmokeInput, SparseSelectionReport
from material_workbench.modeling.model_packages import MissingOptionalDependency, ModelPackageLoader, PackageContractError, validate_predictive_summary
from material_workbench.tasks.task_registry import load_task_contracts
from material_workbench.task_modules import resolve_task_source, task_module


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


@dataclass(frozen=True)
class ExamplePackageVerificationReport:
    package_root: str
    package_id: str
    package_version: str
    predictor_id: str
    runtime_type: str
    manifest_sha256: str
    quality_metrics: dict[str, float]

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


def verify_model_package_example(package_root: str | Path) -> ExamplePackageVerificationReport:
    """Verify an inactive example through the same loader and adapter used in production."""

    package = ModelPackageLoader().load(package_root)
    smoke = package.manifest.smoke_test
    if smoke is None:
        raise ModelPackageVerificationError("example package requires a smoke_test")
    if package.manifest.quality_report is None:
        raise ModelPackageVerificationError("example package requires a quality_report")
    try:
        smoke_input = ExampleSmokeInput.model_validate_json(package.artifact_path(smoke.input).read_text(encoding="utf-8"))
        expected = ExampleSmokeExpected.model_validate_json(package.artifact_path(smoke.expected).read_text(encoding="utf-8"))
        quality = ExampleQualityReport.model_validate_json(
            package.artifact_path(package.manifest.quality_report).read_text(encoding="utf-8")
        )
        if any(item.path == "reports/selection-report.json" for item in package.manifest.artifacts):
            SparseSelectionReport.model_validate_json(
                package.artifact_path("reports/selection-report.json").read_text(encoding="utf-8")
            )
    except (OSError, ValueError) as exc:
        raise ModelPackageVerificationError(f"invalid example verification artifact: {exc}") from exc
    spec = next((item for item in package.manifest.predictors if item.id == smoke_input.predictor_id), None)
    if spec is None:
        raise ModelPackageVerificationError(f"unknown smoke predictor: {smoke_input.predictor_id}")
    actual = package.load_predictor(spec.id).predict(smoke_input.features, seed=smoke_input.seed)
    validate_predictive_summary(actual, spec, expected.capability)
    if actual != expected.summary:
        raise ModelPackageVerificationError("example smoke output differs from expected summary")
    return ExamplePackageVerificationReport(
        package_root=str(package.root),
        package_id=package.manifest.package_id,
        package_version=package.manifest.package_version,
        predictor_id=spec.id,
        runtime_type=spec.runtime_type,
        manifest_sha256=package.manifest_sha256,
        quality_metrics=quality.metrics,
    )


def verify_model_package(
    package_root: str | Path,
    *,
    task_id: str,
    source: str | Path | None = None,
) -> ModelPackageVerificationReport:
    contracts = load_task_contracts()
    if task_id not in contracts:
        raise ModelPackageVerificationError(f"unknown task: {task_id}")
    package = ModelPackageLoader().load(package_root)
    if package.manifest.task_id != task_id:
        raise ModelPackageVerificationError(
            f"task_id mismatch: expected {task_id}, package declares {package.manifest.task_id}"
        )
    module = task_module(task_id)
    data = module.data_loader(resolve_task_source(task_id, source))
    quality = validate_lifecycle_metadata(package, contracts[task_id], profile_path=Path(data.profile_path))
    validate_training_provenance(package, data, contracts[task_id])

    runtime = module.runtime_factory(data, package.root)
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
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--task", dest="task_id")
    mode.add_argument("--example", action="store_true")
    parser.add_argument("--source", type=Path, default=Path("data/source/material_workbench_tutorial_v1.xlsx"))
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = (
            verify_model_package_example(args.package_root)
            if args.example
            else verify_model_package(args.package_root, task_id=args.task_id, source=args.source)
        )
    except (MissingOptionalDependency, PackageContractError, OSError, ValueError) as exc:
        if args.json_output:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"Model Package verification: FAIL\n{exc}", file=sys.stderr)
        return 1
    if args.json_output:
        print(json.dumps({"ok": True, "report": report.model_dump()}, ensure_ascii=False, indent=2))
    elif isinstance(report, ExamplePackageVerificationReport):
        print("\n".join([
            "Model Package example verification: PASS",
            f"Package: {report.package_id} {report.package_version}",
            f"Predictor: {report.predictor_id} ({report.runtime_type})",
            f"Manifest SHA-256: {report.manifest_sha256}",
            "Smoke: reproduced",
        ]))
    else:
        print("\n".join([
            "Model Package verification: PASS",
            f"Package: {report.package_id} {report.package_version}",
            f"Task: {report.task_id}",
            f"Feature Pipeline: {report.feature_pipeline_id} {report.feature_pipeline_version}",
            f"Manifest SHA-256: {report.manifest_sha256}",
            f"Artifacts: {report.artifact_count}",
            "Smoke: reproduced",
        ]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
