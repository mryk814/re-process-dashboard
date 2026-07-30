import { useEffect, useMemo, useState } from "react";
import {
  workbenchApi,
  type ApiInputSpaceEmbedding,
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

type InputSpaceSurface = Extract<WorkbenchSurface, { kind: "input_space" }>;
type CandidatePoint = ApiInputSpaceEmbedding["candidate_points"][number];
type TrainingPoint = ApiInputSpaceEmbedding["training_points"][number];

function plotRange(values: number[]) {
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { min: -1, max: 1 };
  const padding = min === max ? Math.max(Math.abs(min) * 0.1, 1) : (max - min) * 0.08;
  min -= padding;
  max += padding;
  return { min, max };
}

function distanceText(value: number | null) {
  return value === null
    ? "比較候補なし"
    : value.toLocaleString("ja-JP", { maximumFractionDigits: 3 });
}

function statusLabel(status: CandidatePoint["island_status"]) {
  if (status === "supported") return "学習実績の近く";
  if (status === "caution") return "学習実績からやや遠い";
  return "学習実績から遠い";
}

export function InputSpacePanel({
  active,
  ready,
  projectId,
  candidates,
  selectedId,
  taskDefinition,
  surface,
  onSelect,
  onAddCandidate,
}: {
  active: boolean;
  ready: boolean;
  projectId: string;
  candidates: Candidate[];
  selectedId: string;
  taskDefinition: TaskDefinitionContract;
  surface: InputSpaceSurface;
  onSelect: (candidateId: string) => void;
  onAddCandidate: (
    entityKey: string,
    processKey?: string,
    meltKey?: string,
  ) => Promise<boolean>;
}) {
  const selected = candidates.find((candidate) => candidate.id === selectedId);
  const [payload, setPayload] = useState<ApiInputSpaceEmbedding | null>(null);
  const [error, setError] = useState("");
  const [detail, setDetail] = useState<HistoricalEvidenceReference | null>(null);
  const candidateRevisionIdentity = useMemo(
    () => candidates
      .map((candidate) => `${candidate.id}:${candidate.raw.revision}`)
      .sort()
      .join("|"),
    [candidates],
  );

  useEffect(() => {
    if (!active || !ready || !selected) return;
    const controller = new AbortController();
    setPayload(null);
    setError("");
    workbenchApi.inputSpace(
      projectId,
      selected.id,
      selected.raw.revision,
      controller.signal,
    )
      .then((result) => {
        const resultRevisionIdentity = result.candidate_points
          .map((point) => `${point.candidate_id}:${point.candidate_revision}`)
          .sort()
          .join("|");
        if (
          !controller.signal.aborted
          && resultRevisionIdentity === candidateRevisionIdentity
        ) {
          setPayload(result);
        }
      })
      .catch((cause) => {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error ? cause.message : "入力空間を取得できませんでした。");
        }
      });
    return () => controller.abort();
  }, [
    active,
    ready,
    projectId,
    selected?.id,
    selected?.raw.revision,
    candidateRevisionIdentity,
  ]);

  const candidateById = useMemo(
    () => new Map(candidates.map((candidate) => [candidate.id, candidate])),
    [candidates],
  );
  if (!active) return null;
  if (!ready) return <section className="input-space-panel"><p className="panel-loading">すべての候補を保存すると入力空間で位置を確認できます。</p></section>;

  const width = 760;
  const height = 390;
  const plot = { left: 52, right: 24, top: 22, bottom: 46 };
  const points = [
    ...(payload?.training_points ?? []),
    ...(payload?.candidate_points ?? []),
  ];
  const xRange = plotRange(points.map((point) => point.x));
  const yRange = plotRange(points.map((point) => point.y));
  const x = (value: number) => plot.left + ((value - xRange.min) / (xRange.max - xRange.min)) * (width - plot.left - plot.right);
  const y = (value: number) => plot.top + (1 - (value - yRange.min) / (yRange.max - yRange.min)) * (height - plot.top - plot.bottom);
  const selectedPoint = payload?.candidate_points.find((point) => point.candidate_id === selectedId);
  const distanceTarget = taskDefinition.outputs.find((output) => output.key === surface.distance_target_key);
  const openTrainingPoint = (point: TrainingPoint) => {
    setDetail({
      processKey: point.process_key ?? point.parent_key,
      compositionKey: point.composition_key,
      relationContextIds: point.relation_context_ids,
      observationIds: point.observation_ids,
      repeatSummary: point.repeat_summary,
      measurementState: "ready",
      source: "Model Packageの学習cohort",
    });
  };

  return <section className="input-space-panel" aria-labelledby="input-space-heading">
    <header className="input-space-header">
      <div>
        <h2 id="input-space-heading">学習データの中で見る</h2>
        <p>候補が既存実績の島にいるか、ほかの候補と似すぎていないかを分けて確認します。</p>
      </div>
      {payload && <span>{payload.displayed_training_contexts}/{payload.total_training_contexts}実績</span>}
    </header>
    {error && <p className="panel-error" role="alert">{error}</p>}
    {!payload && !error && <p className="panel-loading">入力空間を配置しています…</p>}
    {payload && <div className="input-space-layout">
      <div>
        <div className="input-space-chart">
          <svg viewBox={`0 0 ${width} ${height}`} role="group" aria-label="学習条件と候補の入力空間配置">
            <rect x={plot.left} y={plot.top} width={width - plot.left - plot.right} height={height - plot.top - plot.bottom} />
            {payload.training_points.map((point) => <g
              key={point.context_id}
              role="button"
              tabIndex={0}
              className={`input-space-training${point.landmark ? " landmark" : ""}`}
              aria-label={`${point.context_id}の過去実績を開く`}
              onClick={() => openTrainingPoint(point)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  openTrainingPoint(point);
                }
              }}
            >
              <circle cx={x(point.x)} cy={y(point.y)} r={point.landmark ? 3.2 : 2.4}>
                <title>{point.context_id}</title>
              </circle>
            </g>)}
            {payload.candidate_points.map((point) => {
              const selectedCandidate = point.candidate_id === selectedId;
              const candidateLabel = candidateById.get(point.candidate_id)?.label ?? point.label;
              return <g
                key={point.candidate_id}
                role="button"
                tabIndex={0}
                className={`input-space-candidate ${point.island_status}${selectedCandidate ? " selected" : ""}`}
                aria-label={`${candidateLabel}を選択、${statusLabel(point.island_status)}`}
                onClick={() => onSelect(point.candidate_id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onSelect(point.candidate_id);
                  }
                }}
              >
                <circle cx={x(point.x)} cy={y(point.y)} r={selectedCandidate ? 7 : 5}>
                  <title>{`${candidateLabel}: ${statusLabel(point.island_status)}`}</title>
                </circle>
                {selectedCandidate && <circle cx={x(point.x)} cy={y(point.y)} r="11" className="input-space-selection-ring" />}
                {selectedCandidate && <text x={x(point.x) + 13} y={y(point.y) - 9}>{candidateLabel}</text>}
              </g>;
            })}
            <text x={(plot.left + width - plot.right) / 2} y={height - 11} textAnchor="middle">埋め込み軸 1</text>
            <text transform={`translate(16 ${(plot.top + height - plot.bottom) / 2}) rotate(-90)`} textAnchor="middle">埋め込み軸 2</text>
          </svg>
        </div>
        <p className="input-space-scroll-hint">図は横にスクロールできます</p>
        <div className="input-space-legend" aria-label="凡例">
          <span><i className="training-mark" />学習実績</span>
          <span><i className="candidate-mark" />候補</span>
          <span>青: 近い</span><span>橙: やや遠い</span><span>赤: 遠い</span>
        </div>
      </div>
      <aside className="input-space-reading">
        {selectedPoint ? <>
          <small>選択候補</small>
          <h3>{candidateById.get(selectedPoint.candidate_id)?.label ?? selectedPoint.label}</h3>
          <strong className={selectedPoint.island_status}>{statusLabel(selectedPoint.island_status)}</strong>
          <dl>
            <div><dt>島までの距離</dt><dd>{distanceText(selectedPoint.island_distance)}</dd></div>
            <div><dt>候補間の新規性</dt><dd>{distanceText(selectedPoint.candidate_novelty ?? null)}</dd></div>
          </dl>
          <p>島までの距離は最も近い学習実績との距離です。候補間の新規性は、ほかの候補との最短距離です。</p>
        </> : <p>候補の位置を選択してください。</p>}
      </aside>
    </div>}
    {payload && <details className="input-space-technical">
      <summary>配置と距離の条件</summary>
      <dl>
        <div><dt>距離の基準</dt><dd>{distanceTarget?.label ?? payload.distance_target_key} / {payload.distance_method} {payload.distance_version}</dd></div>
        <div><dt>学習cohort</dt><dd><code>{payload.cohort_digest.slice(0, 18)}…</code></dd></div>
        <div><dt>入力空間identity</dt><dd><code>{payload.vector_space_digest.slice(0, 18)}…</code></dd></div>
        <div><dt>配置方法</dt><dd>Landmark MDS（新規候補を再学習なしで配置） {payload.embedding_version}</dd></div>
        <div><dt>seed / landmark</dt><dd>{payload.seed} / {payload.landmark_count}</dd></div>
        <div><dt>2軸が保持した正の固有値</dt><dd>{(payload.captured_positive_eigenvalue_ratio * 100).toFixed(1)}%</dd></div>
      </dl>
      <p>軸の向きと上下左右そのものに意味はありません。支持範囲と新規性は、図上の距離ではなくTask距離で判定します。</p>
    </details>}
    {payload && <details className="input-space-table">
      <summary>候補の距離を数値で確認</summary>
      <div className="table-scroll"><table>
        <thead><tr><th>候補</th><th>学習実績まで</th><th>候補間の新規性</th><th>判定</th></tr></thead>
        <tbody>{payload.candidate_points.map((point) => <tr key={point.candidate_id}>
          <th><button type="button" className="text-button" aria-pressed={point.candidate_id === selectedId} onClick={() => onSelect(point.candidate_id)}>{candidateById.get(point.candidate_id)?.label ?? point.label}</button></th>
          <td>{distanceText(point.island_distance)}</td>
          <td>{distanceText(point.candidate_novelty ?? null)}</td>
          <td>{statusLabel(point.island_status)}</td>
        </tr>)}</tbody>
      </table></div>
    </details>}
    <HistoricalEvidenceDrawer
      open={detail !== null}
      projectId={projectId}
      reference={detail}
      outputs={taskDefinition.outputs}
      taskDefinition={taskDefinition}
      onClose={() => setDetail(null)}
      onAddCandidate={onAddCandidate}
    />
  </section>;
}
