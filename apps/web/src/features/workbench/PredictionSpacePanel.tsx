import { useEffect, useMemo, useState } from "react";
import type { TargetGoal } from "../../shared/targetGoals";
import { isTargetRange } from "../../shared/targetGoals";
import {
  workbenchApi,
  type ApiOutputSpaceEvidence,
  type ApiPreview,
} from "../../shared/api/workbench-api";
import type {
  CandidateViewModel as Candidate,
  TaskDefinitionContract,
} from "../candidates";
import type { WorkbenchSurface } from "./workbenchSurfaceRegistry";
import {
  HistoricalEvidenceDrawer,
  type HistoricalEvidenceReference,
} from "./HistoricalEvidenceDrawer";

type PredictionSpaceSurface = Extract<
  WorkbenchSurface,
  { kind: "prediction_space" }
>;

type CandidatePoint = {
  id: string;
  label: string;
  x: number;
  y: number;
  xLower: number;
  xUpper: number;
  yLower: number;
  yUpper: number;
  support: "supported" | "caution" | "extrapolated";
};

const supportRank = {
  supported: 0,
  caution: 1,
  extrapolated: 2,
} as const;

function finiteRange(values: number[], preferred?: { min: number; max: number } | null) {
  const finite = values.filter(Number.isFinite);
  if (finite.length === 0 && preferred) finite.push(preferred.min, preferred.max);
  let min = finite.length ? Math.min(...finite) : 0;
  let max = finite.length ? Math.max(...finite) : 1;
  if (min === max) {
    const padding = Math.max(Math.abs(min) * 0.08, 1);
    min -= padding;
    max += padding;
  } else {
    const padding = (max - min) * 0.08;
    min -= padding;
    max += padding;
  }
  return { min, max };
}

function valueText(value: number, unit: string) {
  return `${value.toLocaleString("ja-JP", { maximumFractionDigits: 3 })}${unit ? ` ${unit}` : ""}`;
}

function goalValues(goal: TargetGoal | undefined): number[] {
  if (goal === undefined) return [];
  return isTargetRange(goal) ? [goal.lower, goal.upper] : [goal];
}

function pairingLabel(
  relationship: ApiOutputSpaceEvidence["points"][number]["pairing_relationship"],
) {
  if (relationship === "same_observations") return "同じ実測行";
  if (relationship === "overlapping_observations") return "一部は同じ実測行";
  return "同じ条件の別実測";
}

function pointSupport(preview: ApiPreview, xTarget: string, yTarget: string): CandidatePoint["support"] {
  const statuses = [preview.model_support?.[xTarget]?.status, preview.model_support?.[yTarget]?.status]
    .filter((value): value is CandidatePoint["support"] => value != null);
  return statuses.reduce<CandidatePoint["support"]>(
    (worst, value) => supportRank[value] > supportRank[worst] ? value : worst,
    "supported",
  );
}

function markerPath(support: CandidatePoint["support"], x: number, y: number) {
  if (support === "caution") return `M${x} ${y - 6}L${x + 6} ${y}L${x} ${y + 6}L${x - 6} ${y}Z`;
  if (support === "extrapolated") return `M${x} ${y - 7}L${x + 7} ${y + 6}L${x - 7} ${y + 6}Z`;
  return "";
}

