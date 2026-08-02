import { useEffect, useState } from "react";
import type { ApiScreeningRun } from "../../shared/api/workbench-api";
import { supportStatusLabel } from "../../shared/supportPresentation";
import { CandidateAddButton } from "../../shared/ui/CandidateAddButton";

export type ScreeningResultSurface = "map" | "proposals" | "evaluated";

export type ScreeningSelectionSurface = {
  kind: "proposal" | "batch" | "none";
  label: "提案候補" | "実験バッチ";
  count: number;
  available: boolean;
};

export function screeningSelectionSurface(
  run: Pick<ApiScreeningRun, "purpose" | "proposal_selection" | "batch_proposal">,
): ScreeningSelectionSurface {
  if (run.purpose === "goal_search" && run.proposal_selection) {
    return {
      kind: "proposal",
      label: "提案候補",
      count: run.proposal_selection.selected.length,
      available: true,
    };
  }
  if (run.purpose === "experiment_batch" && run.batch_proposal) {
    return {
      kind: "batch",
      label: "実験バッチ",
      count: run.batch_proposal.selected.length,
      available: true,
    };
  }
  return { kind: "none", label: "提案候補", count: 0, available: false };
}

export function initialScreeningResultSurface(
  run: Pick<ApiScreeningRun, "purpose" | "proposal_selection" | "batch_proposal">,
): ScreeningResultSurface {
  return screeningSelectionSurface(run).available ? "proposals" : "map";
}

export function ScreeningResultSurfaceTabs({
  value,
  onChange,
  selectionLabel,
  selectionCount,
  evaluatedCount,
  selectionAvailable,
}: {
  value: ScreeningResultSurface;
  onChange: (surface: ScreeningResultSurface) => void;
  selectionLabel: ScreeningSelectionSurface["label"];
  selectionCount: number;
  evaluatedCount: number;
  selectionAvailable: boolean;
}) {
  const tabs: Array<{
    id: ScreeningResultSurface;
    label: string;
    count?: number;
    disabled?: boolean;
  }> = [
    { id: "map", label: "地図" },
    {
      id: "proposals",
      label: selectionLabel,
      count: selectionCount,
      disabled: !selectionAvailable,
    },
    { id: "evaluated", label: "全評価点", count: evaluatedCount },
  ];
  return (
    <div className="screening-result-tabs" role="group" aria-label="探索結果の表示">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          aria-pressed={value === tab.id}
          disabled={tab.disabled}
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
          {tab.count != null && <span>{tab.count}</span>}
        </button>
      ))}
    </div>
  );
}

function displayNumber(value: number) {
  return value.toLocaleString("ja-JP", { maximumFractionDigits: 4 });
}

