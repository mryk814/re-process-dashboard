import { useEffect, useRef, useState } from "react";
import { SvgChartTooltip } from "../../shared/ui/SvgChartTooltip";
import { candidateInputIdentity } from "../../shared/api/inferenceRequestCache";
import { clampToRange, isOutsideRange } from "../../shared/outputPresentation";
import { isTargetRange, type TargetGoal } from "../../shared/targetGoals";
import {
  categoricalTaskInputs,
  numericTaskInputs,
  responseCurveVariables,
  type CandidateViewModel as Candidate,
  type NumericTaskInput,
  type TaskDefinitionContract,
  type TaskOutputDefinition,
} from "../candidates";
import {
  workbenchApi,
  type ApiCurveFamily,
  type ApiProject,
  type ApiPreview,
  type ApiResponseCurve,
} from "../../shared/api/workbench-api";
import { workbenchRequestKey } from "./workbenchIdentity";
import {
  emptyInferenceSurface,
  inferenceSurfaceStatus,
  rejectInferenceSurface,
  requestInferenceSurface,
  resolveInferenceSurface,
  type InferenceSurfaceState,
} from "./inferenceSurfaceState";

type CurvePoint = ApiResponseCurve["points"][number];
type CurveRange = { min: number; max: number };
export type ResponseCurveRanges = { x?: Record<string, CurveRange>; y?: Record<string, CurveRange> };
type CurveRangeDraft = { min: string; max: string; enabled: boolean };
const goalAxisValues = (goal: TargetGoal | undefined) => isTargetRange(goal) ? [goal.lower, goal.upper] : typeof goal === "number" ? [goal] : [];

function allowedRange(input: NumericTaskInput) {
  if (!input.allowed_range) throw new Error(`数値fieldにallowed_rangeがありません: ${input.path}`);
  return input.allowed_range;
}

