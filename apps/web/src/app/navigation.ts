import type { CandidateSection } from "../shared/projectActionQuestions";
import type { PreparedProjectBinding } from "../shared/preparedProjectBinding";
import type {
  ModelLibraryDataIntent,
  ModelLibraryProjectIntent,
  ModelLibraryStudioIntent,
  ModelLibraryTab,
} from "../shared/modelLibrary";

export const WORKBENCH_VIEWS = [
  "project",
  "project-settings",
  "candidates",
  "candidate-review",
  "chain-graph",
  "chain-studio",
  "model-library",
  "workspace",
  "quality",
  "lineage",
  "explore",
  "data-library",
  "profile-workbench",
] as const;

export type WorkbenchView = (typeof WORKBENCH_VIEWS)[number];
export type AdminSection = "developer" | "ranges" | "display" | "task" | "model";
export type DeveloperTab = "overview" | "training" | "guide" | "diagnostics";
export type DataLibraryTab = "browse" | "update";
export type SourceLifecycleStage = "raw" | "curation" | "approval" | "training";
export type DataOnboardingMode = "revision" | "mapping" | "new-task";

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
  projectSettings?: "general" | "targets" | "scientific" | "ranges" | "display" | "task" | "evidence";
  dataLibraryTab?: DataLibraryTab;
  modelLibraryTab?: ModelLibraryTab;
  sourceConnectorId?: string;
  sourceStage?: SourceLifecycleStage;
  sourceRevisionId?: string;
  dataOnboardingMode?: DataOnboardingMode;
  baseDatasetRevisionId?: string;
  preparedProjectBinding?: PreparedProjectBinding;
  modelLibraryProject?: ModelLibraryProjectIntent;
  modelLibraryStudio?: ModelLibraryStudioIntent;
  modelLibraryData?: ModelLibraryDataIntent;
}>;

const VIEW_SET = new Set<string>(WORKBENCH_VIEWS);
const ADMIN_SECTIONS = new Set<AdminSection>(["developer", "ranges", "display", "task", "model"]);
const DEVELOPER_TABS = new Set<DeveloperTab>(["overview", "training", "guide", "diagnostics"]);
const SOURCE_STAGES = new Set<SourceLifecycleStage>(["raw", "curation", "approval", "training"]);
const DATA_ONBOARDING_MODES = new Set<DataOnboardingMode>(["revision", "mapping", "new-task"]);
const MODEL_LIBRARY_TABS = new Set<ModelLibraryTab>(["tasks", "packages", "transforms", "graphs"]);

export function isLegacyQualityAdminNavigation(search = window.location.search): boolean {
  const params = new URLSearchParams(search);
  return params.get("view") === "settings" && params.get("admin") === "quality";
}

export function isLegacyCandidateActivityNavigation(search = window.location.search): boolean {
  const params = new URLSearchParams(search);
  return params.get("view") === "candidates"
    && (params.has("activity") || params.has("activity_run"));
}

export function isLegacyProjectSettingsNavigation(search = window.location.search): boolean {
  const params = new URLSearchParams(search);
  const view = params.get("view");
  return (view === "settings" && ["ranges", "display", "task"].includes(params.get("admin") ?? ""))
    || (view === "project" && params.has("project_settings"));
}

