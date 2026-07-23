import { useEffect, useMemo, useRef, useState } from "react";
import { provenanceLabel } from "../../shared/candidateProvenance";
import { formatPredictionPoint, predictionHasInterval, predictionIntervalLabel } from "../../shared/predictionPresentation";
import { assessOutputValues, assessPrediction, resolveOutputDefinition } from "../../shared/outputPresentation";
import { CandidateAddButton } from "../../shared/ui/CandidateAddButton";
import {
  compatiblePackagesForTask,
  compatibleTaskIdsForDataset,
  datasetDisplayName,
  initialProjectBindingForDataset,
  trainingDataset,
} from "../../shared/dataLibraryPresentation";
import { fromApiCandidate, toApiCandidate, type CandidateViewModel, type RuntimeOperations, type TaskDefinitionContract } from "../candidates";
import {
  workbenchApi,
  type ApiModelPackage,
  type ApiPreview,
  type ApiProject,
  type ApiProjectHistory,
  type ApiProjectCreationOptions,
  type ApiSnapshot,
  type ApiTaskCatalogItem,
} from "../../shared/api/workbench-api";

type Props = {
  projects: ApiProject[];
  activeProjectId: string;
  candidate?: CandidateViewModel;
  taskDefinition: TaskDefinitionContract | null;
  supportsLineageCandidate: boolean;
  operations?: RuntimeOperations;
  currentPreviews: Record<string, ApiPreview>;
  requestedSnapshotId?: string;
  requestedDatasetViewId?: string;
  onProjectChanged: (project: ApiProject) => void;
  onProjectDeleted: (projectId: string) => Promise<boolean>;
  onSwitch: (projectId: string) => void;
  onRestore: (candidate: CandidateViewModel) => void;
  onNavigate: (view: "candidates" | "lineage" | "explore" | "settings", candidateId?: string) => void;
  onSnapshotNavigate: (snapshotId?: string) => void;
  onCreationIntentConsumed: () => void;
};

const formatNumber = (value: number, digits = 1) => value.toLocaleString("ja-JP", { maximumFractionDigits: digits });
const formatDate = (value: string) => new Date(value).toLocaleString("ja-JP");

