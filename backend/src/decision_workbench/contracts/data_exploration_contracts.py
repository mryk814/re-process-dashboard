from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from decision_workbench.contracts.candidate_project_contracts import HeatPoint
from decision_workbench.contracts.evidence_contracts import LineageNodeReview

class DataQualityIssue(BaseModel):
    issue_id: str
    issue_type: Literal[
        "missing_key",
        "orphan_entity",
        "duplicate_key",
        "invalid_reference",
        "out_of_range",
        "suspicious_distribution",
        "curation_quarantine",
        "missing_target",
        "predictor_missing",
    ]
    source_sheet: str
    entity_key: str
    detail: str
    focus_entity_key: str | None
    related_entity_keys: list[str]
    missing_reference_key: str | None
    suggested_view: Literal["lineage", "source_sheet"]


class QualityScenario(BaseModel):
    model_config = ConfigDict(extra="allow")
    scenario_id: str
    category: str = Field(alias="分類")
    target_key: str = Field(alias="対象キー")
    target_sheet: str = Field(alias="対象シート")
    expected_insight: str = Field(alias="期待する気づき")


class DatasetIdentity(BaseModel):
    task_id: str
    source_path: str
    source_sha256: str
    profile_id: str
    profile_path: str


class QualityResponse(BaseModel):
    # Legacy scenario fields remain until the UI is switched to detected issues.
    total: int
    by_category: dict[str, int]
    issues: list[QualityScenario]
    reference_scenarios: list[QualityScenario]
    detected_total: int
    detected_by_type: dict[str, int]
    detected_issues: list[DataQualityIssue]
    dataset: DatasetIdentity


class PropertySummary(BaseModel):
    count: int
    min: float
    mean: float
    std: float
    median: float
    max: float


class ConnectedObservation(BaseModel):
    id: str
    source: str
    parent_key: str
    outputs: dict[str, float]


class ObservationGroup(BaseModel):
    stage: str
    test_type: str
    property: str
    count: int
    min: float
    mean: float
    std: float
    median: float
    max: float
    observations: list[ConnectedObservation]


class LineageGraphNode(BaseModel):
    key: str
    entity_type: str
    source_sheet: str
    exists: bool
    selected: bool
    issue_types: list[str]


class LineageGraphEdge(BaseModel):
    source: str
    target: str
    route_rows: list[int]


class LineageGraph(BaseModel):
    nodes: list[LineageGraphNode]
    edges: list[LineageGraphEdge]
    relation_row_count: int
    visible_node_count: int
    total_node_count: int
    node_limit: int
    all_reachable: bool
    has_more: bool
    omitted_node_count: int


class EvidenceImageRef(BaseModel):
    """A micrograph the source row points at, and whether the file is there.

    `available=False` keeps a declared-but-missing image visible as missing
    instead of silently dropping the observation's evidence.
    """

    declared_path: str
    available: bool
    reason: str | None = None


class LineageNodeDetail(BaseModel):
    key: str
    entity_type: str
    source_sheet: str
    evidence_image: EvidenceImageRef | None = None
    source_row: dict[str, str | float | int | bool | None]
    primary_conditions: dict[str, str | float | int | bool | None]
    composition: dict[str, float]
    heat_pattern: list[HeatPoint]
    connected_observation_count: int
    connected_observations: list[ConnectedObservation]
    observation_groups: list[ObservationGroup]
    property_summary: dict[str, PropertySummary]
    related_entities: dict[str, list[str]]
    missing_source: bool = False


class LineageCandidateOption(BaseModel):
    process_key: str
    process_role: Literal["annealing", "hot_rolling"]
    process_label: str
    melt_key: str


class LineageResponse(BaseModel):
    # key/relations/quality_issues are the existing renderer contract.
    key: str
    relations: dict[str, list[str]]
    quality_issues: list[DataQualityIssue]
    node: LineageNodeDetail
    graph: LineageGraph
    candidate_eligible: bool
    candidate_reason: str
    candidate_options: list[LineageCandidateOption] = Field(default_factory=list)
    review: LineageNodeReview | None = None


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