export function ScreeningEvaluatedTable({
  result,
  axisLabel,
  scoreLabel,
  targetLabel,
}: {
  result: ApiScreeningRun;
  axisLabel: (axis: string) => string;
  scoreLabel: string;
  targetLabel: string;
}) {
  const [visibleCount, setVisibleCount] = useState(100);
  useEffect(() => setVisibleCount(100), [result.id]);
  const pool = result.proposal_pool ?? [];
  const varyingFields = Object.entries(result.variables)
    .filter(([, spec]) => spec.mode !== "fixed")
    .map(([field]) => field);
  const selectionSurface = screeningSelectionSurface(result);
  const proposedPoolIndices = new Set(
    selectionSurface.kind === "proposal"
      ? result.proposal_selection?.selected.map((item) => item.pool_index) ?? []
      : [],
  );
  const batchPoolIndices = new Set(
    selectionSurface.kind === "batch"
      ? result.batch_proposal?.selected.map((item) => item.pool_index) ?? []
      : [],
  );
  const visible = pool.slice(0, visibleCount);

  return (
    <section
      id="screening-result-panel-evaluated"
      className="screening-evaluated"
      role="region"
      aria-label="全評価点"
    >
      <div className="screening-results-heading">
        <div>
          <h3>全評価点</h3>
          <small>modelが実際に評価した条件。補間値ではありません。</small>
        </div>
        <span>{pool.length.toLocaleString("ja-JP")}件</span>
      </div>
      {pool.length === 0
        ? <p className="screening-surface-unavailable">旧Runには全評価点が保存されていません。</p>
        : <>
            <div className="screening-evaluated-scroll">
              <table className="quality-table screening-evaluated-table">
                <thead>
                  <tr>
                    <th>評価点</th>
                    {varyingFields.map((field) => <th key={field}>{axisLabel(field)}</th>)}
                    <th>{targetLabel} 予測</th>
                    <th>{scoreLabel}</th>
                    <th>支持範囲</th>
                    <th>利用先</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((point) => {
                    const mean = typeof point.acquisition_components.mean === "number"
                      ? point.acquisition_components.mean
                      : null;
                    return (
                      <tr key={point.pool_index}>
                        <th scope="row">{point.pool_index + 1}</th>
                        {varyingFields.map((field) => (
                          <td key={field}>
                            {typeof point.inputs[field] === "number"
                              ? displayNumber(point.inputs[field])
                              : String(point.inputs[field] ?? "—")}
                          </td>
                        ))}
                        <td>{mean == null ? "—" : displayNumber(mean)}</td>
                        <td>{displayNumber(point.acquisition_score)}</td>
                        <td>{supportStatusLabel(point.support_status)}</td>
                        <td>
                          {batchPoolIndices.has(point.pool_index)
                            ? <b>実験バッチ</b>
                            : proposedPoolIndices.has(point.pool_index)
                            ? <b>提案候補</b>
                            : point.selected_rank != null
                              ? "図に表示"
                              : "評価のみ"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {visibleCount < pool.length && (
              <button
                type="button"
                className="outline-button screening-evaluated-more"
                onClick={() => setVisibleCount((count) => Math.min(pool.length, count + 100))}
              >
                次の{Math.min(100, pool.length - visibleCount)}件を表示
              </button>
            )}
          </>}
    </section>
  );
}

const batchRoleLabels: Record<
  NonNullable<ApiScreeningRun["batch_proposal"]>["selected"][number]["role"],
  string
> = {
  performance: "有望度",
  exploration: "探索",
  boundary_check: "境界確認",
  diversity: "多様性",
  coverage: "範囲補完",
  control: "固定Control",
  replicate: "反復",
};

export function ScreeningBatchTable({
  result,
  stockedPointIndices = new Set<number>(),
  remainingCandidateCapacity = 0,
  promotionPendingPointIndex = null,
  onPromote = () => {},
}: {
  result: ApiScreeningRun;
  stockedPointIndices?: Set<number>;
  remainingCandidateCapacity?: number;
  promotionPendingPointIndex?: number | null;
  onPromote?: (pointIndex: number) => void;
}) {
  const batch = result.batch_proposal;
  if (!batch) return null;
  return (
    <section
      id="screening-result-panel-proposals"
      className="screening-evaluated"
      role="region"
      aria-label="実験バッチ"
    >
      <div className="screening-results-heading">
        <div>
          <h3>実験バッチ</h3>
          <small>提案候補とは別に、このRunで実験へ割り当てた条件です。</small>
        </div>
        <span>{batch.selected.length.toLocaleString("ja-JP")}枠</span>
      </div>
      <div className="screening-evaluated-scroll">
        <table className="quality-table screening-evaluated-table">
          <thead>
            <tr>
              <th>順番</th>
              <th>条件</th>
              <th>役割</th>
              <th>価値</th>
              <th>多様性</th>
              <th>コスト</th>
              <th>候補</th>
            </tr>
          </thead>
          <tbody>
            {batch.selected.map((item) => {
              const candidatePoint = item.source === "acquisition_ranked" && item.point_index != null
                ? item.point_index
                : null;
              const stocked = candidatePoint != null && stockedPointIndices.has(candidatePoint);
              return <tr key={`${item.order}-${item.pool_index}`}>
                <th scope="row">{item.order}</th>
                <td>
                  {item.source === "exact_control"
                    ? `固定Control r${item.candidate_revision ?? "—"}`
                    : item.point_index == null
                      ? `評価点 ${item.pool_index + 1}`
                      : `点 ${item.point_index + 1}`}
                </td>
                <td>{batchRoleLabels[item.role]}</td>
                <td>{displayNumber(item.acquisition_component)}</td>
                <td>{displayNumber(item.diversity_component)}</td>
                <td>{displayNumber(item.estimated_cost)}</td>
                <td>
                  {candidatePoint == null
                    ? <small>Control／反復は新しい候補にしません</small>
                    : <CandidateAddButton
                        compact
                        disabled={stocked || remainingCandidateCapacity < 1 || promotionPendingPointIndex != null}
                        onClick={() => onPromote(candidatePoint)}
                      >
                        {stocked ? "追加済み" : promotionPendingPointIndex === candidatePoint ? "追加中…" : "この条件を候補にする"}
                      </CandidateAddButton>}
                </td>
              </tr>;
            })}
          </tbody>
        </table>
      </div>
      <p className="screening-batch-surface-note">
        固定Controlを含む選定理由と再現情報は、上の「実験バッチ」詳細に記録されています。
      </p>
    </section>
  );
}
