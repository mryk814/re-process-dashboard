import { type CSSProperties, type KeyboardEvent, type PointerEvent, useEffect, useRef, useState } from "react";
import { provenanceLabel, type CandidateProvenance } from "../../shared/candidateProvenance";
import { CandidateAddButton } from "../../shared/ui/CandidateAddButton";
import { SvgChartTooltip } from "../../shared/ui/SvgChartTooltip";
import {
  CandidateInspector,
  ComparisonTable,
  categoricalTaskInputs,
  fromApiCandidate,
  numericTaskInputs,
  responseCurveVariables,
  type CandidateSaveState,
  type CandidateViewModel as Candidate,
  type NumericRange,
  type NumericTaskInput,
  type RuntimeOperations,
  type TaskDefinitionContract,
  type TaskOutputDefinition,
} from "../candidates";
import { candidateInputIdentity } from "../../shared/api/inferenceRequestCache";
import { assessOutputValues, clampToRange, isOutsideRange } from "../../shared/outputPresentation";
import { apiBaseUrl } from "../../shared/api/client";
import {
  workbenchApi,
  type ApiCurveFamily,
  type ApiProject,
  type ApiPreview,
  type ApiResponseCurve,
  type ApiSimilarObservation,
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

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

const workbenchLayoutStorage = {
  inspectorWidth: "material-workbench:layout:inspector-width:v1",
  curveShare: "material-workbench:layout:curve-share:v1",
} as const;

function storedLayoutNumber(key: string, fallback: number) {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    if (raw === null) return fallback;
    const value = Number(raw);
    return Number.isFinite(value) ? value : fallback;
  } catch {
    return fallback;
  }
}

function saveLayoutNumber(key: string, value: number) {
  try {
    window.localStorage.setItem(key, String(value));
  } catch {
    // Layout persistence is optional when local storage is unavailable.
  }
}

function SplitResizer({
  className,
  label,
  value,
  min,
  max,
  step,
  onChange,
  onDrag,
  onReset,
}: {
  className: string;
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
  onDrag: (startValue: number, deltaX: number) => number;
  onReset: () => void;
}) {
  const drag = useRef<{ pointerId: number; startX: number; startValue: number } | null>(null);
  const changeByKeyboard = (event: KeyboardEvent<HTMLDivElement>) => {
    const amount = event.shiftKey ? step * 4 : step;
    const next = event.key === "ArrowLeft"
      ? value - amount
      : event.key === "ArrowRight"
        ? value + amount
        : event.key === "Home"
          ? min
          : event.key === "End"
            ? max
            : null;
    if (next === null) return;
    event.preventDefault();
    onChange(clamp(next, min, max));
  };
  return (
    <div
      className={`split-resizer ${className}`}
      role="separator"
      tabIndex={0}
      aria-label={label}
      aria-orientation="vertical"
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={Math.round(value)}
      title="ドラッグで幅を調整・ダブルクリックで初期幅"
      onDoubleClick={onReset}
      onKeyDown={changeByKeyboard}
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
    ><span aria-hidden="true" /></div>
  );
}

type CurvePoint = ApiResponseCurve["points"][number];
type CurveRange = { min: number; max: number };
type ResponseCurveRanges = { x?: Record<string, CurveRange>; y?: Record<string, CurveRange> };
type CurveRangeDraft = { min: string; max: string; enabled: boolean };

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

export function WorkbenchEmptyState({
  loading,
  error,
  onCreate,
}: {
  loading: boolean;
  error: string | null;
  onCreate: () => void;
}) {
  return (
    <div className="api-empty-state" role={error ? "alert" : "status"}>
      <h2>{loading ? "候補を読み込んでいます" : "候補を表示できません"}</h2>
      <p>{error ?? "データと予測モデルを準備しています。"}</p>
      {error && (
        <p className="api-hint">
          FastAPI を <code>{apiBaseUrl}</code> で起動後、再読み込みしてください。
        </p>
      )}
      {!loading && !error && (
        <CandidateAddButton onClick={onCreate}>
          最初の候補を作る
        </CandidateAddButton>
      )}
    </div>
  );
}

