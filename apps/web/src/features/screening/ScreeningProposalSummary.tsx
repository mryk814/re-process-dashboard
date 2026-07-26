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
  const strategyLabel = strategy?.id === "latin_hypercube_v1"
    ? "Latin hypercube"
    : strategy?.id ?? "旧方式";
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
          ? <span>生成 {diagnostics.generated_count} · 制約内 {diagnostics.valid_count} · 評価 {diagnostics.evaluated_count} · 除外 {diagnostics.rejected_count}（{(diagnostics.rejection_rate * 100).toLocaleString("ja-JP", { maximumFractionDigits: 1 })}%）</span>
          : <span>除外 {legacyRejectionCount}（旧記録・生成総数なし）</span>}
        {result.design_space_digest && <code title={result.design_space_digest}>space {result.design_space_digest.replace("sha256:", "").slice(0, 10)}</code>}
        {result.objective_definition_digest && <code title={result.objective_definition_digest}>objective {result.objective_definition_digest.replace("sha256:", "").slice(0, 10)}</code>}
      </div>
      <details>
        <summary>条件と除外理由</summary>
        <p>strategy {strategy?.id ?? "legacy"} {strategy?.version ?? ""} / seed {result.seed}</p>
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
      </details>
      <button className="outline-button" onClick={onAnotherSample}>別サンプル</button>
    </section>
  );
}
