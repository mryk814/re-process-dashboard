import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiClientError } from "../../shared/api/client";
import { formatPredictionPoint, predictionHasInterval, predictionIntervalLabel } from "../../shared/predictionPresentation";
import { assessPrediction, resolveOutputDefinition } from "../../shared/outputPresentation";
import { CandidateAddButton } from "../../shared/ui/CandidateAddButton";
import { hasValidTargetGoal, isTargetRange, targetGoalText, type TargetGoal } from "../../shared/targetGoals";
import { formatNumberAtDecimals, formatTaskNumber, orderedTaskEntries } from "../../shared/taskPresentation";
import {
  compatiblePackagesForDatasetTask,
  compatibleTaskIdsForDataset,
  datasetDisplayName,
  modelPackageDisplayName,
  modelPackageDisplayNames,
  modelPackageEstimatorIds,
  projectDatasetChoices,
  trainingDataset,
} from "../../shared/dataLibraryPresentation";
import { fromApiCandidate, toApiCandidate, type CandidateViewModel, type RuntimeOperations, type TaskDefinitionContract } from "../candidates";
import {
  workbenchApi,
  type ApiChainEvaluation,
  type ApiChainSnapshot,
  type ApiChainTemplate,
  type ApiModelPackage,
  type ApiPreview,
  type ApiProject,
  type ApiProjectCreationOptions,
  type ApiSampleGalleryItem,
  type ApiSnapshot,
  type ApiSubsystemAvailability,
  type ApiTaskCatalogItem,
  type ApiTaskDefinition,
} from "../../shared/api/workbench-api";
import {
  chainAvailability,
  chainStagePath,
  isExecutableChainDefinition,
  isExecutableChainRevision,
  projectOperationDisabled,
  resolveFixedChain,
  type ExecutableChainRevision,
} from "./chainProjectMetadata";
import type { ResolvedTaskDefinition } from "../candidates";
import { ChainEvaluationPanel } from "./ChainEvaluationPanel";
import { candidateQuestionActions, candidateQuestionState, type CandidateSection } from "../../shared/projectActionQuestions";
import { ProjectEvidenceHistoryList } from "./ProjectEvidenceHistory";
import { ProjectCreationPanel, type PreparedBindingReview } from "./ProjectCreationPanel";
import {
  preparedEstimatorCapabilities,
  type PreparedProjectBinding,
} from "../../shared/preparedProjectBinding";
import { preparedBindingBlockers } from "./preparedBindingValidation";
import { defaultGoalLabel, ProjectSettingsPanel } from "./ProjectSettingsPanel";
import {
  projectGroupMembershipState,
  ungroupedMembershipValue,
} from "./projectSettingsState";
import { useProjectHistory } from "./useProjectHistory";
import {
  beginProjectResourceLoad,
  initialProjectResourceState,
  rejectProjectResourceLoad,
  resolveProjectResourceLoad,
  type ProjectResourceState,
} from "./projectResourceState";

type ProjectSettingsSection = "general" | "targets" | "scientific" | "ranges" | "display" | "task" | "evidence";
type ProjectSettingsResource = "project" | "project-name" | "group-name" | "group-membership";

type Props = {
  surface: "overview" | "settings";
  projects: ApiProject[];
  activeProjectId: string;
  candidate?: CandidateViewModel;
  taskDefinition: TaskDefinitionContract | null;
  supportsLineageCandidate: boolean;
  operations?: RuntimeOperations;
  currentPreviews: Record<string, ApiPreview>;
  taskAvailability?: ResolvedTaskDefinition["availability"];
  subsystemAvailability: ApiSubsystemAvailability[];
  subsystemAvailabilityLoaded: boolean;
  subsystemAvailabilityError: boolean;
  offline: boolean;
  requestedSnapshotId?: string;
  requestedDatasetViewId?: string;
  requestedProjectBinding?: Omit<PreparedProjectBinding, "datasetViewId">;
  requestedSettingsSection?: ProjectSettingsSection;
  renderScientificSettings?: (
    project: ApiProject,
    onProjectChanged: (project: ApiProject) => void,
    readOnly: boolean,
  ) => ReactNode;
  onProjectChanged: (project: ApiProject) => void;
  onOpenSettings: (section?: ProjectSettingsSection, replace?: boolean) => void;
  onProjectArchived: (projectId: string) => Promise<boolean>;
  onProjectRestored: (projectId: string) => Promise<boolean>;
  onSampleGalleryInstall: (projectIds: string[]) => Promise<boolean>;
  onSampleGalleryRemove: (projectId: string) => Promise<boolean>;
  onSwitch: (projectId: string) => void;
  onRestore: (candidate: CandidateViewModel) => void;
  onNavigate: (
    view: "candidates" | "candidate-review" | "lineage" | "explore" | "data-library",
    candidateId?: string,
    options?: { activityId?: string; candidateSection?: CandidateSection },
  ) => void;
  onSnapshotNavigate: (snapshotId?: string) => void;
  onCreationIntentConsumed: () => void;
};

/**
 * Digests stay available as identification, but they are not the label a material
 * engineer reads first. The overview keeps them in one collapsed block.
 */
function ReferenceIdentityDetails({ items }: { items: Array<[string, string | null | undefined]> }) {
  const present = items.filter((item): item is [string, string] => Boolean(item[1]));
  if (!present.length) return null;
  return <details className="project-reference-identity">
    <summary>識別情報</summary>
    <dl>{present.map(([label, digest]) => <div key={label}>
      <dt>{label}</dt>
      <dd title={digest}>{digest.replace(/^sha256:/, "")}</dd>
    </div>)}</dl>
  </details>;
}

function sampleSourceKindLabel(kind: ApiSampleGalleryItem["source_kind"]): string {
  switch (kind) {
    case "public": return "公開データ";
    case "synthetic": return "合成データ";
    case "generated_fixture": return "開発fixture";
    case "bundled_demonstration": return "同梱デモ";
  }
}

function bindingProvenanceLabel(provenance: string | null | undefined, generatedLabel: string): string {
  return provenance === "generated_default"
    ? generatedLabel
    : provenance === "inherited_predecessor"
      ? "前の検討から継承"
      : provenance === "updated_revision"
        ? "目標の変更で版を更新"
        : "明示的に固定";
}

function unresolvedReferenceLabel(kind: string, identifier: string | null | undefined) {
  return identifier
    ? `解決できません（識別子: ${identifier}）`
    : `${kind}の固定参照が記録されていません`;
}

const formatNumber = (value: number, digits = 1) => value.toLocaleString("ja-JP", { maximumFractionDigits: digits });
const formatDate = (value: string) => new Date(value).toLocaleString("ja-JP");
const resourceLoadedAt = (value: string | null) => value
  ? new Date(value).toLocaleString("ja-JP")
  : "";
const resourceUnavailable = (cause: unknown) => cause instanceof ApiClientError && (
  cause.availability?.status === "unavailable"
  || cause.code === "subsystem_unavailable"
  || cause.code === "runtime_unavailable"
);

function ProjectResourceRecovery({
  state,
  label,
  retained,
  onRetry,
  showReadyAction = false,
}: {
  state: ProjectResourceState;
  label: string;
  retained: string;
  onRetry: () => void;
  showReadyAction?: boolean;
}) {
  if ((state.phase === "ready" || state.phase === "empty") && !showReadyAction) return null;
  const loading = state.phase === "loading";
  const failed = state.phase === "stale"
    || state.phase === "error"
    || state.phase === "unavailable";
  return <div
    className={failed ? "data-library-resource-error" : "empty-evidence"}
    role={failed ? "alert" : "status"}
  >
    <div>
      {loading
        ? <strong>{label}を読み込んでいます</strong>
        : failed
          ? <strong>{state.unavailable
            ? `${label}は現在利用できません`
            : state.phase === "stale"
              ? `${label}を更新できませんでした`
              : state.error || `${label}を取得できませんでした`}</strong>
          : <strong>{label}は取得済みです</strong>}
      {loading && state.loadedAt && <>
        <p>{retained}を表示したまま更新しています。</p>
        <small>表示中の内容の取得時刻: {resourceLoadedAt(state.loadedAt)}</small>
      </>}
      {state.phase === "stale" && <>
        <p>{retained}は保持しています。最新情報として扱わないでください。</p>
        <small>この画面での取得時刻: {resourceLoadedAt(state.loadedAt)}</small>
      </>}
      {(state.phase === "error" || state.phase === "unavailable") && (
        <p>{retained}は未確認です。ほかの取得済み情報はそのまま利用できます。</p>
      )}
    </div>
    <button
      type="button"
      className="outline-button"
      disabled={loading}
      onClick={onRetry}
    >{loading ? `${label}を読込中…` : failed ? `${label}を再試行` : `${label}を更新`}</button>
  </div>;
}
// 所属変更のselectでは、未選択（空文字）と「グループなしへ移動」を別の値で持つ。
type ChainStage = ApiChainSnapshot["stages"][number];
type ChainOutputDefinition = ChainStage["output_definitions"][number];
type ChainPrediction = {
  value?: number;
  std?: number;
  lower?: number;
  upper?: number;
};

function chainResultPredictions(result: unknown): Record<string, ChainPrediction> {
  if (!result || typeof result !== "object") return {};
  const predictions = (result as { predictions?: unknown }).predictions;
  return predictions && typeof predictions === "object"
    ? predictions as Record<string, ChainPrediction>
    : {};
}

function chainStagePredictions(stage: ChainStage | undefined): Record<string, ChainPrediction> {
  return chainResultPredictions(stage?.result);
}

function terminalChainStage(stages: ChainStage[]): ChainStage | undefined {
  return [...stages].reverse().find((stage) => (
    stage.output_definitions.length > 0
    && Object.keys(chainStagePredictions(stage)).length > 0
  ));
}

function formatChainOutput(
  prediction: ChainPrediction | undefined,
  definition: ChainOutputDefinition,
): string {
  if (typeof prediction?.value !== "number" || !Number.isFinite(prediction.value)) {
    return "利用不可";
  }
  const value = formatNumberAtDecimals(prediction.value, definition.display_decimals);
  const unit = definition.unit.trim();
  return `${value}${unit ? ` ${unit}` : ""}`;
}

