import type { ApiScreeningRun } from "../../shared/api/workbench-api";

export function ScreeningProposalSummary({
  result,
  onAnotherSample,
}: {
  result: ApiScreeningRun;
  onAnotherSample: () => void;
}) {
  const diagnostics = result.proposal_diagnostics;
  const rejectionReasons = diagnostics?.rejected_by_reason ?? {};
  const legacyRejectionCount = result.rejection_summary
    ? Object.values(result.rejection_summary).reduce((sum, count) => sum + count, 0)
    : 0;
  const strategy = result.proposal_strategy;
  const strategyLabel = {
    latin_hypercube_v1: "Latin hypercube・目標基準",
    sobol_ucb_v1: "Sobol・UCB/LCB",
    sobol_ei_v1: "Sobol・Expected Improvement",
    sobol_thompson_v1: "Sobol・Thompson Sampling",
    sobol_uncertainty_v1: "Sobol・不確かさ探索",
    sobol_support_boundary_v1: "Sobol・学習支持境界",
  }[strategy?.id ?? ""] ?? strategy?.id ?? "旧方式";
  const objective = result.objective_definition;
  const objectiveProvenance = result.objective_binding_provenance === "explicit"
    ? "明示Objective"
    : "旧範囲探索の条件から固定";
  const roleLabel = {
    primary_objective: "主目的",
    hard_outcome_constraint: "必須条件",
    soft_preference: "選好",
    reporting_only: "表示のみ",
  } as const;

  return (
    <section className="screening-proposal-summary" aria-label="探索条件と提案診断">
      <div>
        <b>{strategyLabel}</b>
        <span>seed {result.seed}</span>
        {diagnostics
          ? <span>生成 {diagnostics.generated_count} · 制約内 {diagnostics.valid_count} · 評価 {diagnostics.evaluated_count} · 選抜 {diagnostics.selected_count} · 除外 {diagnostics.rejected_count}（{(diagnostics.rejection_rate * 100).toLocaleString("ja-JP", { maximumFractionDigits: 1 })}%）</span>
          : <span>除外 {legacyRejectionCount}（旧記録・生成総数なし）</span>}
        {result.design_space_digest && <code title={result.design_space_digest}>space {result.design_space_digest.replace("sha256:", "").slice(0, 10)}</code>}
        {result.objective_definition_digest && <code title={result.objective_definition_digest}>objective {result.objective_definition_digest.replace("sha256:", "").slice(0, 10)}</code>}
      </div>
      <details>
        <summary>条件と除外理由</summary>
        <p>
          strategy {strategy?.id ?? "legacy"} {strategy?.version ?? ""} / seed {result.seed}
          {strategy && ` / ${strategy.generator_id} → ${strategy.acquisition_id} → ${strategy.selector_id}`}
          {strategy?.exploration_parameter != null && ` / exploration ${strategy.exploration_parameter}`}
          {strategy?.support_policy && ` / support ${strategy.support_policy}`}
          {strategy?.incumbent_value != null && ` / incumbent ${strategy.incumbent_value}`}
        </p>
        {strategy?.uncertainty_treatment && <p>不確かさ: predictive standard deviation / 副条件: 満たす点を優先して順位付け</p>}
        {strategy?.fallback_from && <p>{strategy.fallback_from} は利用できなかったため、{strategy.id} で実行しました。</p>}
        {objective
          ? <>
              <p>{objective.name} r{objective.revision} · {objectiveProvenance} · {objective.optimization_kind}</p>
              <ul>
                {objective.terms.map((term) => (
                  <li key={term.output_key}>
                    <span>{term.output_key} · {roleLabel[term.role]} · {term.direction ?? "順位付けなし"}</span>
                    <b>{term.unit}</b>
                  </li>
                ))}
              </ul>
            </>
          : <p>旧記録のためObjective Definitionは固定されていません。</p>}
        {diagnostics && <p>生成した全{diagnostics.generated_count}件を制約判定し、制約内の点から{diagnostics.evaluated_count}件を評価しました。</p>}
        {diagnostics && Object.keys(rejectionReasons).length > 0
          ? <ul>{Object.entries(rejectionReasons).map(([reason, count]) => <li key={reason}><span>{reason}</span><b>{count}件</b></li>)}</ul>
          : <p>{diagnostics ? "制約による除外はありません。" : "旧記録のため、全生成数に対する除外率は算出できません。"}</p>}
        {(result.proposal_pool?.length ?? 0) > 0 && (
          <p>
            評価pool {result.proposal_pool?.length ?? 0}件を保存。
            選抜外 {result.proposal_pool?.filter((item) => item.exclusion_reason != null).length ?? 0}件も獲得関数の内訳とともに再現できます。
          </p>
        )}
        {(result.proposal_rejections?.length ?? 0) > 0 && (
          <p>除外した生成点 {result.proposal_rejections?.length ?? 0}件も入力値と理由を保存しています。</p>
        )}
      </details>
      <button className="outline-button" onClick={onAnotherSample}>別サンプル</button>
    </section>
  );
}
