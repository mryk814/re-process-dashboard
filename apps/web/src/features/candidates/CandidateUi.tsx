import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type ReactNode, type UIEvent } from "react";
import type { CandidateViewModel } from "./candidateModel";
import { getCandidateInputValue, numericTaskInputs, orderedInputGroups, type NumericRange, type NumericTaskInput, type TaskDefinitionContract, type TaskInputGroup } from "./taskDefinition";
import type { ApiPreview } from "../../shared/api/workbench-api";
import { CandidateAddButton } from "../../shared/ui/CandidateAddButton";
import type { CandidateSaveState } from "./useCandidateEditor";
import { formatDisplayNumber, formatInputNumber, type DisplayDecimalOverrides } from "./numberFormat";
import { formatPredictionPoint, predictionHasInterval, predictionIntervalLabel } from "../../shared/predictionPresentation";
import { assessPrediction } from "../../shared/outputPresentation";

const saveLabels: Record<CandidateSaveState, string> = {
  idle: "",
  dirty: "未保存",
  saving: "保存中",
  saved: "保存済み",
  conflict: "競合",
  error: "保存失敗",
};

function RowActionIcon({ name }: { name: "trash" | "save" | "check" }) {
  const path = name === "trash"
    ? <path d="M4 7h16M10 11v6m4-6v6M9 7l1-3h4l1 3m-9 0 1 13h10l1-13" />
    : name === "check"
      ? <path d="m5 12 4 4L19 6" />
      : <><path d="M5 4h12l2 2v14H5V4Z" /><path d="M8 4v6h8V4M8 20v-6h8v6" /></>;
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{path}</svg>;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

const comparisonInputShareStorageKey = "material-workbench:layout:comparison-input-share:v1";

function storedComparisonInputShare() {
  if (typeof window === "undefined") return 45;
  try {
    const raw = window.localStorage.getItem(comparisonInputShareStorageKey);
    if (raw === null) return 45;
    const value = Number(raw);
    return Number.isFinite(value) ? value : 45;
  } catch {
    return 45;
  }
}

function saveComparisonInputShare(value: number) {
  try {
    window.localStorage.setItem(comparisonInputShareStorageKey, String(value));
  } catch {
    // Layout persistence is optional when local storage is unavailable.
  }
}

function ComparisonSplitResizer({
  value,
  min,
  max,
  onChange,
  onReset,
  onDrag,
}: {
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
  onReset: () => void;
  onDrag: (startValue: number, deltaX: number) => number;
}) {
  const drag = useRef<{ pointerId: number; startX: number; startValue: number } | null>(null);
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? 8 : 2;
    const next = event.key === "ArrowLeft"
      ? value - step
      : event.key === "ArrowRight"
        ? value + step
        : event.key === "Home"
          ? min
          : event.key === "End"
            ? max
            : null;
    if (next === null) return;
    event.preventDefault();
    onChange(clamp(next, min, max));
  };
  return <div
    className="comparison-split-resizer"
    role="separator"
    tabIndex={0}
    aria-label="入力条件と予測値の幅を調整"
    aria-orientation="vertical"
    aria-valuemin={Math.round(min)}
    aria-valuemax={Math.round(max)}
    aria-valuenow={Math.round(value)}
    aria-valuetext={`入力条件 ${Math.round(value)}%`}
    aria-controls="comparison-input-pane comparison-prediction-pane"
    title="ドラッグで幅を調整・ダブルクリックで初期幅"
    onKeyDown={onKeyDown}
    onDoubleClick={onReset}
    onPointerDown={(event) => {
      drag.current = { pointerId: event.pointerId, startX: event.clientX, startValue: value };
      event.currentTarget.setPointerCapture(event.pointerId);
    }}
    onPointerMove={(event) => {
      const current = drag.current;
      if (!current || current.pointerId !== event.pointerId || !event.currentTarget.hasPointerCapture(event.pointerId)) return;
      onChange(clamp(onDrag(current.startValue, event.clientX - current.startX), min, max));
    }}
    onPointerUp={(event) => {
      if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
      drag.current = null;
    }}
    onPointerCancel={() => { drag.current = null; }}
    onLostPointerCapture={() => { drag.current = null; }}
  />;
}

