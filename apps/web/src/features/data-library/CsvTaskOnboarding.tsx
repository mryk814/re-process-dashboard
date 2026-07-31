import { useState } from "react";
import { apiClient } from "../../shared/api/client";

type Column = { name: string; kind: "number" | "categorical"; non_empty: number; observed_min: number | null; observed_max: number | null; choices: string[] };
type Field = { column: string; role: "" | "composition" | "process" | "categorical" | "output"; key: string; label: string; unit: string; goal_direction: "at_least" | "at_most" | "target"; allowed_range: string; default_range: string; training_range: string; plausible_range: string; display_range: string };

const range = (value: string) => {
  const [min, max] = value.split(",").map((item) => Number(item.trim()));
  return Number.isFinite(min) && Number.isFinite(max) ? [min, max] : undefined;
};

const errorMessage = (value: unknown, fallback: string) => {
  if (typeof value !== "object" || value === null) return fallback;
  const detail = Reflect.get(value, "detail");
  if (typeof detail === "string" && detail) return detail;
  const message = Reflect.get(value, "message");
  return typeof message === "string" && message ? message : fallback;
};

export type PreparedCsvProjectBinding = {
  datasetViewId: string;
  taskId: string;
  modelPackageRefId: string;
};

export function CsvTaskOnboarding({ onPrepared }: { onPrepared: (binding: PreparedCsvProjectBinding) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [rows, setRows] = useState(0);
  const [columns, setColumns] = useState<Column[]>([]);
  const [fields, setFields] = useState<Field[]>([]);
  const [grainConfirmed, setGrainConfirmed] = useState(false);
  const [relationsConfirmed, setRelationsConfirmed] = useState(false);
  const [taskId, setTaskId] = useState("");
  const [label, setLabel] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function inspect() {
    if (!file) return;
    setLoading(true); setError(""); setMessage("");
    const form = new FormData(); form.append("file", file);
    const response = await apiClient.POST("/api/data-library/csv-onboarding/inspect", { body: form as never, parseAs: "json" });
    setLoading(false);
    if (response.error) { setError(errorMessage(response.error, "CSVを確認できませんでした。")); return; }
    const data = response.data as unknown as { columns: Column[]; rows: number; relations: number; notice: string };
    if (data.relations !== 0) { setError("この画面はrelationsなしのCSVだけを扱います。relationのあるデータは専用Task設計へ進んでください。"); return; }
    setRows(data.rows);
    setColumns(data.columns);
    setGrainConfirmed(false);
    setRelationsConfirmed(false);
    setFields(data.columns.map((column) => ({ column: column.name, role: "", key: column.name.replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "").toLowerCase(), label: column.name, unit: "", goal_direction: "at_least", allowed_range: "", default_range: "", training_range: "", plausible_range: "", display_range: "" })));
    setMessage(`${data.rows}行・${data.columns.length}列・relations ${data.relations}件を確認しました。${data.notice}`);
  }

  function update(index: number, change: Partial<Field>) { setFields(fields.map((field, current) => current === index ? { ...field, ...change } : field)); }

  const inputCount = fields.filter((field) => field.role === "composition" || field.role === "process" || field.role === "categorical").length;
  const outputCount = fields.filter((field) => field.role === "output").length;
  const canPrepare = Boolean(file && taskId && label && inputCount && outputCount && grainConfirmed && relationsConfirmed && !loading);

  async function prepare() {
    if (!file) return;
    const payload = fields.filter((field) => field.role).map((field) => ({
      column: field.column, role: field.role, key: field.key, label: field.label, unit: field.unit,
      goal_direction: field.goal_direction,
      allowed_range: range(field.allowed_range), default_range: range(field.default_range), training_range: range(field.training_range),
      plausible_range: range(field.plausible_range), display_range: range(field.display_range),
    }));
    setLoading(true); setError(""); setMessage("");
    const form = new FormData();
    form.append("file", file); form.append("task_id", taskId); form.append("label", label); form.append("estimator_id", "ridge.v1");
    form.append("fields_json", JSON.stringify(payload)); form.append("grain_confirmation", "one-row-one-observation"); form.append("relation_confirmation", "no-relations");
    const response = await apiClient.POST("/api/data-library/csv-onboarding/prepare", { body: form as never, parseAs: "json" });
    setLoading(false);
    if (response.error) { setError(errorMessage(response.error, "新しいTaskを準備できませんでした。")); return; }
    const data = response.data as unknown as { state: string; unresolved?: string[]; dataset_view_revision_id?: string; task_id?: string; model_package_ref_id?: string };
    if (data.state !== "ready" || !data.dataset_view_revision_id || !data.task_id || !data.model_package_ref_id) { setError(`未解決: ${(data.unresolved ?? []).join(" / ")}`); return; }
    setMessage(`${data.task_id}を登録・検証・再読込しました。同じDataset / Task / Model PackageをProject作成へ渡します。`);
    onPrepared({ datasetViewId: data.dataset_view_revision_id, taskId: data.task_id, modelPackageRefId: data.model_package_ref_id });
  }

  return <section className="csv-task-onboarding" aria-labelledby="csv-task-onboarding-heading">
    <header><span className="overline">CSV NEW TASK</span><h3 id="csv-task-onboarding-heading">CSVから新しい予測問題を準備</h3><p>元CSVは読取専用です。観測範囲は表示するだけで、物理範囲には自動設定しません。</p></header>
    <label>CSVファイル<input type="file" accept=".csv,text/csv" onChange={(event) => { setFile(event.target.files?.[0] ?? null); setRows(0); setColumns([]); setFields([]); setGrainConfirmed(false); setRelationsConfirmed(false); }} /></label>
    <button className="outline-button" type="button" disabled={!file || loading} onClick={() => void inspect()}>{loading ? "確認中…" : "CSVをプレビュー"}</button>
    {columns.length > 0 && <>
      <label>Task ID<input value={taskId} onChange={(event) => setTaskId(event.target.value)} placeholder="concrete-slump-v1" /></label>
      <label>表示名<input value={label} onChange={(event) => setLabel(event.target.value)} placeholder="コンクリート流動性" /></label>
      <div className="csv-task-summary" aria-live="polite"><span>{rows}行</span><span>{columns.length}列</span><span>入力 {inputCount}項目</span><span>出力 {outputCount}項目</span></div>
      <p className="csv-task-onboarding-note">数値入力は、物理的許容範囲／通常範囲／学習範囲を明示してください。出力は妥当範囲／表示範囲を明示してください。</p>
      <div className="csv-task-columns">{fields.map((field, index) => { const column = columns[index]; const missing = Math.max(0, rows - column.non_empty); return <article key={field.column}><strong>{field.column}</strong><small>{column.kind} · 欠損 {missing}件 / {rows}件 · 観測 {column.observed_min ?? "—"}–{column.observed_max ?? "—"}</small><label>役割<select value={field.role} onChange={(event) => update(index, { role: event.target.value as Field["role"] })}><option value="">使わない</option><option value="composition">入力: 組成</option><option value="process">入力: 条件</option><option value="categorical">入力: カテゴリ</option><option value="output">出力</option></select></label>{field.role && <><label>canonical key<input value={field.key} onChange={(event) => update(index, { key: event.target.value })} /></label><label>表示名<input value={field.label} onChange={(event) => update(index, { label: event.target.value })} /></label>{field.role !== "categorical" && <label>単位<input value={field.unit} onChange={(event) => update(index, { unit: event.target.value })} placeholder="MPa / mm / kg/m³" /></label>}{field.role === "output" ? <><label>目標方向<select value={field.goal_direction} onChange={(event) => update(index, { goal_direction: event.target.value as Field["goal_direction"] })}><option value="at_least">以上</option><option value="at_most">以下</option><option value="target">目標</option></select></label><label>妥当範囲 min,max<input value={field.plausible_range} onChange={(event) => update(index, { plausible_range: event.target.value })} /></label><label>表示範囲 min,max<input value={field.display_range} onChange={(event) => update(index, { display_range: event.target.value })} /></label></> : field.role !== "categorical" && <><label>物理的許容範囲 min,max<input value={field.allowed_range} onChange={(event) => update(index, { allowed_range: event.target.value })} /></label><label>通常範囲 min,max<input value={field.default_range} onChange={(event) => update(index, { default_range: event.target.value })} /></label><label>学習範囲 min,max<input value={field.training_range} onChange={(event) => update(index, { training_range: event.target.value })} /></label></>}</>}</article>; })}</div>
      <fieldset className="csv-task-confirmations"><legend>学習一行とrelationの確認</legend><label><input type="checkbox" checked={grainConfirmed} onChange={(event) => setGrainConfirmed(event.target.checked)} />1行=1観測であることを確認した</label><label><input type="checkbox" checked={relationsConfirmed} onChange={(event) => setRelationsConfirmed(event.target.checked)} />relationsなしであることを確認した</label><small>この標準CSV Taskはrelationsなしだけを扱います。relationのあるデータは、この画面から無理に準備せず専用Task設計へ進みます。</small></fieldset>
      <button className="primary-button" type="button" disabled={!canPrepare} onClick={() => void prepare()}>{loading ? "準備中…" : "Task・モデル・Datasetを準備してProject作成へ"}</button>
    </>}
    {message && <p role="status">{message}</p>}{error && <p role="alert" className="panel-error">{error}</p>}
  </section>;
}
