import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type ReactNode, type Ref, type UIEvent } from "react";
import type { CandidateViewModel } from "./candidateModel";
import { getCandidateInputValue, numericTaskInputs, orderedInputGroups, type NumericRange, type NumericTaskInput, type TaskDefinitionContract, type TaskFieldDefinition, type TaskInputGroup, type TaskOutputDefinition } from "./taskDefinition";
import type { ApiPreview } from "../../shared/api/workbench-api";
import { CandidateAddButton } from "../../shared/ui/CandidateAddButton";
import type { CandidateSaveState } from "./useCandidateEditor";
import { formatDisplayNumber, formatInputNumber, type DisplayDecimalOverrides } from "./numberFormat";
import { formatPredictionPoint, predictionHasInterval, predictionIntervalLabel } from "../../shared/predictionPresentation";
import { assessOutputValues } from "../../shared/outputPresentation";
import { supportStatusLabel } from "../../shared/supportPresentation";
import { hasValidTargetGoal, targetGoalText, type TargetGoal } from "../../shared/targetGoals";
import { buildCandidateDecisionSummary } from "./decisionSummary";
import { candidateRemovalConfirmationText } from "./candidateRemovalPresentation";
import { comparisonValuesDiffer } from "./comparisonReference";

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

const comparisonInputShareStorageKey = "material-workbench:layout:comparison-input-share:v2";
const comparisonReferenceStoragePrefix = "material-workbench:comparison-reference:v1";
const prominentHeatPatternInputPaths = new Set(["process.ls_mpm"]);

function storedComparisonInputShare() {
  if (typeof window === "undefined") return 34;
  try {
    const raw = window.localStorage.getItem(comparisonInputShareStorageKey);
    if (raw === null) return 34;
    const value = Number(raw);
    return Number.isFinite(value) ? value : 34;
  } catch {
    return 34;
  }
}

function saveComparisonInputShare(value: number) {
  try {
    window.localStorage.setItem(comparisonInputShareStorageKey, String(value));
  } catch {
    // Layout persistence is optional when local storage is unavailable.
  }
}

function comparisonReferenceStorageKey(projectId: string) {
  return `${comparisonReferenceStoragePrefix}:${projectId}`;
}

function storedComparisonReference(projectId: string) {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(comparisonReferenceStorageKey(projectId)) ?? "";
  } catch {
    return "";
  }
}

