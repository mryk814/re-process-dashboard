import { useEffect, useMemo, useState } from "react";
import type { ApiDecisionActivityRun } from "../../../shared/api/workbench-api";
import { candidateDifferenceOptions } from "./candidateDifferenceOptions";
import type { DecisionActivityViewProps } from "./types";

const numberFormat = new Intl.NumberFormat("ja-JP", { maximumFractionDigits: 4 });
const signedFormat = new Intl.NumberFormat("ja-JP", {
  maximumFractionDigits: 4,
  signDisplay: "exceptZero",
});

function differenceResult(run: ApiDecisionActivityRun) {
  return run.result.schema_version === "candidate-difference-summary/v1" ? run.result : null;
}

function formatValue(value: number | string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return typeof value === "number" ? numberFormat.format(value) : value;
}

export function CandidateDifferenceActivityView({
  candidate,
  candidates,
  taskDefinition,
  ready,
  availability,
  runs,
  running,
  onRun,
}: DecisionActivityViewProps) {
  const options = useMemo(
    () => candidateDifferenceOptions(candidate, candidates),
    [candidate, candidates],
  );
  const [comparisonKey, setComparisonKey] = useState("");
  const [activeRunId, setActiveRunId] = useState<string | null>(null);

  useEffect(() => {
    setComparisonKey((current) => (
      options.some((item) => item.key === current)
        ? current
        : options[0]?.key ?? ""
    ));
  }, [candidate.id, options]);

  const comparison = options.find((item) => item.key === comparisonKey) ?? null;
  const activeRun = runs.find((run) => run.id === activeRunId) ?? runs[0] ?? null;
  const result = activeRun ? differenceResult(activeRun) : null;
  const canRun = ready && availability.available && comparison !== null && !running;
  const outputLabels = new Map(taskDefinition.outputs.map((output) => [output.key, output.label]));
  const candidateLabels = new Map(candidates.map((item) => [item.id, item.label]));
  const contributionsByTarget = useMemo(() => {
    const grouped = new Map<string, NonNullable<typeof result>["contributions"][number][]>();
    for (const item of result?.contributions ?? []) {
      const items = grouped.get(item.target) ?? [];
      items.push(item);
      grouped.set(item.target, items);
    }
    return grouped;
  }, [result]);
  const changeLabels = new Map((result?.input_changes ?? []).map((item) => [item.path, item.label]));

  return <>
    <section className="activity-settings">
      <div className="panel-title"><h3>比較する候補</h3><span>この候補との予測差を分解します</span></div>
      <div className="activity-run-settings">
        <label>比較候補
          <select
            aria-label="比較候補"
            value={comparisonKey}
            onChange={(event) => setComparisonKey(event.target.value)}
            disabled={options.length === 0}
          >
            {options.some((item) => item.kind === "history") && <optgroup label="この候補の履歴">
              {options.filter((item) => item.kind === "history").map((item) => <option value={item.key} key={item.key}>{item.label}</option>)}
            </optgroup>}
            {options.some((item) => item.kind === "candidate") && <optgroup label="別の候補">
              {options.filter((item) => item.kind === "candidate").map((item) => <option value={item.key} key={item.key}>{item.label}</option>)}
            </optgroup>}
          </select>
        </label>
        <button type="button" className="primary-button" disabled={!canRun} onClick={() => {
          if (!comparison) return;
          void onRun({
            schema_version: "candidate-difference-parameters/v1",
            comparison_candidate_id: comparison.candidateId,
            comparison_revision: comparison.revision,
          });
        }}>{running ? "分解中…" : "差分を分解"}</button>
      </div>
      {options.length === 0 && <small>比較できる過去版または別の候補がありません。</small>}
      {!ready && <small>候補の入力を保存すると実行できます。</small>}
    </section>

    {runs.length > 0 && <nav className="activity-run-history" aria-label="保存済み候補差分">
      <span>保存済み</span>
      {runs.slice(0, 5).map((run) => <button type="button" className={activeRun?.id === run.id ? "active" : ""} onClick={() => setActiveRunId(run.id)} key={run.id}>{new Date(run.created_at).toLocaleString("ja-JP")}</button>)}
    </nav>}

    {activeRun && result && <section className="activity-result">
      <div className="activity-result-meta">
        <span>候補版 {activeRun.provenance.candidate_revision}</span>
        <span>比較 {candidateLabels.get(result.comparison_candidate_id) ?? result.comparison_candidate_id} 版{result.comparison_candidate_revision}</span>
        <span>相違した入力 {result.changed_input_count}件</span>
      </div>
      <div className="activity-targets">{result.target_summaries.map((summary) => <article key={summary.target}>
        <header><strong>{outputLabels.get(summary.target) ?? summary.target}</strong><b>差 {signedFormat.format(summary.difference)} {summary.unit}</b></header>
        <dl>
          <div><dt>この候補</dt><dd>{numberFormat.format(summary.base_prediction.value)} {summary.unit}</dd></div>
          <div><dt>比較候補</dt><dd>{numberFormat.format(summary.comparison_prediction.value)} {summary.unit}</dd></div>
          <div><dt>置換で説明できた分</dt><dd>{signedFormat.format(summary.attributed_difference)} {summary.unit}</dd></div>
          <div><dt>残差（交互作用）</dt><dd>{signedFormat.format(summary.unexplained_difference)} {summary.unit}</dd></div>
        </dl>
        <small>モデル不確実性：この候補 {numberFormat.format(summary.base_prediction.lower)}–{numberFormat.format(summary.base_prediction.upper)} ／ 比較候補 {numberFormat.format(summary.comparison_prediction.lower)}–{numberFormat.format(summary.comparison_prediction.upper)} {summary.unit}</small>
      </article>)}</div>
      <div className="activity-support-summary">
        <span>この候補の支持 <b>{result.base_support.status}</b></span>
        <span>比較候補の支持 <b>{result.comparison_support.status}</b></span>
      </div>
      <details className="activity-evidence" open>
        <summary>入力の相違（{result.input_changes.length}件）</summary>
        {result.input_changes.map((item) => <div key={item.path}>
          <span>{item.label}{item.unit ? ` [${item.unit}]` : ""}</span>
          <b>{formatValue(item.comparison_value)} → {formatValue(item.base_value)}{item.difference != null ? `（${signedFormat.format(item.difference)}）` : ""}</b>
        </div>)}
      </details>
      {[...contributionsByTarget.entries()].map(([target, items]) => <details className="activity-evidence" key={target}>
        <summary>{outputLabels.get(target) ?? target}への寄与（置換1入力あたり）</summary>
        {items.map((item) => <div key={`${item.target}-${item.path}`}>
          <span>{changeLabels.get(item.path) ?? item.path}</span>
          <b>{signedFormat.format(item.contribution)}</b>
        </div>)}
        <small>比較候補にその入力だけを戻した局所的な差です。因果効果ではありません。</small>
      </details>)}
      <ul className="activity-warnings">{result.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
    </section>}
  </>;
}