function allowedRange(input: NumericTaskInput, inputRanges: Record<string, NumericRange>) {
  const configured = inputRanges[input.id];
  if (configured) return configured;
  if (!input.allowed_range) throw new Error(`数値fieldにallowed_rangeがありません: ${input.path}`);
  return input.allowed_range;
}

function sliderScale(input: NumericTaskInput, value: number, inputRanges: Record<string, NumericRange>) {
  const range = allowedRange(input, inputRanges);
  const learnedMin = input.training_range?.min ?? range.min;
  const learnedMax = input.training_range?.max ?? range.max;
  const divisor = Math.max(range.max - range.min, Number.EPSILON);
  const start = Math.max(0, Math.min(100, ((learnedMin - range.min) / divisor) * 100));
  const end = Math.max(0, Math.min(100, ((learnedMax - range.min) / divisor) * 100));
  return {
    ...range,
    value: Math.max(range.min, Math.min(range.max, value)),
    style: { background: `linear-gradient(90deg, #dfe6ee 0 ${start}%, #6bb69e ${start}% ${end}%, #dfe6ee ${end}% 100%)` },
  };
}

function unplacedErrorLabel(path: string): string {
  if (path.endsWith(".name") || path === "name") return "候補名";
  const heatPoint = path.match(/heat_pattern\.(\d+)\.(time_s|temperature_c)$/);
  if (heatPoint) return `ヒートパターン ${Number(heatPoint[1]) + 1}点目 ${heatPoint[2] === "time_s" ? "時間" : "温度"}`;
  if (path.includes("heat_pattern")) return "ヒートパターン";
  return path;
}

function CandidateInputGroup({ candidate, group, numeric, inputRanges, fieldErrors, onInput }: {
  candidate: CandidateViewModel;
  group: TaskInputGroup;
  numeric: Map<string, NumericTaskInput>;
  inputRanges: Record<string, NumericRange>;
  fieldErrors: Array<{ path: string; message: string }>;
  onInput: (path: string, value: number | string) => void;
}) {
  return (
    <section className={`inspector-section task-input-group ${group.key}`} data-input-group={group.key}>
      <div className="section-heading"><div className="section-heading-label"><h3>{group.label}</h3>{group.fields.some((field) => numeric.get(field.path)?.training_range) && <small className="training-range-legend"><i aria-hidden="true" />緑帯：学習範囲</small>}</div><span>{group.fields[0]?.unit ?? ""}</span></div>
      <div className={group.key === "composition" ? "composition-fields" : "task-field-grid"}>
        {group.fields.map((field) => {
          const value = getCandidateInputValue(candidate.raw.inputs, field.path);
          const errors = fieldErrors.filter((error) => error.path.endsWith(field.path));
          if (field.kind === "categorical") {
            return <label className="task-select-field" key={field.path}><b>{field.label}</b><select disabled={!field.editable} aria-label={`${candidate.label} ${field.label}`} value={String(value ?? "")} onChange={(event) => onInput(field.path, event.target.value)}>{field.choices.map((choice) => <option key={choice}>{choice}</option>)}</select>{errors.map((error) => <small className="field-error" key={error.path}>{error.message}</small>)}</label>;
          }
          const input = numeric.get(field.path);
          if (!input) throw new Error(`Numeric TaskDefinition field is unavailable: ${field.path}`);
          const numberValue = Number(value ?? 0);
          const scale = sliderScale(input, numberValue, inputRanges);
          return (
            <label className="slider-field" key={field.path}>
              <span><b>{field.label}</b><em><input disabled={!field.editable} className="slider-number" type="number" min={scale.min} max={scale.max} step="any" value={numberValue} aria-label={`${candidate.label} ${field.label}の数値`} onChange={(event) => onInput(field.path, Number(event.target.value))} /> {field.unit}</em></span>
              <input disabled={!field.editable} type="range" min={scale.min} max={scale.max} step="any" value={scale.value} style={scale.style} aria-label={`${candidate.label} ${field.label}`} onChange={(event) => onInput(field.path, Number(event.target.value))} />
              {errors.map((error) => <small className="field-error" key={error.path}>{error.message}</small>)}
            </label>
          );
        })}
      </div>
    </section>
  );
}

