import { useEffect, useState } from "react";
import {
  workbenchApi,
  type ApiChangeGuideEntry,
  type ApiDeveloperOverview,
  type ApiRuntimeDiagnostics,
} from "../../shared/api/workbench-api";
import { ChangeGuideCard, CopyCommand } from "./ChangeGuideCard";
import { ObservationTrainingInspector } from "./ObservationTrainingInspector";

type ControlTab = "overview" | "training" | "guide" | "diagnostics";
type OverviewStatus = "" | "ok" | "warning" | "error";

function ShortDigest({ value }: { value?: string | null }) {
  if (!value) return <span>—</span>;
  const normalized = value.replace(/^sha256:/, "");
  return <details className="developer-digest"><summary>{normalized.slice(0, 10)}…</summary><code>{value}</code></details>;
}

export function filterDeveloperOverviewItems(
  items: ApiDeveloperOverview["items"],
  query: string,
  status: OverviewStatus,
  taskId: string,
) {
  const normalizedQuery = query.trim().toLocaleLowerCase("ja-JP");
  return items.filter((item) => {
    if (status && item.validation_status !== status) return false;
    if (taskId && item.task_id !== taskId) return false;
    if (!normalizedQuery) return true;
    return [
      item.project_name,
      item.project_id,
      item.task_id,
      item.chain_revision_id,
      item.source_filename,
      item.profile_id,
      item.package_id,
    ].some((value) => value?.toLocaleLowerCase("ja-JP").includes(normalizedQuery));
  });
}

