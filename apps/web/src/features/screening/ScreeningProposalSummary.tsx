import type { ApiScreeningRun } from "../../shared/api/workbench-api";

const supportPolicyLabels: Record<string, string> = {
  supported_first: "近い学習実績がある条件を優先",
  exclude_extrapolated: "外挿候補を除外",
  allow_with_warning: "外挿候補も警告付きで表示",
};

const strategyDecisionLabels: Record<string, string> = {
  latin_hypercube_v1: "目標との距離で順位付け",
  bounded_simplex_goal_v1: "配合制約を保った点から目標との距離で順位付け",
  sobol_ucb_v1: "予測値と不確かさを合わせて順位付け",
  sobol_ei_v1: "現在の最良値からの改善を重視して順位付け",
  sobol_thompson_v1: "予測分布から候補を抽出",
  sobol_uncertainty_v1: "不確かさが大きい条件を優先",
  sobol_support_boundary_v1: "学習実績の支持境界を優先",
};

const reasonLabels: Record<string, string> = {
  "canonical identityが候補pool内で重複": "同じ条件が候補内で重複",
  "pending candidateとの近接を回避": "実験待ちの条件に近すぎる",
  "選抜済み条件とのnear-duplicate": "すでに選んだ条件に近すぎる",
  "resource constraint": "コスト・設備条件を満たさない",
  "batch全体の価値で選抜外": "バッチ全体のバランスから選外",
};

function displayReason(reason: string) {
  if (reasonLabels[reason]) return reasonLabels[reason];
  return /^[a-z0-9_.:/ -]+$/i.test(reason) ? "その他の制約" : reason;
}

export function ScreeningRunEvidence({ result }: { result: ApiScreeningRun }) {
  const strategy = result.proposal_strategy;
  const objective = result.objective_definition;
  const incumbentResolution = strategy?.incumbent_resolution;
  const diagnostics = result.proposal_diagnostics;
  const packageDigest = result.model_provenance?.package?.manifest_sha256;

  return (
    <details className="screening-run-evidence">
      <summary>計算記録</summary>
      <dl className="screening-run-evidence-summary">
        <div>
          <dt>Run</dt>
          <dd>seed {result.seed} · strategy {strategy?.id ?? "legacy"} {strategy?.version ?? ""}</dd>
        </div>
        <div>
          <dt>固定参照</dt>
          <dd>
            Model Package <code title={packageDigest ?? "記録なし"}>{packageDigest ?? "記録なし"}</code>
            {" · "}Design Space <code title={result.design_space_digest ?? "記録なし"}>{result.design_space_digest ?? "記録なし"}</code>
            {" · "}Objective <code title={result.objective_definition_digest ?? "記録なし"}>{result.objective_definition_digest ?? "記録なし"}</code>
          </dd>
        </div>
      </dl>
      <details className="screening-run-evidence-details">
        <summary>詳細な計算条件</summary>
        <p>
          generator {strategy?.generator_id ?? "記録なし"}
          {" / "}acquisition {strategy?.acquisition_id ?? "記録なし"}
          {" / "}selector {strategy?.selector_id ?? "記録なし"}
          {strategy?.support_policy && ` / support ${strategy.support_policy}`}
          {strategy?.fallback_from && ` / fallback ${strategy.fallback_from}`}
        </p>
        {strategy?.standard_deviation_methods?.length
          ? <p>standard deviation methods {strategy.standard_deviation_methods.join(", ")}</p>
          : null}
        <p>Model Package <code>{packageDigest ?? "記録なし"}</code></p>
        <p>Design Space <code>{result.design_space_digest ?? "記録なし"}</code></p>
        <p>Objective <code>{result.objective_definition_digest ?? "記録なし"}</code></p>
        {objective && <p>
          Objective revision {objective.revision} / {objective.optimization_kind}
          {" / "}binding {result.objective_binding_provenance ?? "記録なし"}
        </p>}
        {objective && <p>
          Objective terms: {objective.terms.map((term) => `${term.output_key}:${term.role}:${term.direction ?? "none"}:${term.unit}`).join(" / ")}
        </p>}
        {incumbentResolution?.source === "observed_project_actuals" && (
          <p>
            incumbent母集団: Project実測 {incumbentResolution.record_count}件
            {" / "}actual {incumbentResolution.actual_id}
            {" / "}filter {incumbentResolution.filter_digest}
            {" / "}population {incumbentResolution.population_digest}
          </p>
        )}
        {diagnostics && Object.keys(diagnostics.coverage_by_path ?? {}).length > 0 && (
          <p>
            生成coverage: {Object.entries(diagnostics.coverage_by_path ?? {})
              .map(([path, item]) => `${path} ${(item.normalized_span * 100).toLocaleString("ja-JP", { maximumFractionDigits: 0 })}%`)
              .join(" / ")}
          </p>
        )}
        {result.batch_proposal?.candidate_pool && (
          <p>batch pool digest <code>{result.batch_proposal.candidate_pool.pool_digest}</code></p>
        )}
        {result.batch_proposal && (
          <p>
            batch selector {result.batch_proposal.selector_id}
            {" / "}distance {result.batch_proposal.distance_id} {result.batch_proposal.distance_version}
            {" / "}tie-break pool index
          </p>
        )}
      </details>
    </details>
  );
}

