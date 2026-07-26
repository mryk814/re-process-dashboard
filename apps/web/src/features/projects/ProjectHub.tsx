import { useEffect, useMemo, useRef, useState } from "react";
import { provenanceLabel } from "../../shared/candidateProvenance";
import { formatPredictionPoint, predictionHasInterval, predictionIntervalLabel } from "../../shared/predictionPresentation";
import { assessOutputValues, assessPrediction, resolveOutputDefinition } from "../../shared/outputPresentation";
import { CandidateAddButton } from "../../shared/ui/CandidateAddButton";
import { ModelPackageDecisionCard } from "../../shared/ui/ModelPackageDecisionCard";
import { hasValidTargetGoal, isTargetRange, targetGoalText, type TargetGoal } from "../../shared/targetGoals";
import { formatTaskNumber, orderedTaskEntries } from "../../shared/taskPresentation";
import {
  compatiblePackagesForDatasetTask,
  compatibleTaskIdsForDataset,
  datasetDisplayName,
  modelPackageDisplayName,
  trainingDataset,
} from "../../shared/dataLibraryPresentation";
import { fromApiCandidate, toApiCandidate, type CandidateViewModel, type RuntimeOperations, type TaskDefinitionContract } from "../candidates";
import {
  workbenchApi,
  type ApiChainEvaluation,
  type ApiChainTemplate,
  type ApiModelPackage,
  type ApiPreview,
  type ApiProject,
  type ApiProjectHistory,
  type ApiProjectCreationOptions,
  type ApiSnapshot,
  type ApiTaskCatalogItem,
} from "../../shared/api/workbench-api";
import type { ResolvedTaskDefinition } from "../candidates";
import { ChainEvaluationPanel } from "./ChainEvaluationPanel";

