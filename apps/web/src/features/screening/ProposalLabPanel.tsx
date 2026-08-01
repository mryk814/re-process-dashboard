import { useEffect, useMemo, useState } from "react";

import {
  workbenchApi,
  type ApiProposalLabReport,
  type ApiScreeningRun,
} from "../../shared/api/workbench-api";


const statusLabel = {
  experimental: "継続評価",
  production: "採用判定（registry変更なし）",
  no_adopt: "不採用",
} as const;

type DecisionStatus = keyof typeof statusLabel;

function percent(value: number) {
  return `${(value * 100).toFixed(0)}%`;
}

export function ProposalLabPanel({
  projectId,
  runs,
}: {
  projectId: string;
  runs: ApiScreeningRun[];
}) {
  const comparableRuns = useMemo(
    () => runs.filter(
      (run) => run.purpose === "goal_search"
        && run.proposal_strategy != null
        && run.schema_version === "screening-run/v8",
    ),
    [runs],
  );
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [reports, setReports] = useState<ApiProposalLabReport[]>([]);
  const [decisionStrategy, setDecisionStrategy] = useState("");
  const [decisionStatus, setDecisionStatus] = useState<DecisionStatus>("experimental");
  const [criterion, setCriterion] = useState("目標達成率と支持範囲内率");
  const [rationale, setRationale] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let active = true;
    setSelected(new Set());
    setReports([]);
    setError("");
    workbenchApi.proposalLabReports(projectId)
      .then((items) => { if (active) setReports(items); })
      .catch((cause) => {
        if (active) setError(cause instanceof Error ? cause.message : "評価記録を取得できませんでした。");
      });
    return () => { active = false; };
  }, [projectId]);

  const selectedRuns = comparableRuns.filter((run) => selected.has(run.id));
  const selectedStrategies = [...new Set(
    selectedRuns.map((run) => run.proposal_strategy?.id).filter((id): id is string => Boolean(id)),
  )];
  const seedSets = new Map<string, Set<number>>();
  selectedRuns.forEach((run) => {
    const id = run.proposal_strategy?.id;
    if (!id) return;
    const seeds = seedSets.get(id) ?? new Set<number>();
    seeds.add(run.seed);
    seedSets.set(id, seeds);
  });
  const alignedSeeds = selectedStrategies.length >= 2
    && [...seedSets.values()].every((seeds) => seeds.size >= 2)
    && new Set(
      [...seedSets.values()].map((seeds) => [...seeds].sort((a, b) => a - b).join(",")),
    ).size === 1;

  useEffect(() => {
    if (!selectedStrategies.includes(decisionStrategy)) {
      setDecisionStrategy(selectedStrategies[0] ?? "");
    }
  }, [decisionStrategy, selectedStrategies]);

  const save = async () => {
    if (!alignedSeeds) {
      setError("2種類以上のstrategyについて、同じ2個以上のseedを選んでください。");
      return;
    }
    if (!decisionStrategy || !rationale.trim()) {
      setError("判定するstrategyと根拠を入力してください。");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const created = await workbenchApi.createProposalLabReport(projectId, {
        run_ids: selectedRuns.map((run) => run.id),
        evaluation_fixture_version: "saved-screening-replay/v1",
        adoption_memos: [{
          strategy_id: decisionStrategy,
          status: decisionStatus,
          primary_criterion: criterion,
          rationale: rationale.trim(),
          trade_offs: [],
        }],
      });
      setReports((current) => [
        created,
        ...current.filter((item) => item.id !== created.id),
      ]);
      setRationale("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Proposal Labを保存できませんでした。");
    } finally {
      setSaving(false);
    }
  };

  return (
    <details className="proposal-lab">
      <summary>
        <span>Proposal Lab</span>
        <small>保存済みRunを同じprotocolで比較 · productionへ自動反映しない</small>
      </summary>
      <div className="proposal-lab-body">
        <p>
          acquisition scoreは成功確率ではありません。同じfixture・budgetと揃えたseedで、
          目標達成、support、失敗、計算量のtrade-offを確認します。
        </p>
        <div className="proposal-lab-run-list" aria-label="比較する保存済みRun">
          {comparableRuns.length === 0 && <p>比較できる有望候補Runがありません。</p>}
          {comparableRuns.map((run) => (
            <label key={run.id}>
              <input
                type="checkbox"
                checked={selected.has(run.id)}
                onChange={(event) => {
                  setSelected((current) => {
                    const next = new Set(current);
                    if (event.target.checked) next.add(run.id);
                    else next.delete(run.id);
                    return next;
                  });
                }}
              />
              <span>
                <b>{run.proposal_strategy?.id}</b>
                <small>seed {run.seed} · budget {(run.proposal_strategy?.pool_multiplier ?? 0) * run.samples} · {run.id.slice(0, 8)}</small>
              </span>
            </label>
          ))}
        </div>
        <p className={alignedSeeds ? "proposal-lab-ready" : "proposal-lab-hint"} role="status">
          {alignedSeeds
            ? `${selectedStrategies.length} strategies · seed ${[...(seedSets.values().next().value ?? [])].sort((a, b) => a - b).join(", ")}`
            : "2種類以上のstrategyに、同じ2個以上のseedを揃えて選択"}
        </p>
        <div className="proposal-lab-decision">
          <label>
            判定対象
            <select value={decisionStrategy} onChange={(event) => setDecisionStrategy(event.target.value)}>
              <option value="">選択してください</option>
              {selectedStrategies.map((id) => <option key={id} value={id}>{id}</option>)}
            </select>
          </label>
          <label>
            判定
            <select value={decisionStatus} onChange={(event) => setDecisionStatus(event.target.value as DecisionStatus)}>
              {Object.entries(statusLabel).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label>
            主基準
            <input value={criterion} onChange={(event) => setCriterion(event.target.value)} />
          </label>
          <label className="proposal-lab-rationale">
            根拠
            <textarea
              rows={2}
              value={rationale}
              placeholder="採用価値とtrade-offを記録"
              onChange={(event) => setRationale(event.target.value)}
            />
          </label>
          <button type="button" className="primary-button" disabled={saving || !alignedSeeds} onClick={() => { void save(); }}>
            {saving ? "保存中…" : "評価記録を保存"}
          </button>
        </div>
        {error && <p className="error-banner">{error}</p>}
        {reports.length > 0 && (
          <div className="proposal-lab-reports">
            <h4>保存済み評価</h4>
            {reports.map((report) => (
              <article key={report.id}>
                <header>
                  <b>{report.adoption_memos.map((memo) => `${memo.strategy_id}: ${statusLabel[memo.status]}`).join(" / ")}</b>
                  <small>{new Date(report.created_at).toLocaleString("ja-JP")} · {report.id.slice(-8)}</small>
                </header>
                <div className="proposal-lab-metrics">
                  {report.strategy_summaries.map((summary) => (
                    <span key={summary.strategy_id}>
                      <b>{summary.strategy_id}</b>
                      目標 {percent(summary.mean_goal_achievement_rate)}
                      {" · "}support {percent(summary.mean_supported_rate)}
                      {" · "}constraint不明 {percent(summary.mean_constraint_unknown_rate)}
                      {" · "}seed差 {percent(summary.goal_achievement_rate_range)}
                      {" · "}{summary.acquisition_scope === "joint" ? "joint acquisition" : "marginal ranking"}
                    </span>
                  ))}
                </div>
                <p>{report.adoption_memos.map((memo) => memo.rationale).join(" / ")}</p>
                <small>この記録はregistryを変更していません。</small>
              </article>
            ))}
          </div>
        )}
      </div>
    </details>
  );
}
