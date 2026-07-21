import { type PointerEvent, useEffect, useRef, useState } from "react";
import { provenanceLabel, type CandidateProvenance } from "../../shared/candidateProvenance";
import {
  CandidateInspector,
  ComparisonTable,
  fromApiCandidate,
  numericTaskInputs,
  type CandidateSaveState,
  type CandidateViewModel as Candidate,
  type NumericRange,
  type NumericTaskInput,
  type RuntimeOperations,
  type TaskDefinitionContract,
  type TaskOutputDefinition,
} from "../candidates";
import { candidateInputIdentity } from "../../shared/api/inferenceRequestCache";
import { apiBaseUrl } from "../../shared/api/client";
import {
  workbenchApi,
  type ApiActual,
  type ApiProject,
  type ApiPredictionVsActual,
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
  type InferenceSurfaceStatus,
} from "./inferenceSurfaceState";

type Metric = {
  key: string;
  unit: string;
  value: number;
  low: number;
  high: number;
  status: string;
  goalValue?: number | null;
  goalProbability?: number | null;
  modelStd?: number | null;
  observationStd?: number | null;
};

type CurvePoint = ApiResponseCurve["points"][number];
type CurveVariable = ApiResponseCurve["variable"];
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

function Icon({ name }: { name: "copy" | "trash" | "plus" }) {
  const common = {
    width: 18,
    height: 18,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.75,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  const paths = {
    copy: <><rect x="8" y="8" width="12" height="12" rx="1" /><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3" /></>,
    trash: <><path d="M4 7h16M10 11v6m4-6v6M9 7l1-3h4l1 3m-9 0 1 13h10l1-13" /></>,
    plus: <><circle cx="12" cy="12" r="9" /><path d="M12 8v8m-4-4h8" /></>,
  };
  return <svg {...common}>{paths[name]}</svg>;
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
        <button className="primary-button" onClick={onCreate}>
          <Icon name="plus" />
          最初の候補を作る
        </button>
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
  fieldErrors: Array<{ path: string; message: string }>;
  onReload: () => void;
  onCopyDraft: () => void;
  metrics: Metric[];
  preview: ApiPreview | null;
  previewStatus: InferenceSurfaceStatus;
  previewError: string;
  onRetryPreview: () => void;
  previewsByCandidate: Record<string, ApiPreview>;
  onSelect: (id: string) => void;
  onHeat: (index: number, field: "time" | "temperature" | "stageName", raw: number | string) => void;
  onInput: (id: string, path: string, value: number | string) => void;
  onText: (id: string, field: "label", value: string) => void;
  onAddHeat: () => void;
  onDeleteHeat: (index: number) => void;
  onCopy: () => void;
  onOpenOrigin: () => void;
  originBroken: boolean;
  onDelete: () => void;
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
    fieldErrors,
    onReload,
    onCopyDraft,
    metrics,
    preview,
    previewStatus,
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
    onAdd,
    onAddCandidateFromLineage,
    onImported,
    onProjectChanged,
  } = props;
  const [comparisonExpanded, setComparisonExpanded] = useState(false);
  useEffect(() => {
    if (candidates.length <= 5) setComparisonExpanded(false);
  }, [candidates.length]);
  return (
    <div className="workbench-grid candidate-workbench-grid">
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
          <div className="comparison-actions" aria-label="候補操作">
            <button className="outline-button" onClick={onCopy}>
              <Icon name="copy" />選択候補を複製
            </button>
            <button
              className="outline-button"
              onClick={onDelete}
              disabled={
                candidates.length <= 1 || decisionCandidateId === selectedId
              }
              title={
                decisionCandidateId === selectedId
                  ? "採用判断を解除してから削除してください"
                  : undefined
              }
            >
              <Icon name="trash" />削除
            </button>
            <CandidateFileControls projectId={projectId} onImported={onImported} />
            <button className="primary-button" onClick={onAdd}>
              <Icon name="plus" />候補を追加
            </button>
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
          onSelect={onSelect}
          onInput={onInput}
          onName={(id, value) => onText(id, "label", value)}
        />}
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
        <LiveSimilarityEvidence projectId={projectId} candidate={selected} available={operations?.similarity === true} ready={["idle", "saved"].includes(saveState)} onAddCandidate={onAddCandidateFromLineage} />
      </section>
      <EvidencePanel projectId={projectId} candidate={selected} inferenceReady={["idle", "saved"].includes(saveState)} metrics={metrics} outputs={taskDefinition?.outputs ?? []} preview={preview} previewStatus={previewStatus} candidateLabel={selected.label} actualsAvailable={operations?.actual_measurement === true} error={previewError} onRetry={onRetryPreview} />
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
        結果をXLSX出力
      </button>
      {message && <small>{message}</small>}
    </div>
  );
}