function saveComparisonReference(projectId: string, candidateId: string) {
  try {
    const key = comparisonReferenceStorageKey(projectId);
    if (candidateId) window.localStorage.setItem(key, candidateId);
    else window.localStorage.removeItem(key);
  } catch {
    // Comparison emphasis remains usable without persistence.
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
  if (input.default_range) return input.default_range;
  if (!input.allowed_range) throw new Error(`数値fieldにallowed_rangeがありません: ${input.path}`);
  return input.allowed_range;
}

function sliderScale(input: NumericTaskInput, value: number, inputRanges: Record<string, NumericRange>) {
  const range = allowedRange(input, inputRanges);
  const learnedMin = input.training_range?.min ?? range.min;
  const learnedMax = input.training_range?.max ?? range.max;
  const logScale = input.search_scale === "log";
  const rangePercent = (position: number) => {
    const normalized = logScale
      ? (Math.log(position) - Math.log(range.min)) / Math.max(Math.log(range.max) - Math.log(range.min), Number.EPSILON)
      : (position - range.min) / Math.max(range.max - range.min, Number.EPSILON);
    return Math.round(Math.max(0, Math.min(100, normalized * 100)) * 1000) / 1000;
  };
  const start = rangePercent(learnedMin);
  const end = rangePercent(learnedMax);
  const numericStep = input.numeric_domain_kind === "integer" ? 1 : input.step ?? "any";
  const snapValue = (rawValue: number) => {
    const step = input.numeric_domain_kind === "integer" ? 1 : input.step;
    if (step == null) return clamp(rawValue, range.min, range.max);
    const origin = input.allowed_range?.min ?? range.min;
    const lowerIndex = Math.ceil((range.min - origin) / step - Number.EPSILON);
    const upperIndex = Math.floor((range.max - origin) / step + Number.EPSILON);
    const index = Math.min(
      upperIndex,
      Math.max(lowerIndex, Math.round((rawValue - origin) / step)),
    );
    return Math.round((origin + index * step) * 1e12) / 1e12;
  };
  const toValue = (position: number) => snapValue(logScale
    ? Math.exp(Math.log(range.min) + (position / 100) * (Math.log(range.max) - Math.log(range.min)))
    : position);
  return {
    min: logScale ? 0 : range.min,
    max: logScale ? 100 : range.max,
    inputMin: range.min,
    inputMax: range.max,
    step: logScale ? 0.1 : numericStep,
    value: logScale ? rangePercent(Math.max(range.min, Math.min(range.max, value))) : Math.max(range.min, Math.min(range.max, value)),
    toValue,
    style: { background: `linear-gradient(90deg, #dfe6ee 0 ${start}%, #6bb69e ${start}% ${end}%, #dfe6ee ${end}% 100%)` },
  };
}

function inputStep(field: TaskFieldDefinition): number | "any" {
  return field.numeric_domain_kind === "integer" ? 1 : field.step ?? "any";
}

function unplacedErrorLabel(path: string): string {
  if (path.endsWith(".name") || path === "name") return "候補名";
  const heatPoint = path.match(/heat_pattern\.(\d+)\.(time_s|temperature_c)$/);
  if (heatPoint) return `ヒートパターン ${Number(heatPoint[1]) + 1}点目 ${heatPoint[2] === "time_s" ? "時間" : "温度"}`;
  if (path.includes("heat_pattern")) return "ヒートパターン";
  return path;
}

function commonGroupUnit(fields: TaskInputGroup["fields"]): string {
  const units = new Set(fields.map((field) => field.unit?.trim()).filter((unit): unit is string => Boolean(unit)));
  return units.size === 1 ? [...units][0] : "";
}

function inputGroupDisplayLabel(label: string): string {
  return label.replace("（安全に単一値へ正規化できた行）", "");
}

function CandidateInputGroup({ candidate, group, numeric, inputRanges, fieldErrors, onInput }: {
  candidate: CandidateViewModel;
  group: TaskInputGroup;
  numeric: Map<string, NumericTaskInput>;
  inputRanges: Record<string, NumericRange>;
  fieldErrors: Array<{ path: string; message: string }>;
  onInput: (path: string, value: number | string | undefined) => void;
}) {
  const groupUnit = commonGroupUnit(group.fields);
  return (
    <section className={`inspector-section task-input-group ${group.key}`} data-input-group={group.key}>
      <div className="section-heading"><div className="section-heading-label"><h3>{inputGroupDisplayLabel(group.label)}</h3></div><span>{groupUnit}</span></div>
      <div className={group.key === "composition" ? "composition-fields" : "task-field-grid"}>
        {group.fields.map((field) => {
          const value = getCandidateInputValue(candidate.raw.inputs, field.path);
          const errors = fieldErrors.filter((error) => error.path.endsWith(field.path));
          if (field.kind === "categorical") {
            return <label className="task-select-field" key={field.path}><b>{field.label}</b><select disabled={!field.editable} aria-label={`${candidate.label} ${field.label}`} value={String(value ?? "")} onChange={(event) => onInput(field.path, event.target.value)}>{field.choices.map((choice) => <option key={choice}>{choice}</option>)}</select>{errors.map((error) => <small className="field-error" key={error.path}>{error.message}</small>)}</label>;
          }
          const input = numeric.get(field.path);
          if (!input) throw new Error(`Numeric TaskDefinition field is unavailable: ${field.path}`);
          const missingOptionalValue = value === undefined && !field.required;
          const numberValue = Number(value ?? 0);
          const scale = sliderScale(input, numberValue, inputRanges);
          return (
            <label className="slider-field" key={field.path}>
              <span><b>{field.label}</b><em><input disabled={!field.editable} className="slider-number" type="number" min={scale.inputMin} max={scale.inputMax} step={inputStep(input)} value={missingOptionalValue ? "" : numberValue} placeholder={missingOptionalValue ? "未設定" : undefined} aria-label={`${candidate.label} ${field.label}の数値`} onChange={(event) => onInput(field.path, event.target.value === "" && !field.required ? undefined : Number(event.target.value))} />{field.unit && field.unit !== groupUnit ? <small>{field.unit}</small> : null}</em></span>
              {missingOptionalValue
                ? <small>ヒートパターンの経過時間を使用中</small>
                : <><input disabled={!field.editable} type="range" min={scale.min} max={scale.max} step={scale.step} value={scale.value} style={scale.style} aria-label={`${candidate.label} ${field.label}`} onChange={(event) => onInput(field.path, scale.toValue(Number(event.target.value)))} />{input.search_scale === "log" && <small>対数スケール</small>}</>}
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
  onCollapse,
  collapseButtonRef,
  heatPattern,
  className = "candidate-inspector",
  hidden = false,
}: {
  candidate: CandidateViewModel;
  taskDefinition: TaskDefinitionContract;
  saveState: CandidateSaveState;
  inputRanges?: Record<string, NumericRange>;
  fieldErrors: Array<{ path: string; message: string }>;
  onInput: (path: string, value: number | string | undefined) => void;
  onReload: () => void;
  onCopyDraft: () => void;
  onCollapse?: () => void;
  collapseButtonRef?: Ref<HTMLButtonElement>;
  heatPattern?: ReactNode;
  className?: string;
  hidden?: boolean;
}) {
  const inputsReadOnly = candidate.raw.provenance?.source_kind === "historical_observation";
  const numeric = new Map(numericTaskInputs(taskDefinition).map((input) => [input.path, input]));
  const groups = orderedInputGroups(taskDefinition);
  const heatGroup = groups.find((group) => group.key === "heat_pattern");
  const primaryGroups = heatGroup
    ? groups.flatMap((group) => {
        if (group.key === "composition") return [group];
        const fields = group.fields.filter((field) => prominentHeatPatternInputPaths.has(field.path));
        return fields.length > 0 ? [{ ...group, fields }] : [];
      })
    : groups.filter((group) => group.key !== "heat_pattern");
  const auxiliaryGroups = heatGroup
    ? groups.flatMap((group) => {
        if (group.key === "composition" || group.key === "heat_pattern") return [];
        const fields = group.fields.filter((field) => !prominentHeatPatternInputPaths.has(field.path));
        return fields.length > 0 ? [{ ...group, fields }] : [];
      })
    : [];
  const ordinaryPaths = groups.filter((group) => group.key !== "heat_pattern").flatMap((group) => group.fields.map((field) => field.path));
  const unplacedErrors = fieldErrors.filter((error) => !ordinaryPaths.some((path) => error.path.endsWith(path)));
  const hasTrainingRanges = [...numeric.values()].some((field) => field.training_range);
  return (
    <aside className={className} aria-label="選択候補の入力" hidden={hidden}>
      <div className="inspector-heading">
        <div className="inspector-heading-row">
          <span className="overline">選択候補の入力</span>
          {onCollapse && <button
            type="button"
            className="inspector-collapse-button"
            ref={collapseButtonRef}
            aria-label="入力パネルを折りたたむ"
            title="入力パネルを折りたたむ"
            onClick={onCollapse}
          >‹</button>}
        </div>
        <h2>{candidate.label}</h2>
        {hasTrainingRanges && <small className="training-range-legend"><i aria-hidden="true" />緑帯：既定の検討範囲</small>}
        <small className={`candidate-save-state ${saveState}`}>{saveLabels[saveState]}</small>
        {saveState === "conflict" && <span className="candidate-conflict-actions"><button type="button" onClick={onReload}>再読込</button><button type="button" onClick={onCopyDraft}>変更をコピー</button></span>}
      </div>
      {unplacedErrors.length > 0 && <div className="candidate-field-errors" role="alert">{unplacedErrors.map((error) => <small className="field-error" key={`${error.path}:${error.message}`}><b>{unplacedErrorLabel(error.path)}:</b> {error.message}</small>)}</div>}
      {inputsReadOnly && <small className="candidate-historical-input-note">過去の実測recordから作成した候補の入力条件は編集できません</small>}
      <fieldset className="candidate-input-readonly" disabled={inputsReadOnly}>
        {primaryGroups.map((group) => <CandidateInputGroup key={group.key} candidate={candidate} group={group} numeric={numeric} inputRanges={inputRanges} fieldErrors={fieldErrors} onInput={onInput} />)}
        {heatGroup && (heatGroup.fields.every((field) => field.editable) ? heatPattern : <fieldset className="readonly-heat-pattern" disabled>{heatPattern}</fieldset>)}
        {auxiliaryGroups.length > 0 && <details className="task-auxiliary-inputs"><summary>その他の入力</summary>{auxiliaryGroups.map((group) => <CandidateInputGroup key={group.key} candidate={candidate} group={group} numeric={numeric} inputRanges={inputRanges} fieldErrors={fieldErrors} onInput={onInput} />)}</details>}
      </fieldset>
      {taskDefinition.fixed_context.length > 0 && <dl className="task-fixed-context">{[...taskDefinition.fixed_context].sort((a, b) => a.order - b.order).map((item) => <div key={item.path}><dt>{item.label}</dt><dd>{String(item.value)}</dd></div>)}</dl>}
    </aside>
  );
}

export function ComparisonTable({
  projectId,
  candidates,
  selectedId,
  comparisonHeight,
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
  onConfigureGoals,
  onConfigureSupport,
  pendingPreviewCount,
  loadingRemainingPreviews,
  onLoadRemainingPreviews,
  context = "comparison",
  editingEnabled = true,
}: {
  projectId: string;
  candidates: CandidateViewModel[];
  selectedId: string;
  comparisonHeight: number;
  taskDefinition: TaskDefinitionContract;
  previewsByCandidate: Record<string, ApiPreview>;
  targetValues: Record<string, TargetGoal>;
  displayDecimalOverrides?: DisplayDecimalOverrides;
  decisionCandidateId: string;
  detailedPredictionAvailable: boolean;
  saveStates: Record<string, CandidateSaveState>;
  savedRevisionsByCandidate: Record<string, number[]>;
  savingCandidateIds: string[];
  snapshotHistoryState: "loading" | "ready" | "error";
  onSelect: (id: string) => void;
  onName: (id: string, value: string) => void;
  onInput: (id: string, path: string, value: number | string | undefined) => void;
  onCopy: (id: string) => void;
  onDelete: (id: string) => void;
  onSave: (candidate: CandidateViewModel) => void;
  onConfigureGoals: () => void;
  onConfigureSupport: () => void;
  pendingPreviewCount: number;
  loadingRemainingPreviews: boolean;
  onLoadRemainingPreviews: () => void;
  context?: "comparison" | "explore";
  editingEnabled?: boolean;
}) {
  const [pendingDeleteCandidateId, setPendingDeleteCandidateId] = useState("");
  const [referenceCandidateId, setReferenceCandidateId] = useState(() => storedComparisonReference(projectId));
  useEffect(() => {
    setReferenceCandidateId(storedComparisonReference(projectId));
  }, [projectId]);
  useEffect(() => {
    if (!referenceCandidateId || candidates.some((candidate) => candidate.id === referenceCandidateId)) return;
    setReferenceCandidateId("");
    saveComparisonReference(projectId, "");
  }, [candidates, projectId, referenceCandidateId]);
  const referenceCandidate = context === "comparison"
    ? candidates.find((candidate) => candidate.id === referenceCandidateId)
    : undefined;
  const toggleReferenceCandidate = (candidateId: string) => {
    const next = referenceCandidateId === candidateId ? "" : candidateId;
    setReferenceCandidateId(next);
    saveComparisonReference(projectId, next);
  };
  const fieldVaries = (path: string) => new Set(candidates.map((candidate) => JSON.stringify(getCandidateInputValue(candidate.raw.inputs, path) ?? null))).size > 1;
  const inputGroups = orderedInputGroups(taskDefinition)
    .filter((group) => group.key !== "heat_pattern")
    .map((group, index) => ({
      ...group,
      index,
      fields: [...group.fields].sort((left, right) => Number(fieldVaries(right.path)) - Number(fieldVaries(left.path))),
      hasVariation: group.fields.some((field) => fieldVaries(field.path)),
    }))
    .sort((left, right) => Number(right.hasVariation) - Number(left.hasVariation) || left.index - right.index);
  const inputFields = inputGroups.flatMap((group) => group.fields);
  const readingInputFields = orderedInputGroups(taskDefinition)
    .filter((group) => group.key !== "heat_pattern")
    .flatMap((group) => group.fields);
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
  const syncRowHeights = () => {
    const panes = [
      nameScrollRef.current,
      inputScrollRef.current,
      predictionScrollRef.current,
      actionScrollRef.current,
    ];
    const headerRowsByPane = panes.map((pane) =>
      Array.from(pane?.querySelectorAll<HTMLTableRowElement>("thead > tr") ?? []),
    );
    headerRowsByPane.flat().forEach((row) => {
      row.style.height = "";
    });
    const detailHeaderRows = headerRowsByPane.slice(1);
    const detailHeaderHeights = [0, 1].map((rowIndex) => Math.max(
      41,
      ...detailHeaderRows.map((rows) => rows[rowIndex]?.getBoundingClientRect().height ?? 0),
    ));
    comparisonGridRef.current?.style.setProperty(
      "--comparison-header-first-row-height",
      `${Math.ceil(detailHeaderHeights[0] ?? 41)}px`,
    );
    detailHeaderRows.forEach((rows) => {
      rows.forEach((row, rowIndex) => {
        row.style.height = `${Math.ceil(detailHeaderHeights[rowIndex] ?? 41)}px`;
      });
    });
    const nameHeaderRow = headerRowsByPane[0]?.[0];
    if (nameHeaderRow) {
      nameHeaderRow.style.height = `${detailHeaderHeights.reduce((sum, height) => sum + Math.ceil(height), 0)}px`;
    }
    const rows = panes.flatMap((pane) =>
      Array.from(pane?.querySelectorAll<HTMLTableRowElement>("tbody > tr") ?? []),
    );
    rows.forEach((row) => {
      row.style.height = "";
    });
    const rowsByCandidate = new Map<string, HTMLTableRowElement[]>();
    rows.forEach((row) => {
      const candidateId = row.dataset.candidateId;
      if (!candidateId) return;
      const candidateRows = rowsByCandidate.get(candidateId) ?? [];
      candidateRows.push(row);
      rowsByCandidate.set(candidateId, candidateRows);
    });
    for (const candidateRows of rowsByCandidate.values()) {
      const sharedHeight = Math.max(
        37,
        ...candidateRows.map((row) => row.getBoundingClientRect().height),
      );
      candidateRows.forEach((row) => {
        row.style.height = `${Math.ceil(sharedHeight)}px`;
      });
    }
  };
  useLayoutEffect(() => {
    const tables = [
      nameScrollRef.current,
      inputScrollRef.current,
      predictionScrollRef.current,
      actionScrollRef.current,
    ].flatMap((pane) => {
      const table = pane?.querySelector("table");
      return table ? [table] : [];
    });
    let syncFrame = 0;
    let releaseFrame = 0;
    let synchronizing = false;
    const scheduleRowHeightSync = () => {
      if (synchronizing || syncFrame) return;
      syncFrame = window.requestAnimationFrame(() => {
        syncFrame = 0;
        synchronizing = true;
        syncRowHeights();
        releaseFrame = window.requestAnimationFrame(() => {
          synchronizing = false;
        });
      });
    };
    const observer = new ResizeObserver(scheduleRowHeightSync);
    tables.forEach((table) => observer.observe(table));
    const rootStyleObserver = new MutationObserver(scheduleRowHeightSync);
    rootStyleObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class", "style"],
    });
    window.addEventListener("resize", scheduleRowHeightSync);
    document.fonts?.addEventListener("loadingdone", scheduleRowHeightSync);
    return () => {
      observer.disconnect();
      rootStyleObserver.disconnect();
      window.removeEventListener("resize", scheduleRowHeightSync);
      document.fonts?.removeEventListener("loadingdone", scheduleRowHeightSync);
      window.cancelAnimationFrame(syncFrame);
      window.cancelAnimationFrame(releaseFrame);
    };
  }, []);
  useLayoutEffect(syncRowHeights, [
    candidates,
    comparisonHeight,
    displayDecimalOverrides,
    effectiveInputShare,
    previewsByCandidate,
    saveStates,
    savedRevisionsByCandidate,
    savingCandidateIds,
    snapshotHistoryState,
    targetValues,
  ]);
  const syncVerticalScroll = (event: UIEvent<HTMLDivElement>) => {
    const scrollTop = event.currentTarget.scrollTop;
    for (const pane of [nameScrollRef.current, inputScrollRef.current, predictionScrollRef.current, actionScrollRef.current]) {
      if (pane && pane !== event.currentTarget && Math.abs(pane.scrollTop - scrollTop) > 0.5) pane.scrollTop = scrollTop;
    }
  };
  const support = (value?: string) => supportStatusLabel(value);
  const formatNumber = (value: number) => value.toLocaleString("ja-JP", { maximumFractionDigits: 1 });
  const decisionSummary = buildCandidateDecisionSummary({
    candidateIds: candidates.map((candidate) => candidate.id),
    outputKeys: outputs.map((output) => output.key),
    previewsByCandidate,
  });
  const configuredTargetCount = outputs.filter((output) => hasValidTargetGoal(targetValues[output.key])).length;
  const selectedCandidate = candidates.find((candidate) => candidate.id === selectedId);
  const supportConcernCount = decisionSummary.supportCounts.caution + decisionSummary.supportCounts.extrapolated;
  const supportUnknownCount = decisionSummary.supportCounts.unknown + candidates.length - decisionSummary.loadedCandidateCount;
  const uniformSupportMessage = decisionSummary.uniformSupportStatus === "supported"
    ? "全候補が学習条件の近傍です。支持範囲では候補を絞れないため、予測区間と目標との差を比較してください。"
    : decisionSummary.uniformSupportStatus === "caution"
      ? "全候補が学習条件の密度が低い領域です。候補間の相対比較に加え、近い実績を確認してください。"
      : decisionSummary.uniformSupportStatus === "extrapolated"
        ? "全候補が学習範囲外です。候補間の相対比較には使えますが、絶対値は探索的な参考です。"
        : null;
  const goalDirectionLabel = (direction: TaskOutputDefinition["goal_direction"]) => direction === "at_most"
    ? "↓ 小さい側が目標"
    : direction === "at_least"
      ? "↑ 大きい側が目標"
      : "↔ 範囲内が目標";
  const predictionValue = (prediction: ApiPreview["predictions"][string], outputKey: string) => prediction.target_kind === "continuous" || prediction.target_kind === "continuous_positive" || prediction.target_kind === "count"
    ? formatDisplayNumber(prediction.value, taskDefinition, `output.${outputKey}`, displayDecimalOverrides)
    : formatPredictionPoint(prediction, formatNumber);
  const uncertaintySummary = (prediction: ApiPreview["predictions"][string], outputKey: string) => {
    const components = prediction.uncertainty_components ?? {};
    const model = components.latent_model_std ?? components.model_std;
    const observation = components.observation_noise_std ?? components.observation_std;
    const hasInterval = predictionHasInterval(prediction);
    const interval = hasInterval
      ? prediction.target_kind === "binary"
        ? `${formatNumber(prediction.lower * 100)}–${formatNumber(prediction.upper * 100)}%`
        : `${formatDisplayNumber(prediction.lower, taskDefinition, `output.${outputKey}`, displayDecimalOverrides)}–${formatDisplayNumber(prediction.upper, taskDefinition, `output.${outputKey}`, displayDecimalOverrides)}`
      : "利用不可";
    const label = predictionIntervalLabel(prediction);
    const details = [
      hasInterval ? `${label} ${interval}` : label,
      prediction.interval_calibration_sample_count != null ? `校正 ${prediction.interval_calibration_sample_count}件` : null,
      prediction.interval_calibration_dataset_digest ? `校正データ ${prediction.interval_calibration_dataset_digest.slice(0, 12)}…` : null,
      model !== undefined ? `モデル由来 ±${formatNumber(model)}` : null,
      observation !== undefined ? `測定由来 ±${formatNumber(observation)}` : null,
    ].filter(Boolean).join(" / ");
    return { interval, details, label };
  };
  const renderField = (candidate: CandidateViewModel, field: (typeof inputFields)[number]) => {
    const current = getCandidateInputValue(candidate.raw.inputs, field.path);
    const inputsReadOnly = candidate.raw.provenance?.source_kind === "historical_observation";
    const differsFromReference = Boolean(
      referenceCandidate
      && candidate.id !== referenceCandidate.id
      && comparisonValuesDiffer(current, getCandidateInputValue(referenceCandidate.raw.inputs, field.path)),
    );
    const differenceTitle = differsFromReference ? `基準「${referenceCandidate?.label}」と異なります` : undefined;
    const cellClass = `composition-col${field.kind === "number" ? " numeric-cell" : ""}${differsFromReference ? " reference-difference-cell" : ""}`;
    const marker = differsFromReference ? <span className="reference-difference-marker" aria-label={differenceTitle} title={differenceTitle}>≠</span> : null;
    if (field.kind === "categorical") return <td className={cellClass} key={field.path}>{marker}<select disabled={!editingEnabled || !field.editable || inputsReadOnly} aria-label={`${candidate.label} ${field.label}`} value={String(current ?? "")} onFocus={() => onSelect(candidate.id)} onChange={(event) => onInput(candidate.id, field.path, event.target.value)}>{field.choices.map((choice) => <option key={choice}>{choice}</option>)}</select></td>;
    const key = `${candidate.id}:${field.path}`;
    const value = drafts[key] ?? (typeof current === "number" ? formatInputNumber(current, taskDefinition, field.path, displayDecimalOverrides) : "");
    return <td className={cellClass} key={field.path}>{marker}<input disabled={!editingEnabled || !field.editable || inputsReadOnly} type="number" min={field.allowed_range?.min} max={field.allowed_range?.max} step={inputStep(field)} value={value} aria-label={`${candidate.label} ${field.label}`} onFocus={() => { onSelect(candidate.id); setDrafts((items) => ({ ...items, [key]: typeof current === "number" ? String(current) : "" })); }} onChange={(event) => setDrafts((items) => ({ ...items, [key]: event.target.value }))} onBlur={(event) => { const raw = event.target.value; const next = Number(raw); setDrafts((items) => { const { [key]: _, ...rest } = items; return rest; }); if (raw === "" && !field.required) onInput(candidate.id, field.path, undefined); else if (Number.isFinite(next) && next !== current) onInput(candidate.id, field.path, next); }} /></td>;
  };
  const outputTargetKind = (key: string) => candidates
    .map((candidate) => previewsByCandidate[candidate.id]?.predictions[key]?.target_kind)
    .find(Boolean);
  const allOutputsBinary = outputs.length > 0 && outputs.every((output) => outputTargetKind(output.key) === "binary");
  const binaryProbabilities = allOutputsBinary
    ? candidates.flatMap((candidate) => outputs.flatMap((output) => {
        const prediction = previewsByCandidate[candidate.id]?.predictions[output.key];
        return prediction ? [prediction.value] : [];
      }))
    : [];
  const binaryProbabilitySpread = binaryProbabilities.length >= 2
    ? (Math.max(...binaryProbabilities) - Math.min(...binaryProbabilities)) * 100
    : null;
  const uncertaintyMessage = allOutputsBinary
    ? "異常確率は校正済みの点確率です。確率区間は算出していないため、近い実績と適用範囲も合わせて判断してください。"
    : candidates.length < 2
      ? "予測区間を比較するには、候補が2件以上必要です。"
      : decisionSummary.assessableOutputKeys.length === 0
        ? "予測区間を比較できる候補を確認しています。"
        : decisionSummary.overlappingOutputKeys.length > 0
          ? `${decisionSummary.overlappingOutputKeys.length} / ${decisionSummary.assessableOutputKeys.length}特性で全候補の予測区間が重なっています。点予測の差だけでは優劣を決めにくい状態です。`
          : "全候補に共通する予測区間はありません。候補間の差そのものを示す検定ではないため、近い実績と支持範囲も確認してください。";
  return (
    <div className="candidate-comparison">
      {context === "comparison" && <aside className="candidate-decision-summary" aria-label="候補比較の判断サマリー">
        <div className="candidate-decision-summary-heading">
          <strong>判断サマリー</strong>
          <span>選択中: {selectedCandidate?.label ?? "—"}</span>
        </div>
        <dl>
          <div>
            <dt>目標</dt>
            <dd className={configuredTargetCount === outputs.length ? "is-ready" : "needs-attention"}>
              {configuredTargetCount} / {outputs.length}特性
              <button type="button" className="decision-summary-link" onClick={onConfigureGoals}>{configuredTargetCount === outputs.length ? "目標を変更" : "目標を設定"}</button>
            </dd>
          </div>
          <div>
            <dt>支持範囲</dt>
            <dd className={supportConcernCount > 0 || supportUnknownCount > 0 ? "needs-attention" : "is-ready"}>
              {decisionSummary.loadedCandidateCount === 0
                ? "確認中"
                : supportUnknownCount > 0
                  ? `${supportUnknownCount}候補は未確認`
                  : supportConcernCount > 0
                    ? `${supportConcernCount}候補を要確認`
                    : "全候補が範囲内"}
              <button type="button" className="decision-summary-link" onClick={onConfigureSupport}>入力範囲を確認</button>
            </dd>
          </div>
          <div>
            <dt>{allOutputsBinary ? "候補間の確率差" : "区間の共通部分"}</dt>
            <dd className={pendingPreviewCount > 0 || (!allOutputsBinary && decisionSummary.overlappingOutputKeys.length > 0) ? "needs-attention" : "is-ready"}>
              {pendingPreviewCount > 0
                ? `未計算 ${pendingPreviewCount}候補`
                : allOutputsBinary
                  ? binaryProbabilitySpread == null ? "確認中" : `${formatNumber(binaryProbabilitySpread)}ポイント`
                  : decisionSummary.assessableOutputKeys.length === 0
                    ? "確認中"
                    : `${decisionSummary.overlappingOutputKeys.length} / ${decisionSummary.assessableOutputKeys.length}特性`}
              {pendingPreviewCount > 0 && <button type="button" className="decision-summary-link" disabled={loadingRemainingPreviews} onClick={onLoadRemainingPreviews}>{loadingRemainingPreviews ? "計算中…" : "残りを計算"}</button>}
            </dd>
          </div>
        </dl>
        {uniformSupportMessage && <p className="uniform-support-message">{uniformSupportMessage}</p>}
        <p>
          {pendingPreviewCount > 0
            ? `${pendingPreviewCount}候補はまだ予測を計算していません。すべて計算すると、目標達成率と予測区間の重なりを全候補で比較できます。`
            : configuredTargetCount === 0
              ? allOutputsBinary
                ? "許容する異常確率を設定すると、候補ごとの基準内・基準外を比較できます。"
                : "目標値が未設定です。目標を設定すると、達成確率を比較できます。"
              : uncertaintyMessage}
        </p>
      </aside>}
      {context === "comparison" && <details className="selected-candidate-reading-view">
        <summary>選択候補を1件ずつ読む</summary>
        {selectedCandidate && (
          <div
            className="selected-candidate-reading-content"
            role="region"
            aria-labelledby="selected-candidate-reading-heading"
          >
            <h3 id="selected-candidate-reading-heading">{selectedCandidate.label}</h3>
            <section>
              <h4>入力条件</h4>
              <dl>
                {readingInputFields.map((field) => {
                  const value = getCandidateInputValue(selectedCandidate.raw.inputs, field.path);
                  const formatted = typeof value === "number"
                    ? formatInputNumber(value, taskDefinition, field.path, displayDecimalOverrides)
                    : String(value ?? "未設定");
                  return (
                    <div key={field.path}>
                      <dt>{field.label}</dt>
                      <dd>{formatted}{field.unit ? ` ${field.unit}` : ""}</dd>
                    </div>
                  );
                })}
                {[...taskDefinition.fixed_context].sort((left, right) => left.order - right.order).map((item) => (
                  <div key={item.path}>
                    <dt>{item.label}</dt>
                    <dd>{String(item.value)}</dd>
                  </div>
                ))}
              </dl>
              {selectedCandidate.heat.length > 0 && (
                <div className="selected-candidate-heat-pattern">
                  <h5>ヒートパターン</h5>
                  <p>時間基準：{selectedCandidate.heatTimeBasis === "line_speed" ? "ライン速度連動" : "経過時間"}</p>
                  <ol>
                    {selectedCandidate.heat.map((point, index) => (
                      <li key={`${point.time}:${point.temperature}:${index}`}>
                        <b>{point.stageName?.trim() || `点${index + 1}`}</b>
                        <span>{formatNumber(point.time)}分、{formatNumber(point.temperature)} °C{point.segmentStart ? "（区間開始）" : ""}</span>
                      </li>
                    ))}
                  </ol>
                </div>
              )}
            </section>
            <section>
              <h4>予測・支持範囲・目標達成</h4>
              <div className="selected-candidate-output-list">
                {outputs.map((output) => {
                  const prediction = previewsByCandidate[selectedCandidate.id]?.predictions[output.key];
                  const targetSupport = previewsByCandidate[selectedCandidate.id]?.model_support?.[output.key];
                  const goal = targetValues[output.key];
                  const predictionUnit = prediction && ["continuous", "continuous_positive", "count"].includes(prediction.target_kind) && prediction.unit
                    ? ` ${prediction.unit}`
                    : "";
                  let goalStatus = "未計算のため判定なし";
                  if (prediction?.target_kind === "binary") {
                    const threshold = typeof goal === "number" ? goal : undefined;
                    const meetsGoal = threshold == null
                      ? null
                      : output.goal_direction === "at_most"
                        ? prediction.value <= threshold
                        : prediction.value >= threshold;
                    goalStatus = meetsGoal == null
                      ? "基準未設定"
                      : `${meetsGoal ? "基準内" : "基準外"}（基準 ${formatNumber(threshold! * 100)}%）`;
                  } else if (prediction) {
                    const configured = prediction.goal_direction === "between"
                      ? prediction.goal_lower != null && prediction.goal_upper != null
                      : prediction.goal_value != null || hasValidTargetGoal(goal);
                    goalStatus = prediction.goal_probability == null
                      ? configured ? "達成率なし" : "目標未設定"
                      : `達成確率 ${formatNumber(prediction.goal_probability * 100)}%`;
                  }
                  return (
                    <article key={output.key}>
                      <h5>{output.label}</h5>
                      <dl>
                        <div>
                          <dt>予測</dt>
                          <dd>{prediction ? `${predictionValue(prediction, output.key)}${predictionUnit}` : "未計算"}</dd>
                        </div>
                        <div>
                          <dt>予測区間</dt>
                          <dd>{prediction && predictionHasInterval(prediction)
                            ? <>{uncertaintySummary(prediction, output.key).label}: {uncertaintySummary(prediction, output.key).interval}{predictionUnit}
                              {prediction.interval_method === "conformal" && <small>校正 {prediction.interval_calibration_sample_count ?? "不明"}件{prediction.interval_calibration_dataset_digest ? `・${prediction.interval_calibration_dataset_digest.slice(0, 12)}…` : ""}</small>}
                            </>
                            : "なし"}</dd>
                        </div>
                        <div>
                          <dt>支持範囲</dt>
                          <dd>{support(targetSupport?.status)}{prediction?.interval_method === "conformal" && targetSupport?.status !== "supported" && <small>学習支持範囲外では被覆率の解釈は弱まります</small>}</dd>
                        </div>
                        <div>
                          <dt>目標達成</dt>
                          <dd>{goalStatus}</dd>
                        </div>
                      </dl>
                    </article>
                  );
                })}
              </div>
            </section>
          </div>
        )}
      </details>}
      <div className="comparison-grid-viewport">
        <section
          ref={comparisonGridRef}
          className="comparison-grid"
          aria-label={context === "explore" ? "探索の基準候補と入力・予測" : "候補の入力と予測結果比較"}
          style={{
            "--comparison-input-share": `${effectiveInputShare}%`,
            "--comparison-table-height": `${comparisonHeight}px`,
          } as CSSProperties}
        >
          <div ref={nameScrollRef} className="comparison-pane-scroll comparison-name-scroll" onScroll={syncVerticalScroll}><table className="candidate-name-table" aria-label="候補名"><colgroup><col className="candidate-select-column" /><col />{context === "comparison" && <col className="candidate-reference-column" />}</colgroup><thead><tr><th scope="colgroup" colSpan={2}>{context === "explore" ? "探索の基準" : "候補"}</th>{context === "comparison" && <th scope="col">基準</th>}</tr></thead><tbody>{candidates.map((candidate) => { const selected = candidate.id === selectedId; const reference = context === "comparison" && candidate.id === referenceCandidateId; return <tr key={candidate.id} data-candidate-id={candidate.id} className={`${selected ? "selected-row" : ""}${reference ? " comparison-reference-row" : ""}`} onClick={() => onSelect(candidate.id)}><td className="candidate-select-cell"><button type="button" className="candidate-select-button" aria-label={`${candidate.label}を選択`} aria-pressed={selected} onClick={(event) => { event.stopPropagation(); onSelect(candidate.id); }}><span aria-hidden="true" /></button></td><th scope="row"><input disabled={!editingEnabled} aria-label={`${candidate.label}の候補名`} maxLength={80} value={candidate.label} onFocus={() => onSelect(candidate.id)} onChange={(event) => onName(candidate.id, event.target.value)} /></th>{context === "comparison" && <td className="candidate-reference-cell"><input type="checkbox" aria-label={`${candidate.label}を比較の基準にする`} checked={reference} onClick={(event) => event.stopPropagation()} onChange={() => toggleReferenceCandidate(candidate.id)} /></td>}</tr>; })}</tbody></table></div>
          <div id="comparison-input-pane" ref={inputScrollRef} className="comparison-pane-scroll comparison-input-scroll" tabIndex={0} aria-label="入力条件" onScroll={syncVerticalScroll}><table className="comparison-detail-table comparison-input-table" aria-label="候補ごとの入力条件"><thead><tr><th scope="col" className="comparison-row-header">候補</th>{inputGroups.map((group) => <th scope="colgroup" colSpan={group.fields.length} key={group.key}>{group.label}</th>)}</tr><tr><th scope="col" className="comparison-row-header">候補</th>{inputFields.map((field) => <th scope="col" className="composition-col" key={field.path}>{field.label}<small>{field.unit ?? ""}</small></th>)}</tr></thead><tbody>{candidates.map((candidate) => <tr key={candidate.id} data-candidate-id={candidate.id} className={candidate.id === selectedId ? "selected-row" : ""} onClick={() => onSelect(candidate.id)}><th scope="row" className="comparison-row-header">{candidate.label}</th>{inputFields.map((field) => renderField(candidate, field))}</tr>)}</tbody></table></div>
          <ComparisonSplitResizer value={effectiveInputShare} min={inputShareRange.min} max={inputShareRange.max} onChange={setInputShare} onDrag={(startValue, deltaX) => startValue + (deltaX / Math.max(comparisonGridRef.current?.clientWidth ?? 1, 1)) * 100} onReset={() => setInputShare(34)} />
          <div id="comparison-prediction-pane" ref={predictionScrollRef} className="comparison-pane-scroll comparison-prediction-scroll" tabIndex={0} aria-label="予測値" onScroll={syncVerticalScroll}>
            <table className="comparison-detail-table comparison-prediction-table" aria-label="候補ごとの予測値">
              <thead>
                <tr>
                  <th scope="col" className="comparison-row-header">候補</th>
                  <th scope="colgroup" colSpan={outputs.length}>予測・判断</th>
                  <th scope="col" className="support-header">適用範囲</th>
                </tr>
                <tr>
                  <th scope="col" className="comparison-row-header">候補</th>
                  {outputs.map((output) => <th scope="col" className="decision-output-col" key={output.key}>{output.label}<small>{outputTargetKind(output.key) === "binary" ? "異常確率" : output.unit}</small><em className="goal-direction">{goalDirectionLabel(output.goal_direction)}</em></th>)}
                  <th scope="col" className="support-header">入力条件</th>
                </tr>
              </thead>
              <tbody>
                {candidates.map((candidate) => {
                  const preview = previewsByCandidate[candidate.id];
                  return <tr key={candidate.id} data-candidate-id={candidate.id} className={candidate.id === selectedId ? "selected-row" : ""} onClick={() => onSelect(candidate.id)}>
                    <th scope="row" className="comparison-row-header">{candidate.label}</th>
                    {outputs.map((output) => {
                      const prediction = preview?.predictions[output.key];
                      const goal = targetValues[output.key];
                      if (!prediction) return <td className="decision-output-cell numeric-cell" key={output.key}><span className="empty-cell" title={`${candidate.label}の${output.label}はまだ計算していません`}>未計算</span></td>;
                      const value = predictionValue(prediction, output.key);
                      const accessibleValue = prediction.target_kind === "binary" ? value : `${value} ${prediction.unit}`.trim();
                      const pointAssessment = assessOutputValues(output, [prediction.value], "予測値");
                      const intervalValues = [prediction.lower, prediction.upper, ...Object.values(prediction.quantiles ?? {})];
                      const intervalAssessment = assessOutputValues(output, intervalValues, "予測区間");
                      const uncertainty = uncertaintySummary(prediction, output.key);
                      let goalContent: ReactNode;
                      if (prediction.target_kind === "binary") {
                        const threshold = typeof goal === "number" ? goal : undefined;
                        const meetsGoal = threshold == null
                          ? null
                          : output.goal_direction === "at_most"
                            ? prediction.value <= threshold
                            : prediction.value >= threshold;
                        goalContent = meetsGoal == null
                          ? <span className="decision-goal empty-cell">基準未設定</span>
                          : <span className={`decision-goal ${meetsGoal ? "is-ready" : "needs-attention"}`}>{meetsGoal ? "基準内" : "基準外"} · {formatNumber(threshold! * 100)}%</span>;
                      } else {
                        const configured = prediction.goal_direction === "between" ? prediction.goal_lower != null && prediction.goal_upper != null : prediction.goal_value != null || hasValidTargetGoal(goal);
                        goalContent = prediction.goal_probability == null
                          ? <span className="decision-goal empty-cell">{configured ? "達成率なし" : "目標未設定"}</span>
                          : <span className="decision-goal">達成確率 {formatNumber(prediction.goal_probability * 100)}%</span>;
                      }
                      return <td className={`decision-output-cell numeric-cell${pointAssessment.implausible ? " implausible-output" : ""}`} key={output.key}>
                        <span className="decision-prediction" title={pointAssessment.warning ?? accessibleValue}>{value}{pointAssessment.implausible && <small>⚠ 物理範囲外</small>}</span>
                        {predictionHasInterval(prediction)
                          ? <span className={`decision-interval${intervalAssessment.implausible ? " implausible-output" : ""}`} title={intervalAssessment.warning ?? uncertainty.details}>{uncertainty.label} {uncertainty.interval}{intervalAssessment.implausible && <small>⚠ 範囲外含む</small>}</span>
                          : <span className="decision-interval empty-cell">区間なし</span>}
                        {goalContent}
                      </td>;
                    })}
                    <td className="support-cell">
                      <div className="target-support-list">
                        {outputs.map((output) => {
                          const targetSupport = preview?.model_support?.[output.key];
                          return <span key={output.key} title={targetSupport?.message}><b>{outputs.length > 1 ? output.label : ""}</b><i className={`status-dot ${targetSupport?.status === "supported" ? "success" : targetSupport ? "caution" : ""}`} />{support(targetSupport?.status)}</span>;
                        })}
                      </div>
                    </td>
                  </tr>;
                })}
              </tbody>
            </table>
          </div>
          <div ref={actionScrollRef} className="comparison-pane-scroll comparison-action-scroll" aria-label="候補ごとの操作" onScroll={syncVerticalScroll}>
            <table className="comparison-action-table">
              <thead><tr><th scope="col" className="comparison-row-header">候補</th><th scope="col">操作</th></tr><tr><th scope="col" className="comparison-row-header">候補</th><th scope="col"><small>候補ごと</small></th></tr></thead>
              <tbody>{candidates.map((candidate) => {
                const saving = savingCandidateIds.includes(candidate.id);
                const editableSaved = ["idle", "saved"].includes(saveStates[candidate.id] ?? "idle");
                const saved = editableSaved && (savedRevisionsByCandidate[candidate.id]?.includes(candidate.raw.revision) ?? false);
                const historyPending = snapshotHistoryState !== "ready";
                const deleteBlocked = candidates.length <= 1 || decisionCandidateId === candidate.id;
                const confirmingDelete = pendingDeleteCandidateId === candidate.id;
                return <tr key={candidate.id} data-candidate-id={candidate.id} className={candidate.id === selectedId ? "selected-row" : ""} onClick={() => onSelect(candidate.id)}><th scope="row" className="comparison-row-header">{candidate.label}</th><td>
                  {confirmingDelete
                    ? <div className="candidate-delete-confirmation" role="group" aria-label={`${candidate.label}を一覧から外す確認`} onClick={(event) => event.stopPropagation()}>
                      <span className="candidate-delete-confirmation-message">{candidateRemovalConfirmationText(candidate.label)}</span>
                      <div>
                        <span className="candidate-delete-recovery-hint" role="img" aria-label="後で復元できます" title="後でプロジェクト概要から復元できます">↩</span>
                        <button type="button" className="danger-button" aria-label="一覧から外す" title="一覧から外す" autoFocus onClick={() => { setPendingDeleteCandidateId(""); onDelete(candidate.id); }}>外す</button>
                        <button type="button" className="outline-button" aria-label="キャンセル" title="キャンセル" onClick={() => {
                          setPendingDeleteCandidateId("");
                          window.requestAnimationFrame(() => {
                            actionScrollRef.current
                              ?.querySelector<HTMLButtonElement>(`tr[data-candidate-id="${candidate.id}"] .candidate-row-delete-button`)
                              ?.focus();
                          });
                        }}>戻る</button>
                      </div>
                    </div>
                    : <div className="candidate-row-actions">
                      <CandidateAddButton compact className="candidate-row-icon-button" aria-label={`${candidate.label}を複製`} title="この候補を複製" onClick={(event) => { event.stopPropagation(); onCopy(candidate.id); }} />
                      <button type="button" className="candidate-row-icon-button candidate-row-delete-button" aria-label={`${candidate.label}を一覧から外す`} title={decisionCandidateId === candidate.id ? "採用判断を解除してから一覧から外してください" : "この候補を一覧から外す"} disabled={deleteBlocked} onClick={(event) => { event.stopPropagation(); setPendingDeleteCandidateId(candidate.id); }}><RowActionIcon name="trash" /></button>
                      <button type="button" className={`candidate-row-save-button${saved ? " saved" : ""}`} aria-label={`${candidate.label}の詳細予測を${saved ? "保存済み" : snapshotHistoryState === "loading" ? "確認中" : snapshotHistoryState === "error" ? "履歴確認失敗" : "保存"}`} title={!detailedPredictionAvailable ? "このタスクでは詳細予測を利用できません" : snapshotHistoryState === "loading" ? "保存履歴を確認しています" : snapshotHistoryState === "error" ? "保存履歴を確認できませんでした" : saved ? "現在の編集版は保存済みです" : !editableSaved ? "入力の保存完了後に実行できます" : "詳細予測を保存"} disabled={!detailedPredictionAvailable || historyPending || !editableSaved || saving || saved} onClick={(event) => { event.stopPropagation(); onSave(candidate); }}><RowActionIcon name={saved ? "check" : "save"} /></button>
                    </div>}
                </td></tr>;
              })}</tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}
