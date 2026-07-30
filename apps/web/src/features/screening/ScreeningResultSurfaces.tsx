import { useEffect, useState } from "react";
import type { ApiScreeningRun } from "../../shared/api/workbench-api";
import { supportStatusLabel } from "../../shared/supportPresentation";

export type ScreeningResultSurface = "map" | "proposals" | "evaluated";

export function ScreeningResultSurfaceTabs({
  value,
  onChange,
  proposalCount,
  evaluatedCount,
  proposalsAvailable,
}: {
  value: ScreeningResultSurface;
  onChange: (surface: ScreeningResultSurface) => void;
  proposalCount: number;
  evaluatedCount: number;
  proposalsAvailable: boolean;
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
      label: "提案候補",
      count: proposalCount,
      disabled: !proposalsAvailable,
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
  const proposedPoolIndices = new Set(
    result.proposal_selection?.selected.map((item) => item.pool_index) ?? [],
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
                          {proposedPoolIndices.has(point.pool_index)
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