type WorkbenchProps = {
  candidates: Candidate[];
  projectId: string;
  project: ApiProject | null;
  targetValues: Record<string, number>;
  inputRanges: Record<string, NumericRange>;
  responseCurveRanges: ResponseCurveRanges;
  decisionCandidateId: string;
  selected: Candidate;
  selectedId: string;
  taskDefinition: TaskDefinitionContract | null;
  operations?: RuntimeOperations;
  saveState: CandidateSaveState;
  saveStates: Record<string, CandidateSaveState>;
  fieldErrors: Array<{ path: string; message: string }>;
  onReload: () => void;
  onCopyDraft: () => void;
  preview: ApiPreview | null;
  previewError: string;
  onRetryPreview: () => void;
  previewsByCandidate: Record<string, ApiPreview>;
  onSelect: (id: string) => void;
  onHeat: (index: number, field: "time" | "temperature" | "stageName", raw: number | string) => void;
  onInput: (id: string, path: string, value: number | string) => void;
  onText: (id: string, field: "label", value: string) => void;
  onAddHeat: () => void;
  onDeleteHeat: (index: number) => void;
  onCopy: (candidateId: string) => void;
  onOpenOrigin: () => void;
  originBroken: boolean;
  onDelete: (candidateId: string) => void;
  onSave: (candidate: Candidate) => void;
  savedRevisionsByCandidate: Record<string, number[]>;
  savingCandidateIds: string[];
  snapshotHistoryState: "loading" | "ready" | "error";
  onAdd: () => void;
  onAddCandidateFromLineage: (entityKey: string) => Promise<boolean>;
  onImported: (items: Candidate[]) => void;
  onProjectChanged: (project: ApiProject) => void | Promise<void>;
};

