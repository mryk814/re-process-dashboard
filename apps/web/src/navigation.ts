export const WORKBENCH_VIEWS = [
  "project",
  "candidates",
  "hot-rolling",
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
  screeningRunId?: string;
  snapshotId?: string;
}>;

const VIEW_SET = new Set<string>(WORKBENCH_VIEWS);

export function readNavigationIntent(
  search = window.location.search,
): NavigationIntent {
  const params = new URLSearchParams(search);
  const requestedView = params.get("view") ?? "candidates";
  return Object.freeze({
    view: VIEW_SET.has(requestedView)
      ? (requestedView as WorkbenchView)
      : "candidates",
    projectId: params.get("project") || undefined,
    candidateId: params.get("candidate") || undefined,
    entityKey: params.get("entity") || undefined,
    screeningRunId: params.get("screening") || undefined,
    snapshotId: params.get("snapshot") || undefined,
  });
}

export function navigationUrl(intent: NavigationIntent): string {
  const params = new URLSearchParams();
  params.set("view", intent.view);
  if (intent.projectId) params.set("project", intent.projectId);
  if (intent.candidateId) params.set("candidate", intent.candidateId);
  if (intent.entityKey) params.set("entity", intent.entityKey);
  if (intent.screeningRunId) params.set("screening", intent.screeningRunId);
  if (intent.snapshotId) params.set("snapshot", intent.snapshotId);
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
    screeningRunId: view === "explore" ? current.screeningRunId : undefined,
    snapshotId: view === "project" ? current.snapshotId : undefined,
  });
}
