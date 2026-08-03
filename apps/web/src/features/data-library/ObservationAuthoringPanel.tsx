import { useEffect, useMemo, useState } from "react";
import {
  workbenchApi,
  type ApiObservationAuthoringResult,
  type ApiObservationAuthoringTask,
  type ApiProfileWorkbenchRegistration,
} from "../../shared/api/workbench-api";
import { resolveObservationAuthoringTaskId } from "./observationAuthoringState";

function suggestedColumn(key: string, label: string, columns: string[]): string {
  return columns.find((column) => column === key)
    ?? columns.find((column) => column.toLowerCase() === label.toLowerCase())
    ?? "";
}

export function ObservationAuthoringPanel({
  onRegistered,
}: {
  onRegistered: (registration: ApiProfileWorkbenchRegistration) => void;
}) {
  const [tasks, setTasks] = useState<ApiObservationAuthoringTask[]>([]);
  const [taskId, setTaskId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [columns, setColumns] = useState<string[]>([]);
  const [observationId, setObservationId] = useState("");
  const [groupId, setGroupId] = useState("");
  const [inputColumns, setInputColumns] = useState<Record<string, string>>({});
  const [targetColumns, setTargetColumns] = useState<Record<string, string>>({});
  const [grain, setGrain] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [datasetName, setDatasetName] = useState("");
  const [validationFolds, setValidationFolds] = useState(5);
  const [ridgeAlpha, setRidgeAlpha] = useState(1);
  const [result, setResult] = useState<ApiObservationAuthoringResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const selectedTask = tasks.find((task) => task.task_id === taskId) ?? null;

  useEffect(() => {
    workbenchApi.listObservationAuthoringTasks()
      .then((items) => {
        setTasks(items);
        setTaskId((current) => resolveObservationAuthoringTaskId(current, items));
      })
      .catch((cause) => setError(cause instanceof Error ? cause.message : "対応Taskを取得できませんでした。"));
  }, []);

  useEffect(() => {
    if (!selectedTask || !columns.length) return;
    setInputColumns(Object.fromEntries(
      selectedTask.inputs.map((field) => [field.key, suggestedColumn(field.key, field.label, columns)]),
    ));
    setTargetColumns(Object.fromEntries(
      selectedTask.targets.map((field) => [field.key, suggestedColumn(field.key, field.label, columns)]),
    ));
  }, [columns, selectedTask]);

  const ready = useMemo(() => Boolean(
    file
    && selectedTask
    && observationId
    && groupId
    && grain.trim()
    && confirmed
    && selectedTask.inputs.every((field) => inputColumns[field.key])
    && selectedTask.targets.every((field) => targetColumns[field.key]),
  ), [confirmed, file, grain, groupId, inputColumns, observationId, selectedTask, targetColumns]);

  async function selectSource(selected: File | null) {
    setFile(selected);
    setResult(null);
    setConfirmed(false);
    setError("");
    if (!selected) {
      setColumns([]);
      return;
    }
    if (!selected.name.toLowerCase().endsWith(".csv")) {
      setColumns([]);
      setError("この導入面では単一表CSVを選択してください。複数表Excelは既存Profile Workbenchで扱います。");
      return;
    }
    const header = (await selected.text()).split(/\r?\n/, 1)[0] ?? "";
    const names = header.split(",").map((value) => value.trim()).filter(Boolean);
    setColumns(names);
    setObservationId(names.find((name) => /(?:specimen|observation|record).*id/i.test(name)) ?? "");
    setGroupId(names.find((name) => /(?:condition|group|batch|run).*id/i.test(name)) ?? "");
  }

  async function authorAndRegister() {
    if (!ready || !file || !selectedTask) return;
    setBusy(true);
    setError("");
    try {
      const authored = await workbenchApi.authorObservationProfile(file, {
        task_id: selectedTask.task_id,
        observation_grain: grain.trim(),
        observation_id_column: observationId,
        group_column: groupId,
        inputs: selectedTask.inputs.map((field) => ({
          path: field.key,
          column: inputColumns[field.key],
          ...(field.unit ? { source_unit: field.unit } : {}),
        })),
        targets: selectedTask.targets.map((field) => ({
          key: field.key,
          column: targetColumns[field.key],
          source_unit: field.unit ?? "1",
        })),
        technical_metadata_columns: [],
        validation_folds: validationFolds,
        ridge_alpha: ridgeAlpha,
      });
      setResult(authored);
      const registration = await workbenchApi.registerProfileWorkbook(
        file,
        authored.profile_digest,
        authored.source_sha256,
        datasetName.trim() || file.name.replace(/\.csv$/i, ""),
      );
      onRegistered(registration);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "反復測定Datasetを準備できませんでした。");
    } finally {
      setBusy(false);
    }
  }

  return <section className="observation-authoring-panel" aria-labelledby="observation-authoring-title">
    <div className="panel-title">
      <div><span className="overline">REPEATED MEASUREMENTS</span><h3 id="observation-authoring-title">反復測定をObservationとして登録</h3></div>
      <span className="profile-ready-badge">単一表CSV</span>
    </div>
    <p>1行を個別観測、同じconditionを分割groupとして保持します。平均行へ潰さず、目的変数ごとの欠測も別cohortとして扱います。</p>
    <div className="observation-authoring-grid">
      <label>予測タスク<select value={taskId} onChange={(event) => setTaskId(event.target.value)}>{tasks.map((task) => <option key={task.task_id} value={task.task_id}>{task.label}</option>)}</select></label>
      <label>CSV<input type="file" accept=".csv,text/csv" onChange={(event) => void selectSource(event.target.files?.[0] ?? null)} /></label>
      <label>Dataset名<input value={datasetName} maxLength={160} onChange={(event) => setDatasetName(event.target.value)} /></label>
      <label>観測ID<select value={observationId} onChange={(event) => setObservationId(event.target.value)}><option value="">選択</option>{columns.map((column) => <option key={column}>{column}</option>)}</select></label>
      <label>分割group<select value={groupId} onChange={(event) => setGroupId(event.target.value)}><option value="">選択</option>{columns.map((column) => <option key={column}>{column}</option>)}</select></label>
      <label className="observation-grain-field">1行が表す観測<input value={grain} placeholder="例: 同一条件から採取した個別試験片" onChange={(event) => setGrain(event.target.value)} /></label>
      <label>Validation Plan<select value={validationFolds} onChange={(event) => setValidationFolds(Number(event.target.value))}>{[3, 5, 10].map((folds) => <option key={folds} value={folds}>grouped {folds}-fold</option>)}</select></label>
      <label>Estimator<span className="observation-estimator-choice">Ridge · alpha <input type="number" min="0.000001" step="0.1" value={ridgeAlpha} onChange={(event) => setRidgeAlpha(Number(event.target.value))} /></span></label>
    </div>
    {selectedTask && columns.length > 0 && <details className="observation-binding-review" open>
      <summary>入力・実測の列対応を確認</summary>
      {[...selectedTask.inputs, ...selectedTask.targets].map((field) => {
        const target = selectedTask.targets.some((item) => item.key === field.key);
        const values = target ? targetColumns : inputColumns;
        const setter = target ? setTargetColumns : setInputColumns;
        return <label key={`${target ? "target" : "input"}-${field.key}`}>
          <span><b>{field.label}</b><code>{field.key}</code>{field.unit && <small>{field.unit}</small>}</span>
          <select value={values[field.key] ?? ""} onChange={(event) => setter({ ...values, [field.key]: event.target.value })}><option value="">未選択</option>{columns.map((column) => <option key={column}>{column}</option>)}</select>
          <small>{target ? "実測" : "候補入力"}</small>
        </label>;
      })}
    </details>}
    <label className="observation-authoring-confirm"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span>観測IDは行ごとに一意で、groupは同一条件の反復を表すことを確認しました。</span></label>
    {error && <p className="panel-error" role="alert">{error}</p>}
    {result && <p className="profile-next-action" role="status"><b>Profile検証済み</b><span>{result.observations}観測 · {result.groups}group · target別eligibilityを保持</span></p>}
    <button type="button" className="primary-button" disabled={!ready || busy} onClick={() => void authorAndRegister()}>{busy ? "検証・登録中…" : "Profileを検証してDataset Revisionを登録"}</button>
  </section>;
}
