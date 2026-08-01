import { useState } from "react";
import type { components } from "../../generated/api-types";
import { inspectCsvOnboarding, prepareCsvOnboarding } from "../../shared/api/client";

type Column = components["schemas"]["CsvInspectionColumn"];
type FieldRole = "" | "composition" | "process" | "categorical" | "output";
type Field = { column: string; role: FieldRole; key: string; label: string; unit: string; goal_direction: "at_least" | "at_most" | "target"; allowed_range: string; default_range: string; training_range: string; plausible_range: string; display_range: string };
type OnboardingError = components["schemas"]["ApiError"];
type TaskIdContract = components["schemas"]["CsvTaskIdContract"];

const canonicalKeyPattern = /^[A-Za-z][A-Za-z0-9_]*$/;
const reservedCanonicalKeys = new Set(["__proto__", "constructor", "prototype"]);
const storageRecoveryCodes = new Set<OnboardingError["code"]>([
  "task-store-unconfigured",
  "task-store-unavailable",
  "model-store-unconfigured",
  "model-store-unavailable",
]);

const range = (value: string) => {
  const [min, max] = value.split(",").map((item) => Number(item.trim()));
  return Number.isFinite(min) && Number.isFinite(max) ? [min, max] as [number, number] : undefined;
};

const validRange = (value: string) => {
  const parsed = range(value);
  return Boolean(parsed && parsed[0] < parsed[1]);
};

const rangeText = (column: Column) => column.observed_min === null || column.observed_max === null
  ? ""
  : `${column.observed_min}, ${column.observed_max}`;

function suggestedCanonicalKey(column: string, index: number, used: Set<string>) {
  const ascii = column.normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^A-Za-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .toLowerCase();
  const base = /^[A-Za-z]/.test(ascii) && !reservedCanonicalKeys.has(ascii) ? ascii : `field_${index + 1}`;
  let candidate = base;
  let suffix = 2;
  while (used.has(candidate) || reservedCanonicalKeys.has(candidate)) candidate = `${base}_${suffix++}`;
  used.add(candidate);
  return candidate;
}

function initialFields(columns: Column[]): Field[] {
  const used = new Set<string>();
  return columns.map((column, index) => ({
    column: column.name,
    role: "",
    key: suggestedCanonicalKey(column.name, index, used),
    label: column.name,
    unit: "",
    goal_direction: "at_least",
    allowed_range: "",
    default_range: "",
    training_range: "",
    plausible_range: "",
    display_range: "",
  }));
}

const columnNames = (fields: Field[], predicate: (field: Field) => boolean) =>
  fields.filter(predicate).map((field) => field.column).join("、");

const errorMessage = (error: OnboardingError | undefined, fallback: string) =>
  [error?.message ?? fallback, error?.next_action ? `次の操作: ${error.next_action}` : ""]
    .filter(Boolean)
    .join("\n");

export type PreparedCsvProjectBinding = {
  datasetViewId: string;
  datasetRevisionId: string;
  taskId: string;
  modelPackageRefId: string;
  sourceSha256: string;
  reloaded: true;
};

