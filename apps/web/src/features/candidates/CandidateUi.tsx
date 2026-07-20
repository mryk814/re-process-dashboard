import { useState, type ReactNode } from "react";
import type { CandidateViewModel } from "./candidateModel";
import { getCandidateInputValue, numericTaskInputs, orderedInputGroups, type NumericTaskInput, type TaskDefinitionContract, type TaskInputGroup } from "./taskDefinition";
import type { ApiPreview } from "../../shared/api/workbench-api";
import type { CandidateSaveState } from "./useCandidateEditor";

const saveLabels: Record<CandidateSaveState, string> = {
  idle: "",
  dirty: "未保存",
  saving: "保存中",
  saved: "保存済み",
  conflict: "競合",
  error: "保存失敗",
};

function allowedRange(input: NumericTaskInput) {
  if (!input.allowed_range) throw new Error(`数値fieldにallowed_rangeがありません: ${input.path}`);
  return input.allowed_range;
}

function sliderScale(input: NumericTaskInput, value: number) {
  const range = allowedRange(input);
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

function CandidateInputGroup({ candidate, group, numeric, fieldErrors, onInput }: {
  candidate: CandidateViewModel;
  group: TaskInputGroup;
  numeric: Map<string, NumericTaskInput>;
  fieldErrors: Array<{ path: string; message: string }>;
  onInput: (path: string, value: number | string) => void;
}) {
  return (
    <section className={`inspector-section task-input-group ${group.key}`} data-input-group={group.key}>
      <div className="section-heading"><h3>{group.label}</h3><span>{group.fields[0]?.unit ?? ""}</span></div>
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
          const scale = sliderScale(input, numberValue);
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
        <span className="overline">SELECTED CANDIDATE</span>
        <h2>選択候補の入力</h2>
        <small className={`candidate-save-state ${saveState}`}>{saveLabels[saveState]}</small>
        {saveState === "conflict" && <span className="candidate-conflict-actions"><button type="button" onClick={onReload}>再読込</button><button type="button" onClick={onCopyDraft}>変更をコピー</button></span>}
      </div>
      {unplacedErrors.length > 0 && <div className="candidate-field-errors" role="alert">{unplacedErrors.map((error) => <small className="field-error" key={`${error.path}:${error.message}`}><b>{unplacedErrorLabel(error.path)}:</b> {error.message}</small>)}</div>}
      {primaryGroups.map((group) => <CandidateInputGroup key={group.key} candidate={candidate} group={group} numeric={numeric} fieldErrors={fieldErrors} onInput={onInput} />)}
      {heatGroup && (heatGroup.fields.every((field) => field.editable) ? heatPattern : <fieldset className="readonly-heat-pattern" disabled>{heatPattern}</fieldset>)}
      {auxiliaryGroups.length > 0 && <details className="task-auxiliary-inputs"><summary>その他の入力</summary>{auxiliaryGroups.map((group) => <CandidateInputGroup key={group.key} candidate={candidate} group={group} numeric={numeric} fieldErrors={fieldErrors} onInput={onInput} />)}</details>}
      {taskDefinition.fixed_context.length > 0 && <dl className="task-fixed-context">{[...taskDefinition.fixed_context].sort((a, b) => a.order - b.order).map((item) => <div key={item.path}><dt>{item.label}</dt><dd>{String(item.value)}</dd></div>)}</dl>}
    </aside>
  );
}

export function ComparisonTable({
  candidates,
  selectedId,
  taskDefinition,
  previewsByCandidate,
  targetValues,
  onSelect,
  onName,
  onInput,
}: {
  candidates: CandidateViewModel[];
  selectedId: string;
  taskDefinition: TaskDefinitionContract;
  previewsByCandidate: Record<string, ApiPreview>;
  targetValues: Record<string, number>;
  onSelect: (id: string) => void;
  onName: (id: string, value: string) => void;
  onInput: (id: string, path: string, value: number | string) => void;
}) {
  const groups = orderedInputGroups(taskDefinition).filter((group) => group.key !== "heat_pattern");
  const preserveAnnealedFlow = taskDefinition.input_groups.some((group) => group.key === "heat_pattern");
  const primaryGroups = preserveAnnealedFlow ? groups.filter((group) => group.key === "composition") : groups;
  const auxiliaryGroups = preserveAnnealedFlow ? groups.filter((group) => group.key !== "composition") : [];
  const primaryFields = primaryGroups.flatMap((group) => group.fields);
  const auxiliaryFields = auxiliaryGroups.flatMap((group) => group.fields);
  const outputs = taskDefinition.outputs;
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const support = (value?: string) => value === "supported" ? "範囲内" : value === "caution" ? "要確認" : value === "extrapolated" ? "外挿" : "未計算";
  return (
    <div className="candidate-comparison">
      <section className="comparison-grid" aria-label="候補の入力と予測結果比較">
        <table className="candidate-name-table"><thead><tr><th>候補</th></tr><tr aria-hidden="true"><th /></tr></thead><tbody>{candidates.map((candidate) => <tr key={candidate.id} className={candidate.id === selectedId ? "selected-row" : ""} onClick={() => onSelect(candidate.id)}><th><input aria-label={`${candidate.label}の候補名`} maxLength={80} value={candidate.label} onFocus={() => onSelect(candidate.id)} onChange={(event) => onName(candidate.id, event.target.value)} /></th></tr>)}</tbody></table>
        <div className="comparison-detail-scroll"><table className="comparison-detail-table"><thead><tr>{primaryGroups.map((group) => <th colSpan={group.fields.length} key={group.key}>{group.label}</th>)}<th colSpan={outputs.length}>予測結果</th><th className="support-header" rowSpan={2}>支持度</th>{auxiliaryGroups.map((group) => <th colSpan={group.fields.length} key={group.key}>{group.label}</th>)}</tr><tr>{primaryFields.map((field) => <th className="composition-col" key={field.path}>{field.label}<small>{field.unit ?? ""}</small></th>)}{outputs.map((output) => <th className="prediction-col" key={output.key}>{output.label}<small>{Number.isFinite(targetValues[output.key]) ? `目標 ${output.goal_direction === "at_most" ? "≤" : "≥"} ${targetValues[output.key]}` : output.unit}</small></th>)}{auxiliaryFields.map((field) => <th className="composition-col" key={field.path}>{field.label}<small>{field.unit ?? ""}</small></th>)}</tr></thead>
          <tbody>{candidates.map((candidate) => { const preview = previewsByCandidate[candidate.id]; const renderField = (field: (typeof primaryFields)[number]) => { const current = getCandidateInputValue(candidate.raw.inputs, field.path); if (field.kind === "categorical") return <td className="composition-col" key={field.path}><select disabled={!field.editable} aria-label={`${candidate.label} ${field.label}`} value={String(current ?? "")} onFocus={() => onSelect(candidate.id)} onChange={(event) => onInput(candidate.id, field.path, event.target.value)}>{field.choices.map((choice) => <option key={choice}>{choice}</option>)}</select></td>; const key = `${candidate.id}:${field.path}`; const value = drafts[key] ?? String(current ?? 0); return <td className="composition-col" key={field.path}><input disabled={!field.editable} type="number" step="any" value={value} aria-label={`${candidate.label} ${field.label}`} onFocus={() => onSelect(candidate.id)} onChange={(event) => setDrafts((items) => ({ ...items, [key]: event.target.value }))} onBlur={(event) => { const next = Number(event.target.value); setDrafts((items) => { const { [key]: _, ...rest } = items; return rest; }); if (Number.isFinite(next) && next !== current) onInput(candidate.id, field.path, next); }} /></td>; }; return <tr key={candidate.id} className={candidate.id === selectedId ? "selected-row" : ""} onClick={() => onSelect(candidate.id)}>{primaryFields.map(renderField)}{outputs.map((output) => { const prediction = preview?.predictions[output.key]; return <td className="prediction-cell prediction-col" key={output.key}>{prediction ? <span className="metric-value">{prediction.value.toLocaleString("ja-JP", { maximumFractionDigits: output.unit === "%" ? 1 : 0 })} <small>{prediction.unit}</small>{typeof prediction.goal_probability === "number" && <em>達成 {(prediction.goal_probability * 100).toLocaleString("ja-JP", { maximumFractionDigits: 0 })}%</em>}</span> : <span className="empty-cell">—</span>}</td>; })}<td className="support-cell"><span className={`status-dot ${preview?.support.status === "supported" ? "success" : preview ? "caution" : ""}`} />{support(preview?.support.status)}</td>{auxiliaryFields.map(renderField)}</tr>; })}</tbody>
        </table></div>
      </section>
    </div>
  );
}
