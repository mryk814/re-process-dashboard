from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import sys
from typing import Any


BACKEND_SRC = Path(__file__).resolve().parents[2] / "src"
GENERATOR_SCRIPTS = Path(__file__).resolve().parents[1] / "generators"
for entry in (BACKEND_SRC, GENERATOR_SCRIPTS):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from decision_workbench.modeling.active_package_history import (  # noqa: E402
    check_active_package_history,
    classify_rollback_target,
    current_task_input_contract_digests,
    rollback_target_note,
    rollback_target_reason,
)
from decision_workbench.modeling.model_lifecycle import (  # noqa: E402
    ACTIVE_PACKAGES_PATH,
    canonical_training_dataset,
    canonical_training_dataset_digest,
    dataset_profile_digest,
    ensure_available_packages_config,
    load_active_packages,
    personal_model_store_path,
    register_available_package,
    resolve_configured_package,
    rollback_active_package,
    set_active_package,
    staged_package_destination,
    validate_active_package_task_set,
    validate_lifecycle_metadata,
)
from decision_workbench.modeling.model_package_verify import verify_model_package  # noqa: E402
from decision_workbench.modeling.packages.contracts import (  # noqa: E402
    MissingOptionalDependency,
    PackageContractError,
)
from decision_workbench.modeling.packages.loader import ModelPackageLoader  # noqa: E402
from decision_workbench.modeling.training.package_assembler import build_standard_model_package  # noqa: E402
from decision_workbench.modeling.training.recipe import ESTIMATOR_IDS, estimator_recipe  # noqa: E402
from decision_workbench.data.profile_family_registry import (  # noqa: E402
    lifecycle_profile_for_data,
    load_profile_document,
    profile_task_ids,
)
from decision_workbench.tasks.task_registry import load_task_contracts  # noqa: E402
from decision_workbench.task_composition.builtin.sources import PRIMARY_DEFAULT_SOURCE  # noqa: E402
from decision_workbench.task_composition.catalog import registered_task_modules, resolve_task_source, task_module  # noqa: E402
from decision_workbench.developer_experience.task_scaffolding import (  # noqa: E402
    link_promoted_package,
    validate_personal_task_store_path,
)


TASKS = tuple(registered_task_modules())
DEFAULT_SOURCE = PRIMARY_DEFAULT_SOURCE


def _task_source(task_id: str, source: Path) -> Path:
    return resolve_task_source(task_id, source)


def _selected_profile(task_id: str, profile: Path | None) -> Any | None:
    if profile is None:
        return None
    resolved = profile.resolve(strict=True)
    loaded = load_profile_document(resolved)
    declared_tasks = profile_task_ids(loaded)
    if task_id not in declared_tasks:
        raise ValueError(
            f"Profile does not declare task {task_id}: {resolved}"
        )
    return loaded


def _load_task_data(task_id: str, source: Path, profile: Path | None = None):
    return task_module(task_id).data_loader(
        resolve_task_source(task_id, source),
        _selected_profile(task_id, profile),
    )


def _write_json(path: Path, payload: Any, *, replace: bool) -> None:
    if path.exists() and not replace:
        try:
            if json.loads(path.read_text(encoding="utf-8")) == payload:
                return
        except (OSError, ValueError):
            pass
        raise FileExistsError(f"refusing to replace different existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def export_dataset(
    task_id: str,
    source: Path,
    output: Path,
    *,
    profile: Path | None = None,
    replace: bool,
) -> dict[str, Any]:
    source = _task_source(task_id, source)
    data = _load_task_data(task_id, source, profile)
    payload = canonical_training_dataset(task_id, data, load_task_contracts()[task_id])
    _write_json(output, payload, replace=replace)
    return {
        "path": str(output.resolve()),
        "rows": len(payload["rows"]),
        "feature_dataset_id": canonical_training_dataset_digest(payload),
        "dataset_profile_id": payload["dataset_profile_digest"],
    }