type Props = {
  projects: ApiProject[];
  activeProjectId: string;
  candidate?: CandidateViewModel;
  taskDefinition: TaskDefinitionContract | null;
  supportsLineageCandidate: boolean;
  operations?: RuntimeOperations;
  currentPreviews: Record<string, ApiPreview>;
  taskAvailability?: ResolvedTaskDefinition["availability"];
  requestedSnapshotId?: string;
  requestedDatasetViewId?: string;
  requestedSettingsSection?: "targets";
  onProjectChanged: (project: ApiProject) => void;
  onProjectDeleted: (projectId: string) => Promise<boolean>;
  onSwitch: (projectId: string) => void;
  onRestore: (candidate: CandidateViewModel) => void;
  onNavigate: (view: "candidates" | "lineage" | "explore" | "settings", candidateId?: string) => void;
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

const formatNumber = (value: number, digits = 1) => value.toLocaleString("ja-JP", { maximumFractionDigits: digits });
const formatDate = (value: string) => new Date(value).toLocaleString("ja-JP");
const defaultGoalLabel = (direction: "at_least" | "at_most" | "target") => direction === "at_most" ? "以下" : direction === "target" ? "目標値付近" : "以上";

export function ProjectHub({
  projects,
  activeProjectId,
  candidate,
  taskDefinition,
  supportsLineageCandidate,
  operations,
  currentPreviews,
  taskAvailability,
  requestedSnapshotId,
  requestedDatasetViewId,
  requestedSettingsSection,
  onProjectChanged,
  onProjectDeleted,
  onSwitch,
  onRestore,
  onNavigate,
  onSnapshotNavigate,
  onCreationIntentConsumed,
}: Props) {
  const [project, setProject] = useState<ApiProject | null>(null);
  const [history, setHistory] = useState<ApiProjectHistory | null>(null);
  const [catalog, setCatalog] = useState<ApiTaskCatalogItem[]>([]);
  const [modelPackage, setModelPackage] = useState<ApiModelPackage | null>(null);
  const [creationOptions, setCreationOptions] = useState<ApiProjectCreationOptions | null>(null);
  const [chainTemplates, setChainTemplates] = useState<ApiChainTemplate[]>([]);
  const [chainEvaluation, setChainEvaluation] = useState<{
    projectId: string;
    value: ApiChainEvaluation;
  } | null>(null);
  const [selectedSnapshot, setSelectedSnapshot] = useState<ApiSnapshot | null>(null);
  const [error, setError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [createMode, setCreateMode] = useState<"empty" | "copy">("empty");
  const [newProjectName, setNewProjectName] = useState("");
  const [newTaskId, setNewTaskId] = useState("");
  const [newDatasetViewId, setNewDatasetViewId] = useState("");
  const [newModelPackageRefId, setNewModelPackageRefId] = useState("");
  const [newChainId, setNewChainId] = useState("");
  const [newChainRevisionId, setNewChainRevisionId] = useState("");
  const [newProjectSeriesId, setNewProjectSeriesId] = useState("");
  const [predecessorProjectId, setPredecessorProjectId] = useState("");
  const [continuationReason, setContinuationReason] = useState("");
  const [seriesName, setSeriesName] = useState("");
  const [groupMembershipId, setGroupMembershipId] = useState("");
  const [decisionNote, setDecisionNote] = useState("");
  const [collapsedSeriesIds, setCollapsedSeriesIds] = useState<Set<string>>(() => new Set());
  const activeProjectRef = useRef(activeProjectId);
  const initializedSeriesIdsRef = useRef(new Set<string>());
  const previousActiveSeriesIdRef = useRef<string | null>(null);
  const decisionDraftRef = useRef({ key: "", dirty: false });
  const projectNameInputRef = useRef<HTMLInputElement>(null);
  const focusCreationFormRef = useRef(false);
  activeProjectRef.current = activeProjectId;
  const outputDefinition = (key: string) => resolveOutputDefinition(taskDefinition?.outputs ?? [], key);
  const orderedPredictions = <T,>(values: Record<string, T>) => taskDefinition
    ? orderedTaskEntries(taskDefinition, values)
    : Object.entries(values);
  const formatOutputNumber = (key: string, value: number) => taskDefinition
    ? formatTaskNumber(value, taskDefinition, `output.${key}`, project?.display_decimals)
    : formatNumber(value);
  const taskUnavailable = taskAvailability?.status === "unavailable";
  const identityProject = project?.id === activeProjectId
    ? project
    : projects.find((item) => item.id === activeProjectId);
  const chainIdentity = identityProject?.scientific_identity?.identity_kind === "chain"
    ? identityProject.scientific_identity
    : null;
  const chainExecutionPending = false;

  const reloadHistory = async (signal?: AbortSignal, expectedProjectId = activeProjectId) => {
    const loaded = await workbenchApi.projectHistory(expectedProjectId, signal);
    if (!signal?.aborted && activeProjectRef.current === expectedProjectId) setHistory(loaded);
  };

  useEffect(() => {
    const selected = projects.find((item) => item.id === activeProjectId) ?? null;
    setProject(selected);
    setError("");
    setDeleteOpen(false);
    setDecisionNote("");
    decisionDraftRef.current = { key: "", dirty: false };
  }, [projects, activeProjectId]);

  useEffect(() => {
    setSettingsOpen(false);
  }, [activeProjectId]);

  useEffect(() => {
    const controller = new AbortController();
    setHistory(null);
    setSelectedSnapshot(null);
    setModelPackage(null);
    setChainEvaluation(null);
    const requests = [
      reloadHistory(controller.signal),
      workbenchApi.listTaskDefinitions().then((items) => {
        if (!controller.signal.aborted) {
          setCatalog(items);
        }
      }),
      workbenchApi.projectCreationOptions().then((item) => !controller.signal.aborted && setCreationOptions(item)),
      workbenchApi.listChainTemplates().then((items) => !controller.signal.aborted && setChainTemplates(items)),
    ];
    if (!taskUnavailable) {
      if (chainIdentity) {
        requests.push(
          workbenchApi.projectChainEvaluation(activeProjectId, controller.signal).then((item) => {
            if (!controller.signal.aborted && activeProjectRef.current === activeProjectId) {
              setChainEvaluation({ projectId: activeProjectId, value: item });
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
    void Promise.all(requests).catch((cause) => {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "プロジェクト概要を取得できませんでした。");
    });
    return () => controller.abort();
  }, [activeProjectId, chainIdentity?.chain_revision_id, taskUnavailable]);

  useEffect(() => {
    if (!requestedSnapshotId || !operations?.snapshot || selectedSnapshot?.id === requestedSnapshotId) return;
    const controller = new AbortController();
    workbenchApi.snapshot(activeProjectId, requestedSnapshotId, controller.signal)
      .then((item) => !controller.signal.aborted && setSelectedSnapshot(item))
      .catch((cause) => !controller.signal.aborted && setError(cause instanceof Error ? cause.message : "保存済み予測を参照できません。"));
    return () => controller.abort();
  }, [activeProjectId, requestedSnapshotId, operations?.snapshot, selectedSnapshot?.id]);

  useEffect(() => {
    if (!selectedSnapshot) return;
    const draftKey = `${activeProjectId}:${selectedSnapshot.id}`;
    if (decisionDraftRef.current.key !== draftKey) {
      decisionDraftRef.current = { key: draftKey, dirty: false };
      setDecisionNote("");
    }
    if (!history || decisionDraftRef.current.dirty) return;
    const decision = history?.candidates.find((item) => item.decision?.snapshot_id === selectedSnapshot.id)?.decision;
    setDecisionNote(decision?.note ?? "");
  }, [activeProjectId, selectedSnapshot?.id, history]);

  const activeCandidates = history?.candidates.filter((item) => !item.candidate.archived_at) ?? [];
  const copyTaskId = candidate ? projects.find((item) => item.id === candidate.raw.project_id)?.task_id : undefined;
  const outputLabels = useMemo(() => new Map((taskDefinition?.outputs ?? []).map((output) => [output.key, output.label])), [taskDefinition]);
  const taskLabels = useMemo(() => new Map(catalog.map((item) => [
    item.definition.task_definition.id,
    item.definition.task_definition.label,
  ])), [catalog]);
  const datasetByView = useMemo(() => new Map(
    (creationOptions?.datasets ?? []).flatMap((dataset) => (dataset.dataset_views ?? []).map((view) => [view.id, dataset] as const)),
  ), [creationOptions]);
  const selectedDataset = datasetByView.get(newDatasetViewId);
  const availableTaskIds = creationOptions
    ? compatibleTaskIdsForDataset(selectedDataset, creationOptions)
    : [];
  const availablePackages = creationOptions
    ? compatiblePackagesForDatasetTask(selectedDataset, newTaskId, creationOptions)
    : [];
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
  const fixedChain = chainIdentity
    ? chainTemplates.find((item) => item.revisions.some(
      (revision) => `${revision.chain_id}:r${revision.revision}` === chainIdentity.chain_revision_id,
    ))
    : undefined;
  const fixedChainRevision = fixedChain?.revisions.find(
    (revision) => `${revision.chain_id}:r${revision.revision}` === chainIdentity?.chain_revision_id,
  );
  const fixedDataset = project?.dataset_view_revision_id ? datasetByView.get(project.dataset_view_revision_id) : undefined;
  const fixedPackage = creationOptions?.model_packages.find((item) => item.id === project?.model_package_ref_id);
  const fixedSeries = creationOptions?.project_series.find((item) => item.id === project?.project_series_id);
  const activeProjectSeries = useMemo(() => {
    const usedSeriesIds = new Set(projects.map((item) => item.project_series_id).filter(Boolean));
    return (creationOptions?.project_series ?? []).filter((item) => usedSeriesIds.has(item.id));
  }, [creationOptions?.project_series, projects]);
  const selectedSeries = activeProjectSeries.find((item) => item.id === newProjectSeriesId);
  const predecessorProject = projects.find((item) => item.id === project?.predecessor_project_id);
  const predecessorSeries = creationOptions?.project_series.find(
    (item) => item.id === predecessorProject?.project_series_id,
  );
  const selectedPackage = creationOptions?.model_packages.find((item) => item.id === newModelPackageRefId);
  const selectedTrainingDataset = trainingDataset(selectedPackage, creationOptions?.datasets ?? []);
  const fixedTrainingDataset = trainingDataset(fixedPackage, creationOptions?.datasets ?? []);
  const selectedTaskId = createMode === "copy" ? copyTaskId ?? "" : newTaskId;

  useEffect(() => {
    if (!settingsOpen) {
      setSeriesName(fixedSeries?.name ?? "");
      setGroupMembershipId(project?.project_series_id ?? "");
    }
  }, [fixedSeries?.id, fixedSeries?.name, project?.project_series_id, settingsOpen]);
  const projectGroups = useMemo(() => {
    const series = new Map((creationOptions?.project_series ?? []).map((item) => [item.id, item]));
    const groups = new Map((creationOptions?.project_series ?? []).map((item) => [item.id, { id: item.id, name: item.name, projects: [] as ApiProject[] }]));
    for (const item of projects) {
      const seriesId = item.project_series_id;
      const id = seriesId && series.has(seriesId) ? seriesId : "unassigned";
      const group = groups.get(id) ?? {
        id,
        name: series.get(id)?.name ?? "その他の検討",
        projects: [],
      };
      group.projects.push(item);
      groups.set(id, group);
    }
    return [...groups.values()].filter((group) => group.projects.length > 0);
  }, [creationOptions?.project_series, projects]);
  const activeSeriesId = projectGroups.find((group) => group.projects.some((item) => item.id === activeProjectId))?.id ?? "unassigned";

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
    setNewProjectSeriesId("");
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
    setSettingsOpen(true);
    window.requestAnimationFrame(() => document.querySelector<HTMLElement>(
      "#project-target-settings input, #project-target-settings select",
    )?.focus());
  };

  useEffect(() => {
    if (requestedSettingsSection === "targets") focusTargetSettings();
  }, [activeProjectId, requestedSettingsSection]);

  async function saveProject() {
    if (!project) return;
    const requestProjectId = activeProjectId;
    if (project.id !== requestProjectId || activeProjectRef.current !== requestProjectId) return;
    try {
      const saved = await workbenchApi.updateProject(requestProjectId, project);
      if (activeProjectRef.current !== requestProjectId) return;
      setProject(saved);
      onProjectChanged(saved);
      setSettingsOpen(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "プロジェクトを保存できませんでした。");
    }
  }

  async function saveSeriesName() {
    const trimmedSeriesName = seriesName.trim();
    if (!fixedSeries || !trimmedSeriesName) return;
    try {
      const savedSeries = await workbenchApi.updateProjectSeries(fixedSeries.id, trimmedSeriesName, fixedSeries.description);
      setCreationOptions((current) => current ? {
        ...current,
        project_series: current.project_series.map((item) => item.id === savedSeries.id ? savedSeries : item),
      } : current);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "検討グループ名を保存できませんでした。");
    }
  }

  async function moveProjectToGroup() {
    if (!project || !groupMembershipId || groupMembershipId === project.project_series_id) return;
    const requestProjectId = project.id;
    try {
      const moved = await workbenchApi.moveProjectToGroup(requestProjectId, {
        project_series_id: groupMembershipId,
        expected_project_series_id: project.project_series_id ?? null,
      });
      if (activeProjectRef.current !== requestProjectId) return;
      setProject(moved);
      onProjectChanged(moved);
      setError("");
      try {
        const refreshedOptions = await workbenchApi.projectCreationOptions();
        if (activeProjectRef.current !== requestProjectId) return;
        setCreationOptions(refreshedOptions);
        setSeriesName(
          refreshedOptions.project_series.find((item) => item.id === moved.project_series_id)?.name ?? "",
        );
      } catch {
        setError("所属は変更しましたが、グループ一覧を更新できませんでした。");
      }
    } catch (cause) {
      setGroupMembershipId(project.project_series_id ?? "");
      setError(cause instanceof Error ? cause.message : "所属グループを変更できませんでした。");
    }
  }

  async function createProject() {
    const taskId = createMode === "copy" ? copyTaskId : newTaskId;
    const creatingChain = createMode === "empty" && Boolean(newChainId);
    if (!newProjectName.trim() || !newDatasetViewId) return setError("Datasetとプロジェクト名を確認してください。");
    if (creatingChain && !selectedChainRevision) return setError("Chain TemplateとRevisionを確認してください。");
    if (!creatingChain && (!taskId || !newModelPackageRefId)) return setError("予測タスクとModel Packageを確認してください。");
    if (createMode === "copy" && !candidate) return setError("コピーする現在候補がありません。");
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
        project_series_id: newProjectSeriesId || undefined,
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
        design_space: (createMode === "copy" || predecessorProjectId)
          ? project?.design_space ?? undefined
          : undefined,
      });
      onProjectChanged(created);
      setCreateOpen(false);
      setNewProjectName("");
      setPredecessorProjectId("");
      setContinuationReason("");
      onSwitch(created.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "新しいプロジェクトを作成できませんでした。");
    }
  }

  async function openSnapshot(snapshotId: string) {
    const requestProjectId = activeProjectId;
    try {
      const loaded = await workbenchApi.snapshot(requestProjectId, snapshotId);
      if (activeProjectRef.current !== requestProjectId) return;
      setSelectedSnapshot(loaded);
      onSnapshotNavigate(snapshotId);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存済み予測を参照できませんでした。");
    }
  }

  async function saveDecision(clear = false) {
    if (!selectedSnapshot) return;
    if (!clear && !decisionNote.trim()) return setError("採用判断には理由を入力してください。");
    try {
      const requestProjectId = activeProjectId;
      const saved = await workbenchApi.updateProjectDecision(requestProjectId, clear ? { candidate_id: "", snapshot_id: "", note: "" } : {
        candidate_id: selectedSnapshot.candidate_id,
        snapshot_id: selectedSnapshot.id,
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
  const configuredTargets = (taskDefinition?.outputs ?? []).filter((output) => hasValidTargetGoal(savedTargetValues[output.key]));
  const invalidTargetRange = Object.values(targetValues).some((goal) => isTargetRange(goal) && !hasValidTargetGoal(goal))
    || (taskDefinition?.outputs ?? []).some((output) => output.goal_direction === "target" && typeof targetValues[output.key] === "number");
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
    setCreateMode("empty");
    setNewProjectName("");
    setNewTaskId("");
    setNewDatasetViewId("");
    setNewModelPackageRefId("");
    setNewChainId("");
    setNewChainRevisionId("");
    setNewProjectSeriesId("");
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
    if (chainIdentity && fixedChainRevision) {
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
      setNewProjectSeriesId(project.project_series_id ?? "");
      setPredecessorProjectId(project.id);
      setContinuationReason("");
      return;
    }
    if (!project.dataset_view_revision_id || !project.model_package_ref_id) {
      setError("このプロジェクトは固定参照が不足しているため、続きとして作成できません。開発・管理で参照状態を確認してください。");
      return;
    }
    focusCreationFormRef.current = true;
    setCreateOpen(true);
    setCreateMode("empty");
    setNewProjectName(`${project.name} 続き`);
    setNewDatasetViewId(project.dataset_view_revision_id ?? "");
    setNewTaskId(project.task_id);
    setNewModelPackageRefId(project.model_package_ref_id ?? "");
    setNewProjectSeriesId(project.project_series_id ?? "");
    setPredecessorProjectId(project.id);
    setContinuationReason("");
  };

  const canDeleteProject = project != null
    && !taskUnavailable
    && !["default", "hot-rolling-default"].includes(project.id);

  async function deleteCurrentProject() {
    if (!project || !canDeleteProject || deleting) return;
    setDeleting(true);
    const deleted = await onProjectDeleted(project.id);
    setDeleting(false);
    if (deleted) setDeleteOpen(false);
  }

  const renderProjectListItem = (item: ApiProject) => (
    <button
      type="button"
      key={item.id}
      className={item.id === activeProjectId ? "project-list-item active" : "project-list-item"}
      aria-current={item.id === activeProjectId ? "page" : undefined}
      onClick={() => switchProject(item.id)}
    >
      <strong>{item.name}</strong>
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
          if (group.projects.length === 1) {
            return <section className="project-list-group singleton" key={group.id}>{renderProjectListItem(group.projects[0])}</section>;
          }
          const collapsed = collapsedSeriesIds.has(group.id);
          const contentId = `project-series-${group.id}`;
          return (
            <section className={`project-list-group${collapsed ? " collapsed" : ""}`} key={group.id}>
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
                  <span><small>検討グループ</small><strong>{group.name}</strong></span>
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
        <button type="button" className="outline-button project-list-create" disabled={createOpen} onClick={toggleCreateProject}>＋ 新規プロジェクト</button>
      </aside>
      <div className="project-hub-content">
        <div className="page-intro project-hub-header">
          <div>
            <span className="overline">PROJECT OVERVIEW</span>
            <h2>{project?.name ?? "プロジェクト"}</h2>
            <p>{project?.purpose || project?.description || "検討の入口、候補比較、判断時点の記録をここからたどれます。"}</p>
          </div>
          <div className="project-actions">
            <button className="outline-button" disabled={taskUnavailable} onClick={continueCurrentProject}>このプロジェクトの続き</button>
            <button className="outline-button" disabled={taskUnavailable} onClick={() => setSettingsOpen((value) => !value)}>設定を編集</button>
          </div>
        </div>
      {taskUnavailable && <section className="task-unavailable-banner" role="status">
        <strong>この予測タスクは一時的に利用できません</strong>
        <span>{taskAvailability.message}</span>
        <small>保存済みの候補・予測・実測・判断履歴は参照できます。推論と変更操作は停止しています。</small>
      </section>}
      {chainIdentity && <section className="task-unavailable-banner chain-ready-banner" role="status">
        <strong>Chain Revisionを固定したプロジェクトです</strong>
        <span>A → B → Cの段別鮮度と中間実測を、候補作業面で確認できます。</span>
        <small>このモードでは範囲探索とデータ探索を利用できません。下の「次の作業」から候補作業面へ進みます。</small>
      </section>}
      {error && <p className="panel-error" role="alert">{error}</p>}
      {project && (chainIdentity
        ? <section className="project-reference-strip" aria-label="プロジェクトのChain参照と所属">
          <div><span>参照Chain</span><strong>{fixedChain?.definition.label ?? "Chain未解決"}</strong><small>A → B → C</small></div>
          <div><span>固定した版</span><strong>{fixedChainRevision ? `r${fixedChainRevision.revision}` : "—"}</strong><small>全Stageの参照をこの版に固定</small></div>
          <div><span>固定Stage</span><strong>{fixedChainRevision?.stages.map((stage) => stage.stage_id).join(" → ") ?? "—"}</strong><small>Package・データセット・プロファイルを版の中に固定</small></div>
          <div><span>検討グループ</span><strong>{fixedSeries?.name ?? "—"}</strong><small>設定から変更できます</small></div>
          <ReferenceIdentityDetails items={[["Chain Revision", chainIdentity.chain_revision_digest]]} />
        </section>
        : <section className="project-reference-strip" aria-label="プロジェクトの参照と所属">
          <div><span>参照データセット</span><strong title={fixedDataset?.data_asset.original_filename}>{fixedDataset?.data_asset.original_filename ?? "—"}</strong><small>{fixedDataset ? `${fixedDataset.profile_revision.name} · r${fixedDataset.profile_revision.revision}` : ""}</small></div>
          <div><span>予測タスク</span><strong>{taskLabels.get(project.task_id) ?? project.task_id}</strong><small>固定</small></div>
          <div><span>予測モデル</span><strong>{modelPackageDisplayName(fixedPackage)}</strong><small title={fixedTrainingDataset ? datasetDisplayName(fixedTrainingDataset) : undefined}>学習元: {fixedTrainingDataset ? datasetDisplayName(fixedTrainingDataset) : "未登録または記録なし"}</small></div>
          <div><span>探索範囲（Design Space）</span><strong>{project.design_space ? `${project.design_space.name} · r${project.design_space.revision}` : "この検討では未設定"}</strong><small>{project.design_space ? bindingProvenanceLabel(project.design_space_binding_provenance, "Taskの許容範囲から生成") : "保存済みの探索結果はそのまま参照できます"}</small></div>
          <div><span>判断基準（Objective）</span><strong>{project.objective_definition ? `${project.objective_definition.name} · r${project.objective_definition.revision}` : "この検討では未設定"}</strong><small>{project.objective_definition ? bindingProvenanceLabel(project.objective_binding_provenance, "プロジェクト目標から生成") : "探索を実行すると、その時点の判断基準を固定します"}</small></div>
          <div><span>検討グループ</span><strong>{fixedSeries?.name ?? "—"}</strong><small>設定から変更できます</small></div>
          <ReferenceIdentityDetails items={[
            ["Model Package manifest", project.model_package_manifest_digest],
            ["Task contract", project.task_contract_digest],
            ["Design Space", project.design_space_digest],
            ["Objective", project.objective_definition_digest],
          ]} />
        </section>)}
      {chainIdentity && (chainEvaluation?.projectId === activeProjectId
        ? <ChainEvaluationPanel evaluation={chainEvaluation.value} />
        : <section className="chain-evaluation-panel loading" aria-live="polite">Chain評価を読み込んでいます。</section>)}
      {project && <section className={`project-goal-strip${configuredTargets.length ? "" : " unset"}`} aria-label="プロジェクトの目標値">
        <div className="project-goal-heading"><span>目標値</span><strong>{configuredTargets.length ? "候補を判断する基準" : "候補を探す前に設定"}</strong></div>
        <div className="project-goal-values">
          {configuredTargets.length
            ? configuredTargets.map((output) => <span key={output.key}><b>{output.label}</b>{targetGoalText(savedTargetValues[output.key], output.goal_direction, formatNumber)} {output.unit}</span>)
            : <span>未設定です。設定すると候補の目標達成率を比較できます。</span>}
        </div>
        <button className={configuredTargets.length ? "outline-button" : "primary-button"} disabled={taskUnavailable || chainExecutionPending} onClick={focusTargetSettings}>{configuredTargets.length ? "目標値を変更" : "目標値を設定"}</button>
      </section>}
      {predecessorProject && <section className="project-continuation-link" aria-label="このプロジェクトの続き元"><span>続き元</span><button type="button" onClick={() => onSwitch(predecessorProject.id)}>{predecessorProject.name}</button><small>{predecessorSeries?.name ?? "所属グループ不明"}{project?.continuation_reason ? ` · ${project.continuation_reason}` : ""}</small></section>}

      {createOpen && <section className="project-create-panel" aria-label="新規プロジェクトの開始方法">
        <div className="panel-title project-create-heading">
          <div><h3>新しいプロジェクト</h3><span>開始方法を選んでから作成します</span></div>
          <button type="button" className="outline-button" onClick={closeCreateProject}>作成をやめる</button>
        </div>
        <label>プロジェクト名<input ref={projectNameInputRef} value={newProjectName} onChange={(event) => setNewProjectName(event.target.value)} placeholder="例: 2026年7月 焼鈍条件の再検討" /></label>
        <div className="project-binding-flow">
          <label><b aria-hidden="true">1</b><span>Dataset</span><select disabled={createMode === "copy"} value={newDatasetViewId} onChange={(event) => { setNewDatasetViewId(event.target.value); setNewTaskId(""); setNewModelPackageRefId(""); setNewChainId(""); setNewChainRevisionId(""); }}><option value="">選択してください</option>{(creationOptions?.dataset_views ?? []).filter((item) => item.kind === "single").map((view) => <option key={view.id} value={view.id}>{view.name} · {datasetByView.get(view.id)?.profile_revision.name}</option>)}</select></label>
          <label><b aria-hidden="true">2</b><span>予測構成</span><select disabled={createMode === "copy" || !newDatasetViewId} value={createMode === "copy" ? `task:${copyTaskId ?? ""}` : newChainId ? `chain:${newChainId}` : newTaskId ? `task:${newTaskId}` : ""} onChange={(event) => { const [kind, id] = event.target.value.split(":", 2); setNewTaskId(kind === "task" ? id : ""); setNewModelPackageRefId(""); setNewChainId(kind === "chain" ? id : ""); setNewChainRevisionId(""); }}><option value="">{newDatasetViewId ? "選択してください" : "先にDatasetを選択"}</option>{catalog.filter((item) => availableTaskIds.includes(item.definition.task_definition.id)).map((item) => <option key={item.definition.task_definition.id} value={`task:${item.definition.task_definition.id}`}>{item.definition.task_definition.label}（単一Task）</option>)}{availableChains.map((item) => <option key={item.definition.chain_id} value={`chain:${item.definition.chain_id}`}>{item.definition.label}（Chain）</option>)}</select></label>
          <label><b aria-hidden="true">3</b><span>{newChainId ? "Chain Revision" : "Model Package"}</span>{newChainId ? <select disabled={!newChainId} value={newChainRevisionId} onChange={(event) => setNewChainRevisionId(event.target.value)}><option value="">Revisionを選択</option>{selectedChain?.revisions.map((revision) => { const id = `${revision.chain_id}:r${revision.revision}`; return <option key={id} value={id}>r{revision.revision} · {revision.stages.map((stage) => stage.stage_id).join(" → ")}</option>; })}</select> : <select disabled={createMode === "copy" || !newTaskId} value={newModelPackageRefId} onChange={(event) => setNewModelPackageRefId(event.target.value)}><option value="">{newTaskId ? "手法を選択してください" : "先にPrediction Taskを選択"}</option>{availablePackages.map((item) => <option key={item.id} value={item.id}>{modelPackageDisplayName(item)}</option>)}</select>}</label>
          <label><b aria-hidden="true">4</b><span>所属グループ</span><select value={newProjectSeriesId} onChange={(event) => setNewProjectSeriesId(event.target.value)}><option value="">新しいグループを作成</option>{activeProjectSeries.map((series) => <option key={series.id} value={series.id}>{series.name}</option>)}</select></label>
        </div>
        {selectedPackage && !newChainId && <ModelPackageDecisionCard modelPackage={selectedPackage} />}
        <section className="project-binding-confirmation" aria-label="作成後に固定される内容">
          <header><strong>作成後に固定される内容</strong><span>{newChainId ? "Chain Revisionと各StageのPackage・Dataset・Profileは後から変わりません" : "Dataset・Prediction Task・Model Packageは後から変更できません"}</span></header>
          <div><span>参照Dataset</span><strong>{selectedDataset ? datasetDisplayName(selectedDataset) : "選択してください"}</strong><small>{selectedDataset ? `${selectedDataset.profile_revision.name} · r${selectedDataset.profile_revision.revision}` : "DatasetとProfileを選択"}</small></div>
          <div><span>{newChainId ? "Chain Template" : "Prediction Task"}</span><strong>{newChainId ? selectedChain?.definition.label ?? "選択してください" : taskLabels.get(selectedTaskId) ?? (selectedTaskId || "選択してください")}</strong><small>{newChainId ? "再利用可能なStageをbindingで接続" : "Projectの予測目的"}</small></div>
          <div><span>{newChainId ? "Chain Revision" : "Model Package"}</span><strong>{newChainId ? selectedChainRevision ? `r${selectedChainRevision.revision}` : "選択してください" : selectedPackage ? modelPackageDisplayName(selectedPackage) : "選択してください"}</strong><small>{newChainId ? selectedChainRevision ? selectedChainRevision.stages.map((stage) => `${stage.stage_id}:${stage.package_manifest_digest.slice(7, 15)}`).join(" · ") : "Revisionを選択してください" : `学習元: ${selectedPackage ? selectedTrainingDataset ? datasetDisplayName(selectedTrainingDataset) : "未登録または記録なし" : "Model Packageを選択してください"}`}</small></div>
        </section>
        <div className="project-group-summary"><span>所属グループ</span><strong>{selectedSeries?.name ?? (newProjectName.trim() || "プロジェクト名から新規作成")}</strong><small>{selectedSeries ? "既存グループに追加" : "プロジェクト名で新しいグループを作成"}</small></div>
        {predecessorProjectId && <label>続ける理由（任意）<textarea value={continuationReason} onChange={(event) => setContinuationReason(event.target.value)} placeholder="予測タスク変更、データ追加、条件変更、判断の再検討など" /></label>}
        <div className="project-start-options">
          <label><input type="radio" checked={createMode === "empty"} onChange={() => setCreateMode("empty")} />空から開始<span>候補を持たない検討として作成</span></label>
          <label><input type="radio" checked={createMode === "copy"} disabled={Boolean(newChainId) || taskUnavailable || !candidate || Boolean(predecessorProjectId)} onChange={() => { setCreateMode("copy"); setNewChainId(""); setNewChainRevisionId(""); if (project) { setNewDatasetViewId(project.dataset_view_revision_id ?? ""); setNewTaskId(project.task_id); setNewModelPackageRefId(project.model_package_ref_id ?? ""); } }} />現在候補をコピー<span>{newChainId ? "Chain Projectは空から開始します" : taskUnavailable ? "利用停止中のタスクからはコピーできません" : candidate ? `${candidate.label}（編集版 ${candidate.raw.revision}）` : "コピーできる候補がありません"}</span></label>
        </div>
        <button className="primary-button" disabled={!newProjectName.trim() || !newDatasetViewId || (newChainId ? !newChainRevisionId : !(createMode === "copy" ? copyTaskId : newTaskId) || !newModelPackageRefId)} onClick={() => void createProject()}>固定してプロジェクトを作成</button>
      </section>}

      {settingsOpen && project && <section className="project-settings-panel">
        <div className="project-form">
          <div className="group-membership-setting"><label>所属グループ<select value={groupMembershipId} onChange={(event) => setGroupMembershipId(event.target.value)}>{activeProjectSeries.map((series) => <option key={series.id} value={series.id}>{series.name}</option>)}</select></label><button className="outline-button" disabled={!groupMembershipId || groupMembershipId === project.project_series_id} onClick={() => void moveProjectToGroup()}>このプロジェクトを移動</button><small>現在のプロジェクトだけを移動します。候補・判断履歴・続き元は変わりません</small></div>
          <div className="series-name-setting"><label>グループ名<input value={seriesName} onChange={(event) => setSeriesName(event.target.value)} /></label><button className="outline-button" disabled={!seriesName.trim()} onClick={() => void saveSeriesName()}>名前を保存</button><small>このグループに含まれるすべてのプロジェクトへ反映されます</small></div>
          <label>プロジェクト名<input value={project.name} onChange={(event) => setProject({ ...project, name: event.target.value })} /></label>
          <label>説明<textarea value={project.description} onChange={(event) => setProject({ ...project, description: event.target.value })} /></label>
          <label>目的<textarea value={project.purpose} onChange={(event) => setProject({ ...project, purpose: event.target.value })} /></label>
          <fieldset className="target-grid" id="project-target-settings"><legend>目標値</legend>{(taskDefinition?.outputs ?? []).map((output) => {
            const goal = targetValues[output.key];
            const range = isTargetRange(goal) ? goal : undefined;
            const rangeOnly = output.goal_direction === "target";
            const showRange = rangeOnly || range != null;
            const rangeDraft = range ?? { lower: Number.NaN, upper: Number.NaN };
            return <div className="target-setting" key={output.key}>
              <label>{output.label}<select disabled={rangeOnly} value={showRange ? "between" : "directional"} onChange={(event) => setTargetMode(output.key, event.target.value as "directional" | "between")}><option value="directional">{defaultGoalLabel(output.goal_direction)}</option><option value="between">範囲内</option></select></label>
              {showRange
                ? <div className="target-range-inputs"><label>下限<input type="number" value={Number.isFinite(rangeDraft.lower) ? rangeDraft.lower : ""} placeholder="下限" onChange={(event) => setRangeTarget(output.key, "lower", event.target.value)} /></label><span>–</span><label>上限<input type="number" value={Number.isFinite(rangeDraft.upper) ? rangeDraft.upper : ""} placeholder="上限" onChange={(event) => setRangeTarget(output.key, "upper", event.target.value)} /></label></div>
                : <input type="number" value={typeof goal === "number" ? goal : ""} placeholder="未設定" onChange={(event) => setScalarTarget(output.key, event.target.value)} />}
            </div>;
          })}{invalidTargetRange && <small className="target-range-error">範囲目標は、下限を上限より小さく設定してください。</small>}</fieldset>
          <label>メモ<textarea value={project.notes} onChange={(event) => setProject({ ...project, notes: event.target.value })} /></label>
        </div>
        <button className="primary-button" disabled={!project.name.trim() || invalidTargetRange} onClick={() => void saveProject()}>設定を保存</button>
      </section>}

      <section className="project-next-actions">
        <div className="panel-title"><h3>次の作業</h3><span>{activeCandidates.length ? `${activeCandidates.length}候補を検討中` : "まだ候補がありません"}</span></div>
        {chainIdentity
          ? <div className="project-action-grid">
            <button className="project-action-card primary" disabled={chainExecutionPending} onClick={() => onNavigate("candidates")}><strong>Chain候補を開く</strong><span>配合と工程条件を編集し、A → B → Cを実行して固定します</span></button>
          </div>
          : <div className="project-action-grid">
            <button className="project-action-card primary" disabled={taskUnavailable || chainExecutionPending} onClick={() => onNavigate(activeCandidates.length ? "candidates" : supportsLineageCandidate ? "lineage" : "explore")}><strong>{activeCandidates.length ? "候補を比較" : supportsLineageCandidate ? "過去データから探す" : "条件範囲から始める"}</strong><span>{activeCandidates.length ? "入力・予測・根拠を横並びで確認" : supportsLineageCandidate ? "既存の条件と問題から出発" : "基準候補を作り、入力範囲から探索"}</span></button>
            <button className="project-action-card" disabled={taskUnavailable || chainExecutionPending} onClick={() => onNavigate("explore")}><strong>範囲探索</strong><span>目標と入力範囲から候補を生成</span></button>
            <button className="project-action-card" disabled={taskUnavailable || chainExecutionPending} onClick={() => onNavigate("candidates")}><strong>直接候補を作る</strong><span>具体的な成分・工程条件を入力</span></button>
          </div>}
      </section>

      <section className="project-history-section">
        <div className="panel-title"><h3>候補と判断履歴</h3><span>現在値と固定した予測を分けて表示</span></div>
        {!history ? <p className="empty-evidence">履歴を読み込んでいます。</p> : !history.candidates.length ? <div className="project-empty-state"><p>{supportsLineageCandidate ? "候補はまだありません。上の「次の作業」から過去データを探すと、由来付き候補としてここに残ります。" : "候補はまだありません。上の「次の作業」から基準候補を用意し、検討を始めます。"}</p></div> : <div className="project-history-list">
          {history.candidates.map((item) => {
            const preview = currentPreviews[item.candidate.id];
            return <article className="project-history-card" key={item.candidate.id}>
              <header><div><strong>{item.candidate.name}</strong>{item.candidate.archived_at && <span className="muted-badge">archive</span>}</div><button className="outline-button" disabled={taskUnavailable || Boolean(item.candidate.archived_at)} onClick={() => onNavigate("candidates", item.candidate.id)}>現在の候補を見る</button></header>
              <div className="history-current-row"><span className="history-kind current">現在</span><span>編集版 {item.current.revision}</span><span>{formatDate(item.current.updated_at)}</span><span className={item.candidate.provenance?.source_kind === "lineage" ? "history-origin reference-data" : "history-origin"}>{item.candidate.provenance?.source_kind === "lineage" && <b>参照データ由来</b>}{item.candidate.provenance ? provenanceLabel(item.candidate.provenance) : "由来不明"}</span></div>
              {preview ? <div className="history-preview"><span>現在のpreview</span>{orderedPredictions(preview.predictions).map(([key, value]) => { const assessment = assessPrediction(outputDefinition(key), value); return <strong className={assessment.implausible ? "implausible-output" : undefined} title={assessment.warning ?? undefined} key={key}>{outputLabels.get(key) ?? key} {formatPredictionPoint(value, (numberValue) => formatOutputNumber(key, numberValue))}{assessment.implausible && <small className="output-warning-badge">⚠ 物理範囲外</small>}</strong>; })}</div> : <p className="history-muted">現在のpreviewは未計算です。候補比較を開くと必要な候補だけ計算します。</p>}
              {item.snapshots.length ? <div className="history-snapshots">{item.snapshots.map((snapshot) => <div className="history-snapshot-row" key={snapshot.id}>
                <span className="history-kind fixed">固定した予測</span>{item.decision?.snapshot_id === snapshot.id && <span className="decision-snapshot-badge">採用判断</span>}<span>編集版 {snapshot.candidate_revision ?? "不明（旧形式）"}</span><span>{formatDate(snapshot.created_at)}</span>
                <span className="history-predictions">{orderedPredictions(snapshot.prediction_summary).map(([key, value], index) => { const assessment = assessPrediction(outputDefinition(key), value); return <span className={assessment.implausible ? "implausible-output" : undefined} title={assessment.warning ?? undefined} key={key}>{index > 0 && " / "}{outputLabels.get(key) ?? key} {formatPredictionPoint(value, (numberValue) => formatOutputNumber(key, numberValue))}{assessment.implausible && <small className="output-warning-badge">⚠ 物理範囲外</small>}</span>; })}</span>
                {item.actuals.filter((actual) => actual.snapshot_id === snapshot.id).map((actual) => { const definition = outputDefinition(actual.property); const assessment = assessOutputValues(definition, [actual.mean], "実測値"); const key = definition?.key ?? actual.property; return <span className={`history-actual${assessment.implausible ? " implausible-output" : ""}`} title={assessment.warning ?? undefined} key={actual.id}>実測 {definition?.label ?? outputLabels.get(actual.property) ?? actual.property} {formatOutputNumber(key, actual.mean)} ± {formatOutputNumber(key, actual.std)} {definition?.unit ?? actual.unit}{actual.experiment_no ? ` / ${actual.experiment_no}` : ""}{assessment.implausible && <small className="output-warning-badge">⚠ 物理範囲外</small>}</span>; })}
                {item.decision?.snapshot_id === snapshot.id && <span className="decision-note-inline">判断理由: {item.decision.note}</span>}
                <button className="outline-button" onClick={() => void openSnapshot(snapshot.id)}>詳細</button><CandidateAddButton compact disabled={taskUnavailable} onClick={() => void restoreSnapshot(snapshot.id)}>新しい候補として複製</CandidateAddButton>
              </div>)}</div> : <div className="project-empty-inline"><span>固定した予測はありません。上の「現在の候補を見る」から詳細予測を保存すると判断時点が残ります。</span></div>}
            </article>;
          })}
        </div>}
      </section>

      {selectedSnapshot?.payload.prediction && <section className="snapshot-detail project-snapshot-detail">
        <div className="panel-title"><h3>固定した予測の詳細</h3><button className="outline-button" onClick={() => { setSelectedSnapshot(null); onSnapshotNavigate(undefined); }}>閉じる</button></div>
        <p>{formatDate(selectedSnapshot.created_at)} / {history?.candidates.find((item) => item.candidate.id === selectedSnapshot.candidate_id)?.candidate.name ?? "保存時の候補"}</p>
        <span className="decision-snapshot-badge">{!selectedSnapshot.payload.provenance?.package?.manifest_sha256 || !modelPackage ? "予測モデル情報を確認できません" : selectedSnapshot.payload.provenance.package.manifest_sha256 === modelPackage.manifest_sha256 ? "現在と同じ予測モデル" : "現在とは別の予測モデル"}</span>
        <table className="quality-table"><thead><tr><th>特性</th><th>固定予測</th><th>区間・分位</th><th>目標達成</th></tr></thead><tbody>{orderedPredictions(selectedSnapshot.payload.prediction.predictions).map(([key, value]) => { const assessment = assessPrediction(outputDefinition(key), value); return <tr className={assessment.implausible ? "implausible-output" : undefined} key={key}><th>{outputLabels.get(key) ?? key}{assessment.implausible && <small className="output-warning-badge">⚠ 物理範囲外</small>}</th><td title={assessment.warning ?? undefined}>{formatPredictionPoint(value, (numberValue) => formatOutputNumber(key, numberValue))}</td><td>{predictionHasInterval(value) ? <>{formatOutputNumber(key, value.lower)}–{formatOutputNumber(key, value.upper)} <small>{predictionIntervalLabel(value)}</small></> : "利用不可"}</td><td>{value.goal_probability == null ? value.goal_value == null ? "目標未設定" : "利用不可" : `${formatNumber(value.goal_probability * 100, 0)}%`}</td></tr>; })}</tbody></table>
        <div className="snapshot-decision-form"><label>判断理由<textarea disabled={taskUnavailable} value={decisionNote} onChange={(event) => { decisionDraftRef.current.dirty = true; setDecisionNote(event.target.value); }} placeholder="この時点の予測を採用判断に使う理由" /></label><button className="outline-button" disabled={taskUnavailable} onClick={() => void saveDecision(false)}>採用判断として固定</button>{project?.decision_snapshot_id === selectedSnapshot.id && <button className="outline-button" disabled={taskUnavailable} onClick={() => void saveDecision(true)}>採用判断を解除</button>}</div>
        <CandidateAddButton disabled={taskUnavailable} onClick={() => void restoreSnapshot(selectedSnapshot.id)}>この時点から新しい候補を作る</CandidateAddButton>
      </section>}

      {canDeleteProject && project && <section className="project-danger-zone" aria-label="プロジェクト削除">
        {!deleteOpen ? <button className="danger-outline-button" onClick={() => setDeleteOpen(true)}>プロジェクトを削除</button> : <div className="project-delete-panel" aria-label="プロジェクト削除の確認">
          <div><strong>「{project.name}」を削除しますか？</strong><p>候補・予測履歴・実測データもまとめて削除され、元に戻せません。</p></div>
          <div className="project-delete-actions"><button className="danger-button" disabled={deleting} onClick={() => void deleteCurrentProject()}>{deleting ? "削除中…" : "削除する"}</button><button className="outline-button" disabled={deleting} onClick={() => setDeleteOpen(false)}>キャンセル</button></div>
        </div>}
      </section>}
      </div>
    </div>
  );
}
