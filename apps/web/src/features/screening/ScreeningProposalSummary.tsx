import type { ApiScreeningRun } from "../../shared/api/workbench-api";

export function ScreeningProposalSummary({
  result,
  onAnotherSample,
  onSaveBatch,
  batchSaveCount,
}: {
  result: ApiScreeningRun;
  onAnotherSample: () => void;
  onSaveBatch: () => void;
  batchSaveCount: number;
}) {
  const diagnostics = result.proposal_diagnostics;
  const rejectionReasons = diagnostics?.rejected_by_reason ?? {};
  const legacyRejectionCount = result.rejection_summary
    ? Object.values(result.rejection_summary).reduce((sum, count) => sum + count, 0)
    : 0;
  const strategy = result.proposal_strategy;
  const strategyLabel = {
    latin_hypercube_v1: "Latin hypercube・目標基準",
    bounded_simplex_goal_v1: "Bounded simplex・目標基準（組成向け）",
    sobol_ucb_v1: "Sobol・UCB/LCB",
    sobol_ei_v1: "Sobol・Expected Improvement",
    sobol_thompson_v1: "Sobol・Thompson Sampling",
    sobol_uncertainty_v1: "Sobol・不確かさ探索",
    sobol_support_boundary_v1: "Sobol・学習支持境界",
  }[strategy?.id ?? ""] ?? strategy?.id ?? "旧方式";
  const distanceLabel = strategy?.distance_id === "group_weighted_bounded_clr_rms"
    ? "組成bounded CLR-RMS + 入力群均等"
    : "各scalar軸のDesign Space幅正規化RMS（汎用）";
  const objective = result.objective_definition;
  const objectiveProvenance = result.objective_binding_provenance === "explicit"
    ? "明示Objective"
    : result.objective_binding_provenance === "project_revision"
      ? "Project固定Objective"
      : "旧範囲探索の条件から固定";
  const incumbentSourceLabel = {
    none: "なし",
    request_override: "手入力",
    objective_candidate_revision: "候補revision",
    objective_prediction_snapshot: "予測snapshot",
    objective_project_decision: "Project採用判断",
    observed_project_actuals: "Project実測の最良値",
  } as const;
  const incumbentResolution = strategy?.incumbent_resolution;
  const roleLabel = {
    primary_objective: "主目的",
    hard_outcome_constraint: "必須条件",
    soft_preference: "選好",
    reporting_only: "表示のみ",
  } as const;
  const batchRoleLabel = {
    performance: "目標追求",
    exploration: "探索",
    boundary_check: "境界確認",
    diversity: "多様性",
    coverage: "カテゴリ網羅",
    control: "Control",
    replicate: "反復",
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
          {strategy && ` / バッチ選抜距離 ${distanceLabel}${result.batch_proposal ? "" : "（このRunでは未使用）"}`}
          {strategy?.exploration_parameter != null && (
            strategy.parameter_role === "improvement_margin"
              ? ` / 改善余裕 ξ ${strategy.exploration_parameter}`
              : ` / 探索σ倍率 ${strategy.exploration_parameter}`
          )}
          {strategy?.support_policy && ` / support ${strategy.support_policy}`}
          {incumbentResolution && incumbentResolution.value != null && (
            ` / incumbent ${incumbentResolution.value}（${incumbentSourceLabel[incumbentResolution.source]}）`
          )}
        </p>
        {strategy?.uncertainty_treatment && (
          <p>
            不確かさ: {strategy.acquisition_representation === "normal_mean_std"
              ? "予測平均 + 標準偏差（正規近似）"
              : "predictive standard deviation"}
            {strategy.standard_deviation_methods?.length
              ? ` / σの由来: ${strategy.standard_deviation_methods.join(", ")}`
              : ""}
            {" / 副条件: 満たす点を優先して順位付け"}
          </p>
        )}
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
        {incumbentResolution?.source === "observed_project_actuals" && (
          <p>
            incumbent母集団: Project実測 {incumbentResolution.record_count}件
            {" / "}actual {incumbentResolution.actual_id}
            {" / "}filter {incumbentResolution.filter_digest?.replace("sha256:", "").slice(0, 10)}
            {" / "}population {incumbentResolution.population_digest?.replace("sha256:", "").slice(0, 10)}
          </p>
        )}
        {diagnostics && <p>生成した全{diagnostics.generated_count}件を制約判定し、制約内の点から{diagnostics.evaluated_count}件を評価しました。</p>}
        {diagnostics && Object.keys(diagnostics.coverage_by_path ?? {}).length > 0 && (
          <p>
            生成coverage: {Object.entries(diagnostics.coverage_by_path ?? {})
              .map(([path, item]) => `${path} ${(item.normalized_span * 100).toLocaleString("ja-JP", { maximumFractionDigits: 0 })}%`)
              .join(" / ")}
          </p>
        )}
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
      {result.batch_proposal && (
        <details className="screening-batch-result" open>
          <summary>
            実験バッチ {result.batch_proposal.selected.length}枠
            <span>
              条件 {new Set(result.batch_proposal.selected.map((item) => item.canonical_identity_digest)).size}件 ·
              最小距離 {result.batch_proposal.summary.min_pairwise_distance.toLocaleString("ja-JP", { maximumFractionDigits: 3 })} ·
              見積コスト {result.batch_proposal.summary.estimated_total_cost.toLocaleString("ja-JP", { maximumFractionDigits: 2 })}
            </span>
          </summary>
          <p>
            {result.batch_proposal.selector_id} / 獲得順位価値 /
            距離 {result.batch_proposal.distance_id === "group_weighted_bounded_clr_rms"
              ? "組成bounded CLR-RMS + 入力群均等"
              : "各scalar軸のDesign Space幅正規化RMS（汎用）"} /
            tie-break: pool index
          </p>
          {result.batch_proposal.candidate_pool && (
            <p>
              batch候補pool: 獲得順位上位 {result.batch_proposal.candidate_pool.acquisition_ranked_count}件
              {" + "}exact Control {result.batch_proposal.candidate_pool.exact_control_count}件
              {" / "}canonical重複除外 {result.batch_proposal.candidate_pool.duplicate_condition_count}件
              {" / "}digest {result.batch_proposal.candidate_pool.pool_digest.replace("sha256:", "").slice(0, 10)}
            </p>
          )}
          <ol>
            {result.batch_proposal.selected.map((item) => (
              <li key={`${item.order}-${item.pool_index}`}>
                <span>
                  {item.point_index == null ? "Control" : `#${item.point_index + 1}`}
                  {" · "}{batchRoleLabel[item.role]} · {item.reason}
                </span>
                <b>
                  {item.source === "exact_control"
                    ? `exact ${item.candidate_id} r${item.candidate_revision}`
                    : `獲得順位価値 ${item.acquisition_component.toLocaleString("ja-JP", { maximumFractionDigits: 3 })}`}
                  {" · "}div {item.diversity_component.toLocaleString("ja-JP", { maximumFractionDigits: 3 })}
                  {" · "}cost {item.estimated_cost.toLocaleString("ja-JP", { maximumFractionDigits: 2 })}
                </b>
              </li>
            ))}
          </ol>
          {result.batch_proposal.excluded.length > 0 && (
            <p>
              選抜外: {Object.entries(result.batch_proposal.excluded.reduce<Record<string, number>>((counts, item) => {
                counts[item.reason] = (counts[item.reason] ?? 0) + 1;
                return counts;
              }, {})).map(([reason, count]) => `${reason} ${count}件`).join(" / ")}
            </p>
          )}
          <button
            className="primary-button"
            disabled={!batchSaveCount}
            onClick={onSaveBatch}
          >
            {batchSaveCount ? `提案した${batchSaveCount}条件を候補へ保存` : "提案条件は保存済み"}
          </button>
          <small>反復は同じ候補条件に複数観測を計画するため、候補保存時は1条件にまとめます。</small>
        </details>
      )}
      <button className="outline-button" onClick={onAnotherSample}>別サンプル</button>
    </section>
  );
}