export function CandidateInspector({
  candidate,
  taskDefinition,
  saveState,
  inputRanges = {},
  fieldErrors,
  onInput,
  onReload,
  onCopyDraft,
  heatPattern,
  className = "candidate-inspector",
}: {
  candidate: CandidateViewModel;
  taskDefinition: TaskDefinitionContract;
  saveState: CandidateSaveState;
  inputRanges?: Record<string, NumericRange>;
  fieldErrors: Array<{ path: string; message: string }>;
  onInput: (path: string, value: number | string) => void;
  onReload: () => void;
  onCopyDraft: () => void;
  heatPattern?: ReactNode;
  className?: string;
}) {
  const numeric = new Map(numericTaskInputs(taskDefinition).map((input) => [input.path, input]));
  const groups = orderedInputGroups(taskDefinition);
  const heatGroup = groups.find((group) => group.key === "heat_pattern");
  const primaryGroups = heatGroup ? groups.filter((group) => group.key === "composition") : groups.filter((group) => group.key !== "heat_pattern");
  const auxiliaryGroups = heatGroup ? groups.filter((group) => group.key !== "composition" && group.key !== "heat_pattern") : [];
  const ordinaryPaths = groups.filter((group) => group.key !== "heat_pattern").flatMap((group) => group.fields.map((field) => field.path));
  const unplacedErrors = fieldErrors.filter((error) => !ordinaryPaths.some((path) => error.path.endsWith(path)));
  return (
    <aside className={className} aria-label="選択候補の入力">
      <div className="inspector-heading">
        <span className="overline">選択候補</span>
        <h2>選択候補の入力</h2>
        <small className={`candidate-save-state ${saveState}`}>{saveLabels[saveState]}</small>
        {saveState === "conflict" && <span className="candidate-conflict-actions"><button type="button" onClick={onReload}>再読込</button><button type="button" onClick={onCopyDraft}>変更をコピー</button></span>}
      </div>
      {unplacedErrors.length > 0 && <div className="candidate-field-errors" role="alert">{unplacedErrors.map((error) => <small className="field-error" key={`${error.path}:${error.message}`}><b>{unplacedErrorLabel(error.path)}:</b> {error.message}</small>)}</div>}
      {primaryGroups.map((group) => <CandidateInputGroup key={group.key} candidate={candidate} group={group} numeric={numeric} inputRanges={inputRanges} fieldErrors={fieldErrors} onInput={onInput} />)}
      {heatGroup && (heatGroup.fields.every((field) => field.editable) ? heatPattern : <fieldset className="readonly-heat-pattern" disabled>{heatPattern}</fieldset>)}
      {auxiliaryGroups.length > 0 && <details className="task-auxiliary-inputs"><summary>その他の入力</summary>{auxiliaryGroups.map((group) => <CandidateInputGroup key={group.key} candidate={candidate} group={group} numeric={numeric} inputRanges={inputRanges} fieldErrors={fieldErrors} onInput={onInput} />)}</details>}
      {taskDefinition.fixed_context.length > 0 && <dl className="task-fixed-context">{[...taskDefinition.fixed_context].sort((a, b) => a.order - b.order).map((item) => <div key={item.path}><dt>{item.label}</dt><dd>{String(item.value)}</dd></div>)}</dl>}
    </aside>
  );
}