function UnavailablePanel({ title }: { title: string }) {
  return <section className="actuals-panel unavailable-panel" aria-label={`${title}は利用できません`}><div className="panel-title"><h2>{title}</h2></div><p className="empty-evidence">このタスクでは利用できません。</p></section>;
}

function ActualsPanel({ projectId, candidate, outputs, enabled }: { projectId: string; candidate: Candidate; outputs: TaskOutputDefinition[]; enabled: boolean }) {
  const [property, setProperty] = useState<ApiActual["property"]>((outputs[0]?.key ?? "TS") as ApiActual["property"]);
  const [mean, setMean] = useState("");
  const [std, setStd] = useState("0");
  const [replicates, setReplicates] = useState("1");
  const [experimentNo, setExperimentNo] = useState("");
  const [measuredAt, setMeasuredAt] = useState("");
  const [note, setNote] = useState("");
  const [comparison, setComparison] = useState<ApiPredictionVsActual | null>(null);
  const [error, setError] = useState("");
  const identity = `${projectId}\u001f${candidate.id}\u001f${candidate.raw.revision}`;
  const identityRef = useRef(identity);
  identityRef.current = identity;
  const refresh = async (signal?: AbortSignal, expectedIdentity = identity) => {
    try {
      const result = await workbenchApi.predictionVsActual(projectId, candidate.id, signal);
      if (!signal?.aborted && identityRef.current === expectedIdentity) setComparison(result);
    } catch {
      if (signal?.aborted || identityRef.current !== expectedIdentity) return;
      setError("実測値を取得できませんでした。");
    }
  };
  useEffect(() => {
    const controller = new AbortController();
    setComparison(null);
    setError("");
    if (!outputs.some((output) => output.key === property)) setProperty((outputs[0]?.key ?? "TS") as ApiActual["property"]);
    void refresh(controller.signal);
    return () => controller.abort();
  }, [candidate.id, candidate.raw.revision, projectId]);
  useEffect(() => {
    setMean("");
    setStd("0");
    setReplicates("1");
    setExperimentNo("");
    setMeasuredAt("");
    setNote("");
  }, [candidate.id, projectId]);
  const add = async () => {
    if (!enabled) return setError("候補の保存完了後に実測を登録してください。");
    if (mean.trim() === "") return setError("実測平均を入力してください。");
    const expectedIdentity = identity;
    try {
      setError("");
      await workbenchApi.createActual(projectId, candidate.id, candidate.raw.revision, {
        property,
        mean: Number(mean),
        std: Number(std),
        replicates: Number(replicates),
        unit: (outputs.find((output) => output.key === property)?.unit ?? "%") as "MPa" | "%",
        experiment_no: experimentNo.trim(),
        measured_at: measuredAt || null,
        note: note.trim(),
      });
      if (identityRef.current !== expectedIdentity) return;
      setMean("");
      setExperimentNo("");
      setMeasuredAt("");
      setNote("");
      await refresh(undefined, expectedIdentity);
    } catch {
      if (identityRef.current !== expectedIdentity) return;
      setError("実測値を保存できませんでした。");
    }
  };
  const remove = async (id: string) => {
    const expectedIdentity = identity;
    try {
      await workbenchApi.deleteActual(projectId, candidate.id, id);
      await refresh(undefined, expectedIdentity);
    } catch {
      if (identityRef.current !== expectedIdentity) return;
      setError("実測値を削除できませんでした。");
    }
  };
  const rows = comparison?.comparisons ?? [];
  return (
    <section className="actuals-panel">
      <div className="panel-title">
        <h2>予測と実測</h2>
        <span>
          {rows.length
            ? "登録時点の予測スナップショットと比較"
            : "実測を登録すると予測を固定保存します"}
        </span>
      </div>
      <div className="actual-form">
        <select
          aria-label="実測特性"
          disabled={!enabled}
          value={property}
          onChange={(e) => setProperty(e.target.value as ApiActual["property"])}
        >
          {outputs.map((output) => <option key={output.key} value={output.key}>{output.label}</option>)}
        </select>
        <input
          aria-label="実測平均"
          disabled={!enabled}
          type="number"
          placeholder="実測平均"
          value={mean}
          onChange={(e) => setMean(e.target.value)}
        />
        <input
          aria-label="標準偏差"
          disabled={!enabled}
          type="number"
          min="0"
          placeholder="標準偏差"
          value={std}
          onChange={(e) => setStd(e.target.value)}
        />
        <input
          aria-label="反復数"
          disabled={!enabled}
          type="number"
          min="1"
          placeholder="反復数"
          value={replicates}
          onChange={(e) => setReplicates(e.target.value)}
        />
        <button
          className="outline-button"
          disabled={!enabled}
          onClick={() => {
            void add();
          }}
        >
          実測を追加
        </button>
      </div>
      <details className="actual-meta-fields">
        <summary>実験情報を追加</summary>
        <div>
          <label>
            実験番号
            <input
              value={experimentNo}
              onChange={(e) => setExperimentNo(e.target.value)}
              placeholder="例: EXP-2026-014"
            />
          </label>
          <label>
            測定日
            <input
              type="date"
              value={measuredAt}
              onChange={(e) => setMeasuredAt(e.target.value)}
            />
          </label>
          <label>
            メモ
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="試験片・測定条件など"
            />
          </label>
        </div>
      </details>
      {error && <p className="empty-evidence">{error}</p>}
      <div className="actual-table-wrap">
        <table className="quality-table actual-table">
        <thead>
          <tr>
            <th>特性 / 実験</th>
            <th>固定予測</th>
            <th>実測平均 ± SD</th>
            <th>差（実測−予測）</th>
            <th>予測区間</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const prediction = row.prediction.predictions[row.actual.property];
            const output = outputs.find((item) => item.key === row.actual.property);
            const delta = row.actual.mean - prediction.value;
            const inside =
              row.actual.mean >= prediction.lower &&
              row.actual.mean <= prediction.upper;
            return (
              <tr key={row.actual.id}>
                <td>
                  <b>{output?.label ?? row.actual.property} <small>({output?.unit ?? row.actual.unit})</small></b>
                  {(row.actual.experiment_no ||
                    row.actual.measured_at ||
                    row.actual.note) && (
                    <small className="actual-meta">
                      {[
                        row.actual.experiment_no,
                        row.actual.measured_at,
                        row.actual.note,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </small>
                  )}
                </td>
                <td>
                  {number(prediction.value, 1)} {prediction.unit}
                </td>
                <td>
                  {number(row.actual.mean, 1)} ± {number(row.actual.std, 1)}{" "}
                  {row.actual.unit}
                  <small> n={row.actual.replicates}</small>
                </td>
                <td className={Math.abs(delta) > 0 ? "metric-value" : ""}>
                  {delta >= 0 ? "+" : ""}
                  {number(delta, 1)}
                </td>
                <td>
                  <span
                    className={`status-dot ${inside ? "success" : "caution"}`}
                  />
                  {inside ? "区間内" : "区間外"}
                  <small>
                    {" "}
                    [{number(prediction.lower, 1)}–{number(prediction.upper, 1)}
                    ]
                  </small>
                </td>
                <td>
                  <button
                    className="icon-delete"
                    aria-label={`${output?.label ?? row.actual.property}の実測を削除`}
                    onClick={() => {
                      void remove(row.actual.id);
                    }}
                  >
                    ×
                  </button>
                </td>
              </tr>
            );
          })}
          {!rows.length && (
            <tr>
              <td colSpan={6} className="empty-evidence">
                実測はまだありません。
              </td>
            </tr>
          )}
        </tbody>
        </table>
      </div>
    </section>
  );
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
            <polyline
              key={item.id}
              points={item.heat
                .map((point) => `${x(point.time)},${y(point.temperature)}`)
                .join(" ")}
              fill="none"
              stroke={candidateColor(item.id, candidate.id)}
              strokeWidth="1.5"
              opacity=".62"
            />
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
  const variables: CurveVariable[] = [
    ...numericTaskInputs(taskDefinition)
      .filter((input) => input.editable)
      .map((input) => {
        const range = allowedRange(input);
        return {
          id: input.group === "composition" ? `${input.group}.${input.field}` : input.field,
          label: input.label,
          unit: input.unit,
          min: range.min,
          max: range.max,
          current: input.group === "composition" ? candidate.raw.inputs.composition[input.field] ?? 0 : candidate.raw.inputs.process[input.field] ?? 0,
        };
      }),
    ...candidate.heat.flatMap((point, index) => [
      { id: `heat.${index}.temperature_c`, label: `ヒート ${index + 1}点目 温度`, unit: "°C", min: 0, max: 1000, current: point.temperature },
      { id: `heat.${index}.time_min`, label: `ヒート ${index + 1}点目 時間`, unit: "min", min: 0, max: Math.max(1, candidate.heat.at(-1)?.time ?? 1), current: point.time },
    ]),
  ];
  const [variableId, setVariableId] = useState(variables[0]?.id ?? "heat.peak_temperature_c");
  const [axisSettingsOpen, setAxisSettingsOpen] = useState(false);
  const [axisDraft, setAxisDraft] = useState<{ x: CurveRangeDraft; y: Record<string, CurveRangeDraft> }>({ x: { min: "", max: "", enabled: false }, y: {} });
  const [axisError, setAxisError] = useState("");
  const [axisSaving, setAxisSaving] = useState(false);
  const [surfacesByKey, setSurfacesByKey] = useState<Record<string, InferenceSurfaceState<ApiResponseCurve>>>({});
  const surfaceRef = useRef(surfacesByKey);
  surfaceRef.current = surfacesByKey;
  const curveCandidates = candidates.filter((item) => !item.raw.archived_at && previewsByCandidate[item.id]);
  const curveCandidatesKey = curveCandidates.map((item) => `${item.id}:${item.raw.revision}:${candidateInputIdentity(item.raw.inputs)}`).join("\u001e");
  const outputKeys = outputs.map((output) => output.key).join("\u001e");
  const xRangeOverride = responseCurveRanges.x?.[variableId];
  const xRangeIdentity = xRangeOverride ? `${xRangeOverride.min}:${xRangeOverride.max}` : "auto";
  useEffect(() => {
    if (variables.length && !variables.some((variable) => variable.id === variableId)) setVariableId(variables[0].id);
  }, [variableId, variables.length]);
  useEffect(() => {
    if (!available || !ready || !taskDefinition || !curveCandidates.length || !outputs.length) return;
    const controller = new AbortController();
    const timers: number[] = [];
    for (const item of curveCandidates) {
      const inputIdentity = candidateInputIdentity(item.raw.inputs);
      for (const output of outputs) {
        const storageKey = `${item.id}\u001f${output.key}\u001f${variableId}\u001f${xRangeIdentity}\u001f${inputIdentity}`;
        const identity = `${workbenchRequestKey({ projectId, taskId: taskDefinition.id, candidateId: item.id, candidateRevision: item.raw.revision }, "response_curve:9")}\u001f${inputIdentity}\u001f${output.key}\u001f${variableId}\u001f${xRangeIdentity}`;
        const existing = surfaceRef.current[storageKey];
        if (existing?.currentIdentity === identity) continue;
        const requested = requestInferenceSurface(existing ?? emptyInferenceSurface<ApiResponseCurve>(), identity);
        const requestedSurfaces = { ...surfaceRef.current, [storageKey]: requested };
        surfaceRef.current = requestedSurfaces;
        setSurfacesByKey(requestedSurfaces);
        const timer = window.setTimeout(async () => {
          try {
            const loaded = await workbenchApi.responseCurve(projectId, item.id, item.raw.revision, inputIdentity, output.key, variableId, 9, xRangeOverride?.min, xRangeOverride?.max, controller.signal);
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
  }, [available, ready, curveCandidatesKey, outputKeys, projectId, taskDefinition?.id, variableId, xRangeIdentity, xRangeOverride?.min, xRangeOverride?.max]);
  const selectedVariable = variables.find((variable) => variable.id === variableId) ?? variables[0];
  const curveStates = curveCandidates.flatMap((item) => outputs.map((output) => {
    const inputIdentity = candidateInputIdentity(item.raw.inputs);
    const storageKey = `${item.id}\u001f${output.key}\u001f${variableId}\u001f${xRangeIdentity}\u001f${inputIdentity}`;
    return surfacesByKey[storageKey];
  }));
  const payloadForOutput = (outputKey: string) => curveCandidates.map((item) => {
    const inputIdentity = candidateInputIdentity(item.raw.inputs);
    return surfacesByKey[`${item.id}\u001f${outputKey}\u001f${variableId}\u001f${xRangeIdentity}\u001f${inputIdentity}`]?.data;
  }).find((payload): payload is ApiResponseCurve => Boolean(payload));
  const selectedPayload = outputs.map((output) => payloadForOutput(output.key)).find((payload): payload is ApiResponseCurve => Boolean(payload));
  const effectiveXRange = selectedPayload?.variable ? { min: selectedPayload.variable.min, max: selectedPayload.variable.max } : selectedVariable ? { min: selectedVariable.min, max: selectedVariable.max } : null;
  const makeDraft = (saved: CurveRange | undefined, effective: CurveRange | null | undefined): CurveRangeDraft => ({
    min: String(saved?.min ?? effective?.min ?? ""),
    max: String(saved?.max ?? effective?.max ?? ""),
    enabled: Boolean(saved),
  });
  const openAxisSettings = () => {
    setAxisDraft({
      x: makeDraft(xRangeOverride, effectiveXRange),
      y: Object.fromEntries(outputs.map((output) => [output.key, makeDraft(responseCurveRanges.y?.[output.key], payloadForOutput(output.key)?.output_range)])),
    });
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
  const saveAxisSettings = async () => {
    if (!project) return;
    try {
      const nextX = { ...(responseCurveRanges.x ?? {}) };
      const parsedX = draftRange(axisDraft.x, "X軸");
      if (parsedX) nextX[variableId] = parsedX;
      else delete nextX[variableId];
      const nextY = { ...(responseCurveRanges.y ?? {}) };
      for (const output of outputs) {
        const parsedY = draftRange(axisDraft.y[output.key] ?? { min: "", max: "", enabled: false }, `${output.label}のY軸`);
        if (parsedY) nextY[output.key] = parsedY;
        else delete nextY[output.key];
      }
      setAxisSaving(true);
      const updated = await workbenchApi.updateProject(projectId, { ...project, response_curve_ranges: { x: nextX, y: nextY } });
      await onProjectChanged(updated);
      setAxisError("");
      setAxisSettingsOpen(false);
    } catch (cause) {
      setAxisError(cause instanceof Error ? cause.message : "軸範囲を保存できませんでした。");
    } finally {
      setAxisSaving(false);
    }
  };
  const rangeText = (range: CurveRange | null | undefined) => range ? `${number(range.min, 2)} – ${number(range.max, 2)}` : "取得中";
  const setXDraft = (patch: Partial<CurveRangeDraft>) => setAxisDraft((current) => ({ ...current, x: { ...current.x, ...patch } }));
  const setYDraft = (key: string, patch: Partial<CurveRangeDraft>) => setAxisDraft((current) => ({ ...current, y: { ...current.y, [key]: { ...(current.y[key] ?? { min: "", max: "", enabled: false }), ...patch } } }));
  const loadedCurveCount = curveStates.filter((state) => state?.data !== null && state?.data !== undefined).length;
  const curveStatus = curveStates.some((state) => state?.error) ? "error" : curveStates.some((state) => state?.pending) || loadedCurveCount < curveStates.length ? "refreshing" : "latest";
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
          <label>変数 <select aria-label="応答曲線の設計変数" value={variableId} onChange={(event) => setVariableId(event.target.value)}>{variables.map((variable) => <option key={variable.id} value={variable.id}>{variable.label} ({variable.unit})</option>)}</select></label>
          <button type="button" className="outline-button curve-range-button" aria-expanded={axisSettingsOpen} onClick={axisSettingsOpen ? () => setAxisSettingsOpen(false) : openAxisSettings}>{axisSettingsOpen ? "閉じる" : "軸範囲"}</button>
        </div>
      </div>
      {axisSettingsOpen && (
        <div className="response-curve-axis-settings">
          <div className="axis-settings-heading"><b>描画範囲</b><small>未指定は自動範囲。学習データ範囲は参照値です。</small><button type="button" className="axis-settings-close" onClick={() => setAxisSettingsOpen(false)}>閉じる</button></div>
          <div className="axis-settings-grid">
            <section>
              <h3>X軸 <span>{selectedVariable?.label ?? "選択変数"}</span></h3>
              <div className="axis-range-fields">
                <label>最小<input type="number" value={axisDraft.x.min} onChange={(event) => setXDraft({ min: event.target.value, enabled: true })} /></label>
                <label>最大<input type="number" value={axisDraft.x.max} onChange={(event) => setXDraft({ max: event.target.value, enabled: true })} /></label>
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
                      <label>最小<input type="number" value={draft.min} onChange={(event) => setYDraft(output.key, { min: event.target.value, enabled: true })} /></label>
                      <label>最大<input type="number" value={draft.max} onChange={(event) => setYDraft(output.key, { max: event.target.value, enabled: true })} /></label>
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
            <button type="button" className="primary-button" disabled={axisSaving} onClick={() => void saveAxisSettings()}>{axisSaving ? "保存中…" : "保存"}</button>
            <button type="button" className="text-button" disabled={axisSaving} onClick={() => { setXDraft({ ...makeDraft(undefined, effectiveXRange) }); outputs.forEach((output) => setYDraft(output.key, { ...makeDraft(undefined, payloadForOutput(output.key)?.output_range) })); }}>すべて自動</button>
          </div>
        </div>
      )}
      {!ready ? <p className="empty-evidence">入力を保存後に更新します。</p> : curveStatus === "error" && loadedCurveCount === 0 ? <p className="empty-evidence">応答曲線を取得できません。</p> : (
        <div className={`response-curves-grid output-count-${Math.min(outputs.length, 4)}`}>
          {outputs.map((output) => {
            const curveSeries = curveCandidates.flatMap((item) => {
              const inputIdentity = candidateInputIdentity(item.raw.inputs);
              const storageKey = `${item.id}\u001f${output.key}\u001f${variableId}\u001f${xRangeIdentity}\u001f${inputIdentity}`;
              const payload = surfacesByKey[storageKey]?.data;
              if (!payload?.points.length) return [];
              return [{ candidate: item, points: payload.points, prediction: previewsByCandidate[item.id]?.predictions?.[output.key], currentX: payload.variable.current }];
            });
  const firstPayload = curveCandidates.map((item) => {
    const inputIdentity = candidateInputIdentity(item.raw.inputs);
    return surfacesByKey[`${item.id}\u001f${output.key}\u001f${variableId}\u001f${xRangeIdentity}\u001f${inputIdentity}`]?.data;
  }).find((payload): payload is ApiResponseCurve => Boolean(payload));
            return <ResponseCurveMiniChart key={output.key} output={output} series={curveSeries} selectedId={candidate.id} prediction={previewsByCandidate[candidate.id]?.predictions?.[output.key] ?? preview?.predictions?.[output.key]} goalValue={targetValues[output.key]} xRange={firstPayload?.variable ? { min: firstPayload.variable.min, max: firstPayload.variable.max } : selectedVariable ? { min: selectedVariable.min, max: selectedVariable.max } : undefined} yRange={responseCurveRanges.y?.[output.key] ?? firstPayload?.output_range ?? undefined} xLabel={firstPayload?.variable.label ?? selectedVariable?.label ?? "設計変数"} xUnit={firstPayload?.variable.unit ?? selectedVariable?.unit ?? ""} />;
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
  const outputAxisValues = yRange ? [yRange.min, yRange.max] : points.flatMap((point) => [point.lower, point.upper]);
  const rawMin = Math.min(...outputAxisValues, goalValue ?? Infinity);
  const rawMax = Math.max(...outputAxisValues, goalValue ?? -Infinity);
  const valuePadding = Math.max(1, (rawMax - rawMin) * 0.08);
  const minValue = rawMin - valuePadding;
  const maxValue = rawMax + valuePadding;
  const x = (value: number) => 30 + ((value - minX) / Math.max(1e-6, maxX - minX)) * 252;
  const y = (value: number) => 124 - ((value - minValue) / Math.max(1, maxValue - minValue)) * 92;
  const xTicks = [minX, (minX + maxX) / 2, maxX];
  return (
    <article className="response-curve-card">
      <header><b>{output.label}</b><span>{prediction ? `${number(prediction.value, output.key === "EL" || output.key === "lambda" ? 1 : 0)} ${prediction.unit}` : "読み込み中"}</span></header>
      {series.length ? <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${output.label}の応答曲線`}>
        {[minValue, (minValue + maxValue) / 2, maxValue].map((tick) => <g key={tick}><line x1="28" y1={y(tick)} x2="284" y2={y(tick)} stroke="#e3e9f0" /><text x="25" y={y(tick) + 3} textAnchor="end" fontSize="9" fill="#617087">{number(tick, output.key === "EL" || output.key === "lambda" ? 1 : 0)}</text></g>)}
        {series.map((item) => {
          const color = candidateColor(item.candidate.id, selectedId);
          const line = item.points.map((point, index) => `${index ? "L" : "M"}${x(point.x)} ${y(point.value)}`).join(" ");
          const band = `${item.points.map((point, index) => `${index ? "L" : "M"}${x(point.x)} ${y(point.upper)}`).join(" ")} ${[...item.points].reverse().map((point) => `L${x(point.x)} ${y(point.lower)}`).join(" ")} Z`;
          return <g key={item.candidate.id}><path d={band} fill={color} opacity={item.candidate.id === selectedId ? ".18" : ".08"} /><path d={line} fill="none" stroke={color} strokeWidth={item.candidate.id === selectedId ? "2.5" : "1.5"} opacity={item.candidate.id === selectedId ? "1" : ".78"} />{item.prediction && Number.isFinite(item.currentX) && <circle cx={x(item.currentX)} cy={y(item.prediction.value)} r={item.candidate.id === selectedId ? "4" : "2.5"} fill="#fff" stroke={color} strokeWidth={item.candidate.id === selectedId ? "2.5" : "1.5"} />}</g>;
        })}
        {Number.isFinite(goalValue) && <line x1="28" y1={y(goalValue!)} x2="284" y2={y(goalValue!)} stroke="#c17816" strokeDasharray="4 3" />}
        {xTicks.map((tick) => <text key={tick} x={x(tick)} y="137" textAnchor="middle" fontSize="8" fill="#617087">{number(tick, xUnit === "min" ? 2 : 1)}</text>)}
        <text x="156" y="153" textAnchor="middle" fontSize="8" fill="#617087">{xLabel} ({xUnit})</text>
      </svg> : <p className="empty-evidence">読み込み中…</p>}
    </article>
  );
}

function LiveSimilarityEvidence({
  projectId,
  candidate,
  available,
  ready,
  onAddCandidate,
}: {
  projectId: string;
  candidate: Candidate;
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
          <h2>近い過去実績</h2>
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
          <table className="similar-table similar-summary-table">
            <thead><tr><th>距離</th><th>溶製成績書 key</th><th>{processLabel} key</th><th>実績値</th><th /></tr></thead>
            <tbody>{similar.map((item) => (
              <tr key={`${item.layer ?? "training"}-${item.parent_key}`}>
                <td className="similar-distance"><b>{item.distance.toFixed(2)}</b><span className={`layer-chip ${item.layer ?? "training"}`}>{item.layer === "historical" ? "学習外" : "学習内"}</span></td>
                <td className="similar-key">{item.melt_key ?? "—"}</td>
                <td className="similar-key">{item.process_key ?? item.parent_key}</td>
                <td><div className="similar-value-list"><small>{item.source || item.observation_id || "実績"}</small>{Object.entries(item.repeat_summary ?? {}).map(([key, value]) => <span key={key}><b>{key}</b> {number(value.mean, 1)} ± {number(value.std, 1)} <small>n={value.n}</small></span>)}</div></td>
                <td className="similar-action-cell">
                  <button type="button" className="outline-button similar-add-button" disabled={!item.process_key || addingKey === item.process_key || addedKeys.includes(item.process_key ?? "")} onClick={() => { if (item.process_key) void add(item.process_key); }}>
                    {addedKeys.includes(item.process_key ?? "") ? "追加済み" : addingKey === item.process_key ? "追加中…" : "候補に追加"}
                  </button>
                </td>
              </tr>
            ))}</tbody>
          </table>
        </>
      ) : status === "error" ? (
        <p className="empty-evidence">類似実験を取得できませんでした。閉じて再度開くと再試行します。</p>
      ) : (
        <p className="empty-evidence">類似実験を取得しています。</p>
      )}
    </section>
  );
}

function EvidencePanel({
  projectId,
  candidate,
  inferenceReady,
  metrics,
  outputs,
  preview,
  previewStatus,
  candidateLabel,
  actualsAvailable,
  error,
  onRetry,
}: {
  projectId: string;
  candidate: Candidate;
  inferenceReady: boolean;
  metrics: Metric[];
  outputs: TaskOutputDefinition[];
  preview: ApiPreview | null;
  previewStatus: InferenceSurfaceStatus;
  candidateLabel: string;
  actualsAvailable: boolean;
  error: string;
  onRetry: () => void;
}) {
  const status = preview?.support?.status;
  const training = preview?.model_meta?.training_data?.records;
  const warnings = (preview?.warnings ?? []).filter(
    (warning) => warning !== preview?.support?.message,
  );
  const outputForMetric = (key: string) => outputs.find((output) => output.key === key || (key === "λ" && output.key === "lambda"));
  return (
    <aside className="evidence-panel">
      <section>
        <div className="evidence-title">
          <h2>予測特性 <span>— {candidateLabel}</span></h2>
          {preview && <span className={`inference-surface-status ${previewStatus}`} role="status">
            {previewStatus === "latest" ? "最新" : previewStatus === "refreshing" ? "更新中" : previewStatus === "stale" ? "旧revision・更新中" : "更新失敗・旧結果"}
          </span>}
        </div>
        {metrics.length ? (
          <table className="metric-table">
            <thead>
              <tr>
                <th>特性</th>
                <th>予測値</th>
                <th>90%予測区間</th>
                <th>目標達成</th>
              </tr>
            </thead>
            <tbody>
              {metrics.map((metric) => (
                <tr key={metric.key}>
                  <th>
                    {outputForMetric(metric.key)?.label ?? metric.key} <small>({outputForMetric(metric.key)?.unit ?? metric.unit})</small>
                  </th>
                  <td>
                    {number(
                      metric.value,
                      metric.key === "EL" || metric.key === "λ" ? 1 : 0,
                    )}
                  </td>
                  <td>
                    {number(metric.low, 1)}{" "}
                      <span className="whisker">
                       <i style={{ left: `${Math.max(0, Math.min(100, ((metric.value - metric.low) / Math.max(1e-9, metric.high - metric.low)) * 100))}%` }} />
                    </span>{" "}
                    {number(metric.high, 1)}
                    {(metric.modelStd !== null || metric.observationStd !== null) && (
                      <small className="uncertainty-detail">
                        モデル ±{number(metric.modelStd ?? 0, 1)} / 測定 ±{number(metric.observationStd ?? 0, 1)}
                      </small>
                    )}
                  </td>
                  <td>
                    {metric.goalProbability === null ||
                    metric.goalProbability === undefined ? (
                      "—"
                    ) : (
                      <>
                        <b>{number(metric.goalProbability * 100, 0)}%</b>
                        <small> {preview?.predictions?.[metric.key === "λ" ? "lambda" : metric.key]?.goal_direction === "at_most" ? "≤" : "≥"} {number(metric.goalValue ?? 0, 1)}</small>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : error ? (
          <p className="empty-evidence panel-error">{error} <button className="text-button" onClick={onRetry}>再試行</button></p>
        ) : (
          <p className="empty-evidence">プレビュー結果を待っています。</p>
        )}
        <p className="interval-note">
          {preview?.model_meta?.prediction_interval?.method === "gaussian_process_predictive_distribution"
            ? "予測区間はモデルの不確かさと過去測定のばらつきを含みます。入力条件の支持度は別に判定しています。"
            : "区間と目標達成率は、親工程単位の交差検証残差から求めた経験的な範囲です。"}
        </p>
        {preview?.support && (
          <div className={`support-summary ${status ?? "caution"}`}>
            <b>入力条件の支持度：{status === "supported" ? "範囲内" : status === "extrapolated" ? "外挿" : "要確認"}</b>
            <span>条件全体に対する判定です。目的変数ごとの学習範囲判定ではありません。</span>
          </div>
        )}
      </section>
      {warnings.map((warning) => (
        <div className="warning" key={warning}>
          <span>⚠</span>
          <p>{warning}</p>
        </div>
      ))}
      {preview?.support?.message && (
        <div className={status === "supported" ? "support-note" : "warning"}>
          <span>{status === "supported" ? "✓" : "⚠"}</span>
          <p>{preview.support.message}</p>
        </div>
      )}
      {actualsAvailable ? <ActualsPanel projectId={projectId} candidate={candidate} outputs={outputs} enabled={inferenceReady} /> : <UnavailablePanel title="予測と実測" />}
      <details className="evidence-card">
        <summary>モデル・開発情報（再現性の詳細）</summary>
        <h2>モデル・開発情報</h2>
        <dl>
          <dt>計算方法</dt>
          <dd>
            {preview?.model_meta?.model?.method ?? "—"} ·{" "}
            {preview?.model_meta?.model?.id ?? "—"} v
            {preview?.model_meta?.model?.version ?? "—"}
          </dd>
          <dt>Package</dt>
          <dd>
            {preview?.model_meta?.package
              ? `${preview.model_meta.package.id} v${preview.model_meta.package.version} / ${preview.model_meta.package.manifest_sha256?.slice(0, 12)}`
              : "—"}
          </dd>
          <dt>特徴量</dt>
          <dd>
            {preview?.model_meta?.feature_pipeline?.id ?? "—"} v
            {preview?.model_meta?.feature_pipeline?.version ?? "—"}
          </dd>
          <dt>学習観測</dt>
          <dd>
            {training
              ? Object.entries(training)
                  .map(([key, value]) => `${key}: ${value}`)
                  .join(" / ")
              : "—"}
          </dd>
          <dt>検証方法</dt>
          <dd>{preview?.model_meta?.prediction_interval?.method ?? "—"}</dd>
          <dt>学習データ</dt>
          <dd>
            {preview?.model_meta?.training_data?.source_sha256?.slice(0, 12) ??
              "—"}
          </dd>
          <dt>支持度</dt>
          <dd>
            {status ?? "—"}
            {preview?.support?.percentile !== undefined
              ? `（距離百分位 ${preview.support.percentile.toFixed(0)}%）`
              : ""}
          </dd>
        </dl>
      </details>
    </aside>
  );
}
