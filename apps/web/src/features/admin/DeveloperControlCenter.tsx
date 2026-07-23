import { useEffect, useState } from "react";
import {
  workbenchApi,
  type ApiChangeGuideEntry,
  type ApiDeveloperDoctor,
  type ApiDeveloperOverview,
} from "../../shared/api/workbench-api";

type ControlTab = "overview" | "guide" | "diagnostics";

const riskLabel = {
  safe: "比較的安全",
  review: "ガイドとレビューが必要",
  specialist: "専門的レビューが必要",
} as const;

function ShortDigest({ value }: { value?: string | null }) {
  if (!value) return <span>—</span>;
  const normalized = value.replace(/^sha256:/, "");
  return <details className="developer-digest"><summary>{normalized.slice(0, 10)}…</summary><code>{value}</code></details>;
}

function CopyCommand({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(command);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  };
  return <div className="developer-command"><code>{command}</code><button type="button" onClick={() => void copy()}>{copied ? "コピー済み" : "コピー"}</button></div>;
}

export function DeveloperControlCenter({ onOpenProfileWorkbench }: { onOpenProfileWorkbench: () => void }) {
  const [tab, setTab] = useState<ControlTab>("overview");
  const [overview, setOverview] = useState<ApiDeveloperOverview | null>(null);
  const [guide, setGuide] = useState<ApiChangeGuideEntry[]>([]);
  const [doctor, setDoctor] = useState<ApiDeveloperDoctor | null>(null);
  const [selectedGuide, setSelectedGuide] = useState("");
  const [error, setError] = useState("");
  const [diagnosing, setDiagnosing] = useState(false);

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
  return <div className="page-panel developer-control-center">
    <div className="page-intro">
      <div><span className="overline">Developer Control Center</span><h2>構成・変更判断・診断</h2><p>編集や自動生成ではなく、影響範囲と次の操作を確認する場所です。</p></div>
    </div>
    <nav className="developer-tabs" aria-label="Developer Control Center">
      {([
        ["overview", "Overview"],
        ["guide", "Change Guide"],
        ["diagnostics", "Diagnostics"],
      ] as const).map(([id, label]) => <button type="button" key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{label}</button>)}
    </nav>
    {error && <p className="panel-error">{error}</p>}

    {tab === "overview" && <section className="developer-section">
      <div className="developer-flow" aria-label="接続関係">
        {["Dataset", "Profile", "Task", "Package", "Project", "Runtime"].map((item, index) => <span key={item}>{index > 0 && <i>→</i>}<b>{item}</b></span>)}
      </div>
      <p className="developer-note">Projectごとに固定された参照です。Dataset Revision・Package・Snapshotは上書きしません。</p>
      {overview ? <div className="developer-overview-list">{overview.items.map((item) => <article key={item.project_id}>
        <header><div><span>{item.validation_status === "ok" ? "✓ 検証済み" : "要確認"}</span><h3>{item.project_name}</h3></div><code>{item.project_id}</code></header>
        <dl>
          <div><dt>Dataset</dt><dd>{item.source_filename ?? "—"}<small>{item.dataset_revision_ids.join(", ") || "revisionなし"}</small></dd></div>
          <div><dt>Source SHA</dt><dd><ShortDigest value={item.source_sha256} /></dd></div>
          <div><dt>Profile</dt><dd>{item.profile_id ?? "—"}<ShortDigest value={item.profile_digest} /></dd></div>
          <div><dt>Task</dt><dd>{item.task_id}<ShortDigest value={item.task_contract_digest} /></dd></div>
          <div><dt>Package</dt><dd>{item.package_id ?? "—"}<ShortDigest value={item.package_manifest_digest} /></dd></div>
          <div><dt>Feature Pipeline</dt><dd>{item.feature_pipeline_id ?? "—"} <small>v{item.feature_pipeline_version ?? "—"}</small></dd></div>
          <div><dt>Runtime</dt><dd>{item.runtime_type ?? "—"}</dd></div>
        </dl>
      </article>)}</div> : <p className="empty-evidence">構成を読み込んでいます。</p>}
    </section>}

    {tab === "guide" && <section className="developer-section change-guide">
      <label>何を変更したいですか？
        <select value={selectedGuide} onChange={(event) => setSelectedGuide(event.target.value)}>
          {guide.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
        </select>
      </label>
      {selected && <article className={`developer-guide-card risk-${selected.risk}`}>
        <div className="developer-risk">{riskLabel[selected.risk]}</div>
        <div className="developer-guide-grid">
          <section><h3>主に変更する</h3><ul>{selected.changes.map((item) => <li key={item}>{item}</li>)}</ul></section>
          <section><h3>原則変更しない</h3><ul>{selected.unchanged.map((item) => <li key={item}>{item}</li>)}</ul></section>
          <section><h3>必要な成果物</h3><ul>{selected.artifacts.length ? selected.artifacts.map((item) => <li key={item}>{item}</li>) : <li>分類後に決定</li>}</ul></section>
          <section><h3>関連文書</h3><ul>{selected.documents.map((item) => <li key={item}><code>{item}</code></li>)}</ul></section>
        </div>
        {selected.human_review && <p className="developer-review">人の判断: {selected.human_review}</p>}
        {(selected.id === "new-excel" || selected.id === "workbook-shape") && <button type="button" className="primary-button developer-open-profile" onClick={onOpenProfileWorkbench}>Profile WorkbenchでExcelを確認</button>}
        <h3>推奨コマンド</h3>
        {selected.commands.map((command) => <CopyCommand command={command} key={command} />)}
      </article>}
    </section>}

    {tab === "diagnostics" && <section className="developer-section diagnostics-section">
      <div className="developer-diagnostics-header"><div><h3>Repository Doctor</h3><p>CLIの `developer-doctor/v1` JSONと同じ判定です。</p></div><button type="button" className="outline-button" disabled={diagnosing} onClick={() => void runDiagnostics()}>{diagnosing ? "診断中…" : "再診断"}</button></div>
      {doctor ? <>
        <div className={`doctor-summary ${doctor.status}`}><b>{doctor.status.toUpperCase()}</b><span>終了コード {doctor.code}</span></div>
        <div className="doctor-checks">{doctor.checks.map((check) => <details key={check.id} className={`doctor-check ${check.severity}`} open={check.severity !== "ok"}>
          <summary><span>{check.severity === "ok" ? "✓" : check.severity === "warning" ? "△" : "✕"}</span><b>{check.title}</b><small>{check.summary}</small></summary>
          {check.cause && <p><b>原因</b>{check.cause}</p>}
          {check.impact && <p><b>影響</b>{check.impact}</p>}
          {check.commands.map((command) => <CopyCommand command={command} key={command} />)}
        </details>)}</div>
      </> : <p className="empty-evidence">{diagnosing ? "Packageと生成物を診断しています。" : "診断を実行してください。"}</p>}
    </section>}
  </div>;
}