export function ComparisonTable({
  candidates,
  selectedId,
  comparisonExpanded,
  onToggleComparisonExpanded,
  taskDefinition,
  previewsByCandidate,
  targetValues,
  displayDecimalOverrides,
  decisionCandidateId,
  detailedPredictionAvailable,
  saveStates,
  savedRevisionsByCandidate,
  savingCandidateIds,
  snapshotHistoryState,
  onSelect,
  onName,
  onInput,
  onCopy,
  onDelete,
  onSave,
}: {
  candidates: CandidateViewModel[];
  selectedId: string;
  comparisonExpanded: boolean;
  onToggleComparisonExpanded: () => void;
  taskDefinition: TaskDefinitionContract;
  previewsByCandidate: Record<string, ApiPreview>;
  targetValues: Record<string, number>;
  displayDecimalOverrides?: DisplayDecimalOverrides;
  decisionCandidateId: string;
  detailedPredictionAvailable: boolean;
  saveStates: Record<string, CandidateSaveState>;
  savedRevisionsByCandidate: Record<string, number[]>;
  savingCandidateIds: string[];
  snapshotHistoryState: "loading" | "ready" | "error";
  onSelect: (id: string) => void;
  onName: (id: string, value: string) => void;
  onInput: (id: string, path: string, value: number | string) => void;
  onCopy: (id: string) => void;
  onDelete: (id: string) => void;
  onSave: (candidate: CandidateViewModel) => void;
}) {
  const inputGroups = orderedInputGroups(taskDefinition).filter((group) => group.key !== "heat_pattern");
  const inputFields = inputGroups.flatMap((group) => group.fields);
  const outputs = taskDefinition.outputs;
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [inputShare, setInputShare] = useState(() => clamp(storedComparisonInputShare(), 28, 58));
  const [inputShareRange, setInputShareRange] = useState({ min: 28, max: 58 });
  const comparisonGridRef = useRef<HTMLElement>(null);
  const nameScrollRef = useRef<HTMLDivElement>(null);
  const inputScrollRef = useRef<HTMLDivElement>(null);
  const predictionScrollRef = useRef<HTMLDivElement>(null);
  const actionScrollRef = useRef<HTMLDivElement>(null);
  const effectiveInputShare = clamp(inputShare, inputShareRange.min, inputShareRange.max);
  useEffect(() => {
    const updateRange = () => {
      const width = comparisonGridRef.current?.clientWidth ?? 0;
      if (!width) return;
      const min = (220 / width) * 100;
      const nextRange = {
        min,
        max: Math.max(min, ((width - 169 - 240 - 120) / width) * 100),
      };
      setInputShareRange(nextRange);
    };
    const observer = new ResizeObserver(updateRange);
    if (comparisonGridRef.current) observer.observe(comparisonGridRef.current);
    updateRange();
    return () => observer.disconnect();
  }, []);
  useEffect(() => saveComparisonInputShare(inputShare), [inputShare]);
  const syncVerticalScroll = (event: UIEvent<HTMLDivElement>) => {
    const scrollTop = event.currentTarget.scrollTop;
    for (const pane of [nameScrollRef.current, inputScrollRef.current, predictionScrollRef.current, actionScrollRef.current]) {
      if (pane && pane !== event.currentTarget && Math.abs(pane.scrollTop - scrollTop) > 0.5) pane.scrollTop = scrollTop;
    }
  };
  const support = (value?: string) => value === "supported" ? "範囲内" : value === "caution" ? "要確認" : value === "extrapolated" ? "外挿" : "未計算";
  const formatNumber = (value: number) => value.toLocaleString("ja-JP", { maximumFractionDigits: 1 });
  const predictionValue = (prediction: ApiPreview["predictions"][string], outputKey: string) => prediction.target_kind === "continuous" || prediction.target_kind === "continuous_positive" || prediction.target_kind === "count"
    ? formatDisplayNumber(prediction.value, taskDefinition, `output.${outputKey}`, displayDecimalOverrides)
    : formatPredictionPoint(prediction, formatNumber);
  const uncertaintySummary = (prediction: ApiPreview["predictions"][string], outputKey: string) => {
    const components = prediction.uncertainty_components ?? {};
    const model = components.latent_model_std ?? components.model_std;
    const observation = components.observation_noise_std ?? components.observation_std;
    const hasInterval = predictionHasInterval(prediction);
    const interval = hasInterval ? `${formatDisplayNumber(prediction.lower, taskDefinition, `output.${outputKey}`, displayDecimalOverrides)}–${formatDisplayNumber(prediction.upper, taskDefinition, `output.${outputKey}`, displayDecimalOverrides)}` : "利用不可";
    const label = predictionIntervalLabel(prediction);
    const details = [
      hasInterval ? `${label} ${interval}` : label,
      model !== undefined ? `モデル由来 ±${formatNumber(model)}` : null,
      observation !== undefined ? `測定由来 ±${formatNumber(observation)}` : null,
    ].filter(Boolean).join(" / ");
    return { interval, details, label };
  };
  const renderField = (candidate: CandidateViewModel, field: (typeof inputFields)[number]) => {
    const current = getCandidateInputValue(candidate.raw.inputs, field.path);
    if (field.kind === "categorical") return <td className="composition-col" key={field.path}><select disabled={!field.editable} aria-label={`${candidate.label} ${field.label}`} value={String(current ?? "")} onFocus={() => onSelect(candidate.id)} onChange={(event) => onInput(candidate.id, field.path, event.target.value)}>{field.choices.map((choice) => <option key={choice}>{choice}</option>)}</select></td>;
    const key = `${candidate.id}:${field.path}`;
    const value = drafts[key] ?? (typeof current === "number" ? formatInputNumber(current, taskDefinition, field.path, displayDecimalOverrides) : "");
    return <td className="composition-col numeric-cell" key={field.path}><input disabled={!field.editable} type="number" step="any" value={value} aria-label={`${candidate.label} ${field.label}`} onFocus={() => { onSelect(candidate.id); setDrafts((items) => ({ ...items, [key]: typeof current === "number" ? String(current) : "" })); }} onChange={(event) => setDrafts((items) => ({ ...items, [key]: event.target.value }))} onBlur={(event) => { const next = Number(event.target.value); setDrafts((items) => { const { [key]: _, ...rest } = items; return rest; }); if (Number.isFinite(next) && next !== current) onInput(candidate.id, field.path, next); }} /></td>;
  };
  return (
    <div className="candidate-comparison">
      <div className="comparison-grid-viewport">
        <section
          ref={comparisonGridRef}
          className="comparison-grid"
          aria-label="候補の入力と予測結果比較"
          style={{ "--comparison-input-share": `${effectiveInputShare}%` } as CSSProperties}
        >
          <div ref={nameScrollRef} className={`comparison-pane-scroll comparison-name-scroll${comparisonExpanded ? " expanded" : ""}`} onScroll={syncVerticalScroll}><table className="candidate-name-table" aria-label="候補名"><colgroup><col className="candidate-select-column" /><col /></colgroup><thead><tr><th colSpan={2}>候補</th></tr><tr aria-hidden="true"><th /><th /></tr></thead><tbody>{candidates.map((candidate) => { const selected = candidate.id === selectedId; return <tr key={candidate.id} className={selected ? "selected-row" : ""} onClick={() => onSelect(candidate.id)}><td className="candidate-select-cell"><button type="button" className="candidate-select-button" aria-label={`${candidate.label}を選択`} aria-pressed={selected} onClick={(event) => { event.stopPropagation(); onSelect(candidate.id); }}><span aria-hidden="true" /></button></td><th><input aria-label={`${candidate.label}の候補名`} maxLength={80} value={candidate.label} onFocus={() => onSelect(candidate.id)} onChange={(event) => onName(candidate.id, event.target.value)} /></th></tr>; })}</tbody></table></div>
          <div id="comparison-input-pane" ref={inputScrollRef} className={`comparison-pane-scroll comparison-input-scroll${comparisonExpanded ? " expanded" : ""}`} tabIndex={0} aria-label="入力条件" onScroll={syncVerticalScroll}><table className="comparison-detail-table comparison-input-table" aria-label="候補ごとの入力条件"><thead><tr>{inputGroups.map((group) => <th colSpan={group.fields.length} key={group.key}>{group.label}</th>)}</tr><tr>{inputFields.map((field) => <th className="composition-col" key={field.path}>{field.label}<small>{field.unit ?? ""}</small></th>)}</tr></thead><tbody>{candidates.map((candidate) => <tr key={candidate.id} className={candidate.id === selectedId ? "selected-row" : ""} onClick={() => onSelect(candidate.id)}>{inputFields.map((field) => renderField(candidate, field))}</tr>)}</tbody></table></div>
          <ComparisonSplitResizer value={effectiveInputShare} min={inputShareRange.min} max={inputShareRange.max} onChange={setInputShare} onDrag={(startValue, deltaX) => startValue + (deltaX / Math.max(comparisonGridRef.current?.clientWidth ?? 1, 1)) * 100} onReset={() => setInputShare(45)} />
          <div id="comparison-prediction-pane" ref={predictionScrollRef} className={`comparison-pane-scroll comparison-prediction-scroll${comparisonExpanded ? " expanded" : ""}`} tabIndex={0} aria-label="予測値" onScroll={syncVerticalScroll}><table className="comparison-detail-table comparison-prediction-table" aria-label="候補ごとの予測値"><thead><tr><th colSpan={outputs.length}>予測値</th><th colSpan={outputs.length}>不確実性</th><th colSpan={outputs.length}>目標達成</th><th className="support-header">支持度</th></tr><tr>{outputs.map((output) => <th className="prediction-col" key={`value-${output.key}`}>{output.label}<small>{output.unit}</small></th>)}{outputs.map((output) => <th className="uncertainty-col" key={`uncertainty-${output.key}`}>{output.label}<small>予測区間</small></th>)}{outputs.map((output) => <th className="goal-col" key={`goal-${output.key}`}>{output.label}<small>{Number.isFinite(targetValues[output.key]) ? `目標 ${output.goal_direction === "at_most" ? "≤" : "≥"} ${targetValues[output.key]}` : "未設定"}</small></th>)}<th className="support-header">状態</th></tr></thead><tbody>{candidates.map((candidate) => { const preview = previewsByCandidate[candidate.id]; return <tr key={candidate.id} className={candidate.id === selectedId ? "selected-row" : ""} onClick={() => onSelect(candidate.id)}>{outputs.map((output) => { const prediction = preview?.predictions[output.key]; const value = prediction ? predictionValue(prediction, output.key) : ""; const assessment = assessPrediction(output, prediction); return <td className={`prediction-cell prediction-col numeric-cell${assessment.implausible ? " implausible-output" : ""}`} key={`value-${output.key}`}>{prediction ? <span className="metric-value" title={assessment.warning ?? `${value} ${prediction.unit}`.trim()} aria-label={`${value} ${prediction.unit}`.trim()}>{value}{assessment.implausible && <small className="output-warning-badge">⚠ 物理範囲外</small>}</span> : <span className="empty-cell">—</span>}</td>; })}{outputs.map((output) => { const prediction = preview?.predictions[output.key]; if (!prediction) return <td className="uncertainty-cell uncertainty-col numeric-cell" key={`uncertainty-${output.key}`}><span className="empty-cell">—</span></td>; const uncertainty = uncertaintySummary(prediction, output.key); const assessment = assessPrediction(output, prediction); return <td className={`uncertainty-cell uncertainty-col numeric-cell${assessment.implausible ? " implausible-output" : ""}`} key={`uncertainty-${output.key}`}><span className="uncertainty-value" title={assessment.warning ?? uncertainty.details} aria-label={uncertainty.details}>{uncertainty.interval}</span></td>; })}{outputs.map((output) => { const prediction = preview?.predictions[output.key]; const goalValue = prediction?.goal_value ?? targetValues[output.key]; return <td className="goal-cell goal-col numeric-cell" key={`goal-${output.key}`}>{prediction?.goal_probability == null ? <span className="empty-cell">{prediction && Number.isFinite(goalValue) ? "利用不可" : "—"}</span> : <span className="goal-value"><b>{formatNumber(prediction.goal_probability * 100)}%</b><small>{prediction.goal_direction === "at_most" ? "以下" : prediction.goal_direction === "at_least" ? "以上" : "目標"}</small></span>}</td>; })}<td className="support-cell"><span className={`status-dot ${preview?.support.status === "supported" ? "success" : preview ? "caution" : ""}`} />{support(preview?.support.status)}</td></tr>; })}</tbody></table></div>
          <div ref={actionScrollRef} className={`comparison-pane-scroll comparison-action-scroll${comparisonExpanded ? " expanded" : ""}`} aria-label="候補ごとの操作" onScroll={syncVerticalScroll}>
            <table className="comparison-action-table">
              <thead><tr><th>操作</th></tr><tr><th><small>候補ごと</small></th></tr></thead>
              <tbody>{candidates.map((candidate) => {
                const saving = savingCandidateIds.includes(candidate.id);
                const editableSaved = ["idle", "saved"].includes(saveStates[candidate.id] ?? "idle");
                const saved = editableSaved && (savedRevisionsByCandidate[candidate.id]?.includes(candidate.raw.revision) ?? false);
                const historyPending = snapshotHistoryState !== "ready";
                const deleteBlocked = candidates.length <= 1 || decisionCandidateId === candidate.id;
                return <tr key={candidate.id} className={candidate.id === selectedId ? "selected-row" : ""} onClick={() => onSelect(candidate.id)}><td><div className="candidate-row-actions">
                  <CandidateAddButton compact className="candidate-row-icon-button" aria-label={`${candidate.label}を複製`} title="この候補を複製" onClick={(event) => { event.stopPropagation(); onCopy(candidate.id); }} />
                  <button type="button" className="candidate-row-icon-button candidate-row-delete-button" aria-label={`${candidate.label}を削除`} title={decisionCandidateId === candidate.id ? "採用判断を解除してから削除してください" : "この候補を削除"} disabled={deleteBlocked} onClick={(event) => { event.stopPropagation(); onDelete(candidate.id); }}><RowActionIcon name="trash" /></button>
                  <button type="button" className={`candidate-row-save-button${saved ? " saved" : ""}`} aria-label={`${candidate.label}の詳細予測を${saved ? "保存済み" : snapshotHistoryState === "loading" ? "確認中" : snapshotHistoryState === "error" ? "履歴確認失敗" : "保存"}`} title={!detailedPredictionAvailable ? "このタスクでは詳細予測を利用できません" : snapshotHistoryState === "loading" ? "保存履歴を確認しています" : snapshotHistoryState === "error" ? "保存履歴を確認できませんでした" : saved ? "現在の編集版は保存済みです" : !editableSaved ? "入力の保存完了後に実行できます" : "詳細予測を保存"} disabled={!detailedPredictionAvailable || historyPending || !editableSaved || saving || saved} onClick={(event) => { event.stopPropagation(); onSave(candidate); }}><RowActionIcon name={saved ? "check" : "save"} /></button>
                </div></td></tr>;
              })}</tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}