export function WorkbenchPage(props: WorkbenchProps) {
  const {
    candidates,
    projectId,
    project,
    targetValues,
    inputRanges,
    responseCurveRanges,
    decisionCandidateId,
    selected,
    selectedId,
    taskDefinition,
    operations,
    saveState,
    saveStates,
    fieldErrors,
    onReload,
    onCopyDraft,
    preview,
    previewError,
    onRetryPreview,
    previewsByCandidate,
    onSelect,
    onInput,
    onText,
    onHeat,
    onAddHeat,
    onDeleteHeat,
    onCopy,
    onOpenOrigin,
    originBroken,
    onDelete,
    onSave,
    savedRevisionsByCandidate,
    savingCandidateIds,
    snapshotHistoryState,
    onAdd,
    onAddCandidateFromLineage,
    onImported,
    onProjectChanged,
  } = props;
  const [comparisonExpanded, setComparisonExpanded] = useState(false);
  const [inspectorWidth, setInspectorWidth] = useState(() => clamp(storedLayoutNumber(workbenchLayoutStorage.inspectorWidth, 330), 260, 520));
  const [inspectorMax, setInspectorMax] = useState(520);
  const [curveShare, setCurveShare] = useState(() => clamp(storedLayoutNumber(workbenchLayoutStorage.curveShare, 50), 30, 70));
  const [curveShareRange, setCurveShareRange] = useState({ min: 30, max: 70 });
  const workbenchRef = useRef<HTMLDivElement>(null);
  const lowerPanelsRef = useRef<HTMLDivElement>(null);
  const effectiveInspectorWidth = clamp(inspectorWidth, 260, inspectorMax);
  const effectiveCurveShare = clamp(curveShare, curveShareRange.min, curveShareRange.max);
  useEffect(() => {
    if (candidates.length <= 5) setComparisonExpanded(false);
  }, [candidates.length]);
  useEffect(() => saveLayoutNumber(workbenchLayoutStorage.inspectorWidth, inspectorWidth), [inspectorWidth]);
  useEffect(() => saveLayoutNumber(workbenchLayoutStorage.curveShare, curveShare), [curveShare]);
  useEffect(() => {
    const updateWidths = () => {
      const workbenchWidth = workbenchRef.current?.clientWidth ?? 0;
      if (workbenchWidth > 0) {
        const nextMax = Math.max(260, Math.min(520, workbenchWidth - 569));
        setInspectorMax(nextMax);
      }
      const lowerWidth = lowerPanelsRef.current?.clientWidth ?? 0;
      if (lowerWidth > 0) {
        const minPanelWidth = 340;
        const nextMin = Math.min(50, (minPanelWidth / lowerWidth) * 100);
        const nextMax = Math.max(50, ((lowerWidth - 9 - minPanelWidth) / lowerWidth) * 100);
        const nextRange = { min: nextMin, max: nextMax };
        setCurveShareRange(nextRange);
      }
    };
    const observer = new ResizeObserver(updateWidths);
    if (workbenchRef.current) observer.observe(workbenchRef.current);
    if (lowerPanelsRef.current) observer.observe(lowerPanelsRef.current);
    updateWidths();
    return () => observer.disconnect();
  }, []);
  return (
    <div
      ref={workbenchRef}
      className={`workbench-grid candidate-workbench-grid${taskDefinition ? " has-inspector" : ""}`}
      style={{ "--candidate-inspector-width": `${effectiveInspectorWidth}px` } as CSSProperties}
    >
      {taskDefinition && <CandidateInspector
        candidate={selected}
        taskDefinition={taskDefinition}
        saveState={saveState}
        inputRanges={inputRanges}
        fieldErrors={fieldErrors}
        onInput={(path, value) => onInput(selected.id, path, value)}
        onReload={onReload}
        onCopyDraft={onCopyDraft}
        heatPattern={taskDefinition.input_groups.some((group) => group.key === "heat_pattern") ? <HeatPattern candidates={candidates} candidate={selected} onUpdate={onHeat} onAdd={onAddHeat} onDelete={onDeleteHeat} /> : undefined}
      />}
      {taskDefinition && <SplitResizer
        className="candidate-inspector-resizer"
        label="選択候補の入力パネル幅を調整"
        value={effectiveInspectorWidth}
        min={260}
        max={inspectorMax}
        step={10}
        onChange={setInspectorWidth}
        onDrag={(startValue, deltaX) => startValue + deltaX}
        onReset={() => setInspectorWidth(330)}
      />}
      <section className="central-workspace">
        <div className="table-heading">
          <div className="table-title">
            <h2>
              候補比較表 <span>（セルを直接編集）</span>
            </h2>
            {candidates.length > 5 && (
              <button
                type="button"
                className="comparison-expand-button"
                aria-expanded={comparisonExpanded}
                onClick={() => setComparisonExpanded((value) => !value)}
              >
                {comparisonExpanded ? "5候補までに戻す" : `全${candidates.length}候補を表示`}
              </button>
            )}
          </div>
          {previewError && <span className="comparison-preview-error" role="alert">{previewError}{operations?.preview && <button type="button" onClick={onRetryPreview}>再試行</button>}</span>}
          <div className="comparison-actions" aria-label="候補操作">
            <CandidateFileControls projectId={projectId} onImported={onImported} />
            <CandidateAddButton onClick={onAdd}>候補を追加</CandidateAddButton>
          </div>
        </div>
        <CandidateOrigin candidate={selected} broken={originBroken} onOpen={onOpenOrigin} />
        {taskDefinition && <ComparisonTable
          candidates={candidates}
          selectedId={selectedId}
          comparisonExpanded={comparisonExpanded}
          onToggleComparisonExpanded={() => setComparisonExpanded((value) => !value)}
          taskDefinition={taskDefinition}
          previewsByCandidate={previewsByCandidate}
          targetValues={targetValues}
          displayDecimalOverrides={project?.display_decimals}
          decisionCandidateId={decisionCandidateId}
          detailedPredictionAvailable={operations?.detailed_prediction === true}
          saveStates={saveStates}
          savedRevisionsByCandidate={savedRevisionsByCandidate}
          savingCandidateIds={savingCandidateIds}
          snapshotHistoryState={snapshotHistoryState}
          onSelect={onSelect}
          onInput={onInput}
          onName={(id, value) => onText(id, "label", value)}
          onCopy={onCopy}
          onDelete={onDelete}
          onSave={onSave}
        />}
        {taskDefinition?.curve_axis_path && operations?.response_curve ? (
          <CurveFamilyPanel
            projectId={projectId}
            candidate={selected}
            taskDefinition={taskDefinition}
            targetValues={targetValues}
            ready={["idle", "saved"].includes(saveState)}
          />
        ) : null}
        <div
          ref={lowerPanelsRef}
          className="workbench-lower-grid"
          style={{ "--response-curve-share": `${effectiveCurveShare}%` } as CSSProperties}
        >
          {operations?.response_curve ? (
              <LiveResponseCurves
              projectId={projectId}
              project={project}
              candidates={candidates}
              candidate={selected}
              preview={preview}
              previewsByCandidate={previewsByCandidate}
              targetValues={targetValues}
              taskDefinition={taskDefinition}
              responseCurveRanges={responseCurveRanges}
              onProjectChanged={onProjectChanged}
              available
              ready={["idle", "saved"].includes(saveState)}
            />
          ) : <UnavailablePanel title="応答曲線" />}
          <SplitResizer
            className="lower-panel-resizer"
            label="応答曲線と近い過去実績の幅を調整"
            value={effectiveCurveShare}
            min={curveShareRange.min}
            max={curveShareRange.max}
            step={2}
            onChange={setCurveShare}
            onDrag={(startValue, deltaX) => startValue + (deltaX / Math.max(lowerPanelsRef.current?.clientWidth ?? 1, 1)) * 100}
            onReset={() => setCurveShare(50)}
          />
          <LiveSimilarityEvidence projectId={projectId} candidate={selected} outputs={taskDefinition?.outputs ?? []} available={operations?.similarity === true} ready={["idle", "saved"].includes(saveState)} onAddCandidate={onAddCandidateFromLineage} />
        </div>
      </section>
    </div>
  );
}

