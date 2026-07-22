export const WORKBENCH_VIEWS = [
  "project",
  "candidates",
  "settings",
  "quality",
  "lineage",
  "explore",
] as const;

export type WorkbenchView = (typeof WORKBENCH_VIEWS)[number];

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
  snapshotId?: string;
  adminSection?: "quality" | "ranges" | "display" | "task" | "model";
}>;

const VIEW_SET = new Set<string>(WORKBENCH_VIEWS);
const ADMIN_SECTIONS = new Set(["quality", "ranges", "display", "task", "model"] as const);

export function readNavigationIntent(
  search = window.location.search,
): NavigationIntent {
  const params = new URLSearchParams(search);
  const requestedView = params.get("view") ?? "candidates";
  const adminSection = params.get("admin");
  return Object.freeze({
    view: VIEW_SET.has(requestedView)
      ? (requestedView as WorkbenchView)
      : "candidates",
    projectId: params.get("project") || undefined,
    candidateId: params.get("candidate") || undefined,
    entityKey: params.get("entity") || undefined,
    qualityIssueId: params.get("quality_issue") || undefined,
    qualityType: params.get("quality_type") || undefined,
    qualitySheet: params.get("quality_sheet") || undefined,
    qualityKey: params.get("quality_key") || undefined,
    screeningRunId: params.get("screening") || undefined,
    snapshotId: params.get("snapshot") || undefined,
    adminSection: adminSection && ADMIN_SECTIONS.has(adminSection as "quality" | "ranges" | "display" | "task" | "model") ? adminSection as "quality" | "ranges" | "display" | "task" | "model" : undefined,
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
  if (intent.snapshotId) params.set("snapshot", intent.snapshotId);
  if (intent.adminSection) params.set("admin", intent.adminSection);
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
    snapshotId: view === "project" ? current.snapshotId : undefined,
    adminSection: view === "settings" ? current.adminSection : undefined,
  });
}
