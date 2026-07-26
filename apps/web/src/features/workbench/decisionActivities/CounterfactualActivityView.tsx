import { useEffect, useMemo, useState } from "react";
import { workbenchApi, type ApiDecisionActivityRun } from "../../../shared/api/workbench-api";
import type { DecisionActivityViewProps } from "./types";

const numberFormat = new Intl.NumberFormat("ja-JP", { maximumFractionDigits: 4 });

function counterfactualResult(run: ApiDecisionActivityRun) {
  return run.result.schema_version === "counterfactual-summary/v1" ? run.result : null;
}

export function CounterfactualActivityView({
  projectId,
  ready,
  availability,
  runs,
  running,
  onRun,
  onCandidateCreated,
  taskDefinition,
}: DecisionActivityViewProps) {
  const [sampleCount, setSampleCount] = useState(128);
  const [resultCount, setResultCount] = useState(5);
  const [maxChangedFields, setMaxChangedFields] = useState(4);
  const [seed, setSeed] = useState(20260726);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [savingId, setSavingId] = useState("");
  const [savedIds, setSavedIds] = useState<Set<string>>(new Set());
  const [saveError, setSaveError] = useState("");

  useEffect(() => {
    setSavedIds(new Set());
    setSaveError("");
  }, [projectId]);

  const activeRun = runs.find((run) => run.id === activeRunId) ?? runs[0] ?? null;
  const result = activeRun ? counterfactualResult(activeRun) : null;
  const canRun = ready
    && availability.available
    && sampleCount >= 48
    && sampleCount <= 512
    && resultCount >= 1
    && resultCount <= 10
    && maxChangedFields >= 1
    && maxChangedFields <= 12
    && !running;

  const proposalById = useMemo(
    () => new Map(result?.proposals.map((item) => [item.proposal_id, item]) ?? []),
    [result],
  );
  const outputLabels = useMemo(
    () => new Map(taskDefinition.outputs.map((item) => [item.key, item.label])),
    [taskDefinition.outputs],
  );

  async function promote(proposalId: string) {
    if (!activeRun || savingId || savedIds.has(proposalId)) return;
    setSavingId(proposalId);
    setSaveError("");
    try {
      const created = await workbenchApi.promoteDecisionActivityProposal(
        projectId,
        activeRun.id,
        proposalId,
      );
      setSavedIds((current) => new Set(current).add(proposalId));
      onCandidateCreated(created);
    } catch (cause) {
      setSaveError(cause instanceof Error ? cause.message : "候補にできませんでした。");
    } finally {
      setSavingId("");
    }
  }

  return <>
    <section className="activity-settings">
      <div className="panel-title">
        <h3>変更案の探索</h3>
        <span>Project目標とDesign Spaceを固定して、現在候補からの変更量を最小化します</span>
      </div>
      <div className="counterfactual-run-settings">
        <label>評価点数<input type="number" min={48} max={512} value={sampleCount} onChange={(event) => setSampleCount(Number(event.target.value))} /></label>
        <label>表示案数<input type="number" min={1} max={10} value={resultCount} onChange={(event) => setResultCount(Number(event.target.value))} /></label>
        <label>変更項目上限<input type="number" min={1} max={12} value={maxChangedFields} onChange={(event) => setMaxChangedFields(Number(event.target.value))} /></label>
        <label>乱数シード（seed）<input type="number" min={0} value={seed} onChange={(event) => setSeed(Number(event.target.value))} /></label>
        <button type="button" className="primary-button" disabled={!canRun} onClick={() => void onRun({
          schema_version: "counterfactual-parameters/v1",
          sample_count: sampleCount,
          result_count: resultCount,
          max_changed_fields: maxChangedFields,
          categorical_change_penalty: 1,
          immutable_paths: [],
          seed,
        })}>{running ? "探索中…" : "最小変更案を探す"}</button>
      </div>
      {!ready && <small>候補の入力を保存すると実行できます。</small>}
    </section>

    {runs.length > 0 && <nav className="activity-run-history" aria-label="保存済み目標到達案">
      <span>保存済み</span>
      {runs.slice(0, 5).map((run) => <button type="button" className={activeRun?.id === run.id ? "active" : ""} onClick={() => setActiveRunId(run.id)} key={run.id}>{new Date(run.created_at).toLocaleString("ja-JP")}</button>)}
    </nav>}

    {saveError && <p className="panel-error" role="alert">{saveError}</p>}
    {activeRun && result && <section className="activity-result">
      <div className="activity-result-meta">
        <span>基準候補版 {result.base_candidate_revision}</span>
        <span>{result.evaluated_count}条件を評価</span>
        <span>変更量＝正規化L1</span>
      </div>
      {result.status === "infeasible" && <div className="activity-unavailable">
        <strong>現在の範囲では目標へ届く案を確認できませんでした</strong>
        {result.infeasibility.map((item) => <span key={item.target}>
          {item.target}: 最良 {numberFormat.format(item.best_value)} {item.unit} — {item.explanation}
        </span>)}
      </div>}
      {result.proposals.map((proposal) => <article className="counterfactual-proposal" key={proposal.proposal_id}>
        <header>
          <span><b>案 {proposal.rank}</b><small>変更量 {numberFormat.format(proposal.change_distance)} / {proposal.changed_field_count}項目</small></span>
          <span className={`support-pill ${proposal.support.status}`}>{proposal.support.status}</span>
        </header>
        <div className="counterfactual-changes">
          {proposal.changes.map((change) => <div key={change.path}>
            <span>{change.label}</span>
            <b>{typeof change.base_value === "number" ? numberFormat.format(change.base_value) : change.base_value} → {typeof change.proposed_value === "number" ? numberFormat.format(change.proposed_value) : change.proposed_value}{change.unit ? ` ${change.unit}` : ""}</b>
          </div>)}
        </div>
        <div className="counterfactual-targets">
          {proposal.target_evaluations.map((target) => <span key={target.target} className={target.achieved ? "achieved" : ""}>
            {outputLabels.get(target.target) ?? target.target} <b>{numberFormat.format(target.predicted_value)} {target.unit}</b>
          </span>)}
        </div>
        {proposal.warnings.map((warning) => <small className="proposal-warning" key={warning}>{warning}</small>)}
        <button type="button" className="outline-button" disabled={savingId !== "" || savedIds.has(proposal.proposal_id) || !proposalById.has(proposal.proposal_id)} onClick={() => void promote(proposal.proposal_id)}>
          {savedIds.has(proposal.proposal_id) ? "候補に追加済み" : savingId === proposal.proposal_id ? "追加中…" : "この案を候補に追加"}
        </button>
      </article>)}
      <ul className="activity-warnings">{result.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
    </section>}
  </>;
}