def diagnose_source(
    source: Path,
    *,
    task_id: str | None,
    profile: Path | None,
) -> dict[str, Any]:
    source = source.resolve(strict=True)
    profile_tasks: tuple[str, ...] = ()
    if profile is not None:
        profile = profile.resolve(strict=True)
        profile_tasks = profile_task_ids(load_profile_document(profile))

    if task_id is None:
        matching = [item for item in profile_tasks if item in TASKS]
        if len(matching) != 1:
            return {
                "route": "new_task_or_profile",
                "source": str(source),
                "profile": str(profile) if profile else None,
                "profile_task_ids": list(profile_tasks),
                "reason": "既存Taskを一意に特定できません。",
                "next": "docs/operations/dataset-input-profile.md#新しいデータフローを追加する場合",
            }
        task_id = matching[0]

    common = {
        "task_id": task_id,
        "source": str(source),
        "profile": str(profile) if profile else None,
        "profile_task_ids": list(profile_tasks),
    }
    if task_id not in TASKS:
        return {
            "route": "new_task_or_profile",
            **common,
            "reason": "登録済みTaskではありません。",
            "next": "docs/operations/dataset-input-profile.md#新しいデータフローを追加する場合",
        }
    if profile_tasks and task_id not in profile_tasks:
        return {
            "route": "new_task_or_profile",
            **common,
            "reason": "選択したProfileは指定Taskを宣言していません。",
            "next": "docs/operations/dataset-input-profile.md#新しいデータフローを追加する場合",
        }

    try:
        data = _load_task_data(task_id, source, profile)
    except (OSError, ValueError) as exc:
        return {
            "route": "new_task_or_profile",
            **common,
            "reason": f"登録済みProfileで読み込めません: {exc}",
            "next": "docs/operations/dataset-input-profile.md#新しいデータフローを追加する場合",
        }

    active_profile_digest = dataset_profile_digest(
        lifecycle_profile_for_data(data)
    )
    return {
        "route": "existing_task_replacement",
        "task_id": task_id,
        "source": str(source),
        "source_sha256": data.source_sha256,
        "profile": str(profile.resolve()) if profile else data.profile_path,
        "profile_digest": active_profile_digest,
        "eligible_rows": sum(
            1
            for row in data.observations
            if row.get("eligible")
            and row.get("task_id", task_id) == task_id
        ),
        "next": (
            "npm run model:build -- --task "
            f"{task_id} --source \"{source}\" --package-id <new-id> "
            f"--package-version <new-version>"
            + (f' --profile "{profile}"' if profile else "")
        ),
    }


def build_package(
    task_id: str,
    source: Path,
    output: Path,
    dataset_output: Path,
    *,
    package_id: str,
    package_version: str,
    replace: bool,
    estimator: str | None = None,
    estimator_options: dict[str, Any] | None = None,
    profile: Path | None = None,
) -> dict[str, Any]:
    if output.exists() and not replace:
        raise FileExistsError(f"refusing to replace existing model package: {output}")
    source = _task_source(task_id, source)
    module = task_module(task_id)
    recipe = None
    authoring = module.standard_model_authoring
    selected_estimator = estimator
    selected_options = estimator_options
    if (
        selected_estimator is None
        and authoring is not None
        and authoring.default_estimator_id is not None
    ):
        selected_estimator = authoring.default_estimator_id
        selected_options = authoring.default_options()
    if selected_estimator is not None:
        if authoring is None or selected_estimator not in authoring.estimator_ids:
            supported = (
                ", ".join(authoring.estimator_ids)
                if authoring is not None
                else "none"
            )
            raise ValueError(
                f"{task_id} does not support standard estimator {selected_estimator}; "
                f"supported: {supported}"
            )
        recipe = estimator_recipe(selected_estimator, selected_options)
    dataset = export_dataset(
        task_id,
        source,
        dataset_output,
        profile=profile,
        replace=replace,
    )
    if recipe is None:
        if module.specialized_package_builder is None:
            raise ValueError(
                f"{task_id} has no specialized builder or default Training Recipe"
            )
        module.specialized_package_builder(
            source,
            output,
            replace=replace,
            package_id=package_id,
            package_version=package_version,
            profile_path=profile,
        )
    else:
        assert authoring is not None
        build_standard_model_package(
            task_id=task_id,
            source=source,
            data=_load_task_data(task_id, source, profile),
            contract=load_task_contracts()[task_id],
            candidate_builder=authoring.candidate_builder,
            recipe=recipe,
            destination=output,
            package_id=package_id,
            package_version=package_version,
            replace=replace,
            positive_targets=authoring.positive_targets,
        )
    report = verify_model_package(
        output,
        task_id=task_id,
        source=source,
        profile=profile,
    )
    manifest = ModelPackageLoader().load(output).manifest
    feature_payload = json.loads(dataset_output.read_text(encoding="utf-8"))
    dataset["feature_dataset_id"] = canonical_training_dataset_digest(
        feature_payload,
        algorithm=manifest.provenance.feature_dataset_digest_algorithm,
    )
    dataset["feature_dataset_digest_algorithm"] = (
        manifest.provenance.feature_dataset_digest_algorithm
    )
    return {"dataset": dataset, "package": report.model_dump()}