export function ProjectHub({
  surface,
  projects,
  activeProjectId,
  candidate,
  taskDefinition,
  supportsLineageCandidate,
  operations,
  currentPreviews,
  taskAvailability,
  subsystemAvailability,
  subsystemAvailabilityLoaded,
  subsystemAvailabilityError,
  offline,
  requestedSnapshotId,
  requestedDatasetViewId,
  requestedProjectBinding,
  requestedSettingsSection,
  renderScientificSettings,
  onProjectChanged,
  onOpenSettings,
  onProjectArchived,
  onProjectRestored,
  onSampleGalleryInstall,
  onSampleGalleryRemove,
  onSwitch,
  onRestore,
  onNavigate,
  onSnapshotNavigate,
  onCreationIntentConsumed,
}: Props) {
  const [project, setProject] = useState<ApiProject | null>(null);
  const [catalog, setCatalog] = useState<ApiTaskCatalogItem[]>([]);
  const [modelPackage, setModelPackage] = useState<ApiModelPackage | null>(null);
  const [creationOptions, setCreationOptions] = useState<ApiProjectCreationOptions | null>(null);
  const [chainTemplates, setChainTemplates] = useState<ApiChainTemplate[]>([]);
  const [chainEvaluation, setChainEvaluation] = useState<{
    projectId: string;
    value: ApiChainEvaluation;
  } | null>(null);
  const [chainTaskDefinition, setChainTaskDefinition] =
    useState<ApiTaskDefinition | null>(null);
  const [selectedSnapshot, setSelectedSnapshot] = useState<ApiSnapshot | null>(null);
  const [selectedChainSnapshot, setSelectedChainSnapshot] =
    useState<ApiChainSnapshot | null>(null);
  const [error, setError] = useState("");
  const [overviewResourceState, setOverviewResourceState] = useState(
    () => initialProjectResourceState(activeProjectId),
  );
  const [overviewRevision, setOverviewRevision] = useState(0);
  const [chainEvaluationResourceState, setChainEvaluationResourceState] = useState(
    () => initialProjectResourceState(activeProjectId),
  );
  const [chainEvaluationRevision, setChainEvaluationRevision] = useState(0);
  const initialSnapshotScope = `${activeProjectId}:${requestedSnapshotId ?? ""}`;
  const [snapshotResourceState, setSnapshotResourceState] = useState(
    () => initialProjectResourceState(initialSnapshotScope),
  );
  const [snapshotRevision, setSnapshotRevision] = useState(0);
  const [settingsPendingResource, setSettingsPendingResource] =
    useState<ProjectSettingsResource | null>(null);
  const [settingsErrors, setSettingsErrors] = useState<
    Partial<Record<ProjectSettingsResource, string>>
  >({});
  const settingsPending = settingsPendingResource !== null;
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [archiveCommandError, setArchiveCommandError] = useState("");
  const [archiveRestoreError, setArchiveRestoreError] = useState("");
  const [archivedProjects, setArchivedProjects] = useState<ApiProject[]>([]);
  const [archiveListResourceState, setArchiveListResourceState] = useState(
    () => initialProjectResourceState("archived-projects"),
  );
  const [archiveListRevision, setArchiveListRevision] = useState(0);
  const [sampleGallery, setSampleGallery] = useState<ApiSampleGalleryItem[]>([]);
  const [sampleGalleryOpen, setSampleGalleryOpen] = useState(false);
  const [installingSampleId, setInstallingSampleId] = useState("");
  const [removingSampleId, setRemovingSampleId] = useState("");
  const [restoringProjectId, setRestoringProjectId] = useState("");
  const [restoringCandidateId, setRestoringCandidateId] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [creationError, setCreationError] = useState("");
  const [preparationReceipt, setPreparationReceipt] = useState<PreparedProjectBinding>();
  const [manualBindingSelectionOpen, setManualBindingSelectionOpen] = useState(false);
  const [createMode, setCreateMode] = useState<"empty" | "copy">("empty");
  const [newProjectName, setNewProjectName] = useState("");
  const [newTaskId, setNewTaskId] = useState("");
  const [newDatasetViewId, setNewDatasetViewId] = useState("");
  const [newModelPackageRefId, setNewModelPackageRefId] = useState("");
  const [newChainId, setNewChainId] = useState("");
  const [newChainRevisionId, setNewChainRevisionId] = useState("");
  const [newProjectGroupChoice, setNewProjectGroupChoice] = useState<"none" | "existing" | "new">("none");
  const [newProjectSeriesId, setNewProjectSeriesId] = useState("");
  const [newProjectSeriesName, setNewProjectSeriesName] = useState("");
  const [predecessorProjectId, setPredecessorProjectId] = useState("");
  const [continuationReason, setContinuationReason] = useState("");
  const [seriesName, setSeriesName] = useState("");
  const [projectNameDraft, setProjectNameDraft] = useState("");
  const [groupMembershipId, setGroupMembershipId] = useState("");
  const [groupSettingsOpen, setGroupSettingsOpen] = useState(false);
  const [decisionNote, setDecisionNote] = useState("");
  const [decisionPending, setDecisionPending] = useState(false);
  const [decisionError, setDecisionError] = useState("");
  const [collapsedSeriesIds, setCollapsedSeriesIds] = useState<Set<string>>(() => new Set());
  const activeProjectRef = useRef(activeProjectId);
  const initializedSeriesIdsRef = useRef(new Set<string>());
  const previousActiveSeriesIdRef = useRef<string | null>(null);
  const decisionDraftRef = useRef({ key: "", dirty: false });
  const projectNameDraftProjectRef = useRef("");
  const projectNameDirtyRef = useRef(false);
  const targetDraftDirtyRef = useRef(false);
  const projectNameInputRef = useRef<HTMLInputElement>(null);
  const focusCreationFormRef = useRef(false);
  const loadedOverviewProjectRef = useRef<string | null>(null);
  const projectRequestGenerationRef = useRef({
    projectId: activeProjectId,
    generation: 0,
  });
  if (projectRequestGenerationRef.current.projectId !== activeProjectId) {
    projectRequestGenerationRef.current = {
      projectId: activeProjectId,
      generation: projectRequestGenerationRef.current.generation + 1,
    };
  }
  activeProjectRef.current = activeProjectId;
  const isCurrentProjectRequest = (projectId: string, generation: number) => (
    activeProjectRef.current === projectId
    && projectRequestGenerationRef.current.projectId === projectId
    && projectRequestGenerationRef.current.generation === generation
  );
  const {
    history,
    state: historyState,
    reload: reloadHistory,
    retry: retryHistory,
  } = useProjectHistory(activeProjectId);
  const taskUnavailable = taskAvailability?.status === "unavailable";
  const identityProject = project?.id === activeProjectId
    ? project
    : projects.find((item) => item.id === activeProjectId);
  const chainIdentity = identityProject?.scientific_identity?.identity_kind === "chain"
    ? identityProject.scientific_identity
    : null;
  const predictionGraphIdentity = identityProject?.scientific_identity?.identity_kind === "prediction_graph"
    ? identityProject.scientific_identity
    : null;
  const overviewScope = chainIdentity
    ? `${activeProjectId}:chain:${chainIdentity.chain_revision_id}`
    : predictionGraphIdentity
      ? `${activeProjectId}:prediction-graph:${predictionGraphIdentity.graph_revision_id}:${predictionGraphIdentity.graph_revision_digest}`
    : [
      activeProjectId,
      "single",
      identityProject?.task_id ?? "",
      identityProject?.dataset_view_revision_id ?? "",
      identityProject?.model_package_ref_id ?? "",
      identityProject?.model_package_manifest_digest ?? "",
      identityProject?.task_contract_digest ?? "",
    ].join(":");
  const { template: fixedChain, revision: fixedChainRevision } = resolveFixedChain(
    chainIdentity,
    chainTemplates,
  );
  const fixedChainId = fixedChainRevision?.chain_id;
  const chainSubsystem = chainAvailability(subsystemAvailability, fixedChainId, "chain");
  const chainEvaluationSubsystem = chainAvailability(
    subsystemAvailability,
    fixedChainId,
    "chain_evaluation",
  );
  const chainOperationsUnavailable = Boolean(chainIdentity) && (
    !subsystemAvailabilityLoaded
    || subsystemAvailabilityError
    || !fixedChainRevision
    || chainSubsystem?.status === "unavailable"
  );
  const fixedStagePath = chainStagePath(fixedChainRevision);
  const effectiveTaskDefinition = taskDefinition
    ?? chainTaskDefinition?.task_definition
    ?? null;
  const outputDefinition = (key: string) => resolveOutputDefinition(
    effectiveTaskDefinition?.outputs ?? [],
    key,
  );
  const orderedPredictions = <T,>(values: Record<string, T>) => effectiveTaskDefinition
    ? orderedTaskEntries(effectiveTaskDefinition, values)
    : Object.entries(values);
  const formatOutputNumber = (key: string, value: number) => effectiveTaskDefinition
    ? formatTaskNumber(
      value,
      effectiveTaskDefinition,
      `output.${key}`,
      project?.display_decimals,
    )
    : formatNumber(value);
  const chainExecutionPending = false;
  const applyProjectSettingsResponse = (nextProject: ApiProject) => {
    if (nextProject.id !== activeProjectRef.current) return;
    setProject((current) => current?.id === nextProject.id && targetDraftDirtyRef.current
      ? { ...nextProject, target_values: current.target_values }
      : nextProject);
    onProjectChanged(nextProject);
  };

  useEffect(() => {
    const selected = projects.find((item) => item.id === activeProjectId) ?? null;
    setProject((current) => selected && current?.id === selected.id && targetDraftDirtyRef.current
      ? { ...selected, target_values: current.target_values }
      : selected);
    setError("");
    setArchiveOpen(false);
    setSettingsPendingResource(null);
    setSettingsErrors({});
    setArchiveCommandError("");
    setArchiveRestoreError("");
    setDecisionNote("");
    setDecisionPending(false);
    setDecisionError("");
    decisionDraftRef.current = { key: "", dirty: false };
  }, [projects, activeProjectId]);

  useEffect(() => {
    const selected = projects.find((item) => item.id === activeProjectId);
    if (projectNameDraftProjectRef.current !== activeProjectId) {
      projectNameDraftProjectRef.current = activeProjectId;
      projectNameDirtyRef.current = false;
      targetDraftDirtyRef.current = false;
    }
    if (!projectNameDirtyRef.current) setProjectNameDraft(selected?.name ?? "");
  }, [activeProjectId, projects.find((item) => item.id === activeProjectId)?.name]);

  useEffect(() => {
    let active = true;
    setArchiveListResourceState((current) => beginProjectResourceLoad(
      current,
      "archived-projects",
    ));
    void workbenchApi.listProjects(true).then((items) => {
      if (!active) return;
      const archived = items.filter((item) => item.archived_at);
      setArchivedProjects(archived);
      setArchiveListResourceState(resolveProjectResourceLoad(
        "archived-projects",
        archived.length === 0,
      ));
    }).catch((cause) => {
      if (!active) return;
      setArchiveListResourceState((current) => rejectProjectResourceLoad(
        current,
        "archived-projects",
        "アーカイブ済みProjectを取得できませんでした。",
        resourceUnavailable(cause),
      ));
    });
    return () => { active = false; };
  }, [projects, archiveListRevision]);

  useEffect(() => {
    let active = true;
    void workbenchApi.listSampleGallery().then((items) => {
      if (active) setSampleGallery(items);
    }).catch(() => {
      if (active) setSampleGallery([]);
    });
    return () => { active = false; };
  }, [projects]);

  useEffect(() => {
    const controller = new AbortController();
    const scope = overviewScope;
    const retainsCurrentEvidence = loadedOverviewProjectRef.current === scope;
    if (!retainsCurrentEvidence) {
      setModelPackage(null);
      setChainTaskDefinition(null);
    }
    setOverviewResourceState((current) => beginProjectResourceLoad(current, scope));
    const requests = [
      workbenchApi.listTaskDefinitions().then((items) => {
        if (!controller.signal.aborted) {
          setCatalog(items);
        }
      }),
      workbenchApi.projectCreationOptions().then((item) => !controller.signal.aborted && setCreationOptions(item)),
      workbenchApi.listChainTemplates().then((items) => !controller.signal.aborted && setChainTemplates(items)),
    ];
    if (!taskUnavailable && identityProject && !predictionGraphIdentity) {
      if (chainIdentity) {
        requests.push(
          workbenchApi.taskDefinition(activeProjectId).then((item) => {
            if (
              !controller.signal.aborted
              && activeProjectRef.current === activeProjectId
            ) {
              setChainTaskDefinition(item);
            }
          }),
        );
      } else {
        requests.push(
          workbenchApi.modelPackage(activeProjectId).then((item) => {
            if (!controller.signal.aborted && activeProjectRef.current === activeProjectId) setModelPackage(item);
          }),
        );
      }
    }
    void Promise.all(requests).then(() => {
      if (controller.signal.aborted || activeProjectRef.current !== activeProjectId) return;
      loadedOverviewProjectRef.current = scope;
      setOverviewResourceState(resolveProjectResourceLoad(scope));
    }).catch((cause) => {
      if (controller.signal.aborted || activeProjectRef.current !== activeProjectId) return;
      setOverviewResourceState((current) => rejectProjectResourceLoad(
        current,
        scope,
        "Project参照情報を取得できませんでした。",
        resourceUnavailable(cause),
      ));
    });
    return () => controller.abort();
  }, [
    activeProjectId,
    overviewScope,
    identityProject?.id,
    predictionGraphIdentity?.graph_revision_id,
    taskUnavailable,
    offline,
    overviewRevision,
  ]);

  useEffect(() => {
    if (!chainIdentity) return;
    const scope = `${activeProjectId}:${chainIdentity.chain_revision_id}`;
    const controller = new AbortController();
    const retainsCurrentEvidence = chainEvaluation?.projectId === activeProjectId
      && chainEvaluationResourceState.scope === scope
      && Boolean(chainEvaluationResourceState.loadedAt);
    if (!retainsCurrentEvidence) setChainEvaluation(null);
    setChainEvaluationResourceState((current) => beginProjectResourceLoad(current, scope));
    if (!subsystemAvailabilityLoaded || !fixedChainId) return () => controller.abort();
    if (subsystemAvailabilityError) {
      setChainEvaluationResourceState((current) => rejectProjectResourceLoad(
        current,
        scope,
        "Chain評価の利用状況を取得できませんでした。",
      ));
      return () => controller.abort();
    }
    if (!chainEvaluationSubsystem) {
      setChainEvaluationResourceState((current) => rejectProjectResourceLoad(
        current,
        scope,
        "Chain評価の利用状況を取得できませんでした。",
      ));
      return () => controller.abort();
    }
    if (chainEvaluationSubsystem?.status === "unavailable") {
      setChainEvaluationResourceState((current) => rejectProjectResourceLoad(
        current,
        scope,
        "Chain評価は現在利用できません。",
        true,
      ));
      return () => controller.abort();
    }
    void workbenchApi.projectChainEvaluation(activeProjectId, controller.signal)
      .then((item) => {
        if (controller.signal.aborted || activeProjectRef.current !== activeProjectId) return;
        setChainEvaluation({ projectId: activeProjectId, value: item });
        setChainEvaluationResourceState(resolveProjectResourceLoad(scope));
      })
      .catch((cause) => {
        if (controller.signal.aborted || activeProjectRef.current !== activeProjectId) return;
        setChainEvaluationResourceState((current) => rejectProjectResourceLoad(
          current,
          scope,
          "Chain評価を取得できませんでした。",
          resourceUnavailable(cause),
        ));
      });
    return () => controller.abort();
  }, [
    activeProjectId,
    chainIdentity?.chain_revision_id,
    chainEvaluationRevision,
    chainEvaluationSubsystem?.status,
    fixedChainId,
    subsystemAvailabilityError,
    subsystemAvailabilityLoaded,
  ]);

  useEffect(() => {
    if (!requestedSnapshotId) return;
    const controller = new AbortController();
    const scope = `${activeProjectId}:${chainIdentity ? "chain" : "single"}:${requestedSnapshotId}`;
    const retainsCurrentEvidence = snapshotResourceState.scope === scope
      && Boolean(snapshotResourceState.loadedAt)
      && (
        chainIdentity
          ? selectedChainSnapshot?.snapshot_id === requestedSnapshotId
          : selectedSnapshot?.id === requestedSnapshotId
      );
    if (!retainsCurrentEvidence) {
      setSelectedSnapshot(null);
      setSelectedChainSnapshot(null);
    }
    setSnapshotResourceState((current) => beginProjectResourceLoad(current, scope));
    if (chainIdentity) {
      workbenchApi.chainSnapshot(activeProjectId, requestedSnapshotId, controller.signal)
        .then((item) => {
          if (!controller.signal.aborted) {
            setSelectedSnapshot(null);
            setSelectedChainSnapshot(item);
            setSnapshotResourceState(resolveProjectResourceLoad(scope));
          }
        })
        .catch((cause) => !controller.signal.aborted && setSnapshotResourceState(
          (current) => rejectProjectResourceLoad(
            current,
            scope,
            "Chain Snapshotを参照できませんでした。",
            resourceUnavailable(cause),
          ),
        ));
    } else if (operations?.snapshot) {
      workbenchApi.snapshot(activeProjectId, requestedSnapshotId, controller.signal)
        .then((item) => {
          if (!controller.signal.aborted) {
            setSelectedChainSnapshot(null);
            setSelectedSnapshot(item);
            setSnapshotResourceState(resolveProjectResourceLoad(scope));
          }
        })
        .catch((cause) => !controller.signal.aborted && setSnapshotResourceState(
          (current) => rejectProjectResourceLoad(
            current,
            scope,
            "保存済み予測を参照できませんでした。",
            resourceUnavailable(cause),
          ),
        ));
    } else {
      setSnapshotResourceState((current) => rejectProjectResourceLoad(
        current,
        scope,
        "このProjectではSnapshotを参照できません。",
        true,
      ));
    }
    return () => controller.abort();
  }, [
    activeProjectId,
    chainIdentity?.chain_revision_id,
    operations?.snapshot,
    requestedSnapshotId,
    snapshotRevision,
  ]);

  useEffect(() => {
    const selectedEvidence = selectedSnapshot
      ? { id: selectedSnapshot.id }
      : selectedChainSnapshot
        ? { id: selectedChainSnapshot.snapshot_id }
        : null;
    if (!selectedEvidence) return;
    const draftKey = `${activeProjectId}:${selectedEvidence.id}`;
    if (decisionDraftRef.current.key !== draftKey) {
      decisionDraftRef.current = { key: draftKey, dirty: false };
      setDecisionNote("");
    }
    if (!history || decisionDraftRef.current.dirty) return;
    const decision = history?.candidates.find(
      (item) => item.decision?.snapshot_id === selectedEvidence.id,
    )?.decision;
    setDecisionNote(decision?.note ?? "");
  }, [
    activeProjectId,
    history,
    selectedChainSnapshot?.snapshot_id,
    selectedSnapshot?.id,
  ]);

  const activeCandidates = history?.candidates.filter((item) => !item.candidate.archived_at) ?? [];
  const actionCandidateId = candidate && activeCandidates.some(
    (item) => item.candidate.id === candidate.id,
  )
    ? candidate.id
    : activeCandidates[0]?.candidate.id;
  const actionBlocked = Boolean(taskUnavailable || predictionGraphIdentity || chainExecutionPending || offline);
  const questionState = candidateQuestionState(actionCandidateId, actionBlocked);
  const copyTaskId = candidate ? projects.find((item) => item.id === candidate.raw.project_id)?.task_id : undefined;
  const outputLabels = useMemo(
    () => new Map(
      (effectiveTaskDefinition?.outputs ?? []).map(
        (output) => [output.key, output.label],
      ),
    ),
    [effectiveTaskDefinition],
  );
  const taskLabels = useMemo(() => new Map(catalog.map((item) => [
    item.definition.task_definition.id,
    item.definition.task_definition.label,
  ])), [catalog]);
  const datasetByView = useMemo(() => new Map(
    (creationOptions?.datasets ?? []).flatMap((dataset) => (dataset.dataset_views ?? []).map((view) => [view.id, dataset] as const)),
  ), [creationOptions]);
  const chainDatasetPresentation = useMemo(() => {
    const labelsByView = new Map<string, Set<string>>();
    const revisionById = new Map<string, ExecutableChainRevision>();
    for (const template of chainTemplates) {
      if (!isExecutableChainDefinition(template.definition)) continue;
      for (const revision of template.revisions) {
        if (!isExecutableChainRevision(revision)) continue;
        revisionById.set(`${revision.chain_id}:r${revision.revision}`, revision);
        for (const stage of revision.stages) {
          if (!stage.dataset_view_revision_id) continue;
          const labels = labelsByView.get(stage.dataset_view_revision_id) ?? new Set<string>();
          labels.add(`${template.definition.label}（Chain）`);
          labelsByView.set(stage.dataset_view_revision_id, labels);
        }
      }
    }
    const viewIdsByProject = new Map<string, string[]>();
    for (const item of projects) {
      const identity = item.scientific_identity;
      if (identity.identity_kind !== "chain") continue;
      const revision = revisionById.get(identity.chain_revision_id);
      if (!revision) continue;
      viewIdsByProject.set(item.id, [...new Set(revision.stages.flatMap(
        (stage) => stage.dataset_view_revision_id ? [stage.dataset_view_revision_id] : [],
      ))]);
    }
    return {
      labelsByViewId: new Map([...labelsByView].map(([viewId, labels]) => [viewId, [...labels]])),
      viewIdsByProjectId: viewIdsByProject,
    };
  }, [chainTemplates, projects]);
  const datasetChoices = useMemo(() => projectDatasetChoices({
    datasets: creationOptions?.datasets ?? [],
    views: creationOptions?.dataset_views ?? [],
    projects,
    taskLabels,
    chainLabelsByViewId: chainDatasetPresentation.labelsByViewId,
    datasetViewIdsByProjectId: chainDatasetPresentation.viewIdsByProjectId,
  }), [chainDatasetPresentation, creationOptions, projects, taskLabels]);
  const usedDatasetChoices = datasetChoices.filter((choice) => choice.group === "used");
  const unusedDatasetChoices = datasetChoices.filter((choice) => choice.group === "unused");
  const selectedDataset = datasetByView.get(newDatasetViewId);
  const selectedDatasetChoice = datasetChoices.find((choice) => choice.id === newDatasetViewId);
  const selectedDatasetProjectSummary = selectedDatasetChoice?.projectNames.length
    ? `利用中: ${selectedDatasetChoice.projectNames.slice(0, 3).join("、")}${selectedDatasetChoice.projectNames.length > 3 ? `、ほか${selectedDatasetChoice.projectNames.length - 3}件` : ""}`
    : "このDatasetを使うProjectはありません";
  const selectedDatasetEvidence = selectedDataset
    ? `${selectedDatasetProjectSummary} · ${selectedDataset.profile_revision.name} · r${selectedDataset.profile_revision.revision}`
    : "DatasetとProfileを選択";
  const availableTaskIds = creationOptions
    ? compatibleTaskIdsForDataset(selectedDataset, creationOptions)
    : [];
  const availablePackages = creationOptions
    ? compatiblePackagesForDatasetTask(selectedDataset, newTaskId, creationOptions)
    : [];
  const availablePackageNames = modelPackageDisplayNames(availablePackages);
  const availableChains = chainTemplates.filter((item) => (
    isExecutableChainDefinition(item.definition)
    && item.revisions.some(
    (revision) => isExecutableChainRevision(revision) && revision.stages.some(
      (stage) => stage.dataset_view_revision_id === newDatasetViewId,
    ),
  )));
  const selectedChain = chainTemplates.find(
    (item) => isExecutableChainDefinition(item.definition)
      && item.definition.chain_id === newChainId,
  );
  const selectedChainRevision = selectedChain?.revisions.find(
    (revision): revision is ExecutableChainRevision => (
      isExecutableChainRevision(revision)
      && `${revision.chain_id}:r${revision.revision}` === newChainRevisionId
    ),
  );
  const fixedDataset = project?.dataset_view_revision_id ? datasetByView.get(project.dataset_view_revision_id) : undefined;
  const fixedPackage = creationOptions?.model_packages.find((item) => item.id === project?.model_package_ref_id);
  const persistedProject = projects.find((item) => item.id === activeProjectId);
  const unresolvedReferences = creationOptions && project
    ? predictionGraphIdentity
      ? []
      : chainIdentity
      ? [
          !fixedChain && "参照Chain",
          !fixedChainRevision && "Chain Revision",
        ].filter((item): item is string => Boolean(item))
      : [
          !fixedDataset && "Dataset",
          !fixedPackage && "Model Package",
        ].filter((item): item is string => Boolean(item))
    : [];
  const settingsCategory = requestedSettingsSection === "evidence"
    ? "evidence"
    : requestedSettingsSection === "scientific"
      || requestedSettingsSection === "ranges"
      || requestedSettingsSection === "display"
      || requestedSettingsSection === "task"
      ? "scientific"
      : "general";
  const effectiveSettingsCategory = (chainIdentity || predictionGraphIdentity) && settingsCategory === "scientific"
    ? "general"
    : settingsCategory;
  const fixedSeries = creationOptions?.project_series.find((item) => item.id === project?.project_series_id);
  const fixedSeriesProjectCount = project?.project_series_id
    ? projects.filter((item) => item.project_series_id === project.project_series_id).length
    : 0;
  const showActiveSeriesMembership = Boolean(fixedSeries && fixedSeriesProjectCount > 1);
  const membershipState = projectGroupMembershipState({
    selectedSeriesId: groupMembershipId,
    currentSeriesId: project?.project_series_id ?? null,
    currentSeriesProjectCount: fixedSeriesProjectCount,
  });
  const membershipTargetSeriesId = membershipState.targetSeriesId;
  const membershipChanged = membershipState.changed;
  const membershipEmptiesFixedSeries = membershipState.emptiesCurrentSeries;
  const activeProjectSeries = useMemo(() => {
    const usedSeriesIds = new Set(projects.map((item) => item.project_series_id).filter(Boolean));
    return (creationOptions?.project_series ?? []).filter((item) => usedSeriesIds.has(item.id));
  }, [creationOptions?.project_series, projects]);
  const selectedSeries = newProjectGroupChoice === "existing"
    ? activeProjectSeries.find((item) => item.id === newProjectSeriesId)
    : undefined;
  const predecessorProject = projects.find((item) => item.id === project?.predecessor_project_id);
  const predecessorSeries = creationOptions?.project_series.find(
    (item) => item.id === predecessorProject?.project_series_id,
  );
  const selectedPackage = creationOptions?.model_packages.find((item) => item.id === newModelPackageRefId);
  const selectedTrainingDataset = trainingDataset(selectedPackage, creationOptions?.datasets ?? []);
  const preparedBindingReview = useMemo<PreparedBindingReview | undefined>(() => {
    if (!preparationReceipt || !creationOptions) return undefined;
    const preparedDataset = datasetByView.get(preparationReceipt.datasetViewId);
    const preparedDatasetChoice = datasetChoices.find((choice) => choice.id === preparationReceipt.datasetViewId);
    const preparedView = creationOptions.dataset_views.find((view) => view.id === preparationReceipt.datasetViewId)
      ?? preparedDataset?.dataset_views?.find((view) => view.id === preparationReceipt.datasetViewId);
    const preparedTask = catalog.find(
      (item) => item.definition.task_definition.id === preparationReceipt.taskId,
    );
    const preparedPackage = creationOptions.model_packages.find(
      (item) => item.id === preparationReceipt.modelPackageRefId,
    );
    const compatibleTasks = compatibleTaskIdsForDataset(preparedDataset, creationOptions);
    const compatiblePackages = compatiblePackagesForDatasetTask(
      preparedDataset,
      preparationReceipt.taskId,
      creationOptions,
    );
    const blockers = preparedBindingBlockers({
      binding: preparationReceipt,
      dataset: preparedDataset && preparedView
        ? {
          revisionId: preparedDataset.dataset_revision.id,
          sourceSha256: preparedDataset.data_asset.sha256,
        }
        : undefined,
      taskExists: Boolean(preparedTask),
      modelPackage: preparedPackage
        ? { refId: preparedPackage.id, taskId: preparedPackage.task_id }
        : undefined,
      taskCompatible: compatibleTasks.includes(preparationReceipt.taskId),
      packageCompatible: compatiblePackages.some((item) => item.id === preparedPackage?.id),
      estimatorCompatible: modelPackageEstimatorIds(preparedPackage).includes(
        preparationReceipt.estimatorId,
      ),
    });
    return {
      dataset: {
        label: preparedDatasetChoice
          ? `${preparedDatasetChoice.purposeLabel} — ${preparedDatasetChoice.sourceLabel}`
          : preparedDataset?.profile_revision.name ?? preparationReceipt.datasetViewId,
        revision: preparationReceipt.datasetRevisionId,
        viewRevision: preparedView
          ? `${preparedView.id} · r${preparedView.revision} · ${preparedView.view_digest}`
          : preparationReceipt.datasetViewId,
      },
      source: {
        filename: preparationReceipt.sourceFilename,
        sha256: preparationReceipt.sourceSha256,
      },
      task: {
        label: preparedTask?.definition.task_definition.label ?? preparationReceipt.taskLabel,
        id: preparationReceipt.taskId,
        contractDigest: creationOptions.task_contract_digests[preparationReceipt.taskId] ?? "解決できません",
      },
      modelPackage: {
        label: preparedPackage ? modelPackageDisplayName(preparedPackage) : preparationReceipt.modelPackageRefId,
        refId: preparationReceipt.modelPackageRefId,
        manifestDigest: preparedPackage?.manifest_digest ?? "解決できません",
      },
      estimator: {
        label: preparationReceipt.estimatorLabel,
        id: preparationReceipt.estimatorId,
        capabilities: preparedEstimatorCapabilities(preparationReceipt.estimatorId),
      },
      preparationResult: preparationReceipt.preparationResult,
      workspace: {
        kind: preparationReceipt.workspaceKind,
        databasePath: preparationReceipt.workspaceDatabasePath,
      },
      blockers,
    };
  }, [catalog, creationOptions, datasetByView, datasetChoices, preparationReceipt]);
  const fixedTrainingDataset = trainingDataset(fixedPackage, creationOptions?.datasets ?? []);
  const selectedTaskId = createMode === "copy" ? copyTaskId ?? "" : newTaskId;

  useEffect(() => {
    if (surface !== "settings") {
      setSeriesName(fixedSeries?.name ?? "");
      setGroupMembershipId(project?.project_series_id ?? "");
      setGroupSettingsOpen(false);
    }
  }, [fixedSeries?.id, fixedSeries?.name, project?.project_series_id, surface]);
  useEffect(() => {
    if (surface === "settings" && chainIdentity && settingsCategory === "scientific") {
      onOpenSettings("general", true);
    }
  }, [chainIdentity?.chain_revision_id, requestedSettingsSection, surface]);
  useEffect(() => {
    if (groupSettingsOpen) setSeriesName(fixedSeries?.name ?? "");
  }, [fixedSeries?.id, fixedSeries?.name, groupSettingsOpen]);
  useEffect(() => {
    // 所属の選択は、パネルを開いた時と実際の所属が変わった時だけ初期化する。
    // グループ一覧が遅れて届いたときに、利用者が選んだ値へ戻さないため。
    if (groupSettingsOpen) setGroupMembershipId(project?.project_series_id ?? "");
  }, [groupSettingsOpen, project?.project_series_id]);
  const projectGroups = useMemo(() => {
    const series = new Map((creationOptions?.project_series ?? []).map((item) => [item.id, item]));
    const groups = new Map((creationOptions?.project_series ?? []).map((item) => [
      item.id,
      { id: item.id, name: item.name, kind: "series" as const, projects: [] as ApiProject[] },
    ]));
    const starters: ApiProject[] = [];
    const unassigned: Array<{ id: string; name: string; kind: "project"; projects: ApiProject[] }> = [];
    for (const item of projects) {
      if (item.starter) {
        starters.push(item);
        continue;
      }
      const seriesId = item.project_series_id;
      if (!seriesId || !series.has(seriesId)) {
        unassigned.push({ id: `project:${item.id}`, name: "", kind: "project", projects: [item] });
        continue;
      }
      const group = groups.get(seriesId)!;
      group.projects.push(item);
    }
    return [
      ...(starters.length > 0
        ? [{
            id: "bundled-samples",
            name: starters.length === 1 ? "クイックスタート" : "同梱サンプル",
            kind: "samples" as const,
            projects: starters,
          }]
        : []),
      ...[...groups.values()].filter((group) => group.projects.length > 0),
      ...unassigned,
    ];
  }, [creationOptions?.project_series, projects]);
  const activeSeriesId = projectGroups.find((group) => group.projects.some((item) => item.id === activeProjectId))?.id ?? `project:${activeProjectId}`;
  const gallerySamples = sampleGallery.filter((item) => !item.legacy);
  const legacySamples = sampleGallery.filter((item) => item.legacy);
  const uninstalledSamples = gallerySamples.filter((item) => !item.installed);

  async function installSamples(projectIds: string[]) {
    setInstallingSampleId(projectIds.length === 1 ? projectIds[0] : "all");
    try {
      const installed = await onSampleGalleryInstall(projectIds);
      if (installed && projectIds.length === 1) setSampleGalleryOpen(false);
    } finally {
      setInstallingSampleId("");
    }
  }

  async function removeSample(projectId: string) {
    setRemovingSampleId(projectId);
    try {
      const removed = await onSampleGalleryRemove(projectId);
      if (removed) setSampleGalleryOpen(false);
    } finally {
      setRemovingSampleId("");
    }
  }

  useEffect(() => {
    const newGroupIds = projectGroups
      .map((group) => group.id)
      .filter((id) => !initializedSeriesIdsRef.current.has(id));
    newGroupIds.forEach((id) => initializedSeriesIdsRef.current.add(id));
    const previousActiveSeriesId = previousActiveSeriesIdRef.current;
    const activeSeriesChanged = previousActiveSeriesId !== activeSeriesId;
    previousActiveSeriesIdRef.current = activeSeriesId;
    setCollapsedSeriesIds((current) => {
      const next = new Set(current);
      newGroupIds.filter((id) => id !== activeSeriesId).forEach((id) => next.add(id));
      if (activeSeriesChanged) {
        if (previousActiveSeriesId !== null) next.add(previousActiveSeriesId);
        next.delete(activeSeriesId);
      }
      return next;
    });
  }, [activeSeriesId, projectGroups]);

  useEffect(() => {
    if (!requestedDatasetViewId || !creationOptions) return;
    const dataset = datasetByView.get(requestedDatasetViewId);
    if (!dataset && !requestedProjectBinding?.reloaded) {
      setError("選択したDatasetをプロジェクト作成に利用できません。");
      onCreationIntentConsumed();
      return;
    }
    focusCreationFormRef.current = true;
    setCreateOpen(true);
    setCreateMode("empty");
    setNewProjectName(
      dataset
        ? `${datasetDisplayName(dataset)} 検討`
        : `${requestedProjectBinding?.taskLabel ?? "準備済みbinding"} 検討`,
    );
    setNewDatasetViewId(requestedDatasetViewId);
    setNewTaskId(requestedProjectBinding?.taskId ?? "");
    setNewModelPackageRefId(requestedProjectBinding?.modelPackageRefId ?? "");
    setPreparationReceipt(requestedProjectBinding?.reloaded
      ? { datasetViewId: requestedDatasetViewId, ...requestedProjectBinding }
      : undefined);
    setManualBindingSelectionOpen(false);
    setNewChainId("");
    setNewChainRevisionId("");
    setNewProjectGroupChoice("none");
    setNewProjectSeriesId("");
    setNewProjectSeriesName("");
    setPredecessorProjectId("");
    setContinuationReason("");
    onCreationIntentConsumed();
  }, [creationOptions, datasetByView, onCreationIntentConsumed, requestedDatasetViewId, requestedProjectBinding]);

  useEffect(() => {
    if (!createOpen || !focusCreationFormRef.current) return;
    focusCreationFormRef.current = false;
    projectNameInputRef.current?.focus();
  }, [createOpen]);

  const focusTargetSettings = () => {
    onOpenSettings("targets");
  };

  useEffect(() => {
    if (surface === "settings" && requestedSettingsSection === "targets") {
      window.requestAnimationFrame(() => document.querySelector<HTMLElement>(
        "#project-target-settings input, #project-target-settings select",
      )?.focus());
    }
  }, [activeProjectId, requestedSettingsSection, surface]);

  async function saveProject(
    nextProject = project,
    options: {
      syncNameDraft?: boolean;
      syncTargetDraft?: boolean;
      resource?: Extract<ProjectSettingsResource, "project" | "project-name">;
    } = {},
  ) {
    if (!nextProject || settingsPending) return;
    const resource = options.resource ?? "project";
    const requestProjectId = activeProjectId;
    const requestGeneration = projectRequestGenerationRef.current.generation;
    if (nextProject.id !== requestProjectId || activeProjectRef.current !== requestProjectId) return;
    setSettingsPendingResource(resource);
    setSettingsErrors((current) => ({ ...current, [resource]: "" }));
    try {
      const saved = await workbenchApi.updateProject(requestProjectId, nextProject);
      if (!isCurrentProjectRequest(requestProjectId, requestGeneration)) return;
      setProject((current) => options.syncNameDraft && current?.id === saved.id
        ? { ...current, name: saved.name }
        : saved);
      if (options.syncNameDraft) {
        projectNameDirtyRef.current = false;
        setProjectNameDraft(saved.name);
      }
      if (options.syncTargetDraft) targetDraftDirtyRef.current = false;
      onProjectChanged(saved);
    } catch {
      if (isCurrentProjectRequest(requestProjectId, requestGeneration)) {
        setSettingsErrors((current) => ({
          ...current,
          [resource]: resource === "project-name"
            ? "Project名を保存できませんでした。入力した名前は保持しています。同じボタンで再試行できます。"
            : "Project設定を保存できませんでした。入力内容は保持しています。同じボタンで再試行できます。",
        }));
      }
    } finally {
      if (isCurrentProjectRequest(requestProjectId, requestGeneration)) setSettingsPendingResource(null);
    }
  }

  async function saveSeriesName() {
    const trimmedSeriesName = seriesName.trim();
    if (!fixedSeries || !trimmedSeriesName || settingsPending) return;
    const requestProjectId = activeProjectId;
    const requestGeneration = projectRequestGenerationRef.current.generation;
    setSettingsPendingResource("group-name");
    setSettingsErrors((current) => ({ ...current, "group-name": "" }));
    try {
      const savedSeries = await workbenchApi.updateProjectSeries(fixedSeries.id, trimmedSeriesName, fixedSeries.description);
      if (!isCurrentProjectRequest(requestProjectId, requestGeneration)) return;
      setCreationOptions((current) => current ? {
        ...current,
        project_series: current.project_series.map((item) => item.id === savedSeries.id ? savedSeries : item),
      } : current);
    } catch {
      if (isCurrentProjectRequest(requestProjectId, requestGeneration)) {
        setSettingsErrors((current) => ({
          ...current,
          "group-name": "検討グループ名を保存できませんでした。入力した名前は保持しています。同じボタンで再試行できます。",
        }));
      }
    } finally {
      if (isCurrentProjectRequest(requestProjectId, requestGeneration)) setSettingsPendingResource(null);
    }
  }

  async function moveProjectToGroup() {
    if (!project || !membershipChanged || settingsPending) return;
    const requestProjectId = project.id;
    const requestGeneration = projectRequestGenerationRef.current.generation;
    setSettingsPendingResource("group-membership");
    setSettingsErrors((current) => ({ ...current, "group-membership": "" }));
    try {
      const moved = await workbenchApi.moveProjectToGroup(requestProjectId, {
        project_series_id: membershipTargetSeriesId,
        expected_project_series_id: project.project_series_id ?? null,
      });
      if (!isCurrentProjectRequest(requestProjectId, requestGeneration)) return;
      setProject(moved);
      setGroupMembershipId(moved.project_series_id ?? "");
      onProjectChanged(moved);
      try {
        const refreshedOptions = await workbenchApi.projectCreationOptions();
        if (!isCurrentProjectRequest(requestProjectId, requestGeneration)) return;
        setCreationOptions(refreshedOptions);
        setSeriesName(
          refreshedOptions.project_series.find((item) => item.id === moved.project_series_id)?.name ?? "",
        );
      } catch {
        if (isCurrentProjectRequest(requestProjectId, requestGeneration)) {
          setSettingsErrors((current) => ({
            ...current,
            "group-membership": "所属は変更しましたが、グループ一覧を更新できませんでした。Projectの所属は保持されています。",
          }));
        }
      }
    } catch {
      if (isCurrentProjectRequest(requestProjectId, requestGeneration)) {
        setGroupMembershipId(project.project_series_id ?? "");
        setSettingsErrors((current) => ({
          ...current,
          "group-membership": "所属グループを変更できませんでした。現在の所属は変わっていません。選択し直して再試行できます。",
        }));
      }
    } finally {
      if (isCurrentProjectRequest(requestProjectId, requestGeneration)) setSettingsPendingResource(null);
    }
  }

  async function createProject() {
    const taskId = createMode === "copy" ? copyTaskId : newTaskId;
    const creatingChain = createMode === "empty" && Boolean(newChainId);
    const trimmedSeriesName = newProjectSeriesName.trim();
    if (!newProjectName.trim() || !newDatasetViewId) return setCreationError("Datasetとプロジェクト名を確認してください。");
    if (creatingChain && !selectedChainRevision) return setCreationError("Chain TemplateとRevisionを確認してください。");
    if (!creatingChain && (!taskId || !newModelPackageRefId)) return setCreationError("予測タスクとModel Packageを確認してください。");
    if (createMode === "copy" && !candidate) return setCreationError("コピーする現在候補がありません。");
    if (newProjectGroupChoice === "existing" && !newProjectSeriesId) return setCreationError("追加する検討グループを選択してください。");
    if (newProjectGroupChoice === "new" && !trimmedSeriesName) return setCreationError("新しい検討グループ名を入力してください。");
    setCreating(true);
    setCreationError("");
    try {
      const initialCandidate = createMode === "copy" && candidate ? {
        ...toApiCandidate(candidate),
        name: `${candidate.label} のコピー`,
        provenance: { source_kind: "copy" as const, source_ref: { project_id: candidate.raw.project_id, candidate_id: candidate.id, candidate_revision: candidate.raw.revision } },
      } : null;
      const shared = {
        name: newProjectName.trim(), description: "", purpose: "",
        target_values: {}, input_ranges: {}, response_curve_points: 17, notes: "", decision_candidate_id: "", decision_snapshot_id: "", decision_note: "",
        initial_candidate: initialCandidate,
        project_series_id: newProjectGroupChoice === "existing" ? newProjectSeriesId : null,
        new_project_series: newProjectGroupChoice === "new"
          ? { name: trimmedSeriesName, description: "" }
          : null,
        predecessor_project_id: predecessorProjectId || undefined,
        continuation_reason: continuationReason,
      };
      const created = await workbenchApi.createProject(creatingChain ? {
        ...shared,
        task_id: "",
        task_contract_digest: "",
        model_package_manifest_digest: "",
        scientific_identity: {
          identity_kind: "chain",
          chain_revision_id: newChainRevisionId,
          chain_revision_digest: selectedChainRevision!.revision_digest,
        },
      } : {
        ...shared,
        task_id: taskId as ApiProject["task_id"],
        dataset_view_revision_id: newDatasetViewId,
        model_package_ref_id: newModelPackageRefId,
        task_contract_digest: selectedPackage?.task_contract_digest ?? "",
        model_package_manifest_digest: selectedPackage?.manifest_digest ?? "",
        design_space: (createMode === "copy" || Boolean(predecessorProjectId))
          && project?.task_id === taskId
          ? project?.design_space ?? undefined
          : undefined,
      });
      onProjectChanged(created);
      setCreateOpen(false);
      resetCreateProjectForm();
      onSwitch(created.id);
    } catch (cause) {
      setCreationError(cause instanceof Error ? cause.message : "新しいプロジェクトを作成できませんでした。");
    } finally {
      setCreating(false);
    }
  }

  function openSnapshot(snapshotId: string) {
    onSnapshotNavigate(snapshotId);
  }

  function openChainSnapshot(snapshot: ApiChainSnapshot) {
    setSelectedSnapshot(null);
    setSelectedChainSnapshot(snapshot);
    setSnapshotResourceState(resolveProjectResourceLoad(
      `${activeProjectId}:chain:${snapshot.snapshot_id}`,
    ));
    onSnapshotNavigate(snapshot.snapshot_id);
  }

  async function saveDecision(clear = false) {
    const evidence = visibleSelectedSnapshot
      ? {
        candidateId: visibleSelectedSnapshot.candidate_id,
        snapshotId: visibleSelectedSnapshot.id,
      }
      : visibleSelectedChainSnapshot
        ? {
          candidateId: visibleSelectedChainSnapshot.identity.candidate_id,
          snapshotId: visibleSelectedChainSnapshot.snapshot_id,
        }
        : null;
    if (!evidence) return;
    if (!clear && !decisionNote.trim()) {
      setDecisionError("採用判断には理由を入力してください。入力内容は保持しています。");
      return;
    }
    if (decisionPending) return;
    const requestProjectId = activeProjectId;
    const requestGeneration = projectRequestGenerationRef.current.generation;
    setDecisionPending(true);
    setDecisionError("");
    try {
      const saved = await workbenchApi.updateProjectDecision(requestProjectId, clear ? { candidate_id: "", snapshot_id: "", note: "" } : {
        candidate_id: evidence.candidateId,
        snapshot_id: evidence.snapshotId,
        note: decisionNote.trim(),
      });
      if (!isCurrentProjectRequest(requestProjectId, requestGeneration)) return;
      setProject(saved);
      onProjectChanged(saved);
      void reloadHistory(undefined, requestProjectId).catch(() => {
        if (isCurrentProjectRequest(requestProjectId, requestGeneration)) {
          setDecisionError("採用判断は保存しましたが、判断履歴を更新できませんでした。履歴だけを再試行してください。");
        }
      });
    } catch {
      if (isCurrentProjectRequest(requestProjectId, requestGeneration)) {
        setDecisionError("採用判断を保存できませんでした。入力内容は保持しています。同じ操作で再試行できます。");
      }
    } finally {
      if (isCurrentProjectRequest(requestProjectId, requestGeneration)) setDecisionPending(false);
    }
  }

  async function restoreSnapshot(snapshotId: string) {
    const requestProjectId = activeProjectId;
    try {
      const restored = await workbenchApi.restoreSnapshot(requestProjectId, snapshotId);
      if (activeProjectRef.current !== requestProjectId) return;
      onRestore(fromApiCandidate(restored));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存済み予測から候補を複製できませんでした。");
    }
  }

  const targetValues = (project?.target_values ?? {}) as Record<string, TargetGoal>;
  const savedTargetValues = (projects.find((item) => item.id === activeProjectId)?.target_values ?? {}) as Record<string, TargetGoal>;
  const configurableOutputs = effectiveTaskDefinition?.outputs ?? [];
  const configuredTargets = configurableOutputs.filter(
    (output) => hasValidTargetGoal(savedTargetValues[output.key]),
  );
  const invalidTargetRange = Object.values(targetValues).some((goal) => isTargetRange(goal) && !hasValidTargetGoal(goal))
    || configurableOutputs.some((output) => (
      output.goal_direction === "target"
      && typeof targetValues[output.key] === "number"
    ));
  const setScalarTarget = (key: string, value: string) => {
    if (!project) return;
    targetDraftDirtyRef.current = true;
    const next = { ...targetValues };
    if (value === "") delete next[key]; else next[key] = Number(value);
    setProject({ ...project, target_values: next });
  };
  const setTargetMode = (key: string, mode: "directional" | "between") => {
    if (!project) return;
    targetDraftDirtyRef.current = true;
    const current = targetValues[key];
    const next = { ...targetValues };
    if (mode === "between") {
      const seed = typeof current === "number" ? current : undefined;
      next[key] = { lower: seed ?? Number.NaN, upper: Number.NaN };
    } else if (isTargetRange(current)) {
      const seed = Number.isFinite(current.lower) ? current.lower : Number.isFinite(current.upper) ? current.upper : undefined;
      if (seed == null) delete next[key]; else next[key] = seed;
    }
    setProject({ ...project, target_values: next });
  };
  const setRangeTarget = (key: string, bound: "lower" | "upper", value: string) => {
    if (!project) return;
    targetDraftDirtyRef.current = true;
    const current = targetValues[key];
    const range = isTargetRange(current) ? current : { lower: Number.NaN, upper: Number.NaN };
    setProject({
      ...project,
      target_values: { ...targetValues, [key]: { ...range, [bound]: value === "" ? Number.NaN : Number(value) } },
    });
  };

  const resetCreateProjectForm = () => {
    setCreationError("");
    setCreating(false);
    setCreateMode("empty");
    setNewProjectName("");
    setNewTaskId("");
    setNewDatasetViewId("");
    setNewModelPackageRefId("");
    setNewChainId("");
    setNewChainRevisionId("");
    setNewProjectGroupChoice("none");
    setNewProjectSeriesId("");
    setNewProjectSeriesName("");
    setPredecessorProjectId("");
    setContinuationReason("");
    setPreparationReceipt(undefined);
    setManualBindingSelectionOpen(false);
  };

  const closeCreateProject = () => {
    setCreateOpen(false);
    resetCreateProjectForm();
  };

  const toggleCreateProject = () => {
    if (createOpen) {
      closeCreateProject();
      return;
    }
    focusCreationFormRef.current = true;
    resetCreateProjectForm();
    setCreateOpen(true);
  };

  const switchProject = (projectId: string) => {
    if (createOpen) closeCreateProject();
    onSwitch(projectId);
  };

  const continueCurrentProject = () => {
    if (!project) return;
    if (chainIdentity) {
      if (chainOperationsUnavailable || !fixedChainRevision) {
        setError("Chain Revisionの参照と利用状況を確認できるまで、続きは作成できません。");
        return;
      }
      const datasetViewId = fixedChainRevision.stages.find(
        (stage) => stage.dataset_view_revision_id,
      )?.dataset_view_revision_id;
      if (!datasetViewId) {
        setError("このChain Revisionには作成元Datasetの参照がありません。");
        return;
      }
      focusCreationFormRef.current = true;
      setCreateOpen(true);
      setCreateMode("empty");
      setNewProjectName(`${project.name} 続き`);
      setNewDatasetViewId(datasetViewId);
      setNewTaskId("");
      setNewModelPackageRefId("");
      setNewChainId(
        fixedChain && isExecutableChainDefinition(fixedChain.definition)
          ? fixedChain.definition.chain_id
          : "",
      );
      setNewChainRevisionId(chainIdentity.chain_revision_id);
      setNewProjectGroupChoice(project.project_series_id ? "existing" : "none");
      setNewProjectSeriesId(project.project_series_id ?? "");
      setNewProjectSeriesName("");
      setPredecessorProjectId(project.id);
      setContinuationReason("");
      return;
    }
    if (!project.dataset_view_revision_id || !project.model_package_ref_id) {
      setError("このプロジェクトは固定参照が不足しているため、続きとして作成できません。ワークスペースで参照状態を確認してください。");
      return;
    }
    focusCreationFormRef.current = true;
    setCreateOpen(true);
    setCreateMode("empty");
    setNewProjectName(`${project.name} 続き`);
    setNewDatasetViewId(project.dataset_view_revision_id ?? "");
    setNewTaskId(project.task_id);
    setNewModelPackageRefId(project.model_package_ref_id ?? "");
    setNewProjectGroupChoice(project.project_series_id ? "existing" : "none");
    setNewProjectSeriesId(project.project_series_id ?? "");
    setNewProjectSeriesName("");
    setPredecessorProjectId(project.id);
    setContinuationReason("");
  };

  const canArchiveProject = project != null
    && !["default", "hot-rolling-default"].includes(project.id);

  async function archiveCurrentProject() {
    if (!project || !canArchiveProject || archiving) return;
    setArchiving(true);
    setArchiveCommandError("");
    const archived = await onProjectArchived(project.id);
    setArchiving(false);
    if (archived) {
      setArchiveOpen(false);
    } else {
      setArchiveCommandError("Projectをアーカイブできませんでした。Projectと保存済み証拠は変更されていません。同じボタンで再試行できます。");
    }
  }

  async function restoreArchivedProject(projectId: string) {
    if (restoringProjectId) return;
    setRestoringProjectId(projectId);
    setArchiveRestoreError("");
    const restored = await onProjectRestored(projectId);
    if (restored) {
      setArchivedProjects((items) => items.filter((item) => item.id !== projectId));
    } else {
      setArchiveRestoreError("Archived Projectを復元できませんでした。保存済み証拠は変更されていません。同じProjectの復元を再試行できます。");
    }
    setRestoringProjectId("");
  }

  async function restoreArchivedCandidate(candidateId: string) {
    if (restoringCandidateId) return;
    setRestoringCandidateId(candidateId);
    setError("");
    try {
      const restored = await workbenchApi.restoreCandidate(activeProjectId, candidateId);
      onRestore(fromApiCandidate(restored));
      await reloadHistory(undefined, activeProjectId);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "候補を復元できませんでした。");
    } finally {
      setRestoringCandidateId("");
    }
  }

  const renderProjectListItem = (item: ApiProject) => (
    <button
      type="button"
      key={item.id}
      className={item.id === activeProjectId ? "project-list-item active" : "project-list-item"}
      aria-current={item.id === activeProjectId ? "page" : undefined}
      onClick={() => switchProject(item.id)}
    >
      <span className="project-list-name"><strong>{item.name}</strong></span>
      <small>{(() => {
        const itemIdentity = item.scientific_identity;
        if (itemIdentity?.identity_kind === "chain") {
          const revision = chainTemplates
            .flatMap((template) => template.revisions.map((value) => ({ template, revision: value })))
            .find(({ revision }) => (
              isExecutableChainRevision(revision)
              && `${revision.chain_id}:r${revision.revision}` === itemIdentity.chain_revision_id
            ));
          return revision
            ? `${revision.template.definition.label} · r${revision.revision.revision} · ${revision.revision.stages.map((stage) => stage.stage_id).join(" → ")}`
            : `Chain Revision · ${itemIdentity.chain_revision_id}`;
        }
        const datasetLabel = datasetByView.get(item.dataset_view_revision_id ?? "")
          ?.data_asset.original_filename.replace(/\.xlsx$/i, "") ?? "Dataset未解決";
        return `${datasetLabel} · ${taskLabels.get(item.task_id) ?? item.task_id}`;
      })()}</small>
    </button>
  );
  const currentOverviewState = overviewResourceState.scope === overviewScope
    ? overviewResourceState
    : initialProjectResourceState(overviewScope);
  const chainEvaluationScope = `${activeProjectId}:${chainIdentity?.chain_revision_id ?? ""}`;
  const currentChainEvaluationState = chainEvaluationResourceState.scope === chainEvaluationScope
    ? chainEvaluationResourceState
    : initialProjectResourceState(chainEvaluationScope);
  const snapshotScope = `${activeProjectId}:${chainIdentity ? "chain" : "single"}:${requestedSnapshotId ?? ""}`;
  const currentSnapshotState = snapshotResourceState.scope === snapshotScope
    ? snapshotResourceState
    : initialProjectResourceState(snapshotScope);
  const visibleSelectedSnapshot = selectedSnapshot?.id === requestedSnapshotId
    ? selectedSnapshot
    : null;
  const visibleSelectedChainSnapshot =
    selectedChainSnapshot?.snapshot_id === requestedSnapshotId
      ? selectedChainSnapshot
      : null;

  return (
    <div className="page-panel project-hub">
      <aside className="project-list-panel" aria-label="プロジェクト一覧">
        <div className="project-list-heading">
          <div><span className="overline">WORKSPACES</span><h2>プロジェクト</h2></div>
          <small>{projects.length}件</small>
        </div>
        <div className="project-list-items">{projectGroups.map((group) => {
          if (group.projects.length === 1 && group.kind !== "samples") {
            return <section className="project-list-group singleton" key={group.id}>{renderProjectListItem(group.projects[0])}</section>;
          }
          const collapsed = collapsedSeriesIds.has(group.id);
          const contentId = `project-series-${group.id}`;
          return (
            <section className={`project-list-group${group.kind === "samples" ? " bundled-samples" : ""}${collapsed ? " collapsed" : ""}`} key={group.id}>
              <header>
                <button
                  type="button"
                  className="project-list-group-toggle"
                  aria-expanded={!collapsed}
                  aria-controls={contentId}
                  onClick={() => setCollapsedSeriesIds((current) => {
                    const next = new Set(current);
                    if (next.has(group.id)) next.delete(group.id);
                    else next.add(group.id);
                    return next;
                  })}
                >
                  <span><small>{group.kind === "samples" ? "STARTER PROJECTS" : "検討グループ"}</small><strong>{group.name}</strong></span>
                  <em>{group.projects.length}件</em>
                  <i aria-hidden="true" />
                </button>
              </header>
              <div className="project-list-group-projects" id={contentId} hidden={collapsed}>
                {group.projects.map(renderProjectListItem)}
              </div>
            </section>
          );
        })}</div>
        {gallerySamples.length > 0 && <button
          type="button"
          className="sample-gallery-open"
          onClick={() => setSampleGalleryOpen(true)}
        ><span>サンプルから始める</span><small>問い・来歴・使える機能で選ぶ</small></button>}
        {legacySamples.length > 0 && <details className="sample-gallery-legacy-list">
          <summary>以前の同梱サンプル <span>{legacySamples.length}件</span></summary>
          <div>{legacySamples.map((item) => <div className="sample-gallery-item" key={item.project_id}>
            <span><strong>{item.name}</strong><small>{item.remove_blocked_reason || "現在は新規追加できません"}</small></span>
            <button
              type="button"
              className="outline-button"
              disabled={offline || Boolean(removingSampleId) || !item.removable}
              title={item.remove_blocked_reason || undefined}
              onClick={() => void removeSample(item.project_id)}
            >{removingSampleId === item.project_id ? "処理中…" : "取り除く"}</button>
          </div>)}</div>
        </details>}
        <ProjectResourceRecovery
          state={archiveListResourceState}
          label="アーカイブ一覧"
          retained="アーカイブ済みProject"
          onRetry={() => setArchiveListRevision((value) => value + 1)}
          showReadyAction
        />
        {archiveRestoreError && <p className="panel-error" role="alert">{archiveRestoreError}</p>}
        {archivedProjects.length > 0 && <details className="archived-project-list">
          <summary>アーカイブ済み <span>{archivedProjects.length}件</span></summary>
          <div>{archivedProjects.map((item) => <div className="archived-project-item" key={item.id}>
            <span><strong>{item.name}</strong><small>{item.archived_at ? formatDate(item.archived_at) : ""}</small></span>
            <button type="button" className="outline-button" disabled={offline || Boolean(restoringProjectId)} onClick={() => void restoreArchivedProject(item.id)}>
              {restoringProjectId === item.id ? "復元中…" : "復元"}
            </button>
          </div>)}</div>
        </details>}
        <button type="button" className="outline-button project-list-create" disabled={createOpen || offline} onClick={toggleCreateProject}>＋ 新規プロジェクト</button>
      </aside>
      <div className="project-hub-content">
        {sampleGalleryOpen && <section className="sample-gallery-panel" aria-label="同梱サンプルを選ぶ">
          <header className="panel-title">
            <div><span className="overline">SAMPLE GALLERY</span><h2>試したい問いからサンプルを選ぶ</h2></div>
            <button type="button" className="outline-button" onClick={() => setSampleGalleryOpen(false)}>戻る</button>
          </header>
          <p className="sample-gallery-intro">予測モデル名ではなく、問い・データの来歴・利用できる判断支援を先に確認します。予測は実測値や実利用の保証ではありません。</p>
          {uninstalledSamples.length > 1 && <button
            type="button"
            className="outline-button sample-gallery-add-all"
            disabled={offline || Boolean(installingSampleId) || Boolean(removingSampleId) || !uninstalledSamples.some((item) => item.available)}
            onClick={() => void installSamples([])}
          >{installingSampleId === "all" ? "追加中…" : `未追加の${uninstalledSamples.length}件を一括追加`}</button>}
          <div className="sample-gallery-cards">
            {gallerySamples.map((item) => <article className="sample-gallery-card" key={item.project_id}>
              <header><span className={`sample-source-kind ${item.source_kind}`}>{sampleSourceKindLabel(item.source_kind)}</span><span>{item.domain}</span></header>
              <h3>{item.name}</h3>
              <p className="sample-gallery-question">{item.question}</p>
              <p>{item.scenario_summary}</p>
              <dl className="sample-gallery-facts">
                <div><dt>データの形</dt><dd>{item.data_shape}</dd></div>
                <div><dt>主な出力</dt><dd>{(item.outputs ?? []).map((output) => `${output.label}${output.unit ? `（${output.unit}）` : ""}`).join(" / ")}</dd></div>
                <div><dt>使える判断支援</dt><dd className="sample-capability-list">{(item.capabilities ?? []).filter((capability) => capability.available).map((capability) => <span key={capability.id}>{capability.label}</span>)}</dd></div>
              </dl>
              <details className="sample-gallery-details">
                <summary>来歴・利用条件・限界を確認</summary>
                <dl>
                  <div><dt>出典</dt><dd>{item.source_url.startsWith("http") ? <a href={item.source_url} target="_blank" rel="noreferrer">{item.source_label}</a> : item.source_label}</dd></div>
                  <div><dt>利用条件</dt><dd>{item.license}</dd></div>
                  <div><dt>引用</dt><dd>{item.citation}</dd></div>
                  <div><dt>データ概要</dt><dd>{item.record_summary}</dd></div>
                  <div className="sample-limitations"><dt>既知の限界</dt><dd>{item.limitations}</dd></div>
                  <div><dt>利用不可の機能</dt><dd>{(item.capabilities ?? []).filter((capability) => !capability.available).map((capability) => `${capability.label}（${capability.unavailable_reason}）`).join(" / ") || "ありません"}</dd></div>
                  {item.documentation_path && <div><dt>関連資料</dt><dd>{item.documentation_path}</dd></div>}
                </dl>
                <details className="sample-gallery-identity"><summary>技術的な識別情報</summary><dl><div><dt>Task</dt><dd>{item.task_id}</dd></div><div><dt>Package</dt><dd>{item.package_id || "現在利用不可"}</dd></div><div><dt>Package digest</dt><dd>{item.package_manifest_digest || "現在利用不可"}</dd></div></dl></details>
              </details>
              {item.installed ? <div className="sample-gallery-card-actions"><button
                type="button"
                className="outline-button"
                onClick={() => { setSampleGalleryOpen(false); onSwitch(item.project_id); }}
              >このサンプルを開く</button><button
                type="button"
                className="outline-button"
                disabled={offline || Boolean(removingSampleId) || !item.removable}
                title={item.remove_blocked_reason || undefined}
                onClick={() => void removeSample(item.project_id)}
              >{removingSampleId === item.project_id ? "処理中…" : "取り除く"}</button></div> : <button
                type="button"
                className="primary-button"
                disabled={offline || Boolean(installingSampleId) || Boolean(removingSampleId) || !item.available}
                title={item.unavailable_reason || undefined}
                onClick={() => void installSamples([item.project_id])}
              >{installingSampleId === item.project_id ? "追加中…" : "このサンプルで始める"}</button>}
            </article>)}
          </div>
        </section>}
        {surface === "overview" ? <div className="page-intro project-hub-header">
          <div>
            <span className="overline">PROJECT OVERVIEW</span>
            <div className="project-inline-name">
              <h2>
                <span className="sr-only">{projectNameDraft || "プロジェクト"}</span>
                <label>
                  <span className="sr-only">プロジェクト名</span>
                <input
                  aria-label="プロジェクト名"
                  value={projectNameDraft}
                  disabled={!project || offline || settingsPending}
                  onChange={(event) => {
                    projectNameDirtyRef.current = true;
                    setSettingsErrors((current) => ({ ...current, "project-name": "" }));
                    setProjectNameDraft(event.target.value);
                  }}
                />
                </label>
              </h2>
              {project?.starter && <span className="starter-project-badge">同梱サンプル</span>}
              {project && <button
                type="button"
                className="outline-button"
                disabled={
                  !projectNameDraft.trim()
                  || projectNameDraft === persistedProject?.name
                  || offline
                  || settingsPending
                }
                 onClick={() => persistedProject && void saveProject(
                   { ...persistedProject, name: projectNameDraft.trim() },
                   { syncNameDraft: true, resource: "project-name" },
                 )}
               >{settingsPendingResource === "project-name" ? "保存中…" : "名前を保存"}</button>}
               <span className="project-name-save-state" role="status">
                 {settingsPendingResource === "project-name" ? "保存中" : ""}
               </span>
               {settingsErrors["project-name"] && (
                 <small className="panel-error" role="alert">{settingsErrors["project-name"]}</small>
               )}
            </div>
          </div>
          <div className="project-actions">
            <button
              className="outline-button"
              disabled={offline
                || taskUnavailable
                || Boolean(predictionGraphIdentity)
                || chainOperationsUnavailable}
              onClick={continueCurrentProject}
            >このプロジェクトの続き</button>
          </div>
        </div> : <div className="page-intro project-settings-header">
          <div>
            <span className="overline">PROJECT SETTINGS</span>
            <h2>{project?.name ?? "プロジェクト"}の設定</h2>
            <p>通常の編集、科学的な条件、固定した証拠を分けて確認します。</p>
          </div>
        </div>}
      {surface === "overview" && project?.starter && <section className="starter-project-notice" aria-label="同梱サンプルの案内">
        <div><strong>これは動作確認用の同梱サンプルです</strong><span>自分のExcelから始めるときは、データを登録して新しいプロジェクトを作成します。</span></div>
        <button type="button" className="outline-button" onClick={() => onNavigate("data-library")}>自分のデータで始める</button>
      </section>}
      {taskUnavailable && <section className="task-unavailable-banner" role="status">
        <strong>この予測タスクは一時的に利用できません</strong>
        <span>{taskAvailability.message}</span>
        <small>保存済みの候補・予測・実測・判断履歴は参照できます。推論と変更操作は停止しています。</small>
      </section>}
      {predictionGraphIdentity && <section className="task-unavailable-banner chain-ready-banner" role="status">
        <strong>Prediction Graph Revisionを固定したProjectです</strong>
        <span>{predictionGraphIdentity.graph_revision_id} を判断根拠として読み込みました。</span>
        <small>Graph実行Workbenchは未対応です。単一Task向け画面を代用せず、概要と固定参照だけを表示します。</small>
      </section>}
      {chainIdentity && <section className="task-unavailable-banner chain-ready-banner" role="status">
        <strong>Chain Revisionを固定したプロジェクトです</strong>
        <span>{fixedStagePath}の段別鮮度と中間実測を、候補作業面で確認できます。</span>
        <small>このモードでは範囲探索とデータ探索を利用できません。下の「次の作業」から候補作業面へ進みます。</small>
      </section>}
      {chainIdentity && chainSubsystem?.status === "unavailable" && (
        <section className="task-unavailable-banner" role="status">
          <strong>{chainSubsystem.message}</strong>
          <span>{chainSubsystem.impact}</span>
          <small>原因: {chainSubsystem.cause}</small>
          <small>復旧: {chainSubsystem.recovery_hint}</small>
        </section>
      )}
      {chainIdentity && subsystemAvailabilityError && (
        <section className="task-unavailable-banner" role="alert">
          <strong>Chainの利用状況を取得できません</strong>
          <span>利用可否を確認できないため、Chainの実行と続きの作成を停止しています。</span>
          <small>API接続を確認して再読み込みしてください。</small>
        </section>
      )}
      {chainIdentity
        && (!subsystemAvailabilityLoaded || !fixedChainRevision)
        && !subsystemAvailabilityError && (
        <section className="task-unavailable-banner" role="status">
          <strong>Chainの利用状況を確認しています</strong>
          <span>利用可否が確定するまで、Chainの実行と続きの作成を停止しています。</span>
        </section>
      )}
      {surface === "overview" && (
        <ProjectResourceRecovery
          state={currentOverviewState}
          label="Project参照情報"
          retained="固定参照・作成条件"
          onRetry={() => setOverviewRevision((value) => value + 1)}
          showReadyAction
        />
      )}
      {error && <p className="panel-error" role="alert">{error}</p>}
      {surface === "overview" && unresolvedReferences.length > 0 && <section className="project-reference-warning" role="alert">
        <div>
          <strong>固定参照を確認できません</strong>
          <span>{unresolvedReferences.join("、")}が未解決です。保存済みの履歴は読めますが、新しい計算は利用できません。</span>
        </div>
        <button type="button" className="outline-button" onClick={() => onOpenSettings("evidence")}>証拠・管理を確認</button>
      </section>}
      {surface === "overview" && <section className="project-next-actions">
        <div className="panel-title"><h3>次の作業</h3><span>{activeCandidates.length ? `${activeCandidates.length}候補を検討中` : "まだ候補がありません"}</span></div>
        {predictionGraphIdentity
          ? <div className="project-action-grid">
            <button className="project-action-card" disabled><strong>Graph候補の実行</strong><span>このProject surfaceでは現在利用できません</span></button>
          </div>
          : chainIdentity
          ? <div className="project-action-grid">
            <button className="project-action-card primary" disabled={projectOperationDisabled({ operation: "prediction", offline, pending: chainExecutionPending, subsystemUnavailable: chainOperationsUnavailable })} onClick={() => onNavigate("candidates")}><strong>Chain候補を開く</strong><span>条件を編集し、{fixedStagePath}を実行して固定します</span></button>
          </div>
          : <div className="project-action-groups">
            <section><h4>候補を作る</h4><div className="project-action-grid">
              <button className="project-action-card" disabled={actionBlocked || !supportsLineageCandidate} onClick={() => onNavigate("lineage")}><strong>過去データから候補を探す</strong><span>{supportsLineageCandidate ? "既存の条件と問題から出発する" : "この予測タスクでは利用できません"}</span></button>
              <button className="project-action-card" disabled={actionBlocked} onClick={() => onNavigate("candidates")}><strong>具体的な候補を入力する</strong><span>入力条件が決まっている案を追加する</span></button>
              <button className="project-action-card" disabled={actionBlocked} onClick={() => onNavigate("explore")}><strong>条件範囲から候補を探す</strong><span>入力範囲を動かして候補を生成する</span></button>
            </div></section>
            <section><h4>候補を確かめる</h4><div className="project-action-grid">
              {candidateQuestionActions.map((item) => <button
                type="button"
                className="project-action-card"
                key={item.activityId}
                disabled={questionState.disabled}
                onClick={() => onNavigate("candidate-review", actionCandidateId, { activityId: item.activityId })}
              ><strong>{item.title}</strong><span>{questionState.reason ?? item.description}</span></button>)}
            </div></section>
            <section><h4>結果を残す</h4><div className="project-action-grid">
              <button type="button" className="project-action-card" disabled={questionState.disabled || !operations?.actual_measurement} onClick={() => onNavigate("candidates", actionCandidateId, { candidateSection: "actuals" })}><strong>実測を記録する</strong><span>{questionState.reason ?? (operations?.actual_measurement ? "選択候補の予測と実測を結び付ける" : "この予測タスクでは利用できません")}</span></button>
              <button type="button" className="project-action-card" onClick={() => document.getElementById("project-candidate-history")?.scrollIntoView({ behavior: "smooth", block: "start" })}><strong>判断履歴を見る</strong><span>固定した予測・実測・採用理由を時系列で確認する</span></button>
            </div></section>
          </div>}
      </section>}
      {surface === "overview" && project && configurableOutputs.length > 0 && <section className={`project-goal-strip${configuredTargets.length ? "" : " unset"}`} aria-label="プロジェクトの目標値">
        <div className="project-goal-heading"><span>目標値</span><strong>{configuredTargets.length ? "候補を判断する基準" : "候補を探す前に設定"}</strong></div>
        {configuredTargets.length
          ? <>
              <div className="project-goal-values">
                {configuredTargets.map((output) => <span key={output.key}><b>{output.label}</b>{targetGoalText(savedTargetValues[output.key], output.goal_direction, formatNumber)} {output.unit}</span>)}
              </div>
              <button className="outline-button" disabled={taskUnavailable || chainExecutionPending || offline} onClick={focusTargetSettings}>目標値を変更</button>
            </>
          : <div className="project-goal-initial-editor">
              <fieldset className="target-grid" disabled={taskUnavailable || chainExecutionPending || offline || settingsPending}>
                <legend className="sr-only">目標値を設定</legend>
                {configurableOutputs.map((output) => {
                  const goal = targetValues[output.key];
                  const range = isTargetRange(goal) ? goal : undefined;
                  const rangeOnly = output.goal_direction === "target";
                  const showRange = rangeOnly || range != null;
                  const rangeDraft = range ?? { lower: Number.NaN, upper: Number.NaN };
                  return <div className="target-setting" key={output.key}>
                    <label>{output.label}
                      <select
                        disabled={rangeOnly}
                        value={showRange ? "between" : "directional"}
                        onChange={(event) => setTargetMode(output.key, event.target.value as "directional" | "between")}
                      >
                        <option value="directional">{defaultGoalLabel(output.goal_direction)}</option>
                        <option value="between">範囲内</option>
                      </select>
                    </label>
                    {showRange
                      ? <div className="target-range-inputs">
                          <label>下限<input aria-label={`${output.label}の下限`} type="number" value={Number.isFinite(rangeDraft.lower) ? rangeDraft.lower : ""} placeholder="下限" onChange={(event) => setRangeTarget(output.key, "lower", event.target.value)} /></label>
                          <span>–</span>
                          <label>上限<input aria-label={`${output.label}の上限`} type="number" value={Number.isFinite(rangeDraft.upper) ? rangeDraft.upper : ""} placeholder="上限" onChange={(event) => setRangeTarget(output.key, "upper", event.target.value)} /></label>
                        </div>
                      : <input type="number" aria-label={`${output.label}の目標値`} value={typeof goal === "number" ? goal : ""} placeholder="未設定" onChange={(event) => setScalarTarget(output.key, event.target.value)} />}
                    {output.unit && <small className="target-setting-unit">{output.unit}</small>}
                  </div>;
                })}
                {invalidTargetRange && <small className="target-range-error">範囲目標は、下限を上限より小さく設定してください。</small>}
              </fieldset>
              <div className="project-goal-initial-actions">
                <small>保存すると判断基準（Objective）の版を作成します。</small>
                <button className="primary-button" disabled={settingsPending || invalidTargetRange || !Object.keys(targetValues).length || taskUnavailable || offline} onClick={() => void saveProject(project, { syncTargetDraft: true })}>
                  {settingsPending ? "保存中…" : "目標値を保存"}
                </button>
              </div>
            </div>}
      </section>}
      {surface === "settings" && <nav className="project-settings-category-nav" aria-label="Project設定カテゴリ">
        <button type="button" className={effectiveSettingsCategory === "general" ? "active" : ""} aria-current={effectiveSettingsCategory === "general" ? "page" : undefined} onClick={() => onOpenSettings("general")}>通常設定</button>
        {!chainIdentity && !predictionGraphIdentity && <button type="button" className={effectiveSettingsCategory === "scientific" ? "active" : ""} aria-current={effectiveSettingsCategory === "scientific" ? "page" : undefined} onClick={() => onOpenSettings("scientific")}>科学設定</button>}
        <button type="button" className={effectiveSettingsCategory === "evidence" ? "active" : ""} aria-current={effectiveSettingsCategory === "evidence" ? "page" : undefined} onClick={() => onOpenSettings("evidence")}>証拠・管理</button>
      </nav>}
      {surface === "settings" && effectiveSettingsCategory === "evidence" && project && <details className="project-reference-details" open>
        <summary><span>固定参照・再現性</span><small>使用中のデータ・予測方法</small></summary>
        {predictionGraphIdentity
          ? <section className="project-reference-strip" aria-label="プロジェクトのPrediction Graph参照">
            <div><span>参照Graph</span><strong>{predictionGraphIdentity.graph_revision_id}</strong><small>公開済みRevisionへ固定</small></div>
            <div><span>Project binding</span><strong>r{predictionGraphIdentity.project_binding.revision}</strong><small>Project固有の入力値</small></div>
            <ReferenceIdentityDetails items={[
              ["Graph Revision", predictionGraphIdentity.graph_revision_digest],
            ]} />
          </section>
          : chainIdentity
          ? <section className="project-reference-strip" aria-label="プロジェクトのChain参照と所属">
            <div><span>参照Chain</span><strong>{fixedChain?.definition.label ?? "Chain未解決"}</strong><small>{fixedStagePath}</small></div>
            <div><span>固定した版</span><strong>{fixedChainRevision ? `r${fixedChainRevision.revision}` : "—"}</strong><small>全Stageの参照をこの版に固定</small></div>
            <div><span>固定Stage</span><strong>{fixedChainRevision?.stages.map((stage) => stage.stage_id).join(" → ") ?? "—"}</strong><small>Package・データセット・プロファイルを版の中に固定</small></div>
            {showActiveSeriesMembership && <div><span>検討グループ</span><strong>{fixedSeries?.name}</strong><small>{fixedSeriesProjectCount}件の検討をまとめています</small></div>}
            <ReferenceIdentityDetails items={[["Chain Revision", chainIdentity.chain_revision_digest]]} />
          </section>
          : <section className="project-reference-strip" aria-label="プロジェクトの参照と所属">
            <div><span>参照データセット</span><strong title={fixedDataset?.data_asset.original_filename ?? project.dataset_view_revision_id ?? undefined}>{fixedDataset?.data_asset.original_filename ?? unresolvedReferenceLabel("Dataset View", project.dataset_view_revision_id)}</strong><small>{fixedDataset ? `${fixedDataset.profile_revision.name} · r${fixedDataset.profile_revision.revision}` : project.dataset_view_revision_id ?? "固定参照なし"}</small></div>
            <div><span>予測タスク</span><strong>{taskLabels.get(project.task_id) ?? project.task_id}</strong><small>固定</small></div>
            <div><span>予測モデル</span><strong title={fixedPackage?.package_id ?? project.model_package_ref_id ?? undefined}>{fixedPackage ? modelPackageDisplayName(fixedPackage) : unresolvedReferenceLabel("Model Package", project.model_package_ref_id)}</strong><small title={fixedTrainingDataset ? datasetDisplayName(fixedTrainingDataset) : project.model_package_manifest_digest || undefined}>{fixedPackage ? `学習元: ${fixedTrainingDataset ? datasetDisplayName(fixedTrainingDataset) : "未登録または記録なし"}` : project.model_package_manifest_digest ? `manifest: ${project.model_package_manifest_digest}` : "manifest digestの記録なし"}</small></div>
            <div><span>探索範囲（Design Space）</span><strong>{project.design_space ? `${project.design_space.name} · r${project.design_space.revision}` : "この検討では未設定"}</strong><small>{project.design_space ? bindingProvenanceLabel(project.design_space_binding_provenance, "Taskの許容範囲から生成") : "保存済みの探索結果はそのまま参照できます"}</small></div>
            <div><span>判断基準（Objective）</span><strong>{project.objective_definition ? `${project.objective_definition.name} · r${project.objective_definition.revision}` : "この検討では未設定"}</strong><small>{project.objective_definition ? bindingProvenanceLabel(project.objective_binding_provenance, "プロジェクト目標から生成") : "探索を実行すると、その時点の判断基準を固定します"}</small></div>
            {showActiveSeriesMembership && <div><span>検討グループ</span><strong>{fixedSeries?.name}</strong><small>{fixedSeriesProjectCount}件の検討をまとめています</small></div>}
            <ReferenceIdentityDetails items={[
              ["Dataset View Revision", project.dataset_view_revision_id],
              ["Model Package Ref", project.model_package_ref_id],
              ["Model Package manifest", project.model_package_manifest_digest],
              ["Task contract", project.task_contract_digest],
              ["Design Space", project.design_space_digest],
              ["Objective", project.objective_definition_digest],
            ]} />
          </section>}
      </details>}
      {surface === "overview" && chainIdentity && (
        <>
          <ProjectResourceRecovery
            state={currentChainEvaluationState}
            label="Chain評価"
            retained="取得済みのChain評価"
            onRetry={() => setChainEvaluationRevision((value) => value + 1)}
            showReadyAction
          />
          {currentChainEvaluationState.unavailable
            && chainEvaluationSubsystem?.status === "unavailable" && (
            <section className="chain-evaluation-panel unavailable" role="status">
              <strong>{chainEvaluationSubsystem.message}</strong>
              <p>{chainEvaluationSubsystem.impact}</p>
              <small>原因: {chainEvaluationSubsystem.cause}</small>
              <small>復旧: {chainEvaluationSubsystem.recovery_hint}</small>
            </section>
          )}
          {chainEvaluation?.projectId === activeProjectId
            && currentChainEvaluationState.loadedAt && (
            <ChainEvaluationPanel evaluation={chainEvaluation.value} stagePath={fixedStagePath} />
          )}
        </>
      )}
      {surface === "overview" && predecessorProject && <section className="project-continuation-link" aria-label="このプロジェクトの続き元"><span>続き元</span><button type="button" onClick={() => onSwitch(predecessorProject.id)}>{predecessorProject.name}</button><small>{predecessorSeries?.name ?? "グループなし"}{project?.continuation_reason ? ` · ${project.continuation_reason}` : ""}</small></section>}

      {surface === "overview" && <ProjectCreationPanel
        open={createOpen}
        loading={creating}
        disabled={offline}
        error={creationError}
        preparedBindingReview={preparedBindingReview}
        manualBindingSelectionOpen={manualBindingSelectionOpen}
        projectNameInputRef={projectNameInputRef}
        projectName={newProjectName}
        datasetViewId={newDatasetViewId}
        predictionConfiguration={createMode === "copy"
          ? `task:${copyTaskId ?? ""}`
          : newChainId
            ? `chain:${newChainId}`
            : newTaskId
              ? `task:${newTaskId}`
              : ""}
        chainId={newChainId}
        chainRevisionId={newChainRevisionId}
        modelPackageRefId={newModelPackageRefId}
        mode={createMode}
        groupChoice={newProjectGroupChoice}
        projectSeriesId={newProjectSeriesId}
        projectSeriesName={newProjectSeriesName}
        showContinuationReason={Boolean(predecessorProjectId)}
        continuationReason={continuationReason}
        copyTaskId={copyTaskId}
        copyDisabled={Boolean(newChainId) || taskUnavailable || !candidate || Boolean(predecessorProjectId)}
        copyDescription={newChainId
          ? "Chain Projectは空から開始します"
          : taskUnavailable
            ? "利用停止中のタスクからはコピーできません"
            : candidate
              ? `${candidate.label}（編集版 ${candidate.raw.revision}）`
              : "コピーできる候補がありません"}
        usedDatasetChoices={usedDatasetChoices}
        unusedDatasetChoices={unusedDatasetChoices}
        predictionConfigurationChoices={[
          ...catalog.filter((item) => availableTaskIds.includes(
            item.definition.task_definition.id,
          )).map((item) => ({
            id: `task:${item.definition.task_definition.id}`,
            label: `${item.definition.task_definition.label}（単一Task）`,
          })),
          ...availableChains.flatMap((item) => (
            isExecutableChainDefinition(item.definition)
              ? [{
                  id: `chain:${item.definition.chain_id}`,
                  label: `${item.definition.label}（Chain）`,
                }]
              : []
          )),
        ]}
        chainRevisionChoices={(selectedChain?.revisions ?? []).flatMap((revision) => (
          isExecutableChainRevision(revision)
            ? [{
                id: `${revision.chain_id}:r${revision.revision}`,
                label: `r${revision.revision} · ${revision.stages.map(
                  (stage) => stage.stage_id,
                ).join(" → ")}`,
              }]
            : []
        ))}
        modelPackageChoices={availablePackages.map((item) => ({
          id: item.id,
          label: availablePackageNames.get(item.id) ?? item.id,
        }))}
        activeProjectSeries={activeProjectSeries.map((series) => ({
          id: series.id,
          label: series.name,
        }))}
        modelPackage={selectedPackage}
        bindingSummary={{
          dataset: {
            label: selectedDatasetChoice
              ? `${selectedDatasetChoice.purposeLabel} — ${selectedDatasetChoice.sourceLabel}`
              : "選択してください",
            detail: selectedDatasetEvidence,
            detailTitle: selectedDatasetChoice?.projectNames.join("、") || undefined,
          },
          prediction: {
            label: newChainId
              ? selectedChain?.definition.label ?? "選択してください"
              : taskLabels.get(selectedTaskId) ?? (selectedTaskId || "選択してください"),
            detail: newChainId
              ? "再利用可能なStageをbindingで接続"
              : "Projectの予測目的",
          },
          package: {
            label: newChainId
              ? selectedChainRevision
                ? `r${selectedChainRevision.revision}`
                : "選択してください"
              : selectedPackage
                ? modelPackageDisplayName(selectedPackage)
                : "選択してください",
            detail: newChainId
              ? selectedChainRevision
                ? selectedChainRevision.stages.map(
                  (stage) => `${stage.stage_id}:${stage.package_manifest_digest.slice(7, 15)}`,
                ).join(" · ")
                : "Revisionを選択してください"
              : `学習元: ${selectedPackage
                ? selectedTrainingDataset
                  ? datasetDisplayName(selectedTrainingDataset)
                  : "未登録または記録なし"
                : "Model Packageを選択してください"}`,
          },
          group: {
            label: newProjectGroupChoice === "none"
              ? "グループなし"
              : (selectedSeries?.name ?? newProjectSeriesName.trim()) || "名前を入力してください",
            detail: newProjectGroupChoice === "none"
              ? "単独のプロジェクトとして作成"
              : selectedSeries
                ? "既存グループに追加"
                : "新しいグループを作成",
          },
        }}
        onClose={closeCreateProject}
        onProjectNameChange={setNewProjectName}
        onDatasetChange={(datasetViewId) => {
          setNewDatasetViewId(datasetViewId);
          setNewTaskId("");
          setNewModelPackageRefId("");
          setNewChainId("");
          setNewChainRevisionId("");
        }}
        onPredictionConfigurationChange={(selection) => {
          const [kind, id] = selection.split(":", 2);
          setNewTaskId(kind === "task" ? id : "");
          setNewModelPackageRefId("");
          setNewChainId(kind === "chain" ? id : "");
          setNewChainRevisionId("");
        }}
        onChainRevisionChange={setNewChainRevisionId}
        onModelPackageChange={setNewModelPackageRefId}
        onGroupChoiceChange={(choice) => {
          setNewProjectGroupChoice(choice);
          if (choice === "none") {
            setNewProjectSeriesId("");
            setNewProjectSeriesName("");
          } else if (choice === "existing") {
            setNewProjectSeriesName("");
          } else {
            setNewProjectSeriesId("");
          }
        }}
        onProjectSeriesChange={setNewProjectSeriesId}
        onProjectSeriesNameChange={setNewProjectSeriesName}
        onContinuationReasonChange={setContinuationReason}
        onModeChange={(mode) => {
          setCreateMode(mode);
          if (mode === "copy" && project) {
            setNewChainId("");
            setNewChainRevisionId("");
            setNewDatasetViewId(project.dataset_view_revision_id ?? "");
            setNewTaskId(project.task_id);
            setNewModelPackageRefId(project.model_package_ref_id ?? "");
          }
        }}
        onOpenManualBindingSelection={() => setManualBindingSelectionOpen(true)}
        onRestorePreparedBinding={() => {
          if (!preparationReceipt) return;
          setNewDatasetViewId(preparationReceipt.datasetViewId);
          setNewTaskId(preparationReceipt.taskId);
          setNewModelPackageRefId(preparationReceipt.modelPackageRefId);
          setNewChainId("");
          setNewChainRevisionId("");
          setCreateMode("empty");
          setNewProjectGroupChoice("none");
          setNewProjectSeriesId("");
          setNewProjectSeriesName("");
          setManualBindingSelectionOpen(false);
        }}
        onSubmit={createProject}
      />}

      {surface === "settings" && effectiveSettingsCategory === "general" && <ProjectSettingsPanel
        open
        project={project}
        loading={settingsPending}
        projectError={settingsErrors.project ?? ""}
        groupNameError={settingsErrors["group-name"] ?? ""}
        groupMembershipError={settingsErrors["group-membership"] ?? ""}
        disabled={projectOperationDisabled({
          operation: "metadata",
          offline,
          pending: settingsPending,
        }) || creationOptions === null}
        outputs={configurableOutputs}
        targetValues={targetValues}
        invalidTargetRange={invalidTargetRange}
        showActiveSeriesMembership={showActiveSeriesMembership}
        groupSettingsOpen={groupSettingsOpen}
        fixedSeries={fixedSeries}
        activeProjectSeries={activeProjectSeries}
        groupMembershipId={groupMembershipId}
        membershipChanged={membershipChanged}
        membershipTargetSeriesId={membershipTargetSeriesId}
        membershipEmptiesFixedSeries={membershipEmptiesFixedSeries}
        seriesName={seriesName}
        scientificSettings={undefined}
        onOpenGroupSettings={() => {
          setSeriesName(fixedSeries?.name ?? "");
          setGroupMembershipId(project?.project_series_id ?? "");
          setGroupSettingsOpen(true);
        }}
        onGroupMembershipChange={(value) => {
          setSettingsErrors((current) => ({ ...current, "group-membership": "" }));
          setGroupMembershipId(value);
        }}
        onMoveProjectToGroup={moveProjectToGroup}
        onSeriesNameChange={(value) => {
          setSettingsErrors((current) => ({ ...current, "group-name": "" }));
          setSeriesName(value);
        }}
        onSaveSeriesName={saveSeriesName}
        onProjectChange={(value) => {
          setSettingsErrors((current) => ({ ...current, project: "" }));
          setProject(value);
        }}
        onTargetModeChange={setTargetMode}
        onScalarTargetChange={setScalarTarget}
        onRangeTargetChange={setRangeTarget}
        onSave={() => saveProject(project, { syncTargetDraft: true, resource: "project" })}
      />}

      {surface === "settings" && effectiveSettingsCategory === "scientific" && !chainIdentity && project && renderScientificSettings?.(
        persistedProject ?? project,
        applyProjectSettingsResponse,
        offline || settingsPending,
      )}

      {surface === "overview" && <ProjectEvidenceHistoryList
        subtitle={chainIdentity ? "Chainの固定結果・実測分析・不確かさを時系列で表示" : "現在値と固定した予測を分けて表示"}
        loading={historyState === "loading"}
        error={historyState === "error"}
        emptyMessage={supportsLineageCandidate ? "候補はまだありません。上の「次の作業」から過去データを探すと、由来付き候補としてここに残ります。" : "候補はまだありません。上の「次の作業」から基準候補を用意し、検討を始めます。"}
        history={history}
        chainMode={Boolean(chainIdentity)}
        currentPreviews={currentPreviews}
        taskDefinition={effectiveTaskDefinition}
        displayDecimalOverrides={project?.display_decimals}
        disabled={Boolean(taskUnavailable || offline)}
        restoringCandidateId={restoringCandidateId}
        onRetry={retryHistory}
        onOpenCandidate={(candidateId) => onNavigate("candidates", candidateId)}
        onRestoreArchivedCandidate={restoreArchivedCandidate}
        onOpenSnapshot={openSnapshot}
        onRestoreSnapshot={restoreSnapshot}
        onOpenChainSnapshot={openChainSnapshot}
      />}

      {surface === "overview" && requestedSnapshotId && <ProjectResourceRecovery
        state={currentSnapshotState}
        label="Snapshot"
        retained="取得済みのSnapshot"
        onRetry={() => setSnapshotRevision((value) => value + 1)}
        showReadyAction
      />}

      {surface === "overview" && visibleSelectedSnapshot?.payload.prediction && <section className="snapshot-detail project-snapshot-detail">
        <div className="panel-title"><h3>固定した予測の詳細</h3><button className="outline-button" onClick={() => { setSelectedSnapshot(null); onSnapshotNavigate(undefined); }}>閉じる</button></div>
        <p>{formatDate(visibleSelectedSnapshot.created_at)} / {history?.candidates.find((item) => item.candidate.id === visibleSelectedSnapshot.candidate_id)?.candidate.name ?? "保存時の候補"}</p>
        <span className="decision-snapshot-badge">{!visibleSelectedSnapshot.payload.provenance?.package?.manifest_sha256 || !modelPackage ? "予測モデル情報を確認できません" : visibleSelectedSnapshot.payload.provenance.package.manifest_sha256 === modelPackage.manifest_sha256 ? "現在と同じ予測モデル" : "現在とは別の予測モデル"}</span>
        <table className="quality-table"><thead><tr><th>特性</th><th>固定予測</th><th>区間・分位</th><th>目標達成</th></tr></thead><tbody>{orderedPredictions(visibleSelectedSnapshot.payload.prediction.predictions).map(([key, value]) => { const assessment = assessPrediction(outputDefinition(key), value); return <tr className={assessment.implausible ? "implausible-output" : undefined} key={key}><th>{outputLabels.get(key) ?? key}{assessment.implausible && <small className="output-warning-badge">⚠ 物理範囲外</small>}</th><td title={assessment.warning ?? undefined}>{formatPredictionPoint(value, (numberValue) => formatOutputNumber(key, numberValue))}</td><td>{predictionHasInterval(value) ? <>{formatOutputNumber(key, value.lower)}–{formatOutputNumber(key, value.upper)} <small>{predictionIntervalLabel(value)}</small></> : "利用不可"}</td><td>{value.goal_probability == null ? value.goal_value == null ? "目標未設定" : "利用不可" : `${formatNumber(value.goal_probability * 100, 0)}%`}</td></tr>; })}</tbody></table>
        <div className="snapshot-decision-form">
          <label>判断理由<textarea disabled={taskUnavailable || offline || decisionPending} value={decisionNote} onChange={(event) => { decisionDraftRef.current.dirty = true; setDecisionError(""); setDecisionNote(event.target.value); }} placeholder="この時点の予測を採用判断に使う理由" /></label>
          <button className="outline-button" disabled={taskUnavailable || offline || decisionPending} onClick={() => void saveDecision(false)}>{decisionPending ? "保存中…" : "採用判断として固定"}</button>
          {project?.decision_snapshot_id === visibleSelectedSnapshot.id && <button className="outline-button" disabled={taskUnavailable || offline || decisionPending} onClick={() => void saveDecision(true)}>{decisionPending ? "保存中…" : "採用判断を解除"}</button>}
          {decisionError && <p className="panel-error" role="alert">{decisionError}</p>}
        </div>
        <CandidateAddButton disabled={taskUnavailable || offline} onClick={() => void restoreSnapshot(visibleSelectedSnapshot.id)}>この時点から新しい候補を作る</CandidateAddButton>
      </section>}

      {surface === "overview" && visibleSelectedChainSnapshot && (() => {
        const terminalStage = terminalChainStage(visibleSelectedChainSnapshot.stages);
        const predictions = chainStagePredictions(terminalStage);
        const selectedCandidateName = history?.candidates.find(
          (item) => item.candidate.id === visibleSelectedChainSnapshot.identity.candidate_id,
        )?.candidate.name ?? "保存時の候補";
        return <section className="snapshot-detail project-snapshot-detail chain-snapshot-detail">
          <div className="panel-title"><h3>全Stageを固定した詳細</h3><button className="outline-button" onClick={() => { setSelectedChainSnapshot(null); onSnapshotNavigate(undefined); }}>閉じる</button></div>
          <p>{formatDate(visibleSelectedChainSnapshot.created_at)} / {selectedCandidateName} / 編集版 {visibleSelectedChainSnapshot.identity.candidate_revision}</p>
          <div className="chain-fixed-stage-list" aria-label="固定したStage">
            {visibleSelectedChainSnapshot.stages.map((stage) => <span key={stage.stage_id}>Stage {stage.stage_id} · {stage.status === "latest" ? "固定済み" : stage.status}</span>)}
          </div>
          {terminalStage?.output_definitions.length
            ? <table className="quality-table"><thead><tr><th>終端Stageの特性</th><th>固定した予測</th><th>不確かさ</th></tr></thead><tbody>{terminalStage.output_definitions.map((definition) => {
              const prediction = predictions[definition.key];
              const unit = definition.unit.trim();
              return <tr key={definition.key}>
                <th>{definition.label}</th>
                <td>{formatChainOutput(prediction, definition)}</td>
                <td>{typeof prediction?.std === "number" && Number.isFinite(prediction.std)
                  ? `標準偏差 ±${formatNumberAtDecimals(prediction.std, definition.display_decimals)}${unit ? ` ${unit}` : ""}`
                  : typeof prediction?.lower === "number" && typeof prediction?.upper === "number"
                    ? `${formatNumberAtDecimals(prediction.lower, definition.display_decimals)}–${formatNumberAtDecimals(prediction.upper, definition.display_decimals)}${unit ? ` ${unit}` : ""}`
                    : "区間なし"}</td>
              </tr>;
            })}</tbody></table>
            : <p className="chain-output-unavailable">このSnapshotの終端Stage出力定義を確認できません。</p>}
          <div className="snapshot-decision-form">
            <label>判断理由<textarea disabled={taskUnavailable || offline || decisionPending} value={decisionNote} onChange={(event) => { decisionDraftRef.current.dirty = true; setDecisionError(""); setDecisionNote(event.target.value); }} placeholder="この時点のChain結果を採用判断に使う理由" /></label>
            <button className="outline-button" disabled={taskUnavailable || offline || decisionPending} onClick={() => void saveDecision(false)}>{decisionPending ? "保存中…" : "採用判断として固定"}</button>
            {project?.decision_snapshot_id === visibleSelectedChainSnapshot.snapshot_id && <button className="outline-button" disabled={taskUnavailable || offline || decisionPending} onClick={() => void saveDecision(true)}>{decisionPending ? "保存中…" : "採用判断を解除"}</button>}
            {decisionError && <p className="panel-error" role="alert">{decisionError}</p>}
          </div>
          <button className="outline-button" onClick={() => onNavigate("candidates", visibleSelectedChainSnapshot.identity.candidate_id)}>Chain候補を開く</button>
        </section>;
      })()}

      {surface === "settings" && settingsCategory === "evidence" && canArchiveProject && project && <section className="project-danger-zone" aria-label="プロジェクトのアーカイブ">
        {archiveCommandError && <p className="panel-error" role="alert">{archiveCommandError}</p>}
        {!archiveOpen ? <button className="danger-outline-button" disabled={offline} onClick={() => setArchiveOpen(true)}>プロジェクトをアーカイブ</button> : <div className="project-delete-panel" aria-label="プロジェクトのアーカイブ確認">
          <div><strong>「{project.name}」をアーカイブしますか？</strong><p>一覧から外します。候補・予測履歴・実測データは保持され、後から復元できます。</p></div>
          <div className="project-delete-actions"><button className="danger-button" disabled={offline || archiving} onClick={() => void archiveCurrentProject()}>{archiving ? "アーカイブ中…" : "アーカイブする"}</button><button className="outline-button" disabled={archiving} onClick={() => setArchiveOpen(false)}>キャンセル</button></div>
        </div>}
      </section>}
      </div>
    </div>
  );
}