export function CsvTaskOnboarding({
  onPrepared,
  onOpenStorage,
}: {
  onPrepared: (binding: PreparedCsvProjectBinding) => void;
  onOpenStorage?: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [rows, setRows] = useState(0);
  const [columns, setColumns] = useState<Column[]>([]);
  const [fields, setFields] = useState<Field[]>([]);
  const [grainConfirmed, setGrainConfirmed] = useState(false);
  const [relationsConfirmed, setRelationsConfirmed] = useState(false);
  const [taskId, setTaskId] = useState("");
  const [taskIdContract, setTaskIdContract] = useState<TaskIdContract | null>(null);
  const [label, setLabel] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [storageError, setStorageError] = useState(false);

  async function inspect() {
    if (!file) return;
    setLoading(true); setError(""); setStorageError(false); setMessage("");
    const response = await inspectCsvOnboarding({ file });
    setLoading(false);
    if (response.error) { setError(errorMessage(response.error, "CSVを確認できませんでした。")); return; }
    if (!response.data) { setError("CSVを確認できませんでした。"); return; }
    const data = response.data;
    if (data.relations !== 0) { setError("この画面はrelationsなしのCSVだけを扱います。relationのあるデータは専用Task設計へ進んでください。"); return; }
    setRows(data.rows);
    setColumns(data.columns);
    setTaskIdContract(data.task_id_contract);
    setGrainConfirmed(false);
    setRelationsConfirmed(false);
    setFields(initialFields(data.columns));
    setMessage(`${data.rows}行・${data.columns.length}列・relations ${data.relations}件を確認しました。${data.notice}`);
  }

  function update(index: number, change: Partial<Field>) { setFields((current) => current.map((field, fieldIndex) => fieldIndex === index ? { ...field, ...change } : field)); }

  const inputCount = fields.filter((field) => field.role === "composition" || field.role === "process" || field.role === "categorical").length;
  const outputCount = fields.filter((field) => field.role === "output").length;
  const taskIdIsValid = taskIdContract !== null && new RegExp(taskIdContract.pattern).test(taskId) && taskId.length >= taskIdContract.min_length;
  const preparationBlockers: string[] = [];
  if (!file) preparationBlockers.push("CSVファイルを選択してください");
  if (!taskId.trim()) preparationBlockers.push("Task IDを入力してください");
  else if (!taskIdIsValid) preparationBlockers.push(`Task IDは利用可能文字と形式を確認してください（例: ${taskIdContract?.example ?? "—"}）`);
  if (!label.trim()) preparationBlockers.push("表示名を入力してください");
  if (!inputCount) preparationBlockers.push("入力列を1項目以上指定してください");
  if (!outputCount) preparationBlockers.push("出力列を1項目以上指定してください");

  const selectedFields = fields.filter((field) => field.role);
  const missingKey = columnNames(selectedFields, (field) => !field.key.trim() || !canonicalKeyPattern.test(field.key) || reservedCanonicalKeys.has(field.key));
  const duplicateKey = [...new Set(selectedFields.map((field) => field.key).filter((key, index, keys) => keys.indexOf(key) !== index))].join("、");
  const missingLabel = columnNames(selectedFields, (field) => !field.label.trim());
  const missingUnit = columnNames(selectedFields, (field) => field.role !== "categorical" && !field.unit.trim());
  const inputFields = fields.filter((field) => field.role === "composition" || field.role === "process");
  const outputFields = fields.filter((field) => field.role === "output");
  const missingAllowedRange = columnNames(inputFields, (field) => !validRange(field.allowed_range));
  const missingDefaultRange = columnNames(inputFields, (field) => !validRange(field.default_range));
  const missingTrainingRange = columnNames(inputFields, (field) => !validRange(field.training_range));
  const missingPlausibleRange = columnNames(outputFields, (field) => !validRange(field.plausible_range));
  const missingDisplayRange = columnNames(outputFields, (field) => !validRange(field.display_range));
  if (missingKey) preparationBlockers.push(`canonical keyは英字で始まる英数字・_だけにしてください: ${missingKey}`);
  if (duplicateKey) preparationBlockers.push(`canonical keyが重複しています。列ごとに一意にしてください: ${duplicateKey}`);
  if (missingLabel) preparationBlockers.push(`表示名を確認してください: ${missingLabel}`);
  if (missingUnit) preparationBlockers.push(`単位を明示してください: ${missingUnit}`);
  if (missingAllowedRange) preparationBlockers.push(`入力の物理的許容範囲をmin,maxで明示してください: ${missingAllowedRange}`);
  if (missingDefaultRange) preparationBlockers.push(`入力の通常範囲をmin,maxで明示してください: ${missingDefaultRange}`);
  if (missingTrainingRange) preparationBlockers.push(`入力の学習範囲をmin,maxで確認してください: ${missingTrainingRange}`);
  if (missingPlausibleRange) preparationBlockers.push(`出力の妥当範囲をmin,maxで明示してください: ${missingPlausibleRange}`);
  if (missingDisplayRange) preparationBlockers.push(`出力の表示範囲をmin,maxで明示してください: ${missingDisplayRange}`);
  if (!grainConfirmed) preparationBlockers.push("1行=1観測であることを確認してください");
  if (!relationsConfirmed) preparationBlockers.push("relationsなしであることを確認してください");
  const canPrepare = preparationBlockers.length === 0 && !loading;

  async function prepare() {
    if (!file || !canPrepare) return;
    const payload = selectedFields.map((field) => ({
      column: field.column, role: field.role, key: field.key, label: field.label, unit: field.unit,
      goal_direction: field.goal_direction,
      allowed_range: range(field.allowed_range), default_range: range(field.default_range), training_range: range(field.training_range),
      plausible_range: range(field.plausible_range), display_range: range(field.display_range),
    }));
    setLoading(true); setError(""); setStorageError(false); setMessage("");
    const response = await prepareCsvOnboarding({
      file,
      task_id: taskId,
      label,
      estimator_id: "ridge.v1",
      fields_json: JSON.stringify(payload),
      grain_confirmation: "one-row-one-observation",
      relation_confirmation: "no-relations",
    });
    setLoading(false);
    if (response.error) {
      setStorageError(storageRecoveryCodes.has(response.error.code));
      setError(errorMessage(response.error, "新しいTaskを準備できませんでした。"));
      return;
    }
    if (!response.data) { setError("新しいTaskを準備できませんでした。"); return; }
    const data = response.data;
    setMessage(data.reused_existing
      ? `${data.task_id}の保存済みTask・Modelを検証し、同じidentityでProject作成へ接続しました。`
      : `${data.task_id}を登録・検証・再読込しました。Project作成画面でidentityを確認できます。`);
    onPrepared({
      datasetViewId: data.dataset_view_revision_id,
      datasetRevisionId: data.dataset_revision_id,
      taskId: data.task_id,
      modelPackageRefId: data.model_package_ref_id,
      sourceSha256: data.source_sha256,
      reloaded: true,
    });
  }

  return <section className="csv-task-onboarding" aria-labelledby="csv-task-onboarding-heading">
    <header><span className="overline">CSV NEW TASK</span><h3 id="csv-task-onboarding-heading">CSVから新しい予測問題を準備</h3><p>元CSVは読取専用です。観測範囲は表示するだけで、物理範囲には自動設定しません。</p></header>
    <label>CSVファイル<input type="file" accept=".csv,text/csv" onChange={(event) => { setFile(event.target.files?.[0] ?? null); setRows(0); setColumns([]); setFields([]); setTaskIdContract(null); setGrainConfirmed(false); setRelationsConfirmed(false); }} /></label>
    <button className="outline-button" type="button" disabled={!file || loading} onClick={() => void inspect()}>{loading ? "確認中…" : "CSVをプレビュー"}</button>
    {columns.length > 0 && <>
      <label>Task ID<input value={taskId} onChange={(event) => setTaskId(event.target.value)} placeholder={taskIdContract?.example ?? ""} aria-invalid={Boolean(taskId && !taskIdIsValid)} aria-describedby="csv-task-id-help" /></label>
      <small id="csv-task-id-help">利用可能文字と形式はこのTask作成APIの契約に従います。例: {taskIdContract?.example ?? "—"}</small>
      <label>表示名<input value={label} onChange={(event) => setLabel(event.target.value)} placeholder="コンクリート流動性" /></label>
      <div className="csv-task-summary" aria-live="polite"><span>{rows}行</span><span>{columns.length}列</span><span>入力 {inputCount}項目</span><span>出力 {outputCount}項目</span></div>
      <p className="csv-task-onboarding-note">数値入力は、物理的許容範囲／通常範囲／学習範囲を明示してください。出力は妥当範囲／表示範囲を明示してください。</p>
      <div className="csv-task-columns">{fields.map((field, index) => { const column = columns[index]; const missing = Math.max(0, rows - column.non_empty); const observed = rangeText(column); const trainingFromObserved = Boolean(observed && field.training_range === observed); return <article key={field.column}><strong>{field.column}</strong><small>元列名: {field.column} · {column.kind} · 欠損 {missing}件 / {rows}件 · 観測 {column.observed_min ?? "—"}–{column.observed_max ?? "—"}</small><label>役割<select value={field.role} onChange={(event) => update(index, { role: event.target.value as FieldRole })}><option value="">使わない</option><option value="composition">入力: 組成</option><option value="process">入力: 条件</option><option value="categorical">入力: カテゴリ</option><option value="output">出力</option></select></label>{field.role && <><label>canonical key<input value={field.key} onChange={(event) => update(index, { key: event.target.value })} aria-invalid={!field.key || !canonicalKeyPattern.test(field.key) || reservedCanonicalKeys.has(field.key)} /></label><small>元列名との対応を確認して編集できます。英字で始まる英数字・_のみ。</small><label>表示名<input value={field.label} onChange={(event) => update(index, { label: event.target.value })} /></label>{field.role !== "categorical" && <label>単位<input value={field.unit} onChange={(event) => update(index, { unit: event.target.value })} placeholder="MPa / mm / kg/m³" /></label>}{field.role === "output" ? <><label>目標方向<select value={field.goal_direction} onChange={(event) => update(index, { goal_direction: event.target.value as Field["goal_direction"] })}><option value="at_least">以上</option><option value="at_most">以下</option><option value="target">目標</option></select></label><label>妥当範囲 min,max<input value={field.plausible_range} onChange={(event) => update(index, { plausible_range: event.target.value })} /></label><label>表示範囲 min,max<input value={field.display_range} onChange={(event) => update(index, { display_range: event.target.value })} /></label></> : field.role !== "categorical" && <><label>物理的許容範囲 min,max<input value={field.allowed_range} onChange={(event) => update(index, { allowed_range: event.target.value })} /></label><label>通常範囲 min,max<input value={field.default_range} onChange={(event) => update(index, { default_range: event.target.value })} /></label><label>学習範囲 min,max<input value={field.training_range} onChange={(event) => update(index, { training_range: event.target.value })} /></label>{observed && <button type="button" className="outline-button" onClick={() => update(index, { training_range: observed })}>観測範囲を学習範囲へ使用</button>}{trainingFromObserved && <small>現在の学習範囲は観測値由来です。物理的許容範囲・通常範囲には反映していません。</small>}</>}</>}</article>; })}</div>
      <fieldset className="csv-task-confirmations"><legend>学習一行とrelationの確認</legend><label><input type="checkbox" checked={grainConfirmed} onChange={(event) => setGrainConfirmed(event.target.checked)} />1行=1観測であることを確認した</label><label><input type="checkbox" checked={relationsConfirmed} onChange={(event) => setRelationsConfirmed(event.target.checked)} />relationsなしであることを確認した</label><small>この標準CSV Taskはrelationsなしだけを扱います。relationのあるデータは、この画面から無理に準備せず専用Task設計へ進みます。</small></fieldset>
      <section id="csv-task-preparation-status" className="csv-task-preparation-status" aria-labelledby="csv-task-preparation-status-heading">
        <h4 id="csv-task-preparation-status-heading">準備条件</h4>
        {preparationBlockers.length > 0 ? <ul>{preparationBlockers.map((blocker) => <li key={blocker}>{blocker}</li>)}</ul> : <p>準備条件を満たしています。</p>}
        <small>観測最小値・最大値はデータの要約であり、物理的な許容範囲や目標値には自動で使いません。未確定のまま進める場合は、モデル化と予測の対象にできません。</small>
      </section>
      <button className="primary-button" type="button" disabled={!canPrepare} aria-describedby={preparationBlockers.length > 0 ? "csv-task-preparation-status" : undefined} onClick={() => void prepare()}>{loading ? "準備中…" : "Task・モデル・Datasetを準備してProject作成へ"}</button>
    </>}
    {message && <p role="status">{message}</p>}
    {error && <div role="alert" className="panel-error csv-task-onboarding-error"><p>{error}</p>{storageError && onOpenStorage && <button type="button" className="outline-button" onClick={onOpenStorage}>保存場所を管理して再確認</button>}</div>}
  </section>;
}