def compare_estimators(
    task_id: str,
    source: Path,
    output: Path,
    dataset_output: Path,
    *,
    estimators: tuple[str, ...],
    estimator_options: dict[str, dict[str, Any]] | None,
    package_prefix: str,
    package_version: str,
    profile: Path | None = None,
) -> dict[str, Any]:
    if len(estimators) < 2 or len(set(estimators)) != len(estimators):
        raise ValueError("comparison requires at least two unique estimators")
    authoring = task_module(task_id).standard_model_authoring
    unsupported = (
        set(estimators)
        - set(authoring.estimator_ids if authoring is not None else ())
    )
    if unsupported:
        raise ValueError(
            f"{task_id} does not support comparison estimators: "
            + ", ".join(sorted(unsupported))
        )
    output.mkdir(parents=True, exist_ok=True)
    options = estimator_options or {}
    unknown_options = set(options) - set(estimators)
    if unknown_options:
        raise ValueError(
            "estimator options reference unselected estimators: "
            + ", ".join(sorted(unknown_options))
        )
    if any(not isinstance(value, dict) for value in options.values()):
        raise ValueError("each comparison estimator option must be a JSON object")

    entries: list[dict[str, Any]] = []
    common_feature_dataset_id: str | None = None
    common_evaluation: dict[str, dict[str, str]] | None = None
    for estimator_id in estimators:
        package_id = f"{package_prefix}-{estimator_id.replace('.', '-')}"
        package_path = output / package_id
        result = build_package(
            task_id,
            source,
            package_path,
            dataset_output,
            package_id=package_id,
            package_version=package_version,
            replace=False,
            estimator=estimator_id,
            estimator_options=options.get(estimator_id, {}),
            profile=profile,
        )
        feature_dataset_id = str(result["dataset"]["feature_dataset_id"])
        stats = json.loads(
            (package_path / "reference" / "training_stats.json").read_text(
                encoding="utf-8"
            )
        )
        evaluation = {
            target: {
                "cohort_digest": str(stats["cohort_digests"][target]),
                "fold_digest": str(stats["fold_digests"][target]),
            }
            for target in sorted(stats["cohort_digests"])
        }
        if common_feature_dataset_id is None:
            common_feature_dataset_id = feature_dataset_id
            common_evaluation = evaluation
        elif (
            feature_dataset_id != common_feature_dataset_id
            or evaluation != common_evaluation
        ):
            raise ValueError(
                "comparison estimators did not use the same "
                "FeatureDataset/cohort/fold plan"
            )
        entries.append({
            "estimator_id": estimator_id,
            "package_id": package_id,
            "package": str(package_path.resolve()),
            "quality_report": result["package"]["quality_report"],
            "evaluation": evaluation,
        })

    report = {
        "schema_version": "standard-model-comparison/v1",
        "task_id": task_id,
        "feature_dataset_id": common_feature_dataset_id,
        "evaluation": common_evaluation,
        "models": entries,
        "selection": None,
        "note": (
            "No automatic winner is selected. Compare target-level quality "
            "and scientific suitability before promotion."
        ),
    }
    _write_json(output / "comparison.json", report, replace=False)
    return report


