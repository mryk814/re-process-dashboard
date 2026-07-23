from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


Severity = Literal["ok", "warning", "error"]


class DeveloperCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    section: str
    title: str
    severity: Severity
    summary: str
    cause: str | None = None
    impact: str | None = None
    commands: list[str] = []
    details: dict[str, Any] = {}


class ProfileCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str
    profile_path: str
    score: int
    task_ids: list[str]
    missing_sheets: list[str] = []
    extra_sheets: list[str] = []
    missing_columns: dict[str, list[str]] = {}
    extra_columns: dict[str, list[str]] = {}
    possible_unit_differences: list[str] = []
    validation_error: str | None = None


class SourceInspection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    source_sha256: str
    selected_profile: str | None = None
    ambiguous: bool = False
    candidates: list[ProfileCandidate]
    canonical_counts: dict[str, Any] = {}
    decisions: dict[str, bool] = {}
    recommendations: list[str] = []


class DeveloperDoctorReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["developer-doctor/v1"] = "developer-doctor/v1"
    generated_at: str
    status: Severity
    code: Literal[0, 1, 2, 3]
    checks: list[DeveloperCheck]
    task_ids: list[str]
    recommendations: list[str]
    source_inspection: SourceInspection | None = None
