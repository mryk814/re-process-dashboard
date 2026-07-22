import { useEffect, useMemo, useRef, useState } from "react";
import { provenanceLabel } from "../../shared/candidateProvenance";
import { predictionHasInterval, predictionIntervalLabel } from "../../shared/predictionPresentation";
import { fromApiCandidate, toApiCandidate, type CandidateViewModel, type RuntimeOperations, type TaskDefinitionContract } from "../candidates";
import {
  workbenchApi,
  type ApiModelPackage,
  type ApiPreview,
  type ApiProject,
  type ApiProjectHistory,
  type ApiSnapshot,
  type ApiTaskCatalogItem,
} from "../../shared/api/workbench-api";

type Props = {
  projects: ApiProject[];
  activeProjectId: string;
  candidate?: CandidateViewModel;
  taskDefinition: TaskDefinitionContract | null;
  operations?: RuntimeOperations;
  currentPreviews: Record<string, ApiPreview>;
  requestedSnapshotId?: string;
  onProjectChanged: (project: ApiProject) => void;
  onProjectDeleted: (projectId: string) => Promise<boolean>;
  onSwitch: (projectId: string) => void;
  onRestore: (candidate: CandidateViewModel) => void;
  onNavigate: (view: "candidates" | "lineage" | "explore" | "settings", candidateId?: string) => void;
  onSnapshotNavigate: (snapshotId?: string) => void;
};

const formatNumber = (value: number, digits = 1) => value.toLocaleString("ja-JP", { maximumFractionDigits: digits });
const formatDate = (value: string) => new Date(value).toLocaleString("ja-JP");