def promote_package(
    task_id: str,
    package: Path,
    source: Path,
    store: Path,
    *,
    profile: Path | None = None,
) -> dict[str, Any]:
    # Validate every personal destination before copying any trusted artifact;
    # a rejected Task store must not leave a partially promoted model behind.
    validate_personal_task_store_path()
    package = package.resolve(strict=True)
    source = _task_source(task_id, source)
    verify_model_package(
        package,
        task_id=task_id,
        source=source,
        profile=profile,
    )
    loaded = ModelPackageLoader().load(package)
    package_id = loaded.manifest.package_id
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,127}", package_id):
        raise PackageContractError(
            "package_id must be a filesystem-safe immutable identifier"
        )
    available_config = ensure_available_packages_config(store)
    trusted_root = available_config.parent / "packages"
    destination = trusted_root / package_id
    promoted = False
    if destination.exists():
        existing = ModelPackageLoader().load(destination)
        if existing.manifest_sha256 != loaded.manifest_sha256:
            raise FileExistsError(
                f"trusted package ID already exists with different content: {destination}"
            )
    else:
        with staged_package_destination(destination, replace=False) as staging:
            shutil.copytree(package, staging)
        promoted = True
    trusted_report = verify_model_package(
        destination,
        task_id=task_id,
        source=source,
        profile=profile,
    )
    available = register_available_package(
        destination,
        config_path=available_config,
    )
    link_promoted_package(task_id, destination)
    return {
        "task_id": task_id,
        "promoted": promoted,
        "store": str(available_config.parent),
        "trusted_package": str(destination),
        "package": trusted_report.model_dump(),
        "restart_required": False,
        "available_package": destination.relative_to(
            available_config.parent
        ).as_posix(),
        "available_package_count": len(available.packages),
        "next": "起動中のアプリで「個人Taskとモデルを再読込」を実行してください。",
    }


def activate_package(
    task_id: str,
    package: Path,
    source: Path,
    config: Path,
) -> dict[str, Any]:
    report = verify_model_package(
        package,
        task_id=task_id,
        source=_task_source(task_id, source),
    )
    updated = set_active_package(task_id, package, config_path=config)
    selection = updated.tasks[task_id]
    return {
        "task_id": task_id,
        "active": selection.active,
        "previous": selection.previous,
        "package": report.model_dump(),
        "restart_required": True,
    }


def rollback_package(task_id: str, source: Path, config: Path) -> dict[str, Any]:
    before = load_active_packages(config)
    selection = before.tasks[task_id]
    if selection.previous is None:
        note = rollback_target_note(task_id, check_active_package_history(config_path=config))
        raise PackageContractError(
            f"no previous active package is recorded for task {task_id}"
            + (
                f" — {note}"
                if note
                else "; activeの切替は npm run model:activate を通す（JSONを直接編集するとpreviousが記録されない）"
            )
        )
    models_root = config.resolve().parent
    status = classify_rollback_target(
        task_id,
        selection.previous,
        models_root=models_root,
        contract_digests=current_task_input_contract_digests(),
    )
    if status != "available":
        raise PackageContractError(
            f"previous active package {selection.previous} cannot be activated for task {task_id}"
            f" — {rollback_target_reason(status)}"
        )
    previous = (models_root / selection.previous).resolve(strict=True)
    report = verify_model_package(previous, task_id=task_id, source=_task_source(task_id, source))
    updated = rollback_active_package(task_id, config_path=config)
    current = updated.tasks[task_id]
    return {
        "task_id": task_id,
        "active": current.active,
        "previous": current.previous,
        "package": report.model_dump(),
        "restart_required": True,
    }