export function PredictionSpacePanel({
  active,
  projectId,
  candidates,
  selectedId,
  taskDefinition,
  surface,
  previewsByCandidate,
  targetValues,
  pendingPreviewCount,
  loadingRemainingPreviews,
  onLoadRemainingPreviews,
  onSelect,
  onAddCandidate,
}: {
  active: boolean;
  projectId: string;
  candidates: Candidate[];
  selectedId: string;
  taskDefinition: TaskDefinitionContract;
  surface: PredictionSpaceSurface;
  previewsByCandidate: Record<string, ApiPreview>;
  targetValues: Record<string, TargetGoal>;
  pendingPreviewCount: number;
  loadingRemainingPreviews: boolean;
  onLoadRemainingPreviews: () => void;
  onSelect: (id: string) => void;
  onAddCandidate: (
    entityKey: string,
    processKey?: string,
    meltKey?: string,
  ) => Promise<boolean>;
}) {
  const [xTarget, setXTarget] = useState(surface.target_keys[0]);
  const [yTarget, setYTarget] = useState(surface.target_keys[1]);
  const [evidence, setEvidence] = useState<ApiOutputSpaceEvidence | null>(null);
  const [evidenceError, setEvidenceError] = useState("");
  const [distanceFilter, setDistanceFilter] = useState<"supported" | "caution" | "all">("supported");
  const [nearestLimit, setNearestLimit] = useState(30);
  const [detailReference, setDetailReference] = useState<HistoricalEvidenceReference | null>(null);
  const selectedCandidate = candidates.find((candidate) => candidate.id === selectedId);

  useEffect(() => {
    setXTarget(surface.target_keys[0]);
    setYTarget(surface.target_keys[1]);
  }, [taskDefinition.id, surface.target_keys.join("\u001f")]);

  useEffect(() => {
    if (!active || !selectedCandidate || !xTarget || !yTarget || xTarget === yTarget) return;
    const controller = new AbortController();
    setEvidence(null);
    setEvidenceError("");
    workbenchApi.outputSpaceEvidence(
      projectId,
      selectedCandidate.id,
      selectedCandidate.raw.revision,
      xTarget,
      yTarget,
      distanceFilter,
      Math.min(nearestLimit, surface.historical_limit),
      controller.signal,
    )
      .then((value) => {
        if (!controller.signal.aborted) setEvidence(value);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) {
          setEvidenceError(cause instanceof Error ? cause.message : "学習実績を取得できませんでした。");
        }
      });
    return () => controller.abort();
  }, [
    active,
    projectId,
    selectedCandidate?.id,
    selectedCandidate?.raw.revision,
    surface.historical_limit,
    distanceFilter,
    nearestLimit,
    xTarget,
    yTarget,
  ]);

  const outputs = surface.target_keys
    .map((key) => taskDefinition.outputs.find((output) => output.key === key))
    .filter((output): output is TaskDefinitionContract["outputs"][number] => output != null);
  const xOutput = outputs.find((output) => output.key === xTarget) ?? outputs[0];
  const yOutput = outputs.find((output) => output.key === yTarget) ?? outputs[1];
  const candidateById = useMemo(
    () => new Map(candidates.map((candidate) => [candidate.id, candidate])),
    [candidates],
  );
  const candidatePoints = useMemo(() => Object.entries(previewsByCandidate).flatMap(([candidateId, preview]) => {
    const candidate = candidateById.get(candidateId);
    const x = preview.predictions[xTarget];
    const y = preview.predictions[yTarget];
    if (!candidate || !x || !y) return [];
    return [{
      id: candidateId,
      label: candidate.label,
      x: x.value,
      y: y.value,
      xLower: x.lower,
      xUpper: x.upper,
      yLower: y.lower,
      yUpper: y.upper,
      support: pointSupport(preview, xTarget, yTarget),
    }];
  }), [candidateById, previewsByCandidate, xTarget, yTarget]);

  if (!xOutput || !yOutput) return null;

  const width = 760;
  const height = 420;
  const plot = { left: 74, right: 24, top: 24, bottom: 62 };
  const plotWidth = width - plot.left - plot.right;
  const plotHeight = height - plot.top - plot.bottom;
  const xRange = finiteRange([
    ...candidatePoints.flatMap((point) => [point.xLower, point.x, point.xUpper]),
    ...(evidence?.points.map((point) => point.x.mean) ?? []),
    ...goalValues(targetValues[xTarget]),
  ], xOutput.preferred_display_range);
  const yRange = finiteRange([
    ...candidatePoints.flatMap((point) => [point.yLower, point.y, point.yUpper]),
    ...(evidence?.points.map((point) => point.y.mean) ?? []),
    ...goalValues(targetValues[yTarget]),
  ], yOutput.preferred_display_range);
  const x = (value: number) => plot.left + ((value - xRange.min) / (xRange.max - xRange.min)) * plotWidth;
  const y = (value: number) => plot.top + plotHeight - ((value - yRange.min) / (yRange.max - yRange.min)) * plotHeight;
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  const selectedPoint = candidatePoints.find((point) => point.id === selectedId);

  const swapAxes = () => {
    setXTarget(yTarget);
    setYTarget(xTarget);
  };
  const openEvidence = (point: ApiOutputSpaceEvidence["points"][number]) => {
    setDetailReference({
      processKey: point.process_key ?? point.parent_key,
      compositionKey: point.composition_key,
      relationContextIds: point.relation_context_ids,
      observationIds: [...new Set([
        ...point.x.observation_ids,
        ...point.y.observation_ids,
      ])],
      repeatSummary: {
        [xTarget]: { mean: point.x.mean, std: point.x.std, n: point.x.count },
        [yTarget]: { mean: point.y.mean, std: point.y.std, n: point.y.count },
      },
      measurementState: "ready",
      distance: point.distance,
      source: "Model Packageの2軸共通学習cohort",
    });
  };

  return <section className="prediction-space-panel" aria-labelledby="prediction-space-heading">
    <header className="prediction-space-header">
      <div>
        <h2 id="prediction-space-heading">特性のトレードオフ</h2>
        <p>候補の予測と、両特性がそろう学習条件の実測平均を重ねます。</p>
      </div>
      <span>{candidatePoints.length} / {candidates.length}候補</span>
    </header>
    <div className="prediction-space-controls">
      <div className="prediction-space-axis-group">
        <label>横軸
          <select value={xTarget} onChange={(event) => {
            const next = event.target.value;
            setXTarget(next);
            if (next === yTarget) setYTarget(xTarget);
          }}>
            {outputs.map((output) => <option value={output.key} key={output.key}>{output.label}</option>)}
          </select>
        </label>
        <button type="button" className="axis-swap" aria-label="横軸と縦軸を入れ替え" onClick={swapAxes}>↔ 入替</button>
        <label>縦軸
          <select value={yTarget} onChange={(event) => {
            const next = event.target.value;
            setYTarget(next);
            if (next === xTarget) setXTarget(yTarget);
          }}>
            {outputs.map((output) => <option value={output.key} key={output.key}>{output.label}</option>)}
          </select>
        </label>
      </div>
      <div className="prediction-space-filter-group">
        <label>実績の範囲
          <select value={distanceFilter} onChange={(event) => setDistanceFilter(event.target.value as typeof distanceFilter)}>
            <option value="supported">近い実績</option>
            <option value="caution">注意を含む</option>
            <option value="all">すべて（近い順）</option>
          </select>
        </label>
        <label>表示数
          <select value={nearestLimit} onChange={(event) => setNearestLimit(Number(event.target.value))}>
            <option value={30}>上位30</option>
            <option value={100}>上位100</option>
            <option value={200}>上位200</option>
          </select>
        </label>
      </div>
      {pendingPreviewCount > 0 && <button
        type="button"
        className="outline-button"
        disabled={loadingRemainingPreviews}
        onClick={onLoadRemainingPreviews}
      >{loadingRemainingPreviews ? "計算中…" : `残り${pendingPreviewCount}候補を計算`}</button>}
    </div>
    <div className="prediction-space-chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${xOutput.label}と${yOutput.label}の候補予測と学習実績`}>
        <rect x={plot.left} y={plot.top} width={plotWidth} height={plotHeight} fill="#fbfcfe" stroke="#ccd6e2" />
        {ticks.map((ratio) => {
          const xValue = xRange.min + (xRange.max - xRange.min) * ratio;
          const yValue = yRange.min + (yRange.max - yRange.min) * ratio;
          return <g key={ratio}>
            <line x1={x(xValue)} x2={x(xValue)} y1={plot.top} y2={plot.top + plotHeight} stroke="#e3e9f0" />
            <line x1={plot.left} x2={plot.left + plotWidth} y1={y(yValue)} y2={y(yValue)} stroke="#e3e9f0" />
            <text x={x(xValue)} y={plot.top + plotHeight + 22} textAnchor="middle">{xValue.toLocaleString("ja-JP", { maximumFractionDigits: 2 })}</text>
            <text x={plot.left - 10} y={y(yValue) + 4} textAnchor="end">{yValue.toLocaleString("ja-JP", { maximumFractionDigits: 2 })}</text>
          </g>;
        })}
        {goalValues(targetValues[xTarget]).map((value) => <line key={`x-goal-${value}`} x1={x(value)} x2={x(value)} y1={plot.top} y2={plot.top + plotHeight} className="prediction-space-goal" />)}
        {goalValues(targetValues[yTarget]).map((value) => <line key={`y-goal-${value}`} x1={plot.left} x2={plot.left + plotWidth} y1={y(value)} y2={y(value)} className="prediction-space-goal" />)}
        {evidence?.points.map((point) => <g
          key={point.context_id}
          className={`prediction-space-actual-point ${point.distance_status}`}
          role="button"
          tabIndex={0}
          aria-label={`${point.context_id}の実績詳細を開く`}
          onClick={() => openEvidence(point)}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              openEvidence(point);
            }
          }}
        >
          <rect
            x={x(point.x.mean) - 3.5}
            y={y(point.y.mean) - 3.5}
            width="7"
            height="7"
            className="prediction-space-actual"
          >
            <title>{`${point.context_id}: ${valueText(point.x.mean, xOutput.unit)} / ${valueText(point.y.mean, yOutput.unit)} (n=${point.x.count}/${point.y.count})`}</title>
          </rect>
        </g>)}
        {candidatePoints.map((point) => {
          const cx = x(point.x);
          const cy = y(point.y);
          const selected = point.id === selectedId;
          const label = `${point.label}: ${valueText(point.x, xOutput.unit)} / ${valueText(point.y, yOutput.unit)} / ${point.support}`;
          return <g
            key={point.id}
            aria-hidden="true"
            className={`prediction-space-candidate ${point.support}${selected ? " selected" : ""}`}
          >
            <title>{label}</title>
            <line x1={x(point.xLower)} x2={x(point.xUpper)} y1={cy} y2={cy} className="prediction-space-interval" />
            <line x1={cx} x2={cx} y1={y(point.yLower)} y2={y(point.yUpper)} className="prediction-space-interval" />
            {point.support === "supported"
              ? <circle cx={cx} cy={cy} r={selected ? 7 : 5} />
              : <path d={markerPath(point.support, cx, cy)} />}
            {selected && <circle cx={cx} cy={cy} r="11" className="prediction-space-selection-ring" />}
            {selected && <text x={cx + 13} y={cy - 10} className="prediction-space-selected-label">{point.label}</text>}
          </g>;
        })}
        <text x={plot.left + plotWidth / 2} y={height - 13} textAnchor="middle">{xOutput.label} ({xOutput.unit})</text>
        <text transform={`translate(18 ${plot.top + plotHeight / 2}) rotate(-90)`} textAnchor="middle">{yOutput.label} ({yOutput.unit})</text>
      </svg>
    </div>
    <div className="prediction-space-legend" aria-label="凡例">
      <span><i className="candidate-mark" />候補予測</span>
      <span><i className="actual-mark" />学習条件の実測平均</span>
      <span><i className="goal-mark" />Project目標</span>
      <span>○ 支持範囲内</span><span>◇ 注意</span><span>△ 学習条件外</span>
    </div>
    {evidenceError && <p className="panel-error" role="alert">{evidenceError}</p>}
    <p className="prediction-space-note">
      線は各特性の個別予測区間です。2特性を同時に含む確率領域ではありません。
      灰色の四角は同じ学習条件に属する実測の条件平均で、予測値や同一試料の相関ではありません。
      同じ実測行で両特性がそろうか、条件だけが共通の別実測かは数値表に示します。
      {evidence
        ? ` ${evidence.returned_contexts}/${evidence.eligible_contexts}件を近い順に表示（2軸共通cohort ${evidence.total_contexts}件、距離 ${evidence.distance_method} ${evidence.distance_version}）。`
        : ""}
    </p>
    {selectedPoint && <div className="prediction-space-selected">
      <b>{selectedPoint.label}</b>
      <span>{xOutput.label} {valueText(selectedPoint.x, xOutput.unit)} ({valueText(selectedPoint.xLower, xOutput.unit)}–{valueText(selectedPoint.xUpper, xOutput.unit)})</span>
      <span>{yOutput.label} {valueText(selectedPoint.y, yOutput.unit)} ({valueText(selectedPoint.yLower, yOutput.unit)}–{valueText(selectedPoint.yUpper, yOutput.unit)})</span>
      <em>{selectedPoint.support === "supported" ? "2軸とも支持範囲を確認" : selectedPoint.support === "caution" ? "少なくとも1軸が注意領域" : "少なくとも1軸が学習条件外"}</em>
    </div>}
    <details className="prediction-space-table">
      <summary>数値で確認</summary>
      <div className="table-scroll"><table>
        <thead><tr><th>種類</th><th>候補・条件</th><th>{xOutput.label}</th><th>{yOutput.label}</th><th>支持・対応</th></tr></thead>
        <tbody>{candidatePoints.map((point) => <tr key={point.id}>
          <td>候補予測</td>
          <th><button
            type="button"
            className="prediction-space-row-select"
            aria-pressed={point.id === selectedId}
            onClick={() => onSelect(point.id)}
          >{point.label}</button></th>
          <td>{valueText(point.x, xOutput.unit)}</td>
          <td>{valueText(point.y, yOutput.unit)}</td>
          <td>{point.support === "supported" ? "範囲内" : point.support === "caution" ? "注意" : "学習条件外"}</td>
        </tr>)}
        {evidence?.points.map((point) => <tr key={`actual-${point.context_id}`}>
          <td>学習実績</td>
          <th>{point.context_id}</th>
          <td>{valueText(point.x.mean, xOutput.unit)}<small>実測ばらつき σ {valueText(point.x.std, xOutput.unit)} / n={point.x.count}</small></td>
          <td>{valueText(point.y.mean, yOutput.unit)}<small>実測ばらつき σ {valueText(point.y.std, yOutput.unit)} / n={point.y.count}</small></td>
          <td><button type="button" className="text-button" onClick={() => openEvidence(point)}>実績を見る</button><small>{pairingLabel(point.pairing_relationship)} / 距離 {valueText(point.distance, "")}</small></td>
        </tr>)}</tbody>
      </table></div>
    </details>
    <HistoricalEvidenceDrawer
      open={detailReference !== null}
      projectId={projectId}
      reference={detailReference}
      outputs={taskDefinition.outputs}
      taskDefinition={taskDefinition}
      onClose={() => setDetailReference(null)}
      onAddCandidate={onAddCandidate}
    />
  </section>;
}