export function DeveloperControlCenter({ onOpenProfileWorkbench }: { onOpenProfileWorkbench: () => void }) {
  const [tab, setTab] = useState<ControlTab>("overview");
  const [overview, setOverview] = useState<ApiDeveloperOverview | null>(null);
  const [guide, setGuide] = useState<ApiChangeGuideEntry[]>([]);
  const [doctor, setDoctor] = useState<ApiRuntimeDiagnostics | null>(null);
  const [selectedGuide, setSelectedGuide] = useState("");
  const [error, setError] = useState("");
  const [diagnosing, setDiagnosing] = useState(false);
  const [overviewQuery, setOverviewQuery] = useState("");
  const [overviewStatus, setOverviewStatus] = useState<OverviewStatus>("");
  const [overviewTask, setOverviewTask] = useState("");

  useEffect(() => {
    let live = true;
    Promise.all([workbenchApi.developerOverview(), workbenchApi.developerChangeGuide()])
      .then(([nextOverview, nextGuide]) => {
        if (!live) return;
        setOverview(nextOverview);
        setGuide(nextGuide);
        setSelectedGuide(nextGuide[0]?.id ?? "");
      })
      .catch((cause) => { if (live) setError(cause instanceof Error ? cause.message : "Developer情報を取得できませんでした。"); });
    return () => { live = false; };
  }, []);

  const runDiagnostics = async () => {
    setDiagnosing(true);
    setError("");
    try {
      setDoctor(await workbenchApi.developerDiagnostics());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "診断を実行できませんでした。");
    } finally {
      setDiagnosing(false);
    }
  };
  useEffect(() => { if (tab === "diagnostics" && doctor === null && !diagnosing) void runDiagnostics(); }, [tab]);

  const selected = guide.find((item) => item.id === selectedGuide);
  // Chain Projectには単一Taskが無い。空のtask_idを選択肢に出さない。
  const overviewTasks = [...new Set(overview?.items.map((item) => item.task_id).filter(Boolean) ?? [])].sort();
  const filteredOverviewItems = overview
    ? filterDeveloperOverviewItems(overview.items, overviewQuery, overviewStatus, overviewTask)
    : [];
  return <div className="page-panel developer-control-center">
    <div className="page-intro">
      <div><span className="overline">DEVELOPER TOOLS</span><h2>構成・変更判断・診断</h2><p>編集や自動生成ではなく、影響範囲と次の操作を確認する場所です。</p></div>
    </div>
    <nav className="developer-tabs" aria-label="Developer Control Center">
      {([
        ["overview", "概要"],
        ["training", "学習View"],
        ["guide", "変更ガイド"],
        ["diagnostics", "診断"],
      ] as const).map(([id, label]) => <button type="button" key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{label}</button>)}
    </nav>
    {error && <p className="panel-error">{error}</p>}

    {tab === "overview" && <section className="developer-section">
      <ol className="developer-flow" aria-label="データから実行までの接続段階">
        <li><b>1</b><div><small>入力を解釈</small><strong>Data Asset と Profile</strong><span>元ファイルと読取規則を組み合わせる</span></div></li>
        <li><b>2</b><div><small>参照を固定</small><strong>Dataset Revision / View</strong><span>登録時点のデータと比較境界を残す</span></div></li>
        <li><b>3</b><div><small>判断条件を固定</small><strong>Project・Task・Package</strong><span>目的と学習済みモデルを一つの検討へ結ぶ</span></div></li>
        <li><b>4</b><div><small>予測を実行</small><strong>Runtime</strong><span>固定した参照から予測Snapshotを作る</span></div></li>
      </ol>
      <p className="developer-note">Projectごとに固定された参照です。Dataset Revision・Package・Snapshotは上書きしません。</p>
      {overview && <div className="developer-overview-toolbar">
        <label className="developer-overview-search">検索<input type="search" value={overviewQuery} placeholder="Project / Dataset / Package" onChange={(event) => setOverviewQuery(event.target.value)} /></label>
        <label>状態<select value={overviewStatus} onChange={(event) => setOverviewStatus(event.target.value as OverviewStatus)}><option value="">すべて</option><option value="ok">検証済み</option><option value="warning">Archive参照</option><option value="error">参照不足</option></select></label>
        <label>予測タスク<select value={overviewTask} onChange={(event) => setOverviewTask(event.target.value)}><option value="">すべて</option>{overviewTasks.map((task) => <option key={task} value={task}>{task}</option>)}</select></label>
        <span>{filteredOverviewItems.length} / {overview.items.length}件</span>
      </div>}
      {overview ? <div className="developer-overview-list">{filteredOverviewItems.map((item) => <details key={item.project_id}>
        <summary><span className={`developer-project-status ${item.validation_status}`}>{item.validation_status === "ok" ? "検証済み" : item.validation_status === "warning" ? "Archive参照" : "参照不足"}</span><strong>{item.project_name}</strong><small>{item.identity_kind === "chain" ? `Chain ${item.chain_revision_id ?? ""}` : item.task_id}</small>{item.active_package && <em>active</em>}<code>{item.project_id}</code></summary>
        {item.archived_references.length > 0 && <p className="developer-archived">Archive参照: {item.archived_references.join(" / ")}</p>}
        <dl>
          <div><dt>Dataset</dt><dd>{item.source_filename ?? "—"}<small>{item.dataset_revision_ids.join(", ") || "revisionなし"}</small></dd></div>
          <div><dt>Source SHA</dt><dd><ShortDigest value={item.source_sha256} /></dd></div>
          <div><dt>Profile</dt><dd>{item.profile_id ?? "—"}<ShortDigest value={item.profile_digest} /></dd></div>
          <div><dt>Task</dt><dd>{item.identity_kind === "chain" ? `Chain ${item.chain_revision_id ?? ""}` : item.task_id}<ShortDigest value={item.task_contract_digest} /></dd></div>
          <div><dt>Package</dt><dd>{item.package_id ?? "—"}<ShortDigest value={item.package_manifest_digest} /></dd></div>
          <div><dt>Feature Pipeline</dt><dd>{item.feature_pipeline_id ?? "—"} <small>v{item.feature_pipeline_version ?? "—"}</small></dd></div>
          <div><dt>Runtime</dt><dd>{item.runtime_type ?? "—"}</dd></div>
        </dl>
      </details>)}
      {filteredOverviewItems.length === 0 && <p className="empty-evidence">条件に合うProjectはありません。</p>}
      </div> : <p className="empty-evidence">構成を読み込んでいます。</p>}
    </section>}

    {tab === "training" && <ObservationTrainingInspector />}

    {tab === "guide" && <section className="developer-section change-guide">
      <label>何を変更したいですか？
        <select value={selectedGuide} onChange={(event) => setSelectedGuide(event.target.value)}>
          {guide.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
        </select>
      </label>
      {selected && <ChangeGuideCard entry={selected} onOpenProfileWorkbench={onOpenProfileWorkbench} />}
    </section>}

    {tab === "diagnostics" && <section className="developer-section diagnostics-section">
      <div className="developer-diagnostics-header"><div><h3>実行環境の診断</h3><p>配布版でも実行できる、Project・Dataset・Package・DB・sidecarの診断です。開発ツールは起動しません。</p></div><button type="button" className="outline-button" disabled={diagnosing} onClick={() => void runDiagnostics()}>{diagnosing ? "診断中…" : "再診断"}</button></div>
      {doctor ? <>
        <div className={`doctor-summary ${doctor.status}`}><b>{doctor.status.toUpperCase()}</b><span>{doctor.project_count} Project</span></div>
        <div className="doctor-checks">{doctor.checks.map((check) => <details key={check.id} className={`doctor-check ${check.severity}`} open={check.severity !== "ok"}>
          <summary><span>{check.severity === "ok" ? "✓" : check.severity === "warning" ? "△" : "✕"}</span><b>{check.title}</b><small>{check.summary}</small></summary>
          {check.cause && <p><b>原因</b>{check.cause}</p>}
          {check.impact && <p><b>影響</b>{check.impact}</p>}
          {check.commands.map((command) => <CopyCommand command={command} key={command.display_text} />)}
        </details>)}</div>
      </> : <p className="empty-evidence">{diagnosing ? "ProjectとRuntimeを診断しています。" : "診断を実行してください。"}</p>}
    </section>}
  </div>;
}