export function readNavigationIntent(
  search = window.location.search,
): NavigationIntent {
  const params = new URLSearchParams(search);
  const requestedView = params.get("view") ?? "project";
  const adminSection = params.get("admin");
  const developerTab = params.get("developer_tab");
  const legacyProjectSection = adminSection === "ranges"
    || adminSection === "display"
    || adminSection === "task"
    ? adminSection
    : undefined;
  const requestedProjectSettings = params.get("project_settings");
  const projectSettings = legacyProjectSection
    ?? (["general", "targets", "scientific", "ranges", "display", "task", "evidence"].includes(requestedProjectSettings ?? "")
      ? requestedProjectSettings as NavigationIntent["projectSettings"]
      : undefined);
  const normalizedView: WorkbenchView = requestedView === "settings"
    ? legacyProjectSection ? "project-settings" : adminSection === "quality" ? "quality" : "workspace"
    : requestedView === "project" && projectSettings
      ? "project-settings"
    : requestedView === "candidates" && (params.has("activity") || params.has("activity_run"))
      ? "candidate-review"
    : VIEW_SET.has(requestedView)
      ? requestedView as WorkbenchView
      : "project";
  const requestedConnectorId = params.get("connector") || undefined;
  const requestedStage = params.get("stage");
  const dataLibraryTab: DataLibraryTab | undefined = normalizedView === "data-library"
    ? params.get("tab") === "update" || Boolean(requestedConnectorId)
      ? "update"
      : "browse"
    : undefined;
  const requestedModelLibraryTab = params.get("asset");
  const modelLibraryTab: ModelLibraryTab | undefined = normalizedView === "model-library"
    && requestedModelLibraryTab
    && MODEL_LIBRARY_TABS.has(requestedModelLibraryTab as ModelLibraryTab)
    ? requestedModelLibraryTab as ModelLibraryTab
    : normalizedView === "model-library" ? "tasks" : undefined;
  const sourceConnectorId = dataLibraryTab === "update" ? requestedConnectorId : undefined;
  const sourceStage = sourceConnectorId && requestedStage && SOURCE_STAGES.has(requestedStage as SourceLifecycleStage)
    ? requestedStage as SourceLifecycleStage
    : undefined;
  const sourceRevisionId = sourceStage ? params.get("revision") || undefined : undefined;
  const requestedOnboardingMode = params.get("onboarding");
  const preparedDatasetViewId = params.get("prepared_dataset_view");
  const preparedDatasetRevisionId = params.get("prepared_dataset_revision");
  const preparedTaskId = params.get("prepared_task");
  const preparedModelPackageRefId = params.get("prepared_package");
  const preparedSourceSha256 = params.get("prepared_source_sha256");
  const preparedProjectBinding: PreparedProjectBinding | undefined = normalizedView === "project"
    && preparedDatasetViewId
    && preparedDatasetRevisionId
    && preparedTaskId
    && preparedModelPackageRefId
    && preparedSourceSha256
    ? {
      datasetViewId: preparedDatasetViewId,
      datasetRevisionId: preparedDatasetRevisionId,
      taskId: preparedTaskId,
      taskLabel: params.get("prepared_task_label") || preparedTaskId,
      modelPackageRefId: preparedModelPackageRefId,
      sourceSha256: preparedSourceSha256,
      sourceFilename: params.get("prepared_source_name") || "CSV",
      estimatorId: params.get("prepared_estimator") || "unknown",
      estimatorLabel: params.get("prepared_estimator_label") || params.get("prepared_estimator") || "記録なし",
      preparationResult: params.get("prepared_result") === "reused" ? "reused" : "new",
      workspaceKind: params.get("prepared_workspace_kind") || "local",
      workspaceDatabasePath: params.get("prepared_workspace_path") || "現在のWorkspace",
      reloaded: true,
    }
    : undefined;
  const modelProjectKind = params.get("model_project_kind");
  const modelLibraryProject: ModelLibraryProjectIntent | undefined = normalizedView === "project"
    && modelProjectKind === "single_task"
    && params.get("model_dataset_view")
    && params.get("model_dataset_revision")
    && params.get("model_task")
    && params.get("model_package")
    && params.get("model_package_digest")
    ? {
      kind: "single_task",
      datasetViewRevisionId: params.get("model_dataset_view")!,
      datasetRevisionId: params.get("model_dataset_revision")!,
      taskId: params.get("model_task")!,
      packageReferenceId: params.get("model_package")!,
      packageManifestDigest: params.get("model_package_digest")!,
    }
    : normalizedView === "project"
      && modelProjectKind === "graph"
      && params.get("model_graph")
      && params.get("model_definition")
      && params.get("model_revision")
      && params.get("model_revision_digest")
      ? {
        kind: "graph",
        graphId: params.get("model_graph")!,
        definitionId: params.get("model_definition")!,
        revisionId: params.get("model_revision")!,
        revisionDigest: params.get("model_revision_digest")!,
        datasetViewRevisionId: params.get("model_dataset_view") || undefined,
      }
      : undefined;
  const modelLibraryStudio: ModelLibraryStudioIntent | undefined = normalizedView === "chain-studio"
    && params.get("clone_graph")
    && params.get("clone_definition")
    && params.get("clone_revision")
    ? {
      graphId: params.get("clone_graph")!,
      definitionId: params.get("clone_definition")!,
      revisionId: params.get("clone_revision")!,
    }
    : undefined;
  const modelLibraryData: ModelLibraryDataIntent | undefined = normalizedView === "data-library"
    && (params.get("focus_dataset_revision") || params.get("focus_package"))
    ? {
      datasetRevisionId: params.get("focus_dataset_revision") || undefined,
      packageReferenceId: params.get("focus_package") || undefined,
    }
    : undefined;
  const dataOnboardingMode = (normalizedView === "data-library" || normalizedView === "profile-workbench")
    && requestedOnboardingMode
    && DATA_ONBOARDING_MODES.has(requestedOnboardingMode as DataOnboardingMode)
    ? requestedOnboardingMode as DataOnboardingMode
    : undefined;
  return Object.freeze({
    view: normalizedView,
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
    adminSection: normalizedView === "workspace" && adminSection && ADMIN_SECTIONS.has(adminSection as AdminSection) ? adminSection as AdminSection : undefined,
    developerTab: adminSection === "model"
      ? "diagnostics"
      : developerTab && DEVELOPER_TABS.has(developerTab as DeveloperTab) ? developerTab as DeveloperTab : undefined,
    developerTabError: developerTab && !DEVELOPER_TABS.has(developerTab as DeveloperTab) ? developerTab : undefined,
    developerGuideId: params.get("developer_guide") || undefined,
    projectSettings,
    dataLibraryTab,
    modelLibraryTab,
    sourceConnectorId,
    sourceStage,
    sourceRevisionId,
    dataOnboardingMode,
    baseDatasetRevisionId: params.get("base_dataset") || undefined,
    preparedProjectBinding,
    modelLibraryProject,
    modelLibraryStudio,
    modelLibraryData,
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
  if (intent.view === "workspace" && intent.adminSection) params.set("admin", intent.adminSection);
  if (intent.adminSection === "developer" && intent.developerTab) params.set("developer_tab", intent.developerTab);
  if (intent.adminSection === "developer" && intent.developerGuideId) params.set("developer_guide", intent.developerGuideId);
  if (intent.view === "project-settings" && intent.projectSettings) {
    params.set("project_settings", intent.projectSettings);
  }
  if (intent.view === "data-library" && intent.dataLibraryTab === "update") params.set("tab", "update");
  if (intent.view === "model-library" && intent.modelLibraryTab && intent.modelLibraryTab !== "tasks") {
    params.set("asset", intent.modelLibraryTab);
  }
  if (intent.view === "data-library" && intent.sourceConnectorId) params.set("connector", intent.sourceConnectorId);
  if (intent.view === "data-library" && intent.sourceStage) params.set("stage", intent.sourceStage);
  if (intent.view === "data-library" && intent.sourceRevisionId) params.set("revision", intent.sourceRevisionId);
  if (intent.view === "data-library" && intent.modelLibraryData?.datasetRevisionId) {
    params.set("focus_dataset_revision", intent.modelLibraryData.datasetRevisionId);
  }
  if (intent.view === "data-library" && intent.modelLibraryData?.packageReferenceId) {
    params.set("focus_package", intent.modelLibraryData.packageReferenceId);
  }
  if (
    (intent.view === "data-library" || intent.view === "profile-workbench")
    && intent.dataOnboardingMode
  ) params.set("onboarding", intent.dataOnboardingMode);
  if (
    (intent.view === "data-library" || intent.view === "profile-workbench")
    && intent.baseDatasetRevisionId
  ) params.set("base_dataset", intent.baseDatasetRevisionId);
  if (intent.view === "project" && intent.preparedProjectBinding) {
    const binding = intent.preparedProjectBinding;
    params.set("prepared_dataset_view", binding.datasetViewId);
    params.set("prepared_dataset_revision", binding.datasetRevisionId);
    params.set("prepared_task", binding.taskId);
    params.set("prepared_task_label", binding.taskLabel);
    params.set("prepared_package", binding.modelPackageRefId);
    params.set("prepared_source_sha256", binding.sourceSha256);
    params.set("prepared_source_name", binding.sourceFilename);
    params.set("prepared_estimator", binding.estimatorId);
    params.set("prepared_estimator_label", binding.estimatorLabel);
    params.set("prepared_result", binding.preparationResult);
    params.set("prepared_workspace_kind", binding.workspaceKind);
    params.set("prepared_workspace_path", binding.workspaceDatabasePath);
  }
  if (intent.view === "project" && intent.modelLibraryProject) {
    const project = intent.modelLibraryProject;
    params.set("model_project_kind", project.kind);
    if (project.kind === "single_task") {
      params.set("model_dataset_view", project.datasetViewRevisionId);
      params.set("model_dataset_revision", project.datasetRevisionId);
      params.set("model_task", project.taskId);
      params.set("model_package", project.packageReferenceId);
      params.set("model_package_digest", project.packageManifestDigest);
    } else {
      params.set("model_graph", project.graphId);
      params.set("model_definition", project.definitionId);
      params.set("model_revision", project.revisionId);
      params.set("model_revision_digest", project.revisionDigest);
      if (project.datasetViewRevisionId) params.set("model_dataset_view", project.datasetViewRevisionId);
    }
  }
  if (intent.view === "chain-studio" && intent.modelLibraryStudio) {
    params.set("clone_graph", intent.modelLibraryStudio.graphId);
    params.set("clone_definition", intent.modelLibraryStudio.definitionId);
    params.set("clone_revision", intent.modelLibraryStudio.revisionId);
  }
  return `${window.location.pathname}?${params.toString()}${window.location.hash}`;
}

/**
 * URL query is an external input. This is the single comparison used by the
 * navigation owner before replacing a legacy, incomplete, or invalid location.
 */
export function navigationLocationNeedsNormalization(
  intent: NavigationIntent,
  search = window.location.search,
): boolean {
  return navigationUrl(intent) !== `${window.location.pathname}${search}${window.location.hash}`;
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
    activityId: view === "candidate-review" ? current.activityId : undefined,
    activityRunId: view === "candidate-review" ? current.activityRunId : undefined,
    candidateSection: view === "candidates" ? current.candidateSection : undefined,
    snapshotId: view === "project" ? current.snapshotId : undefined,
    adminSection: view === "workspace" ? current.adminSection : undefined,
    developerTab: view === "workspace" && current.adminSection === "developer" ? current.developerTab : undefined,
    developerTabError: undefined,
    developerGuideId: view === "workspace" && current.adminSection === "developer" ? current.developerGuideId : undefined,
    projectSettings: view === "project-settings" ? current.projectSettings : undefined,
    dataLibraryTab: view === "data-library" ? current.dataLibraryTab : undefined,
    modelLibraryTab: view === "model-library" ? current.modelLibraryTab : undefined,
    sourceConnectorId: view === "data-library" ? current.sourceConnectorId : undefined,
    sourceStage: view === "data-library" ? current.sourceStage : undefined,
    sourceRevisionId: view === "data-library" ? current.sourceRevisionId : undefined,
    dataOnboardingMode: view === "data-library" || view === "profile-workbench"
      ? current.dataOnboardingMode
      : undefined,
    baseDatasetRevisionId: view === "data-library" || view === "profile-workbench"
      ? current.baseDatasetRevisionId
      : undefined,
    preparedProjectBinding: view === "project" ? current.preparedProjectBinding : undefined,
    modelLibraryProject: view === "project" ? current.modelLibraryProject : undefined,
    modelLibraryStudio: view === "chain-studio" ? current.modelLibraryStudio : undefined,
    modelLibraryData: view === "data-library" ? current.modelLibraryData : undefined,
  });
}
