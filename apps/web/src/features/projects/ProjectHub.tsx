import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
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
  projectOperationDisabled,
  resolveFixedChain,
} from "./chainProjectMetadata";
import type { ResolvedTaskDefinition } from "../candidates";
import { ChainEvaluationPanel } from "./ChainEvaluationPanel";
import { candidateQuestionActions, candidateQuestionState, type CandidateSection } from "../../shared/projectActionQuestions";
import { ProjectEvidenceHistoryList } from "./ProjectEvidenceHistory";
import { ProjectCreationPanel } from "./ProjectCreationPanel";
import { ProjectSettingsPanel } from "./ProjectSettingsPanel";
import {
  isCurrentProjectSettingsRequest,
  projectGroupMembershipState,
  ungroupedMembershipValue,
} from "./projectSettingsState";
import { useProjectHistory } from "./useProjectHistory";

type ProjectSettingsSection = "general" | "targets" | "scientific" | "ranges" | "display" | "task" | "evidence";

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
  requestedSettingsSection?: ProjectSettingsSection;
  renderScientificSettings?: (
    project: ApiProject,
    onProjectChanged: (project: ApiProject) => void,
    readOnly: boolean,
  ) => ReactNode;
  onProjectChanged: (project: ApiProject) => void;
  onOpenSettings: (section?: ProjectSettingsSection) => void;
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
  const [settingsPending, setSettingsPending] = useState(false);
  const [settingsError, setSettingsError] = useState("");
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [archivedProjects, setArchivedProjects] = useState<ApiProject[]>([]);
  const [sampleGallery, setSampleGallery] = useState<ApiSampleGalleryItem[]>([]);
  const [installingSampleId, setInstallingSampleId] = useState("");
  const [removingSampleId, setRemovingSampleId] = useState("");
  const [restoringProjectId, setRestoringProjectId] = useState("");
  const [restoringCandidateId, setRestoringCandidateId] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [creationError, setCreationError] = useState("");
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
  const [collapsedSeriesIds, setCollapsedSeriesIds] = useState<Set<string>>(() => new Set());
  const activeProjectRef = useRef(activeProjectId);
  const initializedSeriesIdsRef = useRef(new Set<string>());
  const previousActiveSeriesIdRef = useRef<string | null>(null);
  const decisionDraftRef = useRef({ key: "", dirty: false });
  const projectNameDraftProjectRef = useRef("");
  const projectNameDirtyRef = useRef(false);
  const projectNameInputRef = useRef<HTMLInputElement>(null);
  const focusCreationFormRef = useRef(false);
  activeProjectRef.current = activeProjectId;
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

  useEffect(() => {
    const selected = projects.find((item) => item.id === activeProjectId) ?? null;
    setProject(selected);
    setError("");
    setArchiveOpen(false);
    setSettingsPending(false);
    setSettingsError("");
    setDecisionNote("");
    decisionDraftRef.current = { key: "", dirty: false };
  }, [projects, activeProjectId]);

  useEffect(() => {
    const selected = projects.find((item) => item.id === activeProjectId);
    if (projectNameDraftProjectRef.current !== activeProjectId) {
      projectNameDraftProjectRef.current = activeProjectId;
      projectNameDirtyRef.current = false;
    }
    if (!projectNameDirtyRef.current) setProjectNameDraft(selected?.name ?? "");
  }, [activeProjectId, projects.find((item) => item.id === activeProjectId)?.name]);

  useEffect(() => {
    let active = true;
    void workbenchApi.listProjects(true).then((items) => {
      if (active) setArchivedProjects(items.filter((item) => item.archived_at));
    }).catch(() => {
      if (active) setArchivedProjects([]);
    });
    return () => { active = false; };
  }, [projects]);

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
    setError("");
    setSelectedSnapshot(null);
    setSelectedChainSnapshot(null);
    setModelPackage(null);
    setChainEvaluation(null);
    setChainTaskDefinition(null);
    const requests = [
      workbenchApi.listTaskDefinitions().then((items) => {
        if (!controller.signal.aborted) {
          setCatalog(items);
        }
      }),
      workbenchApi.projectCreationOptions().then((item) => !controller.signal.aborted && setCreationOptions(item)),
      workbenchApi.listChainTemplates().then((items) => !controller.signal.aborted && setChainTemplates(items)),
    ];
    if (!taskUnavailable && identityProject) {
      if (chainIdentity) {
        requests.push(
          workbenchApi.taskDefinition(activeProjectId).then((item) => {
            if (
              !controller.signal.aborted
              && activeProjectRef.current === activeProjectId
            ) {
              setChainTaskDefinition(item);
            }
          }).catch(() => {
            if (
              !controller.signal.aborted
              && activeProjectRef.current === activeProjectId
            ) {
              setChainTaskDefinition(null);
            }
          }),
        );
        if (
          subsystemAvailabilityLoaded
          && chainEvaluationSubsystem?.status === "available"
        ) {
          requests.push(
            workbenchApi.projectChainEvaluation(activeProjectId, controller.signal).then((item) => {
              if (!controller.signal.aborted && activeProjectRef.current === activeProjectId) {
                setChainEvaluation({ projectId: activeProjectId, value: item });
              }
            }),
          );
        }
      } else {
        requests.push(
          workbenchApi.modelPackage(activeProjectId).then((item) => {
            if (!controller.signal.aborted && activeProjectRef.current === activeProjectId) setModelPackage(item);
          }),
        );
      }
    }
    void Promise.all(requests).catch((cause) => {
      if (controller.signal.aborted) return;
      setError(cause instanceof Error ? cause.message : "プロジェクト概要を取得できませんでした。");
    });
    return () => controller.abort();
    // Recovering the workspace also recovers this overview: one retry is enough.
  }, [
    activeProjectId,
    chainIdentity?.chain_revision_id,
    chainEvaluationSubsystem?.status,
    subsystemAvailabilityLoaded,
    identityProject?.id,
    taskUnavailable,
    offline,
  ]);

  useEffect(() => {
    if (
      !requestedSnapshotId
      || (
        chainIdentity
          ? selectedChainSnapshot?.snapshot_id === requestedSnapshotId
          : selectedSnapshot?.id === requestedSnapshotId
      )
    ) return;
    const controller = new AbortController();
    if (chainIdentity) {
      workbenchApi.chainSnapshot(activeProjectId, requestedSnapshotId, controller.signal)
        .then((item) => {
          if (!controller.signal.aborted) {
            setSelectedSnapshot(null);
            setSelectedChainSnapshot(item);
          }
        })
        .catch((cause) => !controller.signal.aborted && setError(cause instanceof Error ? cause.message : "Chain Snapshotを参照できません。"));
    } else if (operations?.snapshot) {
      workbenchApi.snapshot(activeProjectId, requestedSnapshotId, controller.signal)
        .then((item) => {
          if (!controller.signal.aborted) {
            setSelectedChainSnapshot(null);
            setSelectedSnapshot(item);
          }
        })
        .catch((cause) => !controller.signal.aborted && setError(cause instanceof Error ? cause.message : "保存済み予測を参照できません。"));
    } else {
      return;
    }
    return () => controller.abort();
  }, [
    activeProjectId,
    chainIdentity?.chain_revision_id,
    operations?.snapshot,
    requestedSnapshotId,
    selectedChainSnapshot?.snapshot_id,
    selectedSnapshot?.id,
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
  const actionBlocked = Boolean(taskUnavailable || chainExecutionPending || offline);
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
    const revisionById = new Map<string, ApiChainTemplate["revisions"][number]>();
    for (const template of chainTemplates) {
      for (const revision of template.revisions) {
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
  const availableChains = chainTemplates.filter((item) => item.revisions.some(
    (revision) => revision.stages.some(
      (stage) => stage.dataset_view_revision_id === newDatasetViewId,
    ),
  ));
  const selectedChain = chainTemplates.find(
    (item) => item.definition.chain_id === newChainId,
  );
  const selectedChainRevision = selectedChain?.revisions.find(
    (revision) => `${revision.chain_id}:r${revision.revision}` === newChainRevisionId,
  );
  const fixedDataset = project?.dataset_view_revision_id ? datasetByView.get(project.dataset_view_revision_id) : undefined;
  const fixedPackage = creationOptions?.model_packages.find((item) => item.id === project?.model_package_ref_id);
  const persistedProject = projects.find((item) => item.id === activeProjectId);
  const unresolvedReferences = creationOptions && project
    ? chainIdentity
      ? [
          !fixedChain && "参照Chain",
          !fixedChainRevision && "Chain Revision",
        ].filter((item): item is string => Boolean(item))
      : [
          !fixedDataset && "Dataset",
          !fixedPackage && "Model Package",
          taskUnavailable && "予測タスク",
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
  const uninstalledSamples = sampleGallery.filter((item) => !item.installed);

  async function installSamples(projectIds: string[]) {
    setInstallingSampleId(projectIds.length === 1 ? projectIds[0] : "all");
    try {
      await onSampleGalleryInstall(projectIds);
    } finally {
      setInstallingSampleId("");
    }
  }

  async function removeSample(projectId: string) {
    setRemovingSampleId(projectId);
    try {
      await onSampleGalleryRemove(projectId);
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
    if (!dataset) {
      setError("選択したDatasetをプロジェクト作成に利用できません。");
      onCreationIntentConsumed();
      return;
    }
    focusCreationFormRef.current = true;
    setCreateOpen(true);
    setCreateMode("empty");
    setNewProjectName(`${datasetDisplayName(dataset)} 検討`);
    setNewDatasetViewId(requestedDatasetViewId);
    setNewTaskId("");
    setNewModelPackageRefId("");
    setNewChainId("");
    setNewChainRevisionId("");
    setNewProjectGroupChoice("none");
    setNewProjectSeriesId("");
    setNewProjectSeriesName("");
    setPredecessorProjectId("");
    setContinuationReason("");
    onCreationIntentConsumed();
  }, [creationOptions, datasetByView, onCreationIntentConsumed, requestedDatasetViewId]);

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

  async function saveProject(nextProject = project) {
    if (!nextProject || settingsPending) return;
    const requestProjectId = activeProjectId;
    if (nextProject.id !== requestProjectId || activeProjectRef.current !== requestProjectId) return;
    setSettingsPending(true);
    setSettingsError("");
    try {
      const saved = await workbenchApi.updateProject(requestProjectId, nextProject);
      if (activeProjectRef.current !== requestProjectId) return;
      setProject(saved);
      projectNameDirtyRef.current = false;
      setProjectNameDraft(saved.name);
      onProjectChanged(saved);
    } catch (cause) {
      if (activeProjectRef.current === requestProjectId) {
        setSettingsError(cause instanceof Error ? cause.message : "プロジェクトを保存できませんでした。");
      }
    } finally {
      if (activeProjectRef.current === requestProjectId) setSettingsPending(false);
    }
  }

  async function saveSeriesName() {
    const trimmedSeriesName = seriesName.trim();
    if (!fixedSeries || !trimmedSeriesName || settingsPending) return;
    const requestProjectId = activeProjectId;
    setSettingsPending(true);
    setSettingsError("");
    try {
      const savedSeries = await workbenchApi.updateProjectSeries(fixedSeries.id, trimmedSeriesName, fixedSeries.description);
      if (activeProjectRef.current !== requestProjectId) return;
      setCreationOptions((current) => current ? {
        ...current,
        project_series: current.project_series.map((item) => item.id === savedSeries.id ? savedSeries : item),
      } : current);
    } catch (cause) {
      if (activeProjectRef.current === requestProjectId) {
        setSettingsError(cause instanceof Error ? cause.message : "検討グループ名を保存できませんでした。");
      }
    } finally {
      if (activeProjectRef.current === requestProjectId) setSettingsPending(false);
    }
  }

  async function moveProjectToGroup() {
    if (!project || !membershipChanged || settingsPending) return;
    const requestProjectId = project.id;
    setSettingsPending(true);
    setSettingsError("");
    try {
      const moved = await workbenchApi.moveProjectToGroup(requestProjectId, {
        project_series_id: membershipTargetSeriesId,
        expected_project_series_id: project.project_series_id ?? null,
      });
      if (activeProjectRef.current !== requestProjectId) return;
      setProject(moved);
      setGroupMembershipId(moved.project_series_id ?? "");
      onProjectChanged(moved);
      try {
        const refreshedOptions = await workbenchApi.projectCreationOptions();
        if (activeProjectRef.current !== requestProjectId) return;
        setCreationOptions(refreshedOptions);
        setSeriesName(
          refreshedOptions.project_series.find((item) => item.id === moved.project_series_id)?.name ?? "",
        );
      } catch {
        if (isCurrentProjectSettingsRequest(requestProjectId, activeProjectRef.current)) {
          setSettingsError("所属は変更しましたが、グループ一覧を更新できませんでした。");
        }
      }
    } catch (cause) {
      if (isCurrentProjectSettingsRequest(requestProjectId, activeProjectRef.current)) {
        setGroupMembershipId(project.project_series_id ?? "");
        setSettingsError(cause instanceof Error ? cause.message : "所属グループを変更できませんでした。");
      }
    } finally {
      if (activeProjectRef.current === requestProjectId) setSettingsPending(false);
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

  async function openSnapshot(snapshotId: string) {
    const requestProjectId = activeProjectId;
    try {
      const loaded = await workbenchApi.snapshot(requestProjectId, snapshotId);
      if (activeProjectRef.current !== requestProjectId) return;
      setSelectedChainSnapshot(null);
      setSelectedSnapshot(loaded);
      onSnapshotNavigate(snapshotId);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存済み予測を参照できませんでした。");
    }
  }

  function openChainSnapshot(snapshot: ApiChainSnapshot) {
    setSelectedSnapshot(null);
    setSelectedChainSnapshot(snapshot);
    onSnapshotNavigate(snapshot.snapshot_id);
  }

  async function saveDecision(clear = false) {
    const evidence = selectedSnapshot
      ? {
        candidateId: selectedSnapshot.candidate_id,
        snapshotId: selectedSnapshot.id,
      }
      : selectedChainSnapshot
        ? {
          candidateId: selectedChainSnapshot.identity.candidate_id,
          snapshotId: selectedChainSnapshot.snapshot_id,
        }
        : null;
    if (!evidence) return;
    if (!clear && !decisionNote.trim()) return setError("採用判断には理由を入力してください。");
    try {
      const requestProjectId = activeProjectId;
      const saved = await workbenchApi.updateProjectDecision(requestProjectId, clear ? { candidate_id: "", snapshot_id: "", note: "" } : {
        candidate_id: evidence.candidateId,
        snapshot_id: evidence.snapshotId,
        note: decisionNote.trim(),
      });
      if (activeProjectRef.current !== requestProjectId) return;
      setProject(saved);
      onProjectChanged(saved);
      await reloadHistory(undefined, requestProjectId);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "採用判断を保存できませんでした。");
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
    const next = { ...targetValues };
    if (value === "") delete next[key]; else next[key] = Number(value);
    setProject({ ...project, target_values: next });
  };
  const setTargetMode = (key: string, mode: "directional" | "between") => {
    if (!project) return;
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
      setNewChainId(fixedChain?.definition.chain_id ?? "");
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
    const archived = await onProjectArchived(project.id);
    setArchiving(false);
    if (archived) setArchiveOpen(false);
  }

  async function restoreArchivedProject(projectId: string) {
    if (restoringProjectId) return;
    setRestoringProjectId(projectId);
    const restored = await onProjectRestored(projectId);
    if (restored) {
      setArchivedProjects((items) => items.filter((item) => item.id !== projectId));
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
            .find(({ revision }) => `${revision.chain_id}:r${revision.revision}` === itemIdentity.chain_revision_id);
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
        {sampleGallery.length > 0 && <details className="sample-gallery-list">
          <summary>同梱サンプルを管理 <span>{sampleGallery.length}件</span></summary>
          <div className="sample-gallery-items">
            {uninstalledSamples.length > 1 && <button
              type="button"
              className="outline-button sample-gallery-add-all"
              disabled={offline || Boolean(installingSampleId) || Boolean(removingSampleId) || !uninstalledSamples.some((item) => item.available)}
              onClick={() => void installSamples([])}
            >{installingSampleId === "all" ? "追加中…" : `未追加の${uninstalledSamples.length}件を一括追加`}</button>}
            {sampleGallery.map((item) => <div className="sample-gallery-item" key={item.project_id}>
              <span>
                <strong>{item.name}</strong>
                <small>{item.installed
                  ? item.remove_blocked_reason || "Workspaceに追加済み"
                  : item.unavailable_reason || "必要なときだけWorkspaceへ追加します"}</small>
              </span>
              {item.installed ? <button
                  type="button"
                  className="outline-button"
                  disabled={offline || Boolean(installingSampleId) || Boolean(removingSampleId) || !item.removable}
                  title={item.remove_blocked_reason || undefined}
                  onClick={() => void removeSample(item.project_id)}
                >{removingSampleId === item.project_id ? "処理中…" : "取り除く"}</button>
                : <button
                  type="button"
                  className="outline-button"
                  disabled={offline || Boolean(installingSampleId) || Boolean(removingSampleId) || !item.available}
                  onClick={() => void installSamples([item.project_id])}
                >{installingSampleId === item.project_id ? "追加中…" : "追加"}</button>}
            </div>)}
          </div>
        </details>}
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
        {surface === "overview" ? <div className="page-intro project-hub-header">
          <div>
            <span className="overline">PROJECT OVERVIEW</span>
            <div className="project-inline-name">
              <label>
                <span className="sr-only">プロジェクト名</span>
                <input
                  aria-label="プロジェクト名"
                  value={projectNameDraft}
                  disabled={!project || offline || settingsPending}
                  onChange={(event) => {
                    projectNameDirtyRef.current = true;
                    setProjectNameDraft(event.target.value);
                  }}
                />
              </label>
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
                onClick={() => void saveProject({ ...project, name: projectNameDraft.trim() })}
              >{settingsPending ? "保存中…" : "名前を保存"}</button>}
              <span className="project-name-save-state" role="status">
                {settingsPending ? "保存中" : settingsError ? "保存できませんでした" : ""}
              </span>
            </div>
            {(project?.purpose || project?.description) && <p>{project.purpose || project.description}</p>}
          </div>
          <div className="project-actions">
            <button
              className="outline-button"
              disabled={offline
                || taskUnavailable
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
        {chainIdentity
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
        <div className="project-goal-values">
          {configuredTargets.length
            ? configuredTargets.map((output) => <span key={output.key}><b>{output.label}</b>{targetGoalText(savedTargetValues[output.key], output.goal_direction, formatNumber)} {output.unit}</span>)
            : <span>未設定です。設定すると候補の目標達成率を比較できます。</span>}
        </div>
        <button className={configuredTargets.length ? "outline-button" : "primary-button"} disabled={taskUnavailable || chainExecutionPending || offline} onClick={focusTargetSettings}>{configuredTargets.length ? "目標値を変更" : "目標値を設定"}</button>
      </section>}
      {surface === "settings" && <nav className="project-settings-category-nav" aria-label="Project設定カテゴリ">
        <button type="button" className={settingsCategory === "general" ? "active" : ""} aria-current={settingsCategory === "general" ? "page" : undefined} onClick={() => onOpenSettings("general")}>通常設定</button>
        {!chainIdentity && <button type="button" className={settingsCategory === "scientific" ? "active" : ""} aria-current={settingsCategory === "scientific" ? "page" : undefined} onClick={() => onOpenSettings("scientific")}>科学設定</button>}
        <button type="button" className={settingsCategory === "evidence" ? "active" : ""} aria-current={settingsCategory === "evidence" ? "page" : undefined} onClick={() => onOpenSettings("evidence")}>証拠・管理</button>
      </nav>}
      {surface === "settings" && settingsCategory === "evidence" && project && <details className="project-reference-details" open>
        <summary><span>固定参照・再現性</span><small>使用中のデータ・予測方法</small></summary>
        {chainIdentity
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
        subsystemAvailabilityError
          ? <section className="chain-evaluation-panel unavailable" role="alert">
            <strong>Chain評価の利用状況を取得できません</strong>
            <p>評価APIは呼び出していません。利用可否の取得後に再読み込みしてください。</p>
          </section>
          : chainEvaluationSubsystem?.status === "unavailable"
          ? <section className="chain-evaluation-panel unavailable" role="status">
            <strong>{chainEvaluationSubsystem.message}</strong>
            <p>{chainEvaluationSubsystem.impact}</p>
            <small>原因: {chainEvaluationSubsystem.cause}</small>
            <small>復旧: {chainEvaluationSubsystem.recovery_hint}</small>
          </section>
          : chainEvaluation?.projectId === activeProjectId
            ? <ChainEvaluationPanel evaluation={chainEvaluation.value} stagePath={fixedStagePath} />
            : <section className="chain-evaluation-panel loading" aria-live="polite">Chain評価を読み込んでいます。</section>
      )}
      {surface === "overview" && predecessorProject && <section className="project-continuation-link" aria-label="このプロジェクトの続き元"><span>続き元</span><button type="button" onClick={() => onSwitch(predecessorProject.id)}>{predecessorProject.name}</button><small>{predecessorSeries?.name ?? "グループなし"}{project?.continuation_reason ? ` · ${project.continuation_reason}` : ""}</small></section>}

      {surface === "overview" && <ProjectCreationPanel
        open={createOpen}
        loading={creating}
        disabled={offline}
        error={creationError}
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
          ...availableChains.map((item) => ({
            id: `chain:${item.definition.chain_id}`,
            label: `${item.definition.label}（Chain）`,
          })),
        ]}
        chainRevisionChoices={(selectedChain?.revisions ?? []).map((revision) => ({
          id: `${revision.chain_id}:r${revision.revision}`,
          label: `r${revision.revision} · ${revision.stages.map(
            (stage) => stage.stage_id,
          ).join(" → ")}`,
        }))}
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
        onSubmit={createProject}
      />}

      {surface === "settings" && settingsCategory === "general" && <ProjectSettingsPanel
        open
        project={project}
        loading={settingsPending}
        error={settingsError}
        disabled={projectOperationDisabled({
          operation: "metadata",
          offline,
          pending: settingsPending,
        })}
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
        onGroupMembershipChange={setGroupMembershipId}
        onMoveProjectToGroup={moveProjectToGroup}
        onSeriesNameChange={setSeriesName}
        onSaveSeriesName={saveSeriesName}
        onProjectChange={setProject}
        onTargetModeChange={setTargetMode}
        onScalarTargetChange={setScalarTarget}
        onRangeTargetChange={setRangeTarget}
        onSave={saveProject}
      />}

      {surface === "settings" && settingsCategory === "scientific" && !chainIdentity && project && renderScientificSettings?.(
        project,
        (nextProject) => {
          setProject(nextProject);
          onProjectChanged(nextProject);
        },
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

      {surface === "overview" && selectedSnapshot?.payload.prediction && <section className="snapshot-detail project-snapshot-detail">
        <div className="panel-title"><h3>固定した予測の詳細</h3><button className="outline-button" onClick={() => { setSelectedSnapshot(null); onSnapshotNavigate(undefined); }}>閉じる</button></div>
        <p>{formatDate(selectedSnapshot.created_at)} / {history?.candidates.find((item) => item.candidate.id === selectedSnapshot.candidate_id)?.candidate.name ?? "保存時の候補"}</p>
        <span className="decision-snapshot-badge">{!selectedSnapshot.payload.provenance?.package?.manifest_sha256 || !modelPackage ? "予測モデル情報を確認できません" : selectedSnapshot.payload.provenance.package.manifest_sha256 === modelPackage.manifest_sha256 ? "現在と同じ予測モデル" : "現在とは別の予測モデル"}</span>
        <table className="quality-table"><thead><tr><th>特性</th><th>固定予測</th><th>区間・分位</th><th>目標達成</th></tr></thead><tbody>{orderedPredictions(selectedSnapshot.payload.prediction.predictions).map(([key, value]) => { const assessment = assessPrediction(outputDefinition(key), value); return <tr className={assessment.implausible ? "implausible-output" : undefined} key={key}><th>{outputLabels.get(key) ?? key}{assessment.implausible && <small className="output-warning-badge">⚠ 物理範囲外</small>}</th><td title={assessment.warning ?? undefined}>{formatPredictionPoint(value, (numberValue) => formatOutputNumber(key, numberValue))}</td><td>{predictionHasInterval(value) ? <>{formatOutputNumber(key, value.lower)}–{formatOutputNumber(key, value.upper)} <small>{predictionIntervalLabel(value)}</small></> : "利用不可"}</td><td>{value.goal_probability == null ? value.goal_value == null ? "目標未設定" : "利用不可" : `${formatNumber(value.goal_probability * 100, 0)}%`}</td></tr>; })}</tbody></table>
        <div className="snapshot-decision-form"><label>判断理由<textarea disabled={taskUnavailable || offline} value={decisionNote} onChange={(event) => { decisionDraftRef.current.dirty = true; setDecisionNote(event.target.value); }} placeholder="この時点の予測を採用判断に使う理由" /></label><button className="outline-button" disabled={taskUnavailable || offline} onClick={() => void saveDecision(false)}>採用判断として固定</button>{project?.decision_snapshot_id === selectedSnapshot.id && <button className="outline-button" disabled={taskUnavailable || offline} onClick={() => void saveDecision(true)}>採用判断を解除</button>}</div>
        <CandidateAddButton disabled={taskUnavailable || offline} onClick={() => void restoreSnapshot(selectedSnapshot.id)}>この時点から新しい候補を作る</CandidateAddButton>
      </section>}

      {surface === "overview" && selectedChainSnapshot && (() => {
        const terminalStage = terminalChainStage(selectedChainSnapshot.stages);
        const predictions = chainStagePredictions(terminalStage);
        const selectedCandidateName = history?.candidates.find(
          (item) => item.candidate.id === selectedChainSnapshot.identity.candidate_id,
        )?.candidate.name ?? "保存時の候補";
        return <section className="snapshot-detail project-snapshot-detail chain-snapshot-detail">
          <div className="panel-title"><h3>全Stageを固定した詳細</h3><button className="outline-button" onClick={() => { setSelectedChainSnapshot(null); onSnapshotNavigate(undefined); }}>閉じる</button></div>
          <p>{formatDate(selectedChainSnapshot.created_at)} / {selectedCandidateName} / 編集版 {selectedChainSnapshot.identity.candidate_revision}</p>
          <div className="chain-fixed-stage-list" aria-label="固定したStage">
            {selectedChainSnapshot.stages.map((stage) => <span key={stage.stage_id}>Stage {stage.stage_id} · {stage.status === "latest" ? "固定済み" : stage.status}</span>)}
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
          <div className="snapshot-decision-form"><label>判断理由<textarea disabled={taskUnavailable || offline} value={decisionNote} onChange={(event) => { decisionDraftRef.current.dirty = true; setDecisionNote(event.target.value); }} placeholder="この時点のChain結果を採用判断に使う理由" /></label><button className="outline-button" disabled={taskUnavailable || offline} onClick={() => void saveDecision(false)}>採用判断として固定</button>{project?.decision_snapshot_id === selectedChainSnapshot.snapshot_id && <button className="outline-button" disabled={taskUnavailable || offline} onClick={() => void saveDecision(true)}>採用判断を解除</button>}</div>
          <button className="outline-button" onClick={() => onNavigate("candidates", selectedChainSnapshot.identity.candidate_id)}>Chain候補を開く</button>
        </section>;
      })()}

      {surface === "settings" && settingsCategory === "evidence" && canArchiveProject && project && <section className="project-danger-zone" aria-label="プロジェクトのアーカイブ">
        {!archiveOpen ? <button className="danger-outline-button" disabled={offline} onClick={() => setArchiveOpen(true)}>プロジェクトをアーカイブ</button> : <div className="project-delete-panel" aria-label="プロジェクトのアーカイブ確認">
          <div><strong>「{project.name}」をアーカイブしますか？</strong><p>一覧から外します。候補・予測履歴・実測データは保持され、後から復元できます。</p></div>
          <div className="project-delete-actions"><button className="danger-button" disabled={offline || archiving} onClick={() => void archiveCurrentProject()}>{archiving ? "アーカイブ中…" : "アーカイブする"}</button><button className="outline-button" disabled={archiving} onClick={() => setArchiveOpen(false)}>キャンセル</button></div>
        </div>}
      </section>}
      </div>
    </div>
  );
}
