from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import sys
from typing import Annotated, Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

from material_workbench.adapters.builtin_deterministic_linear import ScientificTransformResult
from material_workbench.contracts.stage_a_contracts import STAGE_A_COMPONENTS, ScientificBlendInput
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


def _smoke_outputs_equivalent(actual: Any, expected: Any) -> bool:
    """Compare smoke evidence exactly except for platform-scale float noise."""

    if isinstance(actual, BaseModel):
        actual = actual.model_dump(mode="json")
    if isinstance(expected, BaseModel):
        expected = expected.model_dump(mode="json")
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _smoke_outputs_equivalent(actual[key], expected[key]) for key in actual
        )
    if isinstance(actual, (list, tuple)) and isinstance(expected, (list, tuple)):
        return len(actual) == len(expected) and all(
            _smoke_outputs_equivalent(left, right)
            for left, right in zip(actual, expected, strict=True)
        )
    if (
        isinstance(actual, (int, float))
        and not isinstance(actual, bool)
        and isinstance(expected, (int, float))
        and not isinstance(expected, bool)
    ):
        return math.isclose(float(actual), float(expected), rel_tol=1e-12, abs_tol=1e-12)
    return actual == expected


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


@dataclass(frozen=True)
class DeterministicTransformVerificationReport:
    package_root: str
    package_id: str
    package_version: str
    transform_id: str
    runtime_type: str
    manifest_sha256: str
    golden_rows: int

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


class _GoldenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StageAGoldenRow(_GoldenModel):
    blend_id: Annotated[str, Field(min_length=1)]
    blend: ScientificBlendInput
    expected: ScientificTransformResult


class StageAGoldenReference(_GoldenModel):
    schema_version: Literal["stage-a-golden/v1"]
    scientific_source_digest: Annotated[
        str,
        Field(pattern=r"^sha256:[0-9a-f]{64}$"),
    ]
    rows: tuple[StageAGoldenRow, ...]


def verify_deterministic_transform_package(
    package_root: str | Path,
) -> DeterministicTransformVerificationReport:
    """Verify an inactive deterministic package without treating it as a predictor."""

    package = ModelPackageLoader().load(package_root)
    if package.manifest.package_kind != "deterministic_transform":
        raise ModelPackageVerificationError("package is not a deterministic transform")
    if len(package.manifest.deterministic_transforms) != 1:
        raise ModelPackageVerificationError(
            "deterministic package smoke requires exactly one transform"
        )
    spec = package.manifest.deterministic_transforms[0]
    provenance = package.manifest.provenance
    if not (
        provenance.training_data_id
        == provenance.feature_dataset_id
        == spec.scientific_master_digest
    ):
        raise ModelPackageVerificationError(
            "deterministic Package provenance must match the scientific master digest"
        )
    if spec.output_names != STAGE_A_COMPONENTS:
        raise ModelPackageVerificationError(
            "deterministic Package outputs must match the canonical 31-axis Stage A contract"
        )
    smoke = package.manifest.smoke_test
    if smoke is None:
        raise ModelPackageVerificationError("deterministic package requires a smoke_test")
    golden_spec = package.manifest.deterministic_golden
    if golden_spec is None:
        raise ModelPackageVerificationError(
            "deterministic package requires a typed golden reference"
        )
    try:
        smoke_input = ScientificBlendInput.model_validate_json(
            package.artifact_path(smoke.input).read_text(encoding="utf-8")
        )
        expected = ScientificTransformResult.model_validate_json(
            package.artifact_path(smoke.expected).read_text(encoding="utf-8")
        )
        golden = StageAGoldenReference.model_validate_json(
            package.artifact_path(golden_spec.path).read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise ModelPackageVerificationError(
            f"invalid deterministic verification artifact: {exc}"
        ) from exc
    transform = package.load_transform(spec.id)
    transform_scientific = getattr(transform, "transform", None)
    if transform_scientific is None:
        raise ModelPackageVerificationError(
            "deterministic transform does not expose scientific execution"
        )
    actual = transform_scientific(smoke_input)
    if actual != expected:
        raise ModelPackageVerificationError(
            "deterministic transform smoke output differs from expected result"
        )
    if golden.schema_version != golden_spec.schema_version:
        raise ModelPackageVerificationError(
            "deterministic golden schema does not match the manifest contract"
        )
    if len(golden.rows) != golden_spec.expected_rows:
        raise ModelPackageVerificationError(
            "deterministic golden row count does not match the manifest contract"
        )
    blend_ids = [row.blend_id for row in golden.rows]
    if len(blend_ids) != len(set(blend_ids)):
        raise ModelPackageVerificationError(
            "deterministic golden blend ids must be unique"
        )
    if golden.scientific_source_digest != spec.scientific_master_digest:
        raise ModelPackageVerificationError(
            "deterministic golden scientific digest does not match the transform"
        )
    for row in golden.rows:
        if (
            row.blend.scientific_master.digest != spec.scientific_master_digest
            or row.expected.scientific_master.digest != spec.scientific_master_digest
        ):
            raise ModelPackageVerificationError(
                f"deterministic golden row {row.blend_id} uses a different scientific master"
            )
        if (
            len(row.expected.material_composition) != len(spec.output_names)
            or set(row.expected.material_composition) != set(spec.output_names)
        ):
            raise ModelPackageVerificationError(
                f"deterministic golden row {row.blend_id} does not match the fixed output axis"
            )
        if transform_scientific(row.blend) != row.expected:
            raise ModelPackageVerificationError(
                f"deterministic golden row {row.blend_id} was not reproduced"
            )
    return DeterministicTransformVerificationReport(
        package_root=str(package.root),
        package_id=package.manifest.package_id,
        package_version=package.manifest.package_version,
        transform_id=spec.id,
        runtime_type=spec.runtime_type,
        manifest_sha256=package.manifest_sha256,
        golden_rows=len(golden.rows),
    )


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
    if not _smoke_outputs_equivalent(actual, expected.summary):
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

    runtime = module.runtime_factory(data, package)
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
    mode.add_argument("--deterministic-transform", action="store_true")
    parser.add_argument("--source", type=Path, default=Path("data/source/material_workbench_tutorial_v2.xlsx"))
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.example:
            report = verify_model_package_example(args.package_root)
        elif args.deterministic_transform:
            report = verify_deterministic_transform_package(args.package_root)
        else:
            report = verify_model_package(
                args.package_root,
                task_id=args.task_id,
                source=args.source,
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
    elif isinstance(report, DeterministicTransformVerificationReport):
        print("\n".join([
            "Deterministic Model Package verification: PASS",
            f"Package: {report.package_id} {report.package_version}",
            f"Transform: {report.transform_id} ({report.runtime_type})",
            f"Manifest SHA-256: {report.manifest_sha256}",
            "Smoke: reproduced",
            f"Golden: {report.golden_rows} rows reproduced",
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