export function ProjectHub({
  projects,
  activeProjectId,
  candidate,
  taskDefinition,
  supportsLineageCandidate,
  operations,
  currentPreviews,
  requestedSnapshotId,
  requestedDatasetViewId,
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
  const [newProjectSeriesId, setNewProjectSeriesId] = useState("");
  const [predecessorProjectId, setPredecessorProjectId] = useState("");
  const [continuationReason, setContinuationReason] = useState("");
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
    const controller = new AbortController();
    setHistory(null);
    setSelectedSnapshot(null);
    setModelPackage(null);
    void Promise.all([
      reloadHistory(controller.signal),
      workbenchApi.listTaskDefinitions().then((items) => {
        if (!controller.signal.aborted) {
          setCatalog(items);
          setNewTaskId((current) => current || items[0]?.definition.task_definition.id || "");
        }
      }),
      workbenchApi.modelPackage(activeProjectId).then((item) => !controller.signal.aborted && activeProjectRef.current === activeProjectId && setModelPackage(item)),
      workbenchApi.projectCreationOptions().then((item) => !controller.signal.aborted && setCreationOptions(item)),
    ]).catch((cause) => {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "プロジェクト概要を取得できませんでした。");
    });
    return () => controller.abort();
  }, [activeProjectId]);

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
    ? compatiblePackagesForTask(newTaskId, creationOptions)
    : [];
  const fixedDataset = project?.dataset_view_revision_id ? datasetByView.get(project.dataset_view_revision_id) : undefined;
  const fixedPackage = creationOptions?.model_packages.find((item) => item.id === project?.model_package_ref_id);
  const fixedSeries = creationOptions?.project_series.find((item) => item.id === project?.project_series_id);
  const selectedSeries = creationOptions?.project_series.find((item) => item.id === newProjectSeriesId);
  const selectedPackage = creationOptions?.model_packages.find((item) => item.id === newModelPackageRefId);
  const selectedTrainingDataset = trainingDataset(selectedPackage, creationOptions?.datasets ?? []);
  const fixedTrainingDataset = trainingDataset(fixedPackage, creationOptions?.datasets ?? []);
  const selectedTaskId = createMode === "copy" ? copyTaskId ?? "" : newTaskId;
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
    const binding = initialProjectBindingForDataset(dataset, creationOptions);
    focusCreationFormRef.current = true;
    setCreateOpen(true);
    setCreateMode("empty");
    setNewProjectName(`${datasetDisplayName(dataset)} 検討`);
    setNewDatasetViewId(requestedDatasetViewId);
    setNewTaskId(binding.taskId);
    setNewModelPackageRefId(binding.modelPackageRefId);
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

  async function createProject() {
    const taskId = createMode === "copy" ? copyTaskId : newTaskId;
    if (!taskId || !newProjectName.trim() || !newDatasetViewId || !newModelPackageRefId) return setError("Dataset・予測タスク・Model Packageを確認してください。");
    if (predecessorProjectId && !continuationReason.trim()) return setError("この検討を続ける理由を入力してください。");
    if (createMode === "copy" && !candidate) return setError("コピーする現在候補がありません。");
    try {
      const initialCandidate = createMode === "copy" && candidate ? {
        ...toApiCandidate(candidate),
        name: `${candidate.label} のコピー`,
        provenance: { source_kind: "copy" as const, source_ref: { project_id: candidate.raw.project_id, candidate_id: candidate.id, candidate_revision: candidate.raw.revision } },
      } : null;
      const created = await workbenchApi.createProject({
        name: newProjectName.trim(), description: "", purpose: "", task_id: taskId as ApiProject["task_id"],
        target_values: {}, input_ranges: {}, notes: "", decision_candidate_id: "", decision_snapshot_id: "", decision_note: "",
        initial_candidate: initialCandidate,
        dataset_view_revision_id: newDatasetViewId || undefined,
        model_package_ref_id: newModelPackageRefId || undefined,
        task_contract_digest: selectedPackage?.task_contract_digest ?? "",
        model_package_manifest_digest: selectedPackage?.manifest_digest ?? "",
        project_series_id: newProjectSeriesId || undefined,
        predecessor_project_id: predecessorProjectId || undefined,
        continuation_reason: continuationReason,
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

  const targetValues = (project?.target_values ?? {}) as Record<string, number>;
  const setTarget = (key: string, value: string) => {
    if (!project) return;
    const next = { ...targetValues };
    if (value === "") delete next[key]; else next[key] = Number(value);
    setProject({ ...project, target_values: next });
  };

  const toggleCreateProject = () => {
    const nextOpen = !createOpen;
    if (nextOpen) focusCreationFormRef.current = true;
    setCreateOpen(nextOpen);
    setCreateMode("empty");
    setNewProjectName("");
    setNewTaskId("");
    setNewDatasetViewId("");
    setNewModelPackageRefId("");
    setNewProjectSeriesId("");
    setPredecessorProjectId("");
    setContinuationReason("");
  };

  const continueCurrentProject = () => {
    if (!project) return;
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

  const canDeleteProject = project != null && !["default", "hot-rolling-default"].includes(project.id);

  async function deleteCurrentProject() {
    if (!project || !canDeleteProject || deleting) return;
    setDeleting(true);
    const deleted = await onProjectDeleted(project.id);
    setDeleting(false);
    if (deleted) setDeleteOpen(false);
  }

  return (
    <div className="page-panel project-hub">
      <aside className="project-list-panel" aria-label="プロジェクト一覧">
        <div className="project-list-heading">
          <div><span className="overline">WORKSPACES</span><h2>プロジェクト</h2></div>
          <small>{projects.length}件</small>
        </div>
        <div className="project-list-items">{projectGroups.map((group) => {
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
                  <span><small>一連の検討</small><strong>{group.name}</strong></span>
                  <em>{group.projects.length}件</em>
                  <i aria-hidden="true" />
                </button>
              </header>
              <div className="project-list-group-projects" id={contentId} hidden={collapsed}>
                {group.projects.length ? group.projects.map((item) => (
                  <button
                    type="button"
                    key={item.id}
                    className={item.id === activeProjectId ? "project-list-item active" : "project-list-item"}
                    aria-current={item.id === activeProjectId ? "page" : undefined}
                    onClick={() => onSwitch(item.id)}
                  >
                    <strong>{item.name}</strong>
                    <small>{datasetByView.get(item.dataset_view_revision_id ?? "")?.data_asset.original_filename.replace(/\.xlsx$/i, "") ?? "Dataset未解決"} · {taskLabels.get(item.task_id) ?? item.task_id}</small>
                  </button>
                )) : <small className="project-list-group-empty">プロジェクトなし</small>}
              </div>
            </section>
          );
        })}</div>
        <button type="button" className="outline-button project-list-create" onClick={toggleCreateProject}>＋ 新規プロジェクト</button>
      </aside>
      <div className="project-hub-content">
        <div className="page-intro project-hub-header">
          <div>
            <span className="overline">PROJECT OVERVIEW</span>
            <h2>{project?.name ?? "プロジェクト"}</h2>
            <p>{project?.purpose || project?.description || "検討の入口、候補比較、判断時点の記録をここからたどれます。"}</p>
          </div>
          <div className="project-actions">
            <button className="outline-button" onClick={continueCurrentProject}>この検討の続き</button>
            <button className="outline-button" onClick={() => setSettingsOpen((value) => !value)}>設定を編集</button>
            {canDeleteProject && <button className="danger-outline-button" onClick={() => setDeleteOpen((value) => !value)}>プロジェクトを削除</button>}
          </div>
        </div>
      {error && <p className="panel-error" role="alert">{error}</p>}
      {project && <section className="project-reference-strip" aria-label="プロジェクトの固定参照"><div><span>参照Dataset</span><strong>{fixedDataset?.data_asset.original_filename ?? "—"}</strong><small>{fixedDataset ? `${fixedDataset.profile_revision.name} · r${fixedDataset.profile_revision.revision}` : ""}</small></div><div><span>Prediction Task</span><strong>{taskLabels.get(project.task_id) ?? project.task_id}</strong><small>固定</small></div><div><span>Model Package</span><strong>{fixedPackage?.package_id ?? "—"}</strong><small>学習元: {fixedTrainingDataset ? datasetDisplayName(fixedTrainingDataset) : "未登録または記録なし"} · Manifest {project.model_package_manifest_digest.slice(0, 10)}</small></div><div><span>一連の検討</span><strong>{fixedSeries?.name ?? "—"}</strong><small>{project.predecessor_project_id ? `継続: ${project.continuation_reason}` : "起点"}</small></div></section>}

      {deleteOpen && project && <section className="project-delete-panel" aria-label="プロジェクト削除の確認">
        <div><strong>「{project.name}」を削除しますか？</strong><p>候補・予測履歴・実測データもまとめて削除され、元に戻せません。</p></div>
        <div className="project-delete-actions"><button className="danger-button" disabled={deleting} onClick={() => void deleteCurrentProject()}>{deleting ? "削除中…" : "削除する"}</button><button className="outline-button" disabled={deleting} onClick={() => setDeleteOpen(false)}>キャンセル</button></div>
      </section>}

      {createOpen && <section className="project-create-panel" aria-label="新規プロジェクトの開始方法">
        <div className="panel-title"><h3>新しいプロジェクト</h3><span>開始方法を選んでから作成します</span></div>
        <label>プロジェクト名<input ref={projectNameInputRef} value={newProjectName} onChange={(event) => setNewProjectName(event.target.value)} placeholder="例: 2026年7月 焼鈍条件の再検討" /></label>
        <div className="project-binding-flow">
          <label><b aria-hidden="true">1</b><span>Dataset</span><select disabled={createMode === "copy"} value={newDatasetViewId} onChange={(event) => { const viewId = event.target.value; const dataset = datasetByView.get(viewId); const binding = creationOptions ? initialProjectBindingForDataset(dataset, creationOptions) : { taskId: "", modelPackageRefId: "" }; setNewDatasetViewId(viewId); setNewTaskId(binding.taskId); setNewModelPackageRefId(binding.modelPackageRefId); }}><option value="">選択してください</option>{(creationOptions?.dataset_views ?? []).filter((item) => item.kind === "single").map((view) => <option key={view.id} value={view.id}>{view.name} · {datasetByView.get(view.id)?.profile_revision.name}</option>)}</select></label>
          <label><b aria-hidden="true">2</b><span>予測タスク（Prediction Task）</span><select disabled={createMode === "copy"} value={createMode === "copy" ? copyTaskId : newTaskId} onChange={(event) => { const taskId = event.target.value; const packages = creationOptions ? compatiblePackagesForTask(taskId, creationOptions) : []; setNewTaskId(taskId); setNewModelPackageRefId(packages.length === 1 ? packages[0].id : ""); }}><option value="">選択してください</option>{catalog.filter((item) => availableTaskIds.includes(item.definition.task_definition.id)).map((item) => <option key={item.definition.task_definition.id} value={item.definition.task_definition.id}>{item.definition.task_definition.label}</option>)}</select></label>
          <label><b aria-hidden="true">3</b><span>Model Package</span><select disabled={createMode === "copy"} value={newModelPackageRefId} onChange={(event) => setNewModelPackageRefId(event.target.value)}><option value="">選択してください</option>{availablePackages.map((item) => <option key={item.id} value={item.id}>{item.package_id}</option>)}</select></label>
          <label><b aria-hidden="true">4</b><span>検討のつながり</span><select disabled={Boolean(predecessorProjectId)} value={newProjectSeriesId} onChange={(event) => setNewProjectSeriesId(event.target.value)}><option value="">新しい一連の検討として開始</option>{(creationOptions?.project_series ?? []).map((series) => <option key={series.id} value={series.id}>{series.name}</option>)}</select></label>
        </div>
        <section className="project-binding-confirmation" aria-label="作成後に固定される内容">
          <header><strong>作成後に固定される内容</strong><span>Dataset・Prediction Task・Model Packageは後から変更できません</span></header>
          <div><span>参照Dataset</span><strong>{selectedDataset ? datasetDisplayName(selectedDataset) : "選択してください"}</strong><small>{selectedDataset ? `${selectedDataset.profile_revision.name} · r${selectedDataset.profile_revision.revision}` : "DatasetとProfileを選択"}</small></div>
          <div><span>Prediction Task</span><strong>{taskLabels.get(selectedTaskId) ?? (selectedTaskId || "選択してください")}</strong><small>Projectの予測目的</small></div>
          <div><span>Model Package</span><strong>{selectedPackage?.package_id ?? "選択してください"}</strong><small>学習元: {selectedPackage ? selectedTrainingDataset ? datasetDisplayName(selectedTrainingDataset) : "未登録または記録なし" : "Model Packageを選択してください"}</small></div>
          <div><span>一連の検討</span><strong>{selectedSeries?.name ?? (newProjectName.trim() || "プロジェクト名から新規作成")}</strong><small>{selectedSeries ? "既存の一連の検討に追加" : "新しい一連の検討として開始"}</small></div>
        </section>
        {predecessorProjectId && <label>続ける理由<textarea value={continuationReason} onChange={(event) => setContinuationReason(event.target.value)} placeholder="予測タスク変更、データ追加、条件変更、判断の再検討など" /></label>}
        <div className="project-start-options">
          <label><input type="radio" checked={createMode === "empty"} onChange={() => setCreateMode("empty")} />空から開始<span>候補を持たない検討として作成</span></label>
          <label><input type="radio" checked={createMode === "copy"} disabled={!candidate || Boolean(predecessorProjectId)} onChange={() => { setCreateMode("copy"); if (project) { setNewDatasetViewId(project.dataset_view_revision_id ?? ""); setNewTaskId(project.task_id); setNewModelPackageRefId(project.model_package_ref_id ?? ""); } }} />現在候補をコピー<span>{candidate ? `${candidate.label}（編集版 ${candidate.raw.revision}）` : "コピーできる候補がありません"}</span></label>
        </div>
        <button className="primary-button" disabled={!newProjectName.trim() || !newDatasetViewId || !(createMode === "copy" ? copyTaskId : newTaskId) || !newModelPackageRefId} onClick={() => void createProject()}>固定してプロジェクトを作成</button>
      </section>}

      {settingsOpen && project && <section className="project-settings-panel">
        <div className="project-fixed-bindings"><div><span>Dataset</span><strong>{fixedDataset?.data_asset.original_filename ?? project.dataset_view_revision_id}</strong><small>{fixedDataset ? `${fixedDataset.profile_revision.name} · r${fixedDataset.profile_revision.revision}` : ""}</small></div><div><span>Prediction Task</span><strong>{taskLabels.get(project.task_id) ?? project.task_id}</strong><small>{project.task_contract_digest.slice(0, 10)}</small></div><div><span>Model Package</span><strong>{fixedPackage?.package_id ?? project.model_package_ref_id}</strong><small>{project.model_package_manifest_digest.slice(0, 10)}</small></div><div><span>一連の検討</span><strong>{fixedSeries?.name ?? project.project_series_id}</strong><small>参照は作成後に変更できません</small></div></div>
        <div className="project-form">
          <label>プロジェクト名<input value={project.name} onChange={(event) => setProject({ ...project, name: event.target.value })} /></label>
          <label>説明<textarea value={project.description} onChange={(event) => setProject({ ...project, description: event.target.value })} /></label>
          <label>目的<textarea value={project.purpose} onChange={(event) => setProject({ ...project, purpose: event.target.value })} /></label>
          <fieldset className="target-grid"><legend>目標値</legend>{(taskDefinition?.outputs ?? []).map((output) => <label key={output.key}>{output.label}<input type="number" value={targetValues[output.key] ?? ""} placeholder="未設定" onChange={(event) => setTarget(output.key, event.target.value)} /></label>)}</fieldset>
          <label>メモ<textarea value={project.notes} onChange={(event) => setProject({ ...project, notes: event.target.value })} /></label>
        </div>
        <button className="primary-button" onClick={() => void saveProject()}>設定を保存</button>
      </section>}

      <section className="project-next-actions">
        <div className="panel-title"><h3>次の作業</h3><span>{activeCandidates.length ? `${activeCandidates.length}候補を検討中` : "まだ候補がありません"}</span></div>
        <div className="project-action-grid">
          <button className="project-action-card primary" onClick={() => onNavigate(activeCandidates.length ? "candidates" : supportsLineageCandidate ? "lineage" : "explore")}><strong>{activeCandidates.length ? "候補を比較" : supportsLineageCandidate ? "過去データから探す" : "条件範囲から始める"}</strong><span>{activeCandidates.length ? "入力・予測・根拠を横並びで確認" : supportsLineageCandidate ? "既存の条件と問題から出発" : "基準候補を作り、入力範囲から探索"}</span></button>
          <button className="project-action-card" onClick={() => onNavigate("explore")}><strong>条件範囲から探す</strong><span>目標と入力範囲から候補を生成</span></button>
          <button className="project-action-card" onClick={() => onNavigate("candidates")}><strong>直接候補を作る</strong><span>具体的な成分・工程条件を入力</span></button>
        </div>
      </section>

      <section className="project-history-section">
        <div className="panel-title"><h3>候補と判断履歴</h3><span>現在値と固定した予測を分けて表示</span></div>
        {!history ? <p className="empty-evidence">履歴を読み込んでいます。</p> : !history.candidates.length ? <div className="project-empty-state"><p>{supportsLineageCandidate ? "候補はまだありません。過去データから条件を探すと、由来付き候補としてここに残ります。" : "候補はまだありません。基準候補を用意し、入力範囲から検討を始めます。"}</p><button className="primary-button" onClick={() => onNavigate(supportsLineageCandidate ? "lineage" : "explore")}>{supportsLineageCandidate ? "過去データから探す" : "条件範囲から始める"}</button></div> : <div className="project-history-list">
          {history.candidates.map((item) => {
            const preview = currentPreviews[item.candidate.id];
            return <article className="project-history-card" key={item.candidate.id}>
              <header><div><strong>{item.candidate.name}</strong>{item.candidate.archived_at && <span className="muted-badge">archive</span>}</div><button className="outline-button" disabled={Boolean(item.candidate.archived_at)} onClick={() => onNavigate("candidates", item.candidate.id)}>現在の候補を見る</button></header>
              <div className="history-current-row"><span className="history-kind current">現在</span><span>編集版 {item.current.revision}</span><span>{formatDate(item.current.updated_at)}</span><span className={item.candidate.provenance?.source_kind === "lineage" ? "history-origin reference-data" : "history-origin"}>{item.candidate.provenance?.source_kind === "lineage" && <b>参照データ由来</b>}{item.candidate.provenance ? provenanceLabel(item.candidate.provenance) : "由来不明"}</span></div>
              {preview ? <div className="history-preview"><span>現在のpreview</span>{Object.entries(preview.predictions).map(([key, value]) => { const assessment = assessPrediction(outputDefinition(key), value); return <strong className={assessment.implausible ? "implausible-output" : undefined} title={assessment.warning ?? undefined} key={key}>{outputLabels.get(key) ?? key} {formatPredictionPoint(value, formatNumber)}{assessment.implausible && <small className="output-warning-badge">⚠ 物理範囲外</small>}</strong>; })}</div> : <p className="history-muted">現在のpreviewは未計算です。候補比較を開くと必要な候補だけ計算します。</p>}
              {item.snapshots.length ? <div className="history-snapshots">{item.snapshots.map((snapshot) => <div className="history-snapshot-row" key={snapshot.id}>
                <span className="history-kind fixed">固定した予測</span>{item.decision?.snapshot_id === snapshot.id && <span className="decision-snapshot-badge">採用判断</span>}<span>編集版 {snapshot.candidate_revision ?? "不明（旧形式）"}</span><span>{formatDate(snapshot.created_at)}</span>
                <span className="history-predictions">{Object.entries(snapshot.prediction_summary).map(([key, value], index) => { const assessment = assessPrediction(outputDefinition(key), value); return <span className={assessment.implausible ? "implausible-output" : undefined} title={assessment.warning ?? undefined} key={key}>{index > 0 && " / "}{outputLabels.get(key) ?? key} {formatPredictionPoint(value, formatNumber)}{assessment.implausible && <small className="output-warning-badge">⚠ 物理範囲外</small>}</span>; })}</span>
                {item.actuals.filter((actual) => actual.snapshot_id === snapshot.id).map((actual) => { const assessment = assessOutputValues(outputDefinition(actual.property), [actual.mean], "実測値"); return <span className={`history-actual${assessment.implausible ? " implausible-output" : ""}`} title={assessment.warning ?? undefined} key={actual.id}>実測 {outputLabels.get(actual.property) ?? actual.property} {formatNumber(actual.mean)} ± {formatNumber(actual.std)} {actual.unit}{actual.experiment_no ? ` / ${actual.experiment_no}` : ""}{assessment.implausible && <small className="output-warning-badge">⚠ 物理範囲外</small>}</span>; })}
                {item.decision?.snapshot_id === snapshot.id && <span className="decision-note-inline">判断理由: {item.decision.note}</span>}
                <button className="outline-button" onClick={() => void openSnapshot(snapshot.id)}>詳細</button><CandidateAddButton compact onClick={() => void restoreSnapshot(snapshot.id)}>新しい候補として複製</CandidateAddButton>
              </div>)}</div> : <div className="project-empty-inline"><span>固定した予測はありません。候補比較で詳細予測を保存すると判断時点が残ります。</span><button className="outline-button" onClick={() => onNavigate("candidates", item.candidate.id)}>候補比較へ</button></div>}
            </article>;
          })}
        </div>}
      </section>

      {selectedSnapshot?.payload.prediction && <section className="snapshot-detail project-snapshot-detail">
        <div className="panel-title"><h3>固定した予測の詳細</h3><button className="outline-button" onClick={() => { setSelectedSnapshot(null); onSnapshotNavigate(undefined); }}>閉じる</button></div>
        <p>{formatDate(selectedSnapshot.created_at)} / {history?.candidates.find((item) => item.candidate.id === selectedSnapshot.candidate_id)?.candidate.name ?? "保存時の候補"}</p>
        <span className="decision-snapshot-badge">{!selectedSnapshot.payload.provenance?.package?.manifest_sha256 || !modelPackage ? "予測モデル情報を確認できません" : selectedSnapshot.payload.provenance.package.manifest_sha256 === modelPackage.manifest_sha256 ? "現在と同じ予測モデル" : "現在とは別の予測モデル"}</span>
        <table className="quality-table"><thead><tr><th>特性</th><th>固定予測</th><th>区間・分位</th><th>目標達成</th></tr></thead><tbody>{Object.entries(selectedSnapshot.payload.prediction.predictions).map(([key, value]) => { const assessment = assessPrediction(outputDefinition(key), value); return <tr className={assessment.implausible ? "implausible-output" : undefined} key={key}><th>{outputLabels.get(key) ?? key}{assessment.implausible && <small className="output-warning-badge">⚠ 物理範囲外</small>}</th><td title={assessment.warning ?? undefined}>{formatPredictionPoint(value, formatNumber)}</td><td>{predictionHasInterval(value) ? <>{formatNumber(value.lower)}–{formatNumber(value.upper)} <small>{predictionIntervalLabel(value)}</small></> : "利用不可"}</td><td>{value.goal_probability == null ? value.goal_value == null ? "目標未設定" : "利用不可" : `${formatNumber(value.goal_probability * 100, 0)}%`}</td></tr>; })}</tbody></table>
        <div className="snapshot-decision-form"><label>判断理由<textarea value={decisionNote} onChange={(event) => { decisionDraftRef.current.dirty = true; setDecisionNote(event.target.value); }} placeholder="この時点の予測を採用判断に使う理由" /></label><button className="outline-button" onClick={() => void saveDecision(false)}>採用判断として固定</button>{project?.decision_snapshot_id === selectedSnapshot.id && <button className="outline-button" onClick={() => void saveDecision(true)}>採用判断を解除</button>}</div>
        <CandidateAddButton onClick={() => void restoreSnapshot(selectedSnapshot.id)}>この時点から新しい候補を作る</CandidateAddButton>
      </section>}

      {!Object.keys(targetValues).length && <div className="project-empty-inline"><span>目標値が未設定です。設定すると候補の目標達成率を比較できます。</span><button className="outline-button" onClick={() => setSettingsOpen(true)}>目標値を設定</button></div>}
      </div>
    </div>
  );
}