export function ScreeningProposalSummary({
  result,
  targetLabel,
  showAnotherSample,
  onAnotherSample,
  onSaveBatch,
  batchSaveCount,
}: {
  result: ApiScreeningRun;
  targetLabel?: string;
  showAnotherSample: boolean;
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
  const isDesignSpaceMap = result.purpose === "design_space_map";
  const isExperimentBatch = result.purpose === "experiment_batch" || result.batch_proposal != null;
  const resolvedTargetLabel = targetLabel ?? result.target ?? "対象特性";
  const objective = result.objective_definition;
  const batchRoleLabel = {
    performance: "目標追求",
    exploration: "探索",
    boundary_check: "境界確認",
    diversity: "多様性",
    coverage: "カテゴリ網羅",
    control: "Control",
    replicate: "反復",
  } as const;
  const primaryTerm = objective?.terms.find((term) => term.role === "primary_objective");
  const proposalIntent = isDesignSpaceMap
    ? "設計領域の予測分布を確認"
    : isExperimentBatch
      ? `${resolvedTargetLabel}の有望点から実験バッチを選定`
      : primaryTerm?.direction === "maximize"
        ? `${resolvedTargetLabel}を最大化`
        : primaryTerm?.direction === "minimize"
          ? `${resolvedTargetLabel}を最小化`
          : result.target_goal?.direction === "between"
            ? `${resolvedTargetLabel}の目標範囲内を優先`
            : result.target_goal?.direction === "at_most"
              ? `${resolvedTargetLabel}の上限目標を満たす条件を優先`
            : result.target_goal?.direction === "at_least"
              ? `${resolvedTargetLabel}の下限目標を満たす条件を優先`
              : result.purpose === "goal_search"
                ? `${resolvedTargetLabel}が有望な条件を優先`
                : "旧記録の探索結果";
  const supportPolicyLabel = supportPolicyLabels[strategy?.support_policy ?? ""]
    ?? "支持範囲を確認";
  const proposedCount = result.batch_proposal?.selected.length
    ?? diagnostics?.selected_count
    ?? result.points?.length
    ?? result.samples;
  const secondaryConditionCount = objective?.terms.filter(
    (term) => term.role === "hard_outcome_constraint" || term.role === "soft_preference",
  ).length ?? 0;
  const rejectionEntries = Object.entries(rejectionReasons).sort((left, right) => right[1] - left[1]);
  const visibleRejections = rejectionEntries.slice(0, 3);
  const remainingRejectionCount = rejectionEntries
    .slice(3)
    .reduce((sum, [, count]) => sum + count, 0);

  return (
    <section className="screening-proposal-summary" aria-label="探索条件と提案診断">
      <div className="screening-proposal-headline">
        <b>{proposalIntent}（{supportPolicyLabel}）</b>
        {diagnostics
          ? <span>
              生成 {diagnostics.generated_count}件 → 制約内 {diagnostics.valid_count}件 → {isDesignSpaceMap ? "表示" : "提案"} {proposedCount}件
              {diagnostics.rejected_count > 0 && `（除外 ${diagnostics.rejected_count}件）`}
            </span>
          : <span>除外 {legacyRejectionCount}（旧記録・生成総数なし）</span>}
      </div>
      <details>
        <summary>判断根拠</summary>
        <dl className="screening-decision-reasons">
          <div>
            <dt>順位付け</dt>
            <dd title={strategy?.fallback_from ? `${strategy.fallback_from} → ${strategy.id}` : undefined}>
              {strategyDecisionLabels[strategy?.id ?? ""] ?? "保存時の提案方法で順位付け"}
              {strategy?.fallback_from && "（利用可能な標準方法へ切替）"}
            </dd>
          </div>
          <div>
            <dt>学習範囲</dt>
            <dd>{supportPolicyLabel}</dd>
          </div>
          <div>
            <dt>副条件</dt>
            <dd>{secondaryConditionCount > 0 ? `${secondaryConditionCount}項目を満たす点を優先` : "なし"}</dd>
          </div>
          <div>
            <dt>除外</dt>
            <dd>
              {visibleRejections.length > 0
                ? <>
                    {visibleRejections.map(([reason, count], index) => (
                      <span key={reason} title={reason}>
                        {index > 0 && " · "}{displayReason(reason)} {count}件
                      </span>
                    ))}
                    {remainingRejectionCount > 0 && ` · その他 ${remainingRejectionCount}件`}
                  </>
                : diagnostics
                  ? "制約による除外なし"
                  : legacyRejectionCount > 0
                    ? `旧記録のため、全生成数に対する除外率は算出できません（除外 ${legacyRejectionCount}件）`
                    : "記録なし"}
            </dd>
          </div>
        </dl>
      </details>
      {result.batch_proposal && (
        <details className="screening-batch-result">
          <summary>
            実験バッチ {result.batch_proposal.selected.length}枠
            <span>
              条件 {new Set(result.batch_proposal.selected.map((item) => item.canonical_identity_digest)).size}件 ·
              最小距離 {result.batch_proposal.summary.min_pairwise_distance.toLocaleString("ja-JP", { maximumFractionDigits: 3 })} ·
              見積コスト {result.batch_proposal.summary.estimated_total_cost.toLocaleString("ja-JP", { maximumFractionDigits: 2 })}
            </span>
          </summary>
          <p>
            有望度と条件間の違いを両立するように選定 /
            条件間の距離 {result.batch_proposal.distance_id === "group_weighted_bounded_clr_rms"
              ? "組成bounded CLR-RMS + 入力群均等"
              : "各入力軸のDesign Space幅で正規化（汎用）"}
          </p>
          {result.batch_proposal.candidate_pool && (
            <p>
              選定対象: 有望度上位 {result.batch_proposal.candidate_pool.acquisition_ranked_count}件
              {" + "}固定Control {result.batch_proposal.candidate_pool.exact_control_count}件
              {" / "}同一条件の重複を {result.batch_proposal.candidate_pool.duplicate_condition_count}件除外
            </p>
          )}
          <ol>
            {result.batch_proposal.selected.map((item) => (
              <li key={`${item.order}-${item.pool_index}`}>
                <span>
                  {item.point_index == null ? "Control" : `#${item.point_index + 1}`}
                  {" · "}{batchRoleLabel[item.role]} · <span title={item.reason}>{displayReason(item.reason)}</span>
                </span>
                <b>
                  {item.source === "exact_control"
                    ? <span title={`${item.candidate_id} r${item.candidate_revision}`}>固定Control revision {item.candidate_revision}</span>
                    : <>価値 {item.acquisition_component.toLocaleString("ja-JP", { maximumFractionDigits: 3 })}</>}
                  {" · "}多様性 {item.diversity_component.toLocaleString("ja-JP", { maximumFractionDigits: 3 })}
                  {" · "}コスト {item.estimated_cost.toLocaleString("ja-JP", { maximumFractionDigits: 2 })}
                </b>
              </li>
            ))}
          </ol>
          {result.batch_proposal.excluded.length > 0 && (
            <p>
              選抜外: {Object.entries(result.batch_proposal.excluded.reduce<Record<string, number>>((counts, item) => {
                counts[item.reason] = (counts[item.reason] ?? 0) + 1;
                return counts;
              }, {})).map(([reason, count]) => <span key={reason} title={reason}>{displayReason(reason)} {count}件 </span>)}
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
      {showAnotherSample && !result.batch_proposal && (
        <button className="outline-button" onClick={onAnotherSample}>
          {isDesignSpaceMap ? "別の点配置" : "別サンプル"}
        </button>
      )}
    </section>
  );
}
