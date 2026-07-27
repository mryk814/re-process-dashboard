import type { CandidateSection } from "../shared/projectActionQuestions";

export const WORKBENCH_VIEWS = [
  "project",
  "candidates",
  "settings",
  "quality",
  "lineage",
  "explore",
  "data-library",
  "profile-workbench",
] as const;

export type WorkbenchView = (typeof WORKBENCH_VIEWS)[number];
export type AdminSection = "developer" | "ranges" | "display" | "task" | "model";
export type DeveloperTab = "overview" | "training" | "guide" | "diagnostics";

export type NavigationIntent = Readonly<{
  view: WorkbenchView;
  projectId?: string;
  candidateId?: string;
  entityKey?: string;
  qualityIssueId?: string;
  qualityType?: string;
  qualitySheet?: string;
  qualityKey?: string;
  screeningRunId?: string;
  activityId?: string;
  activityRunId?: string;
  candidateSection?: CandidateSection;
  snapshotId?: string;
  adminSection?: AdminSection;
  developerTab?: DeveloperTab;
  developerTabError?: string;
  developerGuideId?: string;
  projectSettings?: "targets";
  dataLibraryTab?: "update";
  sourceConnectorId?: string;
  sourceStage?: "raw" | "curation" | "approval" | "training";
  sourceRevisionId?: string;
}>;

const VIEW_SET = new Set<string>(WORKBENCH_VIEWS);
const ADMIN_SECTIONS = new Set<AdminSection>(["developer", "ranges", "display", "task", "model"]);
const DEVELOPER_TABS = new Set<DeveloperTab>(["overview", "training", "guide", "diagnostics"]);
const SOURCE_STAGES = new Set(["raw", "curation", "approval", "training"]);

export function isLegacyQualityAdminNavigation(search = window.location.search): boolean {
  const params = new URLSearchParams(search);
  return params.get("view") === "settings" && params.get("admin") === "quality";
}

export function readNavigationIntent(
  search = window.location.search,
): NavigationIntent {
  const params = new URLSearchParams(search);
  const requestedView = params.get("view") ?? "project";
  const adminSection = params.get("admin");
  const developerTab = params.get("developer_tab");
  const legacyQualityAdmin = requestedView === "settings" && adminSection === "quality";
  return Object.freeze({
    view: legacyQualityAdmin
      ? "quality"
      : VIEW_SET.has(requestedView)
      ? (requestedView as WorkbenchView)
      : "project",
    projectId: params.get("project") || undefined,
    candidateId: params.get("candidate") || undefined,
    entityKey: params.get("entity") || undefined,
    qualityIssueId: params.get("quality_issue") || undefined,
    qualityType: params.get("quality_type") || undefined,
    qualitySheet: params.get("quality_sheet") || undefined,
    qualityKey: params.get("quality_key") || undefined,
    screeningRunId: params.get("screening") || undefined,
    activityId: params.get("activity") || undefined,
    activityRunId: params.get("activity_run") || undefined,
    candidateSection: params.get("candidate_section") === "actuals" ? "actuals" : undefined,
    snapshotId: params.get("snapshot") || undefined,
    adminSection: !legacyQualityAdmin && adminSection && ADMIN_SECTIONS.has(adminSection as AdminSection) ? adminSection as AdminSection : undefined,
    developerTab: developerTab && DEVELOPER_TABS.has(developerTab as DeveloperTab) ? developerTab as DeveloperTab : undefined,
    developerTabError: developerTab && !DEVELOPER_TABS.has(developerTab as DeveloperTab) ? developerTab : undefined,
    developerGuideId: params.get("developer_guide") || undefined,
    projectSettings: params.get("project_settings") === "targets" ? "targets" : undefined,
    dataLibraryTab: params.get("tab") === "update" ? "update" : undefined,
    sourceConnectorId: params.get("connector") || undefined,
    sourceStage: SOURCE_STAGES.has(params.get("stage") ?? "")
      ? params.get("stage") as NavigationIntent["sourceStage"]
      : undefined,
    sourceRevisionId: params.get("revision") || undefined,
  });
}

export function navigationUrl(intent: NavigationIntent): string {
  const params = new URLSearchParams();
  params.set("view", intent.view);
  if (intent.projectId) params.set("project", intent.projectId);
  if (intent.candidateId) params.set("candidate", intent.candidateId);
  if (intent.entityKey) params.set("entity", intent.entityKey);
  if (intent.qualityIssueId) params.set("quality_issue", intent.qualityIssueId);
  if (intent.qualityType) params.set("quality_type", intent.qualityType);
  if (intent.qualitySheet) params.set("quality_sheet", intent.qualitySheet);
  if (intent.qualityKey) params.set("quality_key", intent.qualityKey);
  if (intent.screeningRunId) params.set("screening", intent.screeningRunId);
  if (intent.activityId) params.set("activity", intent.activityId);
  if (intent.activityRunId) params.set("activity_run", intent.activityRunId);
  if (intent.candidateSection) params.set("candidate_section", intent.candidateSection);
  if (intent.snapshotId) params.set("snapshot", intent.snapshotId);
  if (intent.adminSection) params.set("admin", intent.adminSection);
  if (intent.adminSection === "developer" && intent.developerTab) params.set("developer_tab", intent.developerTab);
  if (intent.adminSection === "developer" && intent.developerGuideId) params.set("developer_guide", intent.developerGuideId);
  if (intent.projectSettings) params.set("project_settings", intent.projectSettings);
  if (intent.view === "data-library" && intent.dataLibraryTab) params.set("tab", intent.dataLibraryTab);
  if (intent.view === "data-library" && intent.sourceConnectorId) params.set("connector", intent.sourceConnectorId);
  if (intent.view === "data-library" && intent.sourceStage) params.set("stage", intent.sourceStage);
  if (intent.view === "data-library" && intent.sourceRevisionId) params.set("revision", intent.sourceRevisionId);
  return `${window.location.pathname}?${params.toString()}${window.location.hash}`;
}

export function withView(
  current: NavigationIntent,
  view: WorkbenchView,
): NavigationIntent {
  return Object.freeze({
    view,
    projectId: current.projectId,
    candidateId: current.candidateId,
    entityKey: view === "lineage" ? current.entityKey : undefined,
    qualityIssueId: view === "quality" || view === "lineage" ? current.qualityIssueId : undefined,
    qualityType: view === "quality" || view === "lineage" ? current.qualityType : undefined,
    qualitySheet: view === "quality" || view === "lineage" ? current.qualitySheet : undefined,
    qualityKey: view === "quality" || view === "lineage" ? current.qualityKey : undefined,
    screeningRunId: view === "explore" ? current.screeningRunId : undefined,
    activityId: view === "candidates" ? current.activityId : undefined,
    activityRunId: view === "candidates" ? current.activityRunId : undefined,
    candidateSection: view === "candidates" ? current.candidateSection : undefined,
    snapshotId: view === "project" ? current.snapshotId : undefined,
    adminSection: view === "settings" ? current.adminSection : undefined,
    developerTab: view === "settings" && current.adminSection === "developer" ? current.developerTab : undefined,
    developerTabError: undefined,
    developerGuideId: view === "settings" && current.adminSection === "developer" ? current.developerGuideId : undefined,
    projectSettings: view === "project" ? current.projectSettings : undefined,
    dataLibraryTab: view === "data-library" ? current.dataLibraryTab : undefined,
    sourceConnectorId: view === "data-library" ? current.sourceConnectorId : undefined,
    sourceStage: view === "data-library" ? current.sourceStage : undefined,
    sourceRevisionId: view === "data-library" ? current.sourceRevisionId : undefined,
  });
}