function CandidateOrigin({
  candidate,
  broken,
  onOpen,
}: {
  candidate: Candidate;
  broken: boolean;
  onOpen: () => void;
}) {
  const provenance = candidate.raw.provenance as CandidateProvenance;
  const hasOriginNavigation = provenance.source_kind !== "direct" && provenance.source_kind !== "manual";
  return (
    <div className={`candidate-origin ${broken ? "missing" : ""}`}>
      <span><b>作成元</b>{provenanceLabel(provenance)}</span>
      {broken ? (
        <em>コピー元は削除済みか参照できません</em>
      ) : candidate.raw.archived_at ? (
        <em>archive済み候補を参照中</em>
      ) : hasOriginNavigation ? (
        <button type="button" className="outline-button" onClick={onOpen}>作成元へ戻る</button>
      ) : (
        <small>この候補は比較画面で直接作成されました</small>
      )}
    </div>
  );
}

function CandidateFileControls({
  projectId,
  onImported,
}: {
  projectId: string;
  onImported: (items: Candidate[]) => void;
}) {
  const [message, setMessage] = useState("");
  const upload = async (file?: File) => {
    if (!file) return;
    try {
      const body = await workbenchApi.importCandidates(projectId, file);
      const imported = body.candidates.map(fromApiCandidate);
      onImported(imported);
      setMessage(
        `${body.created}件を取り込みました${body.errors.length ? `（${body.errors.length}件は確認が必要）` : ""}`,
      );
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "XLSXを取り込めませんでした。",
      );
    }
  };
  const download = () => {
    window.location.assign(workbenchApi.candidateExportUrl(projectId));
  };
  return (
    <div className="file-controls">
      <label className="outline-button">
        XLSXを読込
        <input
          type="file"
          accept=".xlsx"
          onChange={(e) => {
            void upload(e.target.files?.[0]);
          }}
          hidden
        />
      </label>
      <button className="outline-button" onClick={download}>
        候補・予測をXLSX出力
      </button>
      {message && <small>{message}</small>}
    </div>
  );
}