export function ProjectHub({
  projects,
  activeProjectId,
  candidate,
  taskDefinition,
  operations,
  currentPreviews,
  requestedSnapshotId,
  onProjectChanged,
  onProjectDeleted,
  onSwitch,
  onRestore,
  onNavigate,
  onSnapshotNavigate,
}: Props) {
  const [project, setProject] = useState<ApiProject | null>(null);
  const [history, setHistory] = useState<ApiProjectHistory | null>(null);
  const [catalog, setCatalog] = useState<ApiTaskCatalogItem[]>([]);
  const [modelPackage, setModelPackage] = useState<ApiModelPackage | null>(null);
  const [selectedSnapshot, setSelectedSnapshot] = useState<ApiSnapshot | null>(null);
  const [error, setError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [createMode, setCreateMode] = useState<"empty" | "copy">("empty");
  const [newProjectName, setNewProjectName] = useState("");
  const [newTaskId, setNewTaskId] = useState("");
  const [decisionNote, setDecisionNote] = useState("");
  const activeProjectRef = useRef(activeProjectId);
  const decisionDraftRef = useRef({ key: "", dirty: false });
  activeProjectRef.current = activeProjectId;

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
  const supportsLineageCandidate = taskDefinition?.input_groups.some((group) => group.key === "heat_pattern") ?? false;
  const copyTaskId = candidate ? projects.find((item) => item.id === candidate.raw.project_id)?.task_id : undefined;
  const outputLabels = useMemo(() => new Map((taskDefinition?.outputs ?? []).map((output) => [output.key, output.label])), [taskDefinition]);

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
    if (!taskId || !newProjectName.trim()) return setError("プロジェクト名と予測タスクを確認してください。");
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
      });
      onProjectChanged(created);
      setCreateOpen(false);
      setNewProjectName("");
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
    setCreateOpen((value) => !value);
    setNewProjectName(`新しい検討 ${projects.length + 1}`);
    setNewTaskId(project?.task_id ?? catalog[0]?.definition.task_definition.id ?? "");
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
        <div className="project-list-items">
          {projects.map((item) => (
            <button
              type="button"
              key={item.id}
              className={item.id === activeProjectId ? "project-list-item active" : "project-list-item"}
              aria-current={item.id === activeProjectId ? "page" : undefined}
              onClick={() => onSwitch(item.id)}
            >
              <strong>{item.name}</strong>
              <small>{item.task_id === "hot-rolled-properties-v1" ? "熱延条件" : item.task_id === "flank-wear-v1" ? "切削摩耗" : "焼鈍条件"}</small>
            </button>
          ))}
        </div>
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
            <button className="outline-button" onClick={() => setSettingsOpen((value) => !value)}>設定を編集</button>
            {canDeleteProject && <button className="danger-outline-button" onClick={() => setDeleteOpen((value) => !value)}>プロジェクトを削除</button>}
          </div>
        </div>
      {error && <p className="panel-error" role="alert">{error}</p>}

      {deleteOpen && project && <section className="project-delete-panel" aria-label="プロジェクト削除の確認">
        <div><strong>「{project.name}」を削除しますか？</strong><p>候補・予測履歴・実測データもまとめて削除され、元に戻せません。</p></div>
        <div className="project-delete-actions"><button className="danger-button" disabled={deleting} onClick={() => void deleteCurrentProject()}>{deleting ? "削除中…" : "削除する"}</button><button className="outline-button" disabled={deleting} onClick={() => setDeleteOpen(false)}>キャンセル</button></div>
      </section>}

      {createOpen && <section className="project-create-panel" aria-label="新規プロジェクトの開始方法">
        <div className="panel-title"><h3>新しいプロジェクト</h3><span>開始方法を選んでから作成します</span></div>
        <label>プロジェクト名<input value={newProjectName} onChange={(event) => setNewProjectName(event.target.value)} /></label>
        {catalog.length > 1 ? <label>予測タスク<select disabled={createMode === "copy"} value={createMode === "copy" ? copyTaskId : newTaskId} onChange={(event) => setNewTaskId(event.target.value)}>{catalog.map((item) => <option key={item.definition.task_definition.id} value={item.definition.task_definition.id}>{item.definition.task_definition.label}</option>)}</select></label> : catalog[0] ? <p>予測タスク: {catalog[0].definition.task_definition.label}</p> : null}
        <div className="project-start-options">
          <label><input type="radio" checked={createMode === "empty"} onChange={() => setCreateMode("empty")} />空から開始<span>候補を持たない検討として作成</span></label>
          <label><input type="radio" checked={createMode === "copy"} disabled={!candidate} onChange={() => setCreateMode("copy")} />現在候補をコピー<span>{candidate ? `${candidate.label}（編集版 ${candidate.raw.revision}）` : "コピーできる候補がありません"}</span></label>
        </div>
        <button className="primary-button" onClick={() => void createProject()}>この内容で作成</button>
      </section>}

      {settingsOpen && project && <section className="project-settings-panel">
        <div className="project-form">
          <label>プロジェクト名<input value={project.name} onChange={(event) => setProject({ ...project, name: event.target.value })} /></label>
          <label>説明<textarea value={project.description} onChange={(event) => setProject({ ...project, description: event.target.value })} /></label>
          <label>目的<textarea value={project.purpose} onChange={(event) => setProject({ ...project, purpose: event.target.value })} /></label>
          {catalog.length > 1 ? <label>予測タスク<select value={project.task_id} onChange={(event) => setProject({ ...project, task_id: event.target.value as ApiProject["task_id"] })}>{catalog.map((item) => <option key={item.definition.task_definition.id} value={item.definition.task_definition.id}>{item.definition.task_definition.label}</option>)}</select><small>候補があるプロジェクトでは変更できません。</small></label> : null}
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
              <div className="history-current-row"><span className="history-kind current">現在</span><span>編集版 {item.current.revision}</span><span>{formatDate(item.current.updated_at)}</span><span>{item.candidate.provenance ? provenanceLabel(item.candidate.provenance) : "由来不明"}</span></div>
              {preview ? <div className="history-preview"><span>現在のpreview</span>{Object.entries(preview.predictions).map(([key, value]) => <strong key={key}>{outputLabels.get(key) ?? key} {formatNumber(value.value)} {value.unit}</strong>)}</div> : <p className="history-muted">現在のpreviewは未計算です。候補比較を開くと必要な候補だけ計算します。</p>}
              {item.snapshots.length ? <div className="history-snapshots">{item.snapshots.map((snapshot) => <div className="history-snapshot-row" key={snapshot.id}>
                <span className="history-kind fixed">固定した予測</span>{item.decision?.snapshot_id === snapshot.id && <span className="decision-snapshot-badge">採用判断</span>}<span>編集版 {snapshot.candidate_revision ?? "不明（旧形式）"}</span><span>{formatDate(snapshot.created_at)}</span>
                <span className="history-predictions">{Object.entries(snapshot.prediction_summary).map(([key, value]) => `${outputLabels.get(key) ?? key} ${formatNumber(value.value)} ${value.unit}`).join(" / ")}</span>
                {item.actuals.filter((actual) => actual.snapshot_id === snapshot.id).map((actual) => <span className="history-actual" key={actual.id}>実測 {outputLabels.get(actual.property) ?? actual.property} {formatNumber(actual.mean)} ± {formatNumber(actual.std)} {actual.unit}{actual.experiment_no ? ` / ${actual.experiment_no}` : ""}</span>)}
                {item.decision?.snapshot_id === snapshot.id && <span className="decision-note-inline">判断理由: {item.decision.note}</span>}
                <button className="outline-button" onClick={() => void openSnapshot(snapshot.id)}>詳細</button><button className="outline-button" onClick={() => void restoreSnapshot(snapshot.id)}>新しい候補として複製</button>
              </div>)}</div> : <div className="project-empty-inline"><span>固定した予測はありません。候補比較で詳細予測を保存すると判断時点が残ります。</span><button className="outline-button" onClick={() => onNavigate("candidates", item.candidate.id)}>候補比較へ</button></div>}
            </article>;
          })}
        </div>}
      </section>

      {selectedSnapshot?.payload.prediction && <section className="snapshot-detail project-snapshot-detail">
        <div className="panel-title"><h3>固定した予測の詳細</h3><button className="outline-button" onClick={() => { setSelectedSnapshot(null); onSnapshotNavigate(undefined); }}>閉じる</button></div>
        <p>{formatDate(selectedSnapshot.created_at)} / {history?.candidates.find((item) => item.candidate.id === selectedSnapshot.candidate_id)?.candidate.name ?? "保存時の候補"}</p>
        <span className="decision-snapshot-badge">{!selectedSnapshot.payload.provenance?.package?.manifest_sha256 || !modelPackage ? "予測モデル情報を確認できません" : selectedSnapshot.payload.provenance.package.manifest_sha256 === modelPackage.manifest_sha256 ? "現在と同じ予測モデル" : "現在とは別の予測モデル"}</span>
        <table className="quality-table"><thead><tr><th>特性</th><th>固定予測</th><th>予測区間</th><th>目標達成</th></tr></thead><tbody>{Object.entries(selectedSnapshot.payload.prediction.predictions).map(([key, value]) => <tr key={key}><th>{outputLabels.get(key) ?? key}</th><td>{formatNumber(value.value)} {value.unit}</td><td>{predictionHasInterval(value) ? <>{formatNumber(value.lower)}–{formatNumber(value.upper)} <small>{predictionIntervalLabel(value)}</small></> : "利用不可"}</td><td>{value.goal_probability == null ? "目標未設定" : `${formatNumber(value.goal_probability * 100, 0)}%`}</td></tr>)}</tbody></table>
        <div className="snapshot-decision-form"><label>判断理由<textarea value={decisionNote} onChange={(event) => { decisionDraftRef.current.dirty = true; setDecisionNote(event.target.value); }} placeholder="この時点の予測を採用判断に使う理由" /></label><button className="outline-button" onClick={() => void saveDecision(false)}>採用判断として固定</button>{project?.decision_snapshot_id === selectedSnapshot.id && <button className="outline-button" onClick={() => void saveDecision(true)}>採用判断を解除</button>}</div>
        <button className="primary-button" onClick={() => void restoreSnapshot(selectedSnapshot.id)}>この時点から新しい候補を作る</button>
      </section>}

      {!Object.keys(targetValues).length && <div className="project-empty-inline"><span>目標値が未設定です。設定すると候補の目標達成率を比較できます。</span><button className="outline-button" onClick={() => setSettingsOpen(true)}>目標値を設定</button></div>}
      </div>
    </div>
  );
}