function number(value: number, digits = 0) {
  return value.toLocaleString("ja-JP", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

const CANDIDATE_COLORS = ["#d97706", "#0f766e", "#9333a8", "#dc2626", "#0891b2", "#4f46e5", "#65a30d", "#c2410c"];

function candidateColor(candidateId: string, selectedId: string) {
  if (candidateId === selectedId) return "#1f5fc4";
  let hash = 0;
  for (const character of candidateId) hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  return CANDIDATE_COLORS[hash % CANDIDATE_COLORS.length];
}

export function UnavailablePanel({ title }: { title: string }) {
  return <section className="response-curves-panel unavailable-panel" aria-label={`${title}は利用できません`}><div className="panel-title"><h2>{title}</h2></div><p className="empty-evidence">このタスクでは利用できません。</p></section>;
}

function chartDigits(min: number, max: number) {
  const span = Math.abs(max - min);
  if (span < 0.001) return 6;
  if (span < 0.01) return 5;
  if (span < 0.1) return 4;
  if (span < 1) return 3;
  if (span < 10) return 2;
  if (span < 100) return 1;
  return 0;
}
function levelColor(index: number, count: number, selectedTone = "#1f5fc4") {
  if (count <= 1) return selectedTone;
  // 低水準→高水準を明→暗の同系色で塗り、傾きの変化を追いやすくする
  const ratio = count === 1 ? 1 : index / (count - 1);
  const lightness = 72 - ratio * 42;
  return `hsl(215, 72%, ${lightness}%)`;
}

export function CurveFamilyPanel({
  projectId,
  candidate,
  taskDefinition,
  targetValues,
  ready,
}: {
  projectId: string;
  candidate: Candidate;
  taskDefinition: TaskDefinitionContract;
  targetValues: Record<string, TargetGoal>;
  ready: boolean;
}) {
  const outputs = taskDefinition.outputs;
  const axisPath = taskDefinition.curve_axis_path ?? "";
  const axisInput = numericTaskInputs(taskDefinition).find((input) => input.path === axisPath);
  const varyOptions = numericTaskInputs(taskDefinition).filter((input) => input.editable && input.path !== axisPath);
  const varyCategoricalOptions = categoricalTaskInputs(taskDefinition).filter((input) => input.editable);
  const [varyId, setVaryId] = useState("");
  const isCategoricalVary = varyCategoricalOptions.some((input) => input.path === varyId);
  const [levels, setLevels] = useState(5);
  const [loadedPayloads, setLoadedPayloads] = useState<{ identity: string; values: Record<string, ApiCurveFamily> }>({ identity: "", values: {} });
  const [error, setError] = useState<Error | null>(null);
  const inputIdentity = candidateInputIdentity(candidate.raw.inputs);
  const outputKeys = outputs.map((output) => output.key).join("");
  const requestIdentity = JSON.stringify({ projectId, candidateId: candidate.id, revision: candidate.raw.revision, inputIdentity, varyId, levels, outputKeys, axisPath });
  const payloads = loadedPayloads.identity === requestIdentity ? loadedPayloads.values : {};
  useEffect(() => {
    if (!ready || !axisPath || !outputs.length) return;
    const controller = new AbortController();
    setError(null);
    const timer = window.setTimeout(async () => {
      try {
        const loaded = await Promise.all(outputs.map((output) =>
          workbenchApi.curveFamily(projectId, candidate.id, candidate.raw.revision, inputIdentity, output.key, varyId, varyId ? levels : 2, 15, controller.signal)));
        if (controller.signal.aborted) return;
        setLoadedPayloads({ identity: requestIdentity, values: Object.fromEntries(outputs.map((output, index) => [output.key, loaded[index]])) });
        setError(null);
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause : new Error(String(cause)));
      }
    }, 320);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [requestIdentity, ready, axisPath]);
  if (!axisPath) return null;
  const axisLabel = axisInput?.label ?? axisPath;
  const firstPayload = outputs.map((output) => payloads[output.key]).find(Boolean);
  const legendSeries = firstPayload?.series ?? [];
  return (
    <section className="response-curves-panel curve-family-panel" aria-label={`${axisLabel}に沿った特性曲線`}>
      <div className="panel-title">
        <div className="response-curves-title-group">
          <h2>特性曲線 <span>（横軸: {axisLabel}。選んだ変数を数水準ふって重ね描き）</span></h2>
          {varyId && legendSeries.length > 1 ? (
            <div className="candidate-color-legend" aria-label="水準の凡例">
              {legendSeries.map((series, index) => (
                <span key={series.label}><i style={{ background: levelColor(index, legendSeries.length) }} />{series.label}</span>
              ))}
            </div>
          ) : <span className="curve-scope">現在の候補の曲線</span>}
        </div>
        <label>ふる変数 <select aria-label="水準をふる変数" value={varyId} onChange={(event) => setVaryId(event.target.value)}>
          <option value="">なし（現在の候補のみ）</option>
          {varyOptions.length ? <optgroup label="数値">
            {varyOptions.map((input) => <option key={input.path} value={input.path}>{input.label}{input.unit ? ` (${input.unit})` : ""}</option>)}
          </optgroup> : null}
          {varyCategoricalOptions.length ? <optgroup label="区分">
            {varyCategoricalOptions.map((input) => <option key={input.path} value={input.path}>{input.label}</option>)}
          </optgroup> : null}
        </select></label>
        {varyId && !isCategoricalVary ? <label>水準数 <select aria-label="水準数" value={levels} onChange={(event) => setLevels(Number(event.target.value))}>
          {[3, 5, 7].map((count) => <option key={count} value={count}>{count}</option>)}
        </select></label> : null}
      </div>
      {!ready ? <p className="empty-evidence">入力を保存後に更新します。</p> : error && !firstPayload ? <p className="empty-evidence">曲線を取得できません。 ({error.message})</p> : !firstPayload ? <p className="empty-evidence">曲線を読み込んでいます。</p> : (
        <div className={`response-curves-grid output-count-${Math.min(outputs.length, 4)}`}>
          {outputs.map((output) => {
            const payload = payloads[output.key];
            if (!payload) return <article key={output.key} className="response-curve-card"><header><b>{output.label}</b><span>読み込み中</span></header></article>;
            return <CurveFamilyChart key={output.key} output={output} payload={payload} goalValue={targetValues[output.key]} showVaryLevels={Boolean(varyId)} />;
          })}
        </div>
      )}
    </section>
  );
}

function CurveFamilyChart({
  output,
  payload,
  goalValue,
  showVaryLevels,
}: {
  output: TaskOutputDefinition;
  payload: ApiCurveFamily;
  goalValue?: TargetGoal;
  showVaryLevels: boolean;
}) {
  const width = 300;
  const height = 156;
  const series = showVaryLevels ? payload.series : payload.series.slice(0, 1);
  const points = series.flatMap((item) => item.points);
  const minX = payload.axis.min;
  const maxX = payload.axis.max;
  const bandVisible = series.length === 1;
  const valueSamples = points.flatMap((point) => bandVisible ? [point.lower, point.upper] : [point.value]);
  const [showFullRange, setShowFullRange] = useState(false);
  const preferredRange = output.preferred_display_range;
  const goalValues = goalAxisValues(goalValue);
  const rawMin = showFullRange || !preferredRange ? Math.min(...valueSamples, ...goalValues, 0) : preferredRange.min;
  const rawMax = showFullRange || !preferredRange ? Math.max(...valueSamples, ...goalValues) : preferredRange.max;
  const padding = Math.max(1, (rawMax - rawMin) * 0.08);
  const minValue = showFullRange || !preferredRange ? rawMin : preferredRange.min;
  const maxValue = showFullRange || !preferredRange ? rawMax + padding : preferredRange.max;
  const visibleRange = { min: minValue, max: maxValue };
  const clippedAbove = valueSamples.filter((value) => value > maxValue).length;
  const clippedBelow = valueSamples.filter((value) => value < minValue).length;
  const x = (value: number) => 30 + ((value - minX) / Math.max(1e-6, maxX - minX)) * 252;
  const y = (value: number) => 124 - ((clampToRange(value, visibleRange) - minValue) / Math.max(1, maxValue - minValue)) * 92;
  const xTicks = [minX, (minX + maxX) / 2, maxX];
  const yTicks = [minValue, (minValue + maxValue) / 2, maxValue];
  const xDigits = chartDigits(minX, maxX);
  const yDigits = chartDigits(minValue, maxValue);
  const [hoveredPoint, setHoveredPoint] = useState<{ x: number; y: number; lines: string[] } | null>(null);
  return (
    <article className="response-curve-card">
      <header><b>{output.label}</b><span>{payload.axis.label}: {number(payload.axis.current)} {payload.axis.unit}</span>{preferredRange && <button type="button" className="text-button curve-display-range-toggle" onClick={() => setShowFullRange((value) => !value)}>{showFullRange ? "推奨範囲" : "全範囲"}</button>}</header>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${output.label}の${payload.axis.label}に沿った曲線`}>
        {yTicks.map((tick) => <g key={tick}><line x1="28" y1={y(tick)} x2="284" y2={y(tick)} stroke="#e3e9f0" /><text x="25" y={y(tick) + 3} textAnchor="end" fontSize="9" fill="#617087">{number(tick, yDigits)}</text></g>)}
        {xTicks.map((tick) => <line key={`grid-${tick}`} x1={x(tick)} y1="32" x2={x(tick)} y2="124" stroke="#edf1f6" />)}
        {series.map((item, index) => {
          const color = levelColor(index, series.length);
          const line = item.points.map((point, pointIndex) => `${pointIndex ? "L" : "M"}${x(point.x)} ${y(point.value)}`).join(" ");
          const band = `${item.points.map((point, pointIndex) => `${pointIndex ? "L" : "M"}${x(point.x)} ${y(point.upper)}`).join(" ")} ${[...item.points].reverse().map((point) => `L${x(point.x)} ${y(Math.max(point.lower, minValue))}`).join(" ")} Z`;
          return <g key={item.label}>{bandVisible && <path d={band} fill={color} opacity=".12" />}<path d={line} fill="none" stroke={color} strokeWidth={series.length === 1 ? "2.5" : "1.8"} />{item.points.map((point, pointIndex) => <circle
            className="svg-chart-hit-target" tabIndex={pointIndex === 0 ? 0 : -1} key={`${item.label}-${point.x}`} cx={x(point.x)} cy={y(point.value)} r="5" fill="transparent"
            aria-label={`${item.label}, ${payload.axis.label} ${number(point.x, xDigits)}, ${output.label} ${number(point.value, yDigits)} ${output.unit}`}
            onMouseEnter={() => setHoveredPoint({ x: x(point.x), y: y(point.value), lines: [item.label, `${payload.axis.label} ${number(point.x, xDigits)} ${payload.axis.unit}`, `${output.label} ${number(point.value, yDigits)} ${output.unit}`, ...(bandVisible ? [`90%区間 ${number(point.lower, yDigits)}–${number(point.upper, yDigits)}`] : [])] })}
            onMouseLeave={() => setHoveredPoint(null)}
            onFocus={() => setHoveredPoint({ x: x(point.x), y: y(point.value), lines: [item.label, `${payload.axis.label} ${number(point.x, xDigits)} ${payload.axis.unit}`, `${output.label} ${number(point.value, yDigits)} ${output.unit}`, ...(bandVisible ? [`90%区間 ${number(point.lower, yDigits)}–${number(point.upper, yDigits)}`] : [])] })}
            onBlur={() => setHoveredPoint(null)}
          />)}</g>;
        })}
        {isTargetRange(goalValue) ? <rect x="28" y={y(goalValue.upper)} width="256" height={Math.max(1, y(goalValue.lower) - y(goalValue.upper))} fill="#c17816" opacity=".1" /> : Number.isFinite(goalValue) && <line x1="28" y1={y(goalValue as number)} x2="284" y2={y(goalValue as number)} stroke="#c17816" strokeDasharray="4 3" />}
        {Number.isFinite(payload.axis.current) && <line x1={x(payload.axis.current)} y1="32" x2={x(payload.axis.current)} y2="124" stroke="#94a5ba" strokeDasharray="2 3" />}
        {clippedAbove > 0 && <text className="curve-clip-indicator" x="280" y="40" textAnchor="end">▲ {clippedAbove}</text>}
        {clippedBelow > 0 && <text className="curve-clip-indicator" x="280" y="121" textAnchor="end">▼ {clippedBelow}</text>}
        {xTicks.map((tick) => <text key={tick} x={x(tick)} y="137" textAnchor="middle" fontSize="8" fill="#617087">{number(tick, xDigits)}</text>)}
        {hoveredPoint && <SvgChartTooltip {...hoveredPoint} chartWidth={width} chartHeight={height} />}
        <text x="158" y="150" textAnchor="middle" fontSize="9" fill="#617087">{payload.axis.label} ({payload.axis.unit})</text>
      </svg>
    </article>
  );
}
export function LiveResponseCurves({
  projectId,
  project,
  candidates,
  candidate,
  preview,
  previewsByCandidate,
  targetValues,
  taskDefinition,
  responseCurveRanges,
  onProjectChanged,
  available,
  ready,
}: {
  projectId: string;
  project: ApiProject | null;
  candidates: Candidate[];
  candidate: Candidate;
  preview: ApiPreview | null;
  previewsByCandidate: Record<string, ApiPreview>;
  targetValues: Record<string, TargetGoal>;
  taskDefinition: TaskDefinitionContract | null;
  responseCurveRanges: ResponseCurveRanges;
  onProjectChanged: (project: ApiProject) => void | Promise<void>;
  available: boolean;
  ready: boolean;
}) {
  const outputs = taskDefinition?.outputs ?? [];
  const curveCandidates = candidates.filter((item) => !item.raw.archived_at && previewsByCandidate[item.id]);
  const variables = responseCurveVariables(
    taskDefinition,
    candidate.raw.inputs,
    curveCandidates.map((item) => item.raw.inputs),
    project?.heat_stage_positions_m ?? {},
  );
  const [variableId, setVariableId] = useState(variables[0]?.id ?? "heat.peak_temperature_c");
  const [axisSettingsOpen, setAxisSettingsOpen] = useState(false);
  const [outputRangeMode, setOutputRangeMode] = useState<"preferred" | "full" | "configured">(
    Object.keys(responseCurveRanges.y ?? {}).length ? "configured" : "preferred",
  );
  const [axisDraft, setAxisDraft] = useState<{ x: CurveRangeDraft; y: Record<string, CurveRangeDraft>; stagePosition: string }>({ x: { min: "", max: "", enabled: false }, y: {}, stagePosition: "" });
  const [axisDraftDirty, setAxisDraftDirty] = useState(false);
  const [axisError, setAxisError] = useState("");
  const [axisSaving, setAxisSaving] = useState(false);
  const [surfacesByKey, setSurfacesByKey] = useState<Record<string, InferenceSurfaceState<ApiResponseCurve>>>({});
  const axisSettingsButtonRef = useRef<HTMLButtonElement>(null);
  const axisDraftRef = useRef(axisDraft);
  axisDraftRef.current = axisDraft;
  const surfaceRef = useRef(surfacesByKey);
  surfaceRef.current = surfacesByKey;
  const curveCandidatesKey = curveCandidates.map((item) => `${item.id}:${item.raw.revision}:${candidateInputIdentity(item.raw.inputs)}`).join("\u001e");
  const variableIdsIdentity = variables.map((variable) => variable.id).join("\u001e");
  const outputKeys = outputs.map((output) => output.key).join("\u001e");
  const selectedVariable = variables.find((variable) => variable.id === variableId) ?? variables[0];
  const activeVariableId = selectedVariable?.id ?? variableId;
  const xRangeOverride = responseCurveRanges.x?.[activeVariableId];
  const stageRequestIdentity = selectedVariable?.stageName ? `${selectedVariable.stageName}:${selectedVariable.stagePositionM}` : "scalar";
  const xRangeIdentity = `${xRangeOverride ? `${xRangeOverride.min}:${xRangeOverride.max}` : "auto"}:${stageRequestIdentity}`;
  useEffect(() => {
    if (variables.length && !variables.some((variable) => variable.id === variableId)) setVariableId(variables[0].id);
  }, [variableId, variableIdsIdentity]);
  useEffect(() => {
    if (!available || !ready || !taskDefinition || !curveCandidates.length || !outputs.length) return;
    const controller = new AbortController();
    const timers: number[] = [];
    for (const item of curveCandidates) {
      const inputIdentity = candidateInputIdentity(item.raw.inputs);
      for (const output of outputs) {
        const storageKey = `${item.id}\u001f${output.key}\u001f${activeVariableId}\u001f${xRangeIdentity}\u001f${inputIdentity}`;
        const identity = `${workbenchRequestKey({ projectId, taskId: taskDefinition.id, candidateId: item.id, candidateRevision: item.raw.revision }, "response_curve:9")}\u001f${inputIdentity}\u001f${output.key}\u001f${activeVariableId}\u001f${xRangeIdentity}`;
        const existing = surfaceRef.current[storageKey];
        if (existing?.currentIdentity === identity) continue;
        const requested = requestInferenceSurface(existing ?? emptyInferenceSurface<ApiResponseCurve>(), identity);
        const requestedSurfaces = { ...surfaceRef.current, [storageKey]: requested };
        surfaceRef.current = requestedSurfaces;
        setSurfacesByKey(requestedSurfaces);
        const timer = window.setTimeout(async () => {
          try {
            const loaded = await workbenchApi.responseCurve(projectId, item.id, item.raw.revision, inputIdentity, output.key, selectedVariable.requestVariable, 9, xRangeOverride?.min, xRangeOverride?.max, selectedVariable.stageName, selectedVariable.stagePositionM, controller.signal);
            if (controller.signal.aborted) return;
            const current = surfaceRef.current[storageKey] ?? requested;
            const resolved = resolveInferenceSurface(current, requested.requestSequence, identity, loaded);
            const next = { ...surfaceRef.current, [storageKey]: resolved };
            surfaceRef.current = next;
            setSurfacesByKey(next);
          } catch (cause) {
            if (controller.signal.aborted) return;
            const current = surfaceRef.current[storageKey] ?? requested;
            const rejected = rejectInferenceSurface(current, requested.requestSequence, identity, cause);
            const next = { ...surfaceRef.current, [storageKey]: rejected };
            surfaceRef.current = next;
            setSurfacesByKey(next);
          }
        }, 320);
        timers.push(timer);
      }
    }
    return () => { timers.forEach((timer) => window.clearTimeout(timer)); controller.abort(); };
  }, [available, ready, curveCandidatesKey, outputKeys, projectId, taskDefinition?.id, activeVariableId, xRangeIdentity, xRangeOverride?.min, xRangeOverride?.max, selectedVariable?.requestVariable, selectedVariable?.stageName, selectedVariable?.stagePositionM]);
  const curveStates = curveCandidates.flatMap((item) => outputs.map((output) => {
    const inputIdentity = candidateInputIdentity(item.raw.inputs);
    const storageKey = `${item.id}\u001f${output.key}\u001f${activeVariableId}\u001f${xRangeIdentity}\u001f${inputIdentity}`;
    return surfacesByKey[storageKey];
  }));
  const payloadsForOutput = (outputKey: string) => curveCandidates.map((item) => {
    const inputIdentity = candidateInputIdentity(item.raw.inputs);
    return surfacesByKey[`${item.id}\u001f${outputKey}\u001f${activeVariableId}\u001f${xRangeIdentity}\u001f${inputIdentity}`]?.data;
  }).filter((payload): payload is ApiResponseCurve => Boolean(payload));
  const payloadForOutput = (outputKey: string) => payloadsForOutput(outputKey)[0];
  const selectedPayload = outputs.map((output) => payloadForOutput(output.key)).find((payload): payload is ApiResponseCurve => Boolean(payload));
  const loadedVariables = outputs.flatMap((output) => payloadsForOutput(output.key).map((payload) => payload.variable));
  const automaticXValues = loadedVariables.flatMap((variable) => [variable.min, variable.max, variable.current]);
  const effectiveXRange = automaticXValues.length
    ? { min: Math.min(...automaticXValues), max: Math.max(...automaticXValues) }
    : selectedVariable ? { min: selectedVariable.min, max: selectedVariable.max } : null;
  const makeDraft = (saved: CurveRange | undefined, effective: CurveRange | null | undefined): CurveRangeDraft => ({
    min: String(saved?.min ?? effective?.min ?? ""),
    max: String(saved?.max ?? effective?.max ?? ""),
    enabled: Boolean(saved),
  });
  const openAxisSettings = () => {
    setAxisDraft({
      x: makeDraft(xRangeOverride, effectiveXRange),
      y: Object.fromEntries(outputs.map((output) => [output.key, makeDraft(responseCurveRanges.y?.[output.key], payloadForOutput(output.key)?.output_range)])),
      stagePosition: selectedVariable?.stagePositionM == null ? "" : String(selectedVariable.stagePositionM),
    });
    setAxisDraftDirty(false);
    setAxisError("");
    setAxisSettingsOpen(true);
  };
  const draftRange = (draft: CurveRangeDraft, label: string): CurveRange | null => {
    if (!draft.enabled) return null;
    const min = Number(draft.min);
    const max = Number(draft.max);
    if (!Number.isFinite(min) || !Number.isFinite(max) || min >= max) throw new Error(`${label}は有限の数値で、最小値 < 最大値にしてください`);
    return { min, max };
  };
  const saveAxisSettings = async (draft = axisDraft) => {
    if (!project) return;
    const draftIdentity = JSON.stringify({ variableId: activeVariableId, draft });
    try {
      const nextX = { ...(responseCurveRanges.x ?? {}) };
      const parsedX = draftRange(draft.x, "X軸");
      if (parsedX) nextX[activeVariableId] = parsedX;
      else delete nextX[activeVariableId];
      const nextY = { ...(responseCurveRanges.y ?? {}) };
      for (const output of outputs) {
        const parsedY = draftRange(draft.y[output.key] ?? { min: "", max: "", enabled: false }, `${output.label}のY軸`);
        if (parsedY) nextY[output.key] = parsedY;
        else delete nextY[output.key];
      }
      const nextStagePositions = { ...(project.heat_stage_positions_m ?? {}) };
      if (selectedVariable?.stageName) {
        const position = Number(draft.stagePosition);
        if (!Number.isFinite(position) || position < 0) throw new Error("工程位置は0以上の有限値にしてください");
        nextStagePositions[selectedVariable.stageName] = position;
      }
      setAxisSaving(true);
      const updated = await workbenchApi.updateProject(projectId, { ...project, response_curve_ranges: { x: nextX, y: nextY }, heat_stage_positions_m: nextStagePositions });
      await onProjectChanged(updated);
      if (JSON.stringify({ variableId: activeVariableId, draft: axisDraftRef.current }) === draftIdentity) setAxisDraftDirty(false);
      setAxisError("");
    } catch (cause) {
      setAxisError(cause instanceof Error ? cause.message : "軸範囲を保存できませんでした。");
    } finally {
      setAxisSaving(false);
    }
  };
  useEffect(() => {
    if (!axisDraftDirty) return;
    const timer = window.setTimeout(() => { void saveAxisSettings(axisDraft); }, 420);
    return () => window.clearTimeout(timer);
  }, [axisDraft, axisDraftDirty, activeVariableId]);
  const rangeText = (range: CurveRange | null | undefined) => range ? `${number(range.min, 2)} – ${number(range.max, 2)}` : "取得中";
  const setXDraft = (patch: Partial<CurveRangeDraft>) => { setAxisDraft((current) => ({ ...current, x: { ...current.x, ...patch } })); setAxisDraftDirty(true); };
  const setYDraft = (key: string, patch: Partial<CurveRangeDraft>) => { setAxisDraft((current) => ({ ...current, y: { ...current.y, [key]: { ...(current.y[key] ?? { min: "", max: "", enabled: false }), ...patch } } })); setAxisDraftDirty(true); };
  const loadedCurveCount = curveStates.filter((state) => state?.data !== null && state?.data !== undefined).length;
  const curveStatus = curveStates.some((state) => state?.error) ? "error" : curveStates.some((state) => state?.pending) || loadedCurveCount < curveStates.length ? "refreshing" : "latest";
  const curveErrorMessage = curveStates.find((state) => state?.error)?.error;
  if (!available) return <UnavailablePanel title="応答曲線" />;
  if (!preview && !curveCandidates.length) return <section className="response-curves-panel"><div className="panel-title"><h2>応答曲線</h2></div><p className="empty-evidence">候補の保存とプレビュー完了後に表示します。</p></section>;
  return (
    <section className="response-curves-panel" aria-label="設計変数ごとの応答曲線" data-candidate-id={candidate.id} data-candidate-count={curveCandidates.length}>
      <div className="panel-title">
        <div className="response-curves-title-group">
          <h2>応答曲線 <span>（選択した変数を動かしたときの特性）</span></h2>
          <span className="curve-scope">{curveCandidates.length}候補 × {outputs.length}特性</span>
          <span className={`inference-surface-status ${curveStatus}`}>{curveStatus === "latest" ? "最新" : curveStatus === "refreshing" ? `${loadedCurveCount}/${curveStates.length}件を更新中` : "一部取得失敗"}</span>
          <div className="candidate-color-legend" aria-label="応答曲線の候補色">
            {curveCandidates.map((item) => <span className={item.id === candidate.id ? "selected" : ""} key={item.id}><i style={{ background: candidateColor(item.id, candidate.id) }} />{item.label}</span>)}
          </div>
        </div>
        <div className="response-curve-controls">
          <label>変数 <select aria-label="応答曲線の設計変数" value={activeVariableId} disabled={axisSaving || axisDraftDirty} onChange={(event) => { setAxisSettingsOpen(false); setAxisDraftDirty(false); setVariableId(event.target.value); }}>{[...new Set(variables.map((variable) => variable.group))].map((group) => <optgroup key={group} label={group}>{variables.filter((variable) => variable.group === group).map((variable) => <option key={variable.id} value={variable.id}>{variable.label} ({variable.unit})</option>)}</optgroup>)}</select></label>
          <label>Y軸 <select aria-label="Y軸の表示範囲" value={outputRangeMode} onChange={(event) => setOutputRangeMode(event.target.value as "preferred" | "full" | "configured")}><option value="preferred">推奨範囲</option><option value="full">全範囲</option>{Object.keys(responseCurveRanges.y ?? {}).length > 0 && <option value="configured">保存設定</option>}</select></label>
          <button ref={axisSettingsButtonRef} type="button" className={`outline-button curve-range-button${axisSettingsOpen ? " active" : ""}`} aria-label={axisSettingsOpen ? "軸範囲設定を閉じる" : "軸範囲を設定"} title={axisSettingsOpen ? "軸範囲設定を閉じる" : "軸範囲を設定"} aria-expanded={axisSettingsOpen} aria-controls="response-curve-axis-settings" onClick={axisSettingsOpen ? () => setAxisSettingsOpen(false) : openAxisSettings}>
            <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 8.3a3.7 3.7 0 1 0 0 7.4 3.7 3.7 0 0 0 0-7.4Zm8.1 4.9v-2.4l-2.3-.7a7.4 7.4 0 0 0-.7-1.6l1.1-2.1-1.7-1.7-2.1 1.1a7.4 7.4 0 0 0-1.6-.7L12.1 3H9.7L9 5.3a7.4 7.4 0 0 0-1.6.7L5.3 4.9 3.6 6.6l1.1 2.1a7.4 7.4 0 0 0-.7 1.6l-2.3.7v2.4l2.3.7a7.4 7.4 0 0 0 .7 1.6l-1.1 2.1 1.7 1.7 2.1-1.1a7.4 7.4 0 0 0 1.6.7l.7 2.3h2.4l.7-2.3a7.4 7.4 0 0 0 1.6-.7l2.1 1.1 1.7-1.7-1.1-2.1a7.4 7.4 0 0 0 .7-1.6l2.3-.7Z" /></svg>
          </button>
        </div>
      </div>
      {axisSettingsOpen && (
        <div id="response-curve-axis-settings" className="response-curve-axis-settings" role="dialog" aria-label="描画範囲">
          <div className="axis-settings-heading"><b>描画範囲</b><small>変更は自動保存。未指定は自動範囲、学習データ範囲は参照値です。</small><button type="button" className="axis-settings-close" aria-label="軸範囲設定を閉じる" title="閉じる" onClick={() => { setAxisSettingsOpen(false); window.requestAnimationFrame(() => axisSettingsButtonRef.current?.focus()); }}>×</button></div>
          <div className="axis-settings-grid">
            <section>
              <h3>X軸 <span>{selectedVariable?.label ?? "選択変数"}</span></h3>
              {selectedVariable?.stageName && <label className="stage-position-field">未登録候補への仮挿入位置<input type="number" min="0" step="0.1" disabled={axisSaving} value={axisDraft.stagePosition} onChange={(event) => { setAxisDraft((current) => ({ ...current, stagePosition: event.target.value })); setAxisDraftDirty(true); }} /><span>m</span></label>}
              <div className="axis-range-fields">
                <label>最小<input type="number" disabled={axisSaving} value={axisDraft.x.min} onChange={(event) => setXDraft({ min: event.target.value, enabled: true })} /></label>
                <label>最大<input type="number" disabled={axisSaving} value={axisDraft.x.max} onChange={(event) => setXDraft({ max: event.target.value, enabled: true })} /></label>
              </div>
              <small>学習データ範囲: {rangeText(selectedPayload?.variable?.training_range)}</small>
              <button type="button" className="text-button" onClick={() => setXDraft({ ...makeDraft(undefined, effectiveXRange) })}>自動</button>
            </section>
            <section>
              <h3>Y軸 <span>目的変数ごと</span></h3>
              <div className="axis-settings-output-list">
                {outputs.map((output) => {
                  const draft = axisDraft.y[output.key] ?? makeDraft(undefined, payloadForOutput(output.key)?.output_range);
                  return <div className="axis-settings-output" key={output.key}>
                    <b>{output.label}</b>
                    <div className="axis-range-fields">
                      <label>最小<input type="number" disabled={axisSaving} value={draft.min} onChange={(event) => setYDraft(output.key, { min: event.target.value, enabled: true })} /></label>
                      <label>最大<input type="number" disabled={axisSaving} value={draft.max} onChange={(event) => setYDraft(output.key, { max: event.target.value, enabled: true })} /></label>
                    </div>
                    <small>学習データ範囲: {rangeText(payloadForOutput(output.key)?.output_range)}</small>
                    <button type="button" className="text-button" onClick={() => setYDraft(output.key, { ...makeDraft(undefined, payloadForOutput(output.key)?.output_range) })}>自動</button>
                  </div>;
                })}
              </div>
            </section>
          </div>
          {axisError && <p className="axis-settings-error" role="alert">{axisError}</p>}
          <div className="axis-settings-actions">
            <button type="button" className="text-button" disabled={axisSaving} onClick={() => { setXDraft({ ...makeDraft(undefined, effectiveXRange) }); outputs.forEach((output) => setYDraft(output.key, { ...makeDraft(undefined, payloadForOutput(output.key)?.output_range) })); }}>すべて自動に戻す</button>
            <small className="axis-settings-autosave" role="status">{axisSaving ? "自動保存中…" : axisDraftDirty ? "変更を確認中…" : "自動保存済み"}</small>
          </div>
        </div>
      )}
      {!ready ? <p className="empty-evidence">入力を保存後に更新します。</p> : curveStatus === "error" && loadedCurveCount === 0 ? <p className="empty-evidence">応答曲線を取得できません。{curveErrorMessage instanceof Error ? ` (${curveErrorMessage.message})` : ""}</p> : (
        <div className={`response-curves-grid output-count-${Math.min(outputs.length, 4)}`}>
          {outputs.map((output) => {
            const curveSeries = curveCandidates.flatMap((item) => {
              const inputIdentity = candidateInputIdentity(item.raw.inputs);
              const storageKey = `${item.id}\u001f${output.key}\u001f${activeVariableId}\u001f${xRangeIdentity}\u001f${inputIdentity}`;
              const payload = surfacesByKey[storageKey]?.data;
              if (!payload?.points.length) return [];
              return [{ candidate: item, points: payload.points, prediction: previewsByCandidate[item.id]?.predictions?.[output.key], currentX: payload.variable.current }];
            });
            const payloads = payloadsForOutput(output.key);
            const firstPayload = payloads[0];
            const autoValues = payloads.flatMap((payload) => [payload.variable.min, payload.variable.max, payload.variable.current]);
            const chartXRange = xRangeOverride ?? (autoValues.length ? { min: Math.min(...autoValues), max: Math.max(...autoValues) } : undefined);
            const yRange = outputRangeMode === "full"
              ? undefined
              : outputRangeMode === "configured"
                ? responseCurveRanges.y?.[output.key] ?? output.preferred_display_range ?? firstPayload?.output_range ?? undefined
                : output.preferred_display_range ?? firstPayload?.output_range ?? undefined;
            return <ResponseCurveMiniChart key={output.key} output={output} series={curveSeries} selectedId={candidate.id} prediction={previewsByCandidate[candidate.id]?.predictions?.[output.key] ?? preview?.predictions?.[output.key]} goalValue={targetValues[output.key]} xRange={chartXRange} yRange={yRange} xLabel={firstPayload?.variable.label ?? selectedVariable?.label ?? "設計変数"} xUnit={firstPayload?.variable.unit ?? selectedVariable?.unit ?? ""} />;
          })}
        </div>
      )}
    </section>
  );
}

function ResponseCurveMiniChart({
  output,
  series,
  selectedId,
  prediction,
  goalValue,
  xRange,
  yRange,
  xLabel,
  xUnit,
}: {
  output: TaskOutputDefinition;
  series: Array<{ candidate: Candidate; points: CurvePoint[]; prediction?: NonNullable<ApiPreview["predictions"]>[string]; currentX: number }>;
  selectedId: string;
  prediction?: NonNullable<ApiPreview["predictions"]>[string];
  goalValue?: TargetGoal;
  xRange?: { min: number; max: number };
  yRange?: { min: number; max: number };
  xLabel: string;
  xUnit: string;
}) {
  const width = 300;
  const height = 156;
  const points = series.flatMap((item) => item.points);
  const minX = xRange?.min ?? Math.min(...points.map((point) => point.x), 0);
  const maxX = xRange?.max ?? Math.max(...points.map((point) => point.x), 1);
  const outputAxisValues = yRange
    ? [yRange.min, yRange.max]
    : [
        ...points.flatMap((point) => [point.value, point.lower, point.upper, ...Object.values(point.quantiles ?? {})]),
        ...series.flatMap((item) => item.prediction ? [item.prediction.value, item.prediction.lower, item.prediction.upper, ...Object.values(item.prediction.quantiles ?? {})] : []),
      ];
  const goalValues = goalAxisValues(goalValue);
  const rawMin = Math.min(...outputAxisValues, ...goalValues);
  const rawMax = Math.max(...outputAxisValues, ...goalValues);
  const valuePadding = Math.max(1, (rawMax - rawMin) * 0.08);
  const minValue = yRange?.min ?? rawMin - valuePadding;
  const maxValue = yRange?.max ?? rawMax + valuePadding;
  const visibleRange = { min: minValue, max: maxValue };
  const clippedPoints = points.filter((point) => [point.value, point.lower, point.upper, ...Object.values(point.quantiles ?? {})].some((value) => isOutsideRange(value, visibleRange)));
  const clippedAbove = clippedPoints.filter((point) => [point.value, point.lower, point.upper, ...Object.values(point.quantiles ?? {})].some((value) => value > maxValue)).length;
  const clippedBelow = clippedPoints.filter((point) => [point.value, point.lower, point.upper, ...Object.values(point.quantiles ?? {})].some((value) => value < minValue)).length;
  const x = (value: number) => 30 + ((value - minX) / Math.max(1e-6, maxX - minX)) * 252;
  const y = (value: number) => 124 - ((clampToRange(value, visibleRange) - minValue) / Math.max(1, maxValue - minValue)) * 92;
  const xTicks = [minX, (minX + maxX) / 2, maxX];
  const declaredQuantiles = [...new Set(points.flatMap((point) => Object.keys(point.quantiles ?? {})))].sort((left, right) => Number(left) - Number(right));
  const quantileLabel = declaredQuantiles.length ? `分位線 ${declaredQuantiles.map((level) => `q${Math.round(Number(level) * 100)}`).join("・")}` : "予測線";
  const yTicks = [minValue, (minValue + maxValue) / 2, maxValue];
  const xDigits = chartDigits(minX, maxX);
  const yDigits = output.key === "EL" || output.key === "lambda" ? 1 : chartDigits(minValue, maxValue);
  const [hoveredPoint, setHoveredPoint] = useState<{ x: number; y: number; lines: string[] } | null>(null);
  return (
    <article className="response-curve-card">
      <header><b>{output.label}</b><span>{prediction ? `${number(prediction.value, output.key === "EL" || output.key === "lambda" ? 1 : 0)} ${prediction.unit} / ${quantileLabel}` : "読み込み中"}</span>{clippedPoints.length > 0 && <span className="curve-clipped-summary" title="表示範囲外の実値は各点の詳細で確認できます">表示外 {clippedPoints.length}点</span>}</header>
      {series.length ? <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${output.label}の応答曲線、${quantileLabel}`}>
        {yTicks.map((tick) => <g key={tick}><line x1="28" y1={y(tick)} x2="284" y2={y(tick)} stroke="#e3e9f0" /><text x="25" y={y(tick) + 3} textAnchor="end" fontSize="9" fill="#617087">{number(tick, yDigits)}</text></g>)}
        {xTicks.map((tick) => <line key={`grid-${tick}`} x1={x(tick)} y1="32" x2={x(tick)} y2="124" stroke="#edf1f6" />)}
        {series.map((item) => {
          const color = candidateColor(item.candidate.id, selectedId);
          const line = item.points.map((point, index) => `${index ? "L" : "M"}${x(point.x)} ${y(point.value)}`).join(" ");
          const band = `${item.points.map((point, index) => `${index ? "L" : "M"}${x(point.x)} ${y(point.upper)}`).join(" ")} ${[...item.points].reverse().map((point) => `L${x(point.x)} ${y(point.lower)}`).join(" ")} Z`;
          const quantileLines = declaredQuantiles.map((level) => item.points.every((point) => point.quantiles?.[level] != null)
            ? <path key={level} data-quantile={level} d={item.points.map((point, index) => `${index ? "L" : "M"}${x(point.x)} ${y(point.quantiles[level])}`).join(" ")} fill="none" stroke={color} strokeWidth=".75" strokeDasharray={Number(level) === 0.5 ? "none" : "3 2"} opacity=".55" />
            : null);
          return <g key={item.candidate.id}><path d={band} fill={color} opacity={item.candidate.id === selectedId ? ".18" : ".08"} />{quantileLines}<path d={line} fill="none" stroke={color} strokeWidth={item.candidate.id === selectedId ? "2.5" : "1.5"} opacity={item.candidate.id === selectedId ? "1" : ".78"} />{item.points.map((point) => <circle
            className="svg-chart-hit-target" tabIndex={-1} key={`${item.candidate.id}-${point.x}`} cx={x(point.x)} cy={y(point.value)} r="5" fill="transparent"
            aria-label={`${item.candidate.label}, ${xLabel} ${number(point.x, xDigits)}, ${output.label} ${number(point.value, yDigits)} ${output.unit}`}
            onMouseEnter={() => setHoveredPoint({ x: x(point.x), y: y(point.value), lines: [item.candidate.label, `${xLabel} ${number(point.x, xDigits)} ${xUnit}`, `${output.label} ${number(point.value, yDigits)} ${output.unit}`, `予測区間 ${number(point.lower, yDigits)}–${number(point.upper, yDigits)}`] })}
            onMouseLeave={() => setHoveredPoint(null)}
          />)}{item.prediction && Number.isFinite(item.currentX) && <circle
            className="svg-chart-hit-target" tabIndex={0} cx={x(item.currentX)} cy={y(item.prediction.value)} r={item.candidate.id === selectedId ? "4" : "2.5"} fill="#fff" stroke={color} strokeWidth={item.candidate.id === selectedId ? "2.5" : "1.5"}
            aria-label={`${item.candidate.label}の現在値、${xLabel} ${number(item.currentX, xDigits)}、${output.label} ${number(item.prediction.value, yDigits)} ${output.unit}`}
            onMouseEnter={() => setHoveredPoint({ x: x(item.currentX), y: y(item.prediction!.value), lines: [item.candidate.label, `現在の${xLabel} ${number(item.currentX, xDigits)} ${xUnit}`, `${output.label} ${number(item.prediction!.value, yDigits)} ${output.unit}`, `予測区間 ${number(item.prediction!.lower, yDigits)}–${number(item.prediction!.upper, yDigits)}`] })}
            onMouseLeave={() => setHoveredPoint(null)}
            onFocus={() => setHoveredPoint({ x: x(item.currentX), y: y(item.prediction!.value), lines: [item.candidate.label, `現在の${xLabel} ${number(item.currentX, xDigits)} ${xUnit}`, `${output.label} ${number(item.prediction!.value, yDigits)} ${output.unit}`, `予測区間 ${number(item.prediction!.lower, yDigits)}–${number(item.prediction!.upper, yDigits)}`] })}
            onBlur={() => setHoveredPoint(null)}
          />}</g>;
        })}
        {isTargetRange(goalValue) ? <rect x="28" y={y(goalValue.upper)} width="256" height={Math.max(1, y(goalValue.lower) - y(goalValue.upper))} fill="#c17816" opacity=".1" /> : Number.isFinite(goalValue) && <line x1="28" y1={y(goalValue as number)} x2="284" y2={y(goalValue as number)} stroke="#c17816" strokeDasharray="4 3" />}
        {clippedAbove > 0 && <text className="curve-clip-indicator" x="280" y="40" textAnchor="end">▲ {clippedAbove}</text>}
        {clippedBelow > 0 && <text className="curve-clip-indicator" x="280" y="121" textAnchor="end">▼ {clippedBelow}</text>}
        {xTicks.map((tick) => <text key={tick} x={x(tick)} y="137" textAnchor="middle" fontSize="8" fill="#617087">{number(tick, xDigits)}</text>)}
        {hoveredPoint && <SvgChartTooltip {...hoveredPoint} chartWidth={width} chartHeight={height} />}
        <text x="156" y="153" textAnchor="middle" fontSize="8" fill="#617087">{xLabel} ({xUnit})</text>
      </svg> : <p className="empty-evidence">読み込み中…</p>}
    </article>
  );
}
