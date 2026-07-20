from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import sys
from typing import Any, Sequence

from .model_packages import MissingOptionalDependency, ModelPackageLoader, PackageContractError, VerifiedModelPackage


@dataclass(frozen=True)
class PredictorVerification:
    predictor_id: str
    target: str
    runtime_type: str
    architecture_id: str | None
    predictive_family: str
    feature_count: int


@dataclass(frozen=True)
class ModelPackageVerificationReport:
    package_root: str
    package_id: str
    package_version: str
    task_id: str
    input_schema_version: str
    manifest_sha256: str
    feature_pipeline_id: str
    feature_pipeline_version: str
    feature_count: int
    artifact_count: int
    smoke_test_present: bool
    predictors: tuple[PredictorVerification, ...]

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


class ModelPackageVerificationError(PackageContractError):
    """The package is loadable but not ready to be selected as an active package."""


def _json_artifact(package: VerifiedModelPackage, relative_path: str, label: str) -> Any:
    path = package.artifact_path(relative_path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelPackageVerificationError(f"{label} is not valid UTF-8 JSON: {relative_path}") from exc


def _feature_names_from_pipeline_spec(payload: Any) -> tuple[str, ...] | None:
    if not isinstance(payload, dict):
        raise ModelPackageVerificationError("feature pipeline spec must be a JSON object")
    raw_features = payload.get("features")
    if raw_features is None:
        return None
    if not isinstance(raw_features, list):
        raise ModelPackageVerificationError("feature pipeline spec features must be an array")
    names: list[str] = []
    for index, item in enumerate(raw_features):
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"]:
            raise ModelPackageVerificationError(f"feature pipeline spec features[{index}] must declare a non-empty name")
        names.append(item["name"])
    if len(names) != len(set(names)):
        raise ModelPackageVerificationError("feature pipeline spec feature names must be unique")
    return tuple(names)


def _verify_smoke_assets(package: VerifiedModelPackage, *, require_smoke: bool) -> bool:
    smoke = package.manifest.smoke_test
    if smoke is None:
        if require_smoke:
            raise ModelPackageVerificationError("active model package must declare a smoke test")
        return False
    if set(smoke) != {"input", "expected"}:
        raise ModelPackageVerificationError("smoke_test must contain exactly input and expected paths")
    input_path, expected_path = smoke["input"], smoke["expected"]
    if not isinstance(input_path, str) or not input_path or not isinstance(expected_path, str) or not expected_path:
        raise ModelPackageVerificationError("smoke_test input and expected must be non-empty package-relative paths")
    smoke_input = _json_artifact(package, input_path, "smoke input")
    expected = _json_artifact(package, expected_path, "smoke expected")
    if not isinstance(smoke_input, dict):
        raise ModelPackageVerificationError("smoke input must be a JSON object")
    if not isinstance(expected, dict):
        raise ModelPackageVerificationError("smoke expected must be a JSON object")
    expected_targets = {predictor.target for predictor in package.manifest.predictors}
    if set(expected) != expected_targets:
        missing = sorted(expected_targets - set(expected))
        extra = sorted(set(expected) - expected_targets)
        detail = []
        if missing:
            detail.append(f"missing targets: {', '.join(missing)}")
        if extra:
            detail.append(f"unexpected targets: {', '.join(extra)}")
        raise ModelPackageVerificationError(f"smoke expected targets do not match predictors ({'; '.join(detail)})")
    for target, value in expected.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ModelPackageVerificationError(f"smoke expected value for {target} must be a finite number")
    return True


def verify_model_package(
    package_root: str | Path,
    *,
    expected_task_id: str | None = None,
    expected_input_schema_version: str | None = None,
    require_smoke: bool = True,
    loader: ModelPackageLoader | None = None,
) -> ModelPackageVerificationReport:
    package = (loader or ModelPackageLoader()).load(package_root)
    manifest = package.manifest
    if expected_task_id is not None and manifest.task_id != expected_task_id:
        raise ModelPackageVerificationError(
            f"task_id mismatch: expected {expected_task_id}, package declares {manifest.task_id}",
        )
    if expected_input_schema_version is not None and manifest.input_schema_version != expected_input_schema_version:
        raise ModelPackageVerificationError(
            "input_schema_version mismatch: "
            f"expected {expected_input_schema_version}, package declares {manifest.input_schema_version}",
        )

    feature_names = tuple(manifest.feature_pipeline.output_features)
    if not feature_names:
        raise ModelPackageVerificationError("feature pipeline must declare output_features")
    if len(feature_names) != len(set(feature_names)):
        raise ModelPackageVerificationError("feature pipeline output_features must be unique")

    pipeline_payload = _json_artifact(package, manifest.feature_pipeline.spec, "feature pipeline spec")
    pipeline_names = _feature_names_from_pipeline_spec(pipeline_payload)
    if pipeline_names is not None and pipeline_names != feature_names:
        raise ModelPackageVerificationError("feature pipeline spec feature names/order do not match manifest output_features")

    targets = [predictor.target for predictor in manifest.predictors]
    if len(targets) != len(set(targets)):
        raise ModelPackageVerificationError("predictor targets must be unique")

    verified_predictors: list[PredictorVerification] = []
    for predictor in manifest.predictors:
        if tuple(predictor.feature_names) != feature_names:
            raise ModelPackageVerificationError(
                f"predictor {predictor.id} feature names/order do not match feature pipeline output_features",
            )
        try:
            package.load_predictor(predictor.id)
        except MissingOptionalDependency as exc:
            raise ModelPackageVerificationError(
                f"predictor {predictor.id} runtime dependency is unavailable: {exc}",
            ) from exc
        verified_predictors.append(
            PredictorVerification(
                predictor_id=predictor.id,
                target=predictor.target,
                runtime_type=predictor.runtime_type,
                architecture_id=predictor.architecture_id,
                predictive_family=predictor.predictive_family,
                feature_count=len(predictor.feature_names),
            ),
        )

    smoke_present = _verify_smoke_assets(package, require_smoke=require_smoke)
    return ModelPackageVerificationReport(
        package_root=str(package.root),
        package_id=manifest.package_id,
        package_version=manifest.package_version,
        task_id=manifest.task_id,
        input_schema_version=manifest.input_schema_version,
        manifest_sha256=package.manifest_sha256,
        feature_pipeline_id=manifest.feature_pipeline.id,
        feature_pipeline_version=manifest.feature_pipeline.version,
        feature_count=len(feature_names),
        artifact_count=len(manifest.artifacts),
        smoke_test_present=smoke_present,
        predictors=tuple(verified_predictors),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify a data-only Model Package before selecting it as an active runtime package.",
    )
    parser.add_argument("package_root", type=Path)
    parser.add_argument("--expect-task-id")
    parser.add_argument("--expect-input-schema-version")
    parser.add_argument("--allow-missing-smoke", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def _human_report(report: ModelPackageVerificationReport) -> str:
    predictor_lines = "\n".join(
        f"  - {item.predictor_id}: {item.target} / {item.runtime_type} / {item.predictive_family} / {item.feature_count} features"
        for item in report.predictors
    )
    return "\n".join(
        [
            "Model Package verification: PASS",
            f"Package: {report.package_id} {report.package_version}",
            f"Task: {report.task_id} ({report.input_schema_version})",
            f"Feature Pipeline: {report.feature_pipeline_id} {report.feature_pipeline_version} ({report.feature_count} features)",
            f"Manifest SHA-256: {report.manifest_sha256}",
            f"Artifacts: {report.artifact_count}",
            f"Smoke assets: {'present' if report.smoke_test_present else 'not required'}",
            "Predictors:",
            predictor_lines or "  - none",
        ],
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = verify_model_package(
            args.package_root,
            expected_task_id=args.expect_task_id,
            expected_input_schema_version=args.expect_input_schema_version,
            require_smoke=not args.allow_missing_smoke,
        )
    except (PackageContractError, OSError) as exc:
        if args.json_output:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"Model Package verification: FAIL\n{exc}", file=sys.stderr)
        return 1
    if args.json_output:
        print(json.dumps({"ok": True, "report": report.model_dump()}, ensure_ascii=False, indent=2))
    else:
        print(_human_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