def package_status(config: Path) -> dict[str, Any]:
    contracts = load_task_contracts()
    configured = load_active_packages(config)
    validate_active_package_task_set(configured, set(TASKS))
    tasks: dict[str, Any] = {}
    data_by_source: dict[str, Any] = {}
    for task_id, selection in configured.tasks.items():
        module = task_module(task_id)
        if task_id not in data_by_source:
            data_by_source[task_id] = module.data_loader(resolve_task_source(task_id))
        data = data_by_source[task_id]
        root = resolve_configured_package(task_id, config_path=config)
        package = ModelPackageLoader().load(root)
        validate_lifecycle_metadata(package, contracts[task_id], profile_path=Path(data.profile_path))
        tasks[task_id] = {
            "active": selection.active,
            "previous": selection.previous,
            "package_id": package.manifest.package_id,
            "package_version": package.manifest.package_version,
            "manifest_sha256": package.manifest_sha256,
        }
    return {
        "schema_version": configured.schema_version,
        "tasks": tasks,
        "history": check_active_package_history(config_path=config).model_dump(mode="json"),
    }


def estimator_inventory(task_id: str | None = None) -> dict[str, Any]:
    selected = (task_id,) if task_id else TASKS
    return {
        "schema_version": "standard-estimator-inventory/v1",
        "tasks": {
            item: list(
                task_module(item).standard_model_authoring.estimator_ids
                if task_module(item).standard_model_authoring is not None
                else ()
            )
            for item in selected
        },
        "note": (
            "Estimator IDs are allow-listed training recipes. "
            "Omitting --estimator uses the Task's default Training Recipe when "
            "one is declared; advanced Tasks keep their specialized workflow."
        ),
    }


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("estimator options must be a JSON object")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build, verify, activate, and roll back trusted Model Packages.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    data = subparsers.add_parser("data", help="Export the canonical training dataset used by a task pipeline.")
    data.add_argument("--task", required=True, choices=TASKS)
    data.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    data.add_argument("--profile", type=Path)
    data.add_argument("--output", type=Path, required=True)
    data.add_argument("--replace", action="store_true")

    diagnose = subparsers.add_parser(
        "diagnose",
        help="Decide whether a source can replace data for an existing Task.",
    )
    diagnose.add_argument("--task")
    diagnose.add_argument("--source", type=Path, required=True)
    diagnose.add_argument("--profile", type=Path)

    build = subparsers.add_parser("build", help="Export canonical data, train, package, and verify one task.")
    build.add_argument("--task", required=True, choices=TASKS)
    build.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    build.add_argument("--profile", type=Path)
    build.add_argument("--output", type=Path)
    build.add_argument("--dataset-output", type=Path)
    build.add_argument("--package-id", required=True)
    build.add_argument("--package-version", required=True)
    build.add_argument(
        "--estimator",
        choices=ESTIMATOR_IDS,
        help="Use an allow-listed standard estimator after the Task Feature Pipeline.",
    )
    build.add_argument(
        "--estimator-options",
        type=_json_object,
        default={},
        help='Optional bounded recipe parameters as JSON, e.g. \'{"restarts": 3}\'.',
    )
    build.add_argument("--replace", action="store_true")

    compare = subparsers.add_parser(
        "compare",
        help="Build explicit candidates on one FeatureDataset and fold plan without selecting a winner.",
    )
    compare.add_argument("--task", required=True, choices=TASKS)
    compare.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    compare.add_argument("--profile", type=Path)
    compare.add_argument("--output", type=Path, required=True)
    compare.add_argument("--dataset-output", type=Path, required=True)
    compare.add_argument(
        "--estimators",
        nargs="+",
        required=True,
        choices=ESTIMATOR_IDS,
    )
    compare.add_argument(
        "--estimator-options",
        type=_json_object,
        default={},
        help="JSON object keyed by selected estimator ID.",
    )
    compare.add_argument("--package-prefix", required=True)
    compare.add_argument("--package-version", required=True)

    estimators = subparsers.add_parser(
        "estimators",
        help="List standard estimators supported by each Task.",
    )
    estimators.add_argument("--task", choices=TASKS)

    verify = subparsers.add_parser("verify", help="Run loader, lifecycle, adapter, and production smoke checks.")
    verify.add_argument("--task", required=True, choices=TASKS)
    verify.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    verify.add_argument("--profile", type=Path)
    verify.add_argument("--package", type=Path, required=True)

    activate = subparsers.add_parser("activate", help="Verify then update the trusted active-package reference.")
    activate.add_argument("--task", required=True, choices=TASKS)
    activate.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    activate.add_argument("--package", type=Path, required=True)
    activate.add_argument("--config", type=Path, default=ACTIVE_PACKAGES_PATH)

    promote = subparsers.add_parser(
        "promote",
        help="Verify and immutably copy a candidate into trusted models.",
    )
    promote.add_argument("--task", required=True, choices=TASKS)
    promote.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    promote.add_argument("--profile", type=Path)
    promote.add_argument("--package", type=Path, required=True)
    promote.add_argument(
        "--store",
        type=Path,
        default=personal_model_store_path(),
    )

    rollback = subparsers.add_parser("rollback", help="Verify and restore the previously active package.")
    rollback.add_argument("--task", required=True, choices=TASKS)
    rollback.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    rollback.add_argument("--config", type=Path, default=ACTIVE_PACKAGES_PATH)

    status = subparsers.add_parser("status", help="Show trusted active package IDs, versions, and hashes.")
    status.add_argument("--config", type=Path, default=ACTIVE_PACKAGES_PATH)
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    arguments = _parser().parse_args()
    try:
        if arguments.command == "diagnose":
            result = diagnose_source(
                arguments.source,
                task_id=arguments.task,
                profile=arguments.profile,
            )
        elif arguments.command == "data":
            result = export_dataset(
                arguments.task,
                arguments.source,
                arguments.output,
                profile=arguments.profile,
                replace=arguments.replace,
            )
        elif arguments.command == "build":
            output = (
                arguments.output
                or Path("artifacts/model-package-candidates")
                / arguments.package_id
            )
            dataset_output = (
                arguments.dataset_output
                or Path("artifacts/model-data")
                / f"{arguments.package_id}.json"
            )
            result = build_package(
                arguments.task,
                arguments.source,
                output,
                dataset_output,
                package_id=arguments.package_id,
                package_version=arguments.package_version,
                replace=arguments.replace,
                estimator=arguments.estimator,
                estimator_options=arguments.estimator_options,
                profile=arguments.profile,
            )
        elif arguments.command == "estimators":
            result = estimator_inventory(arguments.task)
        elif arguments.command == "compare":
            result = compare_estimators(
                arguments.task,
                arguments.source,
                arguments.output,
                arguments.dataset_output,
                estimators=tuple(arguments.estimators),
                estimator_options=arguments.estimator_options,
                package_prefix=arguments.package_prefix,
                package_version=arguments.package_version,
                profile=arguments.profile,
            )
        elif arguments.command == "verify":
            result = verify_model_package(
                arguments.package,
                task_id=arguments.task,
                source=_task_source(arguments.task, arguments.source),
                profile=arguments.profile,
            ).model_dump()
        elif arguments.command == "promote":
            result = promote_package(
                arguments.task,
                arguments.package,
                arguments.source,
                arguments.store,
                profile=arguments.profile,
            )
        elif arguments.command == "activate":
            result = activate_package(
                arguments.task,
                arguments.package,
                arguments.source,
                arguments.config,
            )
        elif arguments.command == "rollback":
            result = rollback_package(arguments.task, arguments.source, arguments.config)
        else:
            result = package_status(arguments.config)
    except (MissingOptionalDependency, OSError, ValueError, PackageContractError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
