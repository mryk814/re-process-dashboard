from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from openpyxl import load_workbook

from material_workbench.data.dataset_profile import DatasetInputProfile, load_dataset_profile
from material_workbench.data.profile_workbench import validate_workbook_profile
from material_workbench.developer_experience.schemas import ProfileCandidate, SourceInspection


PROFILE_GLOB = "dataset-input-profile-*.json"
_UNIT_SUFFIX = re.compile(r"\s*\[([^\]]+)\]\s*$")


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _workbook_headers(source: Path) -> dict[str, set[str]]:
    workbook = load_workbook(source, read_only=True, data_only=True)
    try:
        return {
            sheet.title: {
                str(value).strip()
                for value in next(sheet.iter_rows(values_only=True), ())
                if value is not None and str(value).strip()
            }
            for sheet in workbook.worksheets
        }
    finally:
        workbook.close()


def _profile_columns(profile: DatasetInputProfile) -> dict[str, set[str]]:
    columns: dict[str, set[str]] = defaultdict(set)
    optional_roles = set(profile.shared.optional_roles)
    optional_technical = set(profile.shared.optional_technical_fields)

    def add(role: str, values: Iterable[str | None]) -> None:
        if role in optional_roles:
            return
        sheet = profile.sheet_for_role(role)
        columns[sheet].update(value for value in values if value)

    for entity in profile.shared.entities:
        add(entity.role, (entity.key,))
    for join in profile.shared.relation.joins:
        add(profile.shared.relation.role, join.source_columns)
    for policy in profile.shared.eligibility:
        add(policy.role, (policy.column,))
    for technical in profile.shared.technical:
        if technical.name in optional_technical or f"{technical.role}.{technical.name}" in optional_technical:
            continue
        add(technical.role, (technical.column,))
    for task in profile.tasks.values():
        for mapping in task.mappings:
            add(mapping.role, (mapping.column,))
            if mapping.series_columns:
                add(
                    mapping.role,
                    (
                        mapping.series_columns.parent,
                        mapping.series_columns.order,
                        mapping.series_columns.time,
                        mapping.series_columns.value,
                    ),
                )
            for source in mapping.observation_sources:
                add(source.role, (source.column,))
            # Measurement-point fallback is an alternative route, not a
            # requirement when a complete history series is already present.
        for observation in task.observations:
            required_metadata = (
                column
                for key, column in observation.metadata_columns.items()
                if key not in observation.optional_metadata_keys
            )
            required_measurements = (
                column
                for target in (*observation.targets, *observation.auxiliary)
                if target.key not in observation.optional_auxiliary_keys
                for column in target.source_columns
            )
            add(
                observation.role,
                (
                    observation.id_column,
                    observation.parent_column,
                    *required_metadata,
                    *required_measurements,
                ),
            )
    return dict(columns)


def _base_header(header: str) -> str:
    return _UNIT_SUFFIX.sub("", header).replace(" ", "").lower()


def inspect_source_against_profiles(
    source: Path,
    *,
    profile_path: Path | None = None,
    profile_directory: Path | None = None,
) -> SourceInspection:
    source = source.resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    headers = _workbook_headers(source)
    profile_root = profile_directory or Path(__file__).resolve().parents[1] / "data"
    profile_paths = [
        path for path in sorted(profile_root.glob(PROFILE_GLOB))
        if not path.stem.endswith("-base")
    ]
    if profile_path is not None:
        explicit = profile_path.resolve()
        profile_paths = [explicit, *(path for path in profile_paths if path.resolve() != explicit)]

    candidates: list[ProfileCandidate] = []
    for path in profile_paths:
        profile = load_dataset_profile(path)
        expected = _profile_columns(profile)
        expected_sheets = {
            sheet
            for role, sheet in profile.shared.sheets.items()
            if role not in profile.shared.optional_roles
        }
        actual_sheets = set(headers)
        missing_sheets = sorted(expected_sheets - actual_sheets)
        extra_sheets = sorted(actual_sheets - expected_sheets)
        missing_columns: dict[str, list[str]] = {}
        extra_columns: dict[str, list[str]] = {}
        possible_units: list[str] = []
        matched_columns = 0
        total_columns = sum(len(values) for values in expected.values())
        for sheet, wanted in expected.items():
            actual = headers.get(sheet, set())
            missing = sorted(wanted - actual)
            if missing:
                missing_columns[sheet] = missing
                by_base = {_base_header(column): column for column in actual}
                for column in missing:
                    alternate = by_base.get(_base_header(column))
                    if alternate and alternate != column:
                        possible_units.append(f"{sheet}: {column} ↔ {alternate}")
            matched_columns += len(wanted & actual)
            unused = sorted(actual - wanted)
            if unused:
                extra_columns[sheet] = unused
        sheet_score = len(expected_sheets & actual_sheets) / max(len(expected_sheets), 1)
        column_score = matched_columns / max(total_columns, 1)
        score = round((sheet_score * 0.4 + column_score * 0.6) * 100)
        validation_error = None
        if not missing_sheets and not missing_columns:
            try:
                validate_workbook_profile(source, path)
            except Exception as exc:  # diagnostic boundary: preserve the exact preflight failure
                validation_error = str(exc)
        candidates.append(ProfileCandidate(
            profile_id=profile.profile_id,
            profile_path=str(path.resolve()),
            score=score,
            task_ids=sorted(profile.tasks),
            missing_sheets=missing_sheets,
            extra_sheets=extra_sheets,
            missing_columns=missing_columns,
            extra_columns=extra_columns,
            possible_unit_differences=possible_units,
            validation_error=validation_error,
        ))
    candidates.sort(key=lambda item: (-item.score, item.profile_id))
    selected = candidates[0] if candidates else None
    if profile_path is not None:
        selected = next(item for item in candidates if Path(item.profile_path) == profile_path.resolve())
    ambiguous = (
        profile_path is None
        and len(candidates) > 1
        and candidates[0].score == candidates[1].score
    )
    counts: dict[str, object] = {}
    if selected and not selected.missing_sheets and not selected.missing_columns:
        try:
            counts = validate_workbook_profile(source, Path(selected.profile_path))
        except Exception:
            pass
    new_profile_required = bool(
        selected is None
        or selected.missing_sheets
        or selected.missing_columns
        or selected.validation_error
    )
    recommendations = []
    if ambiguous:
        recommendations.append("候補Profileが同点です。--profile で明示してください。")
    if new_profile_required:
        recommendations.append("既存Profileとの差分を確認し、元ExcelではなくProfile側で吸収できるか判断してください。")
    else:
        recommendations.append("既存Profileを再利用できます。行追加だけならTaskDefinitionや特徴量コードの変更は不要です。")
    return SourceInspection(
        source=str(source),
        source_sha256=_sha256(source),
        selected_profile=selected.profile_path if selected else None,
        ambiguous=ambiguous,
        candidates=candidates,
        canonical_counts=counts,
        decisions={
            "new_profile_required": new_profile_required,
            "reuse_task_definition": bool(selected and selected.task_ids),
            "feature_pipeline_change_required": False,
            "model_package_rebuild_required": True,
            "new_task_may_be_required": False,
        },
        recommendations=recommendations,
    )