function UnavailablePanel({ title }: { title: string }) {
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
function HeatPattern({
  candidates,
  candidate,
  onUpdate,
  onAdd,
  onDelete,
}: {
  candidates: Candidate[];
  candidate: Candidate;
  onUpdate: (index: number, field: "time" | "temperature" | "stageName", raw: number | string) => void;
  onAdd: () => void;
  onDelete: (index: number) => void;
}) {
  const width = 440;
  const height = 210;
  const pad = { x: 42, y: 18 };
  const times = candidates.flatMap((item) => item.heat.map((point) => point.time));
  const rawMinTime = Math.min(...times);
  const rawMaxTime = Math.max(...times);
  const timePadding = Math.max((rawMaxTime - rawMinTime) * 0.08, 0.05);
  const minTime = Math.max(0, rawMinTime - timePadding);
  const maxTime = rawMaxTime + timePadding;
  const maxTemp = Math.max(
    1000,
    ...candidates.flatMap((item) =>
      item.heat.map((point) => point.temperature),
    ),
  );
  const x = (time: number) =>
    pad.x + ((time - minTime) / Math.max(0.001, maxTime - minTime)) * (width - pad.x - 18);
  const y = (temp: number) =>
    height - 31 - (temp / maxTemp) * (height - pad.y - 31);
  const points = candidate.heat
    .map((point) => `${x(point.time)},${y(point.temperature)}`)
    .join(" ");
  const timeTicks = [minTime, (minTime + maxTime) / 2, maxTime];
  const [hoveredHeatPoint, setHoveredHeatPoint] = useState<{ x: number; y: number; lines: string[] } | null>(null);
  const dragPoint = (event: PointerEvent<SVGCircleElement>, index: number) => {
    const svg = event.currentTarget.ownerSVGElement;
    if (!svg) return;
    const bounds = svg.getBoundingClientRect();
    const temperature = Math.round(
      Math.max(
        0,
        Math.min(
          maxTemp,
          ((height -
            31 -
            ((event.clientY - bounds.top) / bounds.height) * height) /
            (height - pad.y - 31)) *
            maxTemp,
        ),
      ),
    );
    const point = candidate.heat[index];
    setHoveredHeatPoint({ x: x(point.time), y: y(temperature), lines: [candidate.label, point.stageName || point.stageCategory || `点 ${index + 1}`, `時間 ${number(point.time, 2)} min`, `温度 ${number(temperature, 0)} °C`] });
    onUpdate(index, "temperature", temperature);
  };
  return (
    <section className="chart-panel heat-panel">
      <div className="panel-title">
        <h2>
          ヒートパターン <span>（焼鈍温度・時間）</span>
        </h2>
        <div className="candidate-color-legend" aria-label="候補の色">
          {candidates.map((item) => <span className={item.id === candidate.id ? "selected" : ""} key={item.id}><i style={{ background: candidateColor(item.id, candidate.id) }} />{item.label}</span>)}
        </div>
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="heat-chart"
        role="img"
        aria-label="候補を重ねたヒートパターン。選択候補の温度点をドラッグして編集できます。"
      >
        <g className="grid-lines">
          {[0, 200, 400, 600, 800, 1000].map((value) => (
            <g key={value}>
              <line x1={pad.x} x2={width - 18} y1={y(value)} y2={y(value)} />
              <text x="3" y={y(value) + 4}>
                {value}
              </text>
            </g>
          ))}
          {timeTicks.map((value) => (
            <g key={`time-${value}`}>
              <line x1={x(value)} x2={x(value)} y1={pad.y} y2={height - 31} />
              <text x={x(value)} y={height - 18} textAnchor="middle">
                {number(value, 2)}
              </text>
            </g>
          ))}
        </g>
        {candidates
          .filter((item) => item.id !== candidate.id)
          .map((item) => (
            <g key={item.id}>
              <polyline
                points={item.heat.map((point) => `${x(point.time)},${y(point.temperature)}`).join(" ")}
                fill="none"
                stroke={candidateColor(item.id, candidate.id)}
                strokeWidth="1.5"
                opacity=".62"
              />
              {item.heat.map((point, index) => <circle
                className="svg-chart-hit-target"
                tabIndex={-1}
                aria-label={`${item.label}: ${number(point.time, 2)}分, ${point.temperature}度`}
                key={`${item.id}-${point.time}-${index}`}
                cx={x(point.time)} cy={y(point.temperature)} r="7" fill="transparent"
                onMouseEnter={() => setHoveredHeatPoint({ x: x(point.time), y: y(point.temperature), lines: [item.label, `時間 ${number(point.time, 2)} min`, `温度 ${number(point.temperature, 0)} °C`] })}
                onMouseLeave={() => setHoveredHeatPoint(null)}
                onFocus={() => setHoveredHeatPoint({ x: x(point.time), y: y(point.temperature), lines: [item.label, `時間 ${number(point.time, 2)} min`, `温度 ${number(point.temperature, 0)} °C`] })}
                onBlur={() => setHoveredHeatPoint(null)}
              />)}
            </g>
          ))}
        <polyline
          points={points}
          fill="none"
          stroke={candidateColor(candidate.id, candidate.id)}
          strokeWidth="3"
        />
        {candidate.heat.map((point, index) => (
          <circle
            tabIndex={0}
            aria-label={`${number(point.time, 2)}分, ${point.temperature}度`}
            key={`${point.time}-${index}`}
            cx={x(point.time)}
            cy={y(point.temperature)}
            r="5"
            fill="#1F5FC4"
            onMouseEnter={() => setHoveredHeatPoint({ x: x(point.time), y: y(point.temperature), lines: [candidate.label, point.stageName || point.stageCategory || `点 ${index + 1}`, `時間 ${number(point.time, 2)} min`, `温度 ${number(point.temperature, 0)} °C`] })}
            onMouseLeave={() => setHoveredHeatPoint(null)}
            onFocus={() => setHoveredHeatPoint({ x: x(point.time), y: y(point.temperature), lines: [candidate.label, point.stageName || point.stageCategory || `点 ${index + 1}`, `時間 ${number(point.time, 2)} min`, `温度 ${number(point.temperature, 0)} °C`] })}
            onBlur={() => setHoveredHeatPoint(null)}
            onPointerDown={(event) => {
              event.currentTarget.setPointerCapture(event.pointerId);
              dragPoint(event, index);
            }}
            onPointerMove={(event) =>
              event.currentTarget.hasPointerCapture(event.pointerId) &&
              dragPoint(event, index)
            }
          />
        ))}
        {hoveredHeatPoint && <SvgChartTooltip {...hoveredHeatPoint} chartWidth={width} chartHeight={height} />}
        <text className="axis-title" x="3" y="13">
          温度 (°C)
        </text>
        <text
          className="axis-title"
          x={(pad.x + width - 18) / 2}
          y={height - 1}
          textAnchor="middle"
        >
          時間 (min)
        </text>
      </svg>
      <div className="heat-edit">
        <div>
          <b>ヒートパターン編集</b>
          <span>点をドラッグ、または数値を編集</span>
          <button className="text-button" onClick={onAdd}>
            点を追加
          </button>
        </div>
        <div className="heat-point-table-wrap">
          <table className="heat-point-table">
            <thead>
              <tr><th>#</th><th>工程名</th><th>時間 <small>min</small></th><th>温度 <small>°C</small></th><th aria-label="操作" /></tr>
            </thead>
            <tbody>
              {candidate.heat.map((point, index) => (
                <tr key={`${point.time}-${index}`}>
                  <th scope="row">{index + 1}</th>
                  <td><input type="text" value={point.stageName ?? point.stageCategory ?? ""} aria-label={`点${index + 1}の工程名`} onChange={(event) => onUpdate(index, "stageName", event.target.value)} /></td>
                  <td><input type="number" step="0.01" value={Number(point.time.toFixed(3))} aria-label={`点${index + 1}の時間（分）`} onChange={(event) => onUpdate(index, "time", Number(event.target.value))} /></td>
                  <td><input type="number" value={point.temperature} aria-label={`点${index + 1}の温度（℃）`} onChange={(event) => onUpdate(index, "temperature", Number(event.target.value))} /></td>
                  <td><button className="icon-delete" aria-label={`点${index + 1}を削除`} disabled={candidate.heat.length <= 2} onClick={() => onDelete(index)}>×</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <small>RT = 室温（25°C）</small>
      </div>
    </section>
  );
}
function levelColor(index: number, count: number, selectedTone = "#1f5fc4") {
  if (count <= 1) return selectedTone;
  // 低水準→高水準を明→暗の同系色で塗り、傾きの変化を追いやすくする
  const ratio = count === 1 ? 1 : index / (count - 1);
  const lightness = 72 - ratio * 42;
  return `hsl(215, 72%, ${lightness}%)`;
}

function CurveFamilyPanel({
  projectId,
  candidate,
  taskDefinition,
  targetValues,
  ready,
}: {
  projectId: string;
  candidate: Candidate;
  taskDefinition: TaskDefinitionContract;
  targetValues: Record<string, number>;
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
  goalValue?: number;
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
  const rawMin = showFullRange || !preferredRange ? Math.min(...valueSamples, goalValue ?? Infinity, 0) : preferredRange.min;
  const rawMax = showFullRange || !preferredRange ? Math.max(...valueSamples, goalValue ?? -Infinity) : preferredRange.max;
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
        {Number.isFinite(goalValue) && <line x1="28" y1={y(goalValue!)} x2="284" y2={y(goalValue!)} stroke="#c17816" strokeDasharray="4 3" />}
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

function LiveResponseCurves({
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
  targetValues: Record<string, number>;
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
        <div id="response-curve-axis-settings" className="response-curve-axis-settings">
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
  goalValue?: number;
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
  const rawMin = Math.min(...outputAxisValues, goalValue ?? Infinity);
  const rawMax = Math.max(...outputAxisValues, goalValue ?? -Infinity);
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
        {Number.isFinite(goalValue) && <line x1="28" y1={y(goalValue!)} x2="284" y2={y(goalValue!)} stroke="#c17816" strokeDasharray="4 3" />}
        {clippedAbove > 0 && <text className="curve-clip-indicator" x="280" y="40" textAnchor="end">▲ {clippedAbove}</text>}
        {clippedBelow > 0 && <text className="curve-clip-indicator" x="280" y="121" textAnchor="end">▼ {clippedBelow}</text>}
        {xTicks.map((tick) => <text key={tick} x={x(tick)} y="137" textAnchor="middle" fontSize="8" fill="#617087">{number(tick, xDigits)}</text>)}
        {hoveredPoint && <SvgChartTooltip {...hoveredPoint} chartWidth={width} chartHeight={height} />}
        <text x="156" y="153" textAnchor="middle" fontSize="8" fill="#617087">{xLabel} ({xUnit})</text>
      </svg> : <p className="empty-evidence">読み込み中…</p>}
    </article>
  );
}

function LiveSimilarityEvidence({
  projectId,
  candidate,
  outputs,
  available,
  ready,
  onAddCandidate,
}: {
  projectId: string;
  candidate: Candidate;
  outputs: TaskOutputDefinition[];
  available: boolean;
  ready: boolean;
  onAddCandidate: (entityKey: string) => Promise<boolean>;
}) {
  const [surface, setSurface] = useState(() => emptyInferenceSurface<ApiSimilarObservation[]>());
  const [addingKey, setAddingKey] = useState("");
  const [addedKeys, setAddedKeys] = useState<string[]>([]);
  const surfaceRef = useRef(surface);
  const inputIdentity = candidateInputIdentity(candidate.raw.inputs);
  const similarityScope = `${projectId}\u001f${candidate.id}\u001fsimilarity:6`;
  const identity = `${similarityScope}\u001f${candidate.raw.revision}\u001f${inputIdentity}`;
  useEffect(() => {
    const empty = emptyInferenceSurface<ApiSimilarObservation[]>();
    surfaceRef.current = empty;
    setSurface(empty);
    setAddedKeys([]);
    setAddingKey("");
  }, [candidate.id]);
  useEffect(() => {
    if (!available || !ready || candidate.raw.archived_at) return;
    const controller = new AbortController();
    const requested = requestInferenceSurface(surfaceRef.current, identity);
    surfaceRef.current = requested;
    setSurface(requested);
    void workbenchApi.similarCandidates(
      projectId,
      candidate.id,
      candidate.raw.revision,
      inputIdentity,
      6,
      controller.signal,
    ).then((loaded) => {
      if (controller.signal.aborted) return;
      const resolved = resolveInferenceSurface(surfaceRef.current, requested.requestSequence, identity, loaded);
      surfaceRef.current = resolved;
      setSurface(resolved);
    }).catch((cause) => {
      if (controller.signal.aborted) return;
      const rejected = rejectInferenceSurface(surfaceRef.current, requested.requestSequence, identity, cause);
      surfaceRef.current = rejected;
      setSurface(rejected);
    });
    return () => controller.abort();
  }, [available, candidate.id, candidate.raw.archived_at, candidate.raw.revision, identity, inputIdentity, projectId, ready]);
  const status = inferenceSurfaceStatus(surface);
  const similar = surface.currentIdentity?.startsWith(`${similarityScope}\u001f`) ? surface.data ?? [] : [];
  const processLabel = similar.find((item) => item.process_label)?.process_label ?? "工程履歴";
  const measuredOutputs = (item: ApiSimilarObservation) => outputs.flatMap((output) => {
    const summaryKey = [...(output.measurement_keys ?? []), output.key, output.label]
      .find((key) => item.repeat_summary?.[key]);
    const summary = summaryKey ? item.repeat_summary?.[summaryKey] : undefined;
    return summary ? [{ output, summary }] : [];
  });
  const add = async (entityKey: string) => {
    setAddingKey(entityKey);
    try {
      if (await onAddCandidate(entityKey)) setAddedKeys((current) => current.includes(entityKey) ? current : [...current, entityKey]);
    } finally {
      setAddingKey("");
    }
  };
  return (
    <section className="similar-evidence-panel">
      <div className="evidence-title">
        <div>
          <h2>近い過去実績 <span>（予測対象の実績値）</span></h2>
          <span className="similar-caption">距離が小さいほど、成分・工程・熱履歴が近い条件です</span>
        </div>
        {similar.length > 0 && <span className={`inference-surface-status ${status}`}>{status === "latest" ? "最新" : status === "refreshing" ? "更新中" : status === "stale" ? "旧revision・更新中" : "更新失敗・旧結果"}</span>}
      </div>
      {!available ? (
        <p className="empty-evidence">このタスクでは類似実験を利用できません。</p>
      ) : candidate.raw.archived_at ? (
        <p className="empty-evidence">archive済み候補では新しい根拠計算を行いません。</p>
      ) : !ready ? (
        <p className="empty-evidence">入力を保存後に近さを更新します。</p>
      ) : similar.length ? (
        <>
          <div className="similar-table-scroll"><table className="similar-table similar-summary-table">
            <thead><tr><th>距離</th><th>溶製成績書 key</th><th>{processLabel} key</th><th>実績値</th><th /></tr></thead>
            <tbody>{similar.map((item) => (
              <tr key={`${item.layer ?? "training"}-${item.parent_key}`}>
                <td className="similar-distance"><b>{item.distance.toFixed(2)}</b><span className={`layer-chip ${item.layer ?? "training"}`}>{item.layer === "historical" ? "学習外" : "学習内"}</span></td>
                <td className="similar-key">{item.melt_key ?? "—"}</td>
                <td className="similar-key">{item.process_key ?? item.parent_key}</td>
                <td><div className="similar-value-list"><small>{item.source || item.observation_id || "実績"}</small>{measuredOutputs(item).map(({ output, summary }) => { const assessment = assessOutputValues(output, [summary.mean], "実測値"); return <span className={assessment.implausible ? "implausible-output" : undefined} key={output.key} title={assessment.warning ?? `${output.label}: ${number(summary.mean, 1)} ± ${number(summary.std, 1)} ${output.unit} / n=${summary.n}`}><b>{output.key === "lambda" ? "λ" : output.key}</b><strong>{number(summary.mean, 1)}</strong>{assessment.implausible && <small className="output-warning-badge">⚠</small>}</span>; })}</div></td>
                <td className="similar-action-cell">
                  <CandidateAddButton compact disabled={!item.process_key || addingKey === item.process_key || addedKeys.includes(item.process_key ?? "")} onClick={() => { if (item.process_key) void add(item.process_key); }}>
                    {addedKeys.includes(item.process_key ?? "") ? "追加済み" : addingKey === item.process_key ? "追加中…" : "候補に追加"}
                  </CandidateAddButton>
                </td>
              </tr>
            ))}</tbody>
          </table></div>
        </>
      ) : status === "error" ? (
        <p className="empty-evidence">類似実験を取得できませんでした。閉じて再度開くと再試行します。</p>
      ) : (
        <p className="empty-evidence">類似実験を取得しています。</p>
      )}
    </section>
  );
}
