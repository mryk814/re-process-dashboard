import { useEffect, useMemo, useState } from "react";

import type { TaskDefinitionContract } from "../candidates";
import {
  workbenchApi,
  type ApiDecisionCase,
  type ApiDecisionCaseDraftContext,
  type ApiDecisionReplayRun,
} from "../../shared/api/workbench-api";

const localDateTime = (value: string) => {
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, 23);
};

const candidateKey = (candidate: { candidate_id: string; candidate_revision: number }) =>
  `${candidate.candidate_id}@${candidate.candidate_revision}`;

export function DecisionReplayPanel({
  projectId,
  taskDefinition,
  onSelectCandidate,
}: {
  projectId: string;
  taskDefinition: TaskDefinitionContract;
  onSelectCandidate: (candidateId: string) => void;
}) {
  const [context, setContext] = useState<ApiDecisionCaseDraftContext | null>(null);
  const [cases, setCases] = useState<ApiDecisionCase[]>([]);
  const [runs, setRuns] = useState<ApiDecisionReplayRun[]>([]);
  const [activeCaseId, setActiveCaseId] = useState("");
  const [cutoff, setCutoff] = useState("");
  const [selectionKey, setSelectionKey] = useState("no_decision");
  const [rationale, setRationale] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    Promise.all([
      workbenchApi.decisionCaseDraftContext(projectId, controller.signal),
      workbenchApi.decisionCases(projectId, controller.signal),
      workbenchApi.decisionReplayRuns(projectId, undefined, controller.signal),
    ]).then(([nextContext, nextCases, nextRuns]) => {
      if (controller.signal.aborted) return;
      setContext(nextContext);
      setCases(nextCases);
      setRuns(nextRuns);
      setActiveCaseId(nextCases[0]?.id ?? "");
      const latestSnapshot = nextContext.snapshots.at(-1);
      setCutoff(localDateTime(latestSnapshot?.created_at ?? new Date().toISOString()));
      setSelectionKey(
        nextContext.current_selection.candidate
          ? candidateKey(nextContext.current_selection.candidate)
          : "no_decision",
      );
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) {
        setError(cause instanceof Error ? cause.message : "Decision Replayを読み込めませんでした。");
      }
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [projectId]);

  const cutoffIso = cutoff ? new Date(cutoff).toISOString() : "";
  const cutoffTime = cutoffIso ? Date.parse(cutoffIso) : Number.NaN;
  const evidence = useMemo(() => {
    if (!context || !cutoffIso) return [];
    const latest = new Map<string, (typeof context.snapshots)[number]>();
    for (const snapshot of context.snapshots) {
      if (Date.parse(snapshot.created_at) > cutoffTime) continue;
      const previous = latest.get(snapshot.candidate.candidate_id);
      if (!previous || Date.parse(previous.created_at) < Date.parse(snapshot.created_at)) {
        latest.set(snapshot.candidate.candidate_id, snapshot);
      }
    }
    return [...latest.values()].sort((left, right) =>
      left.candidate_name.localeCompare(right.candidate_name, "ja"));
  }, [context, cutoffIso, cutoffTime]);
  const evidenceKeys = new Set(evidence.map((item) => candidateKey(item.candidate)));
  const effectiveSelection = evidenceKeys.has(selectionKey) ? selectionKey : "no_decision";
  const laterActuals = useMemo(() => {
    if (!context || !cutoffIso) return [];
    const candidateIds = new Set(evidence.map((item) => item.candidate.candidate_id));
    return context.actuals.filter((actual) =>
      Date.parse(actual.created_at) > cutoffTime
      && candidateIds.has(actual.candidate_id)
      && context.target_keys.includes(actual.property));
  }, [context, cutoffIso, cutoffTime, evidence]);
  const activeCase = cases.find((item) => item.id === activeCaseId) ?? null;
  const activeRun = runs.find((item) => item.case_id === activeCaseId) ?? null;
  const outputByKey = new Map(taskDefinition.outputs.map((item) => [item.key, item]));

  const createCase = async () => {
    if (!context || !cutoffIso || evidence.length === 0) return;
    setSaving(true);
    setError("");
    try {
      const selected = evidence.find((item) => candidateKey(item.candidate) === effectiveSelection);
      const created = await workbenchApi.createDecisionCase(projectId, {
        schema_version: "decision-case-create/v1",
        decision_timestamp: cutoffIso,
        candidates: evidence.map((item) => item.candidate),
        snapshot_ids: evidence.map((item) => item.snapshot_id),
        selection: selected
          ? { status: "selected", candidate: selected.candidate }
          : { status: "no_decision", candidate: null },
        rationale: rationale.trim()
          ? {
              disposition: selected ? "selected" : "no_decision",
              rationale: rationale.trim(),
            }
          : null,
        actual_measurement_ids: laterActuals.map((item) => item.id),
        outcome_policy: {
          schema_version: "decision-outcome-policy/v1",
          target_keys: context.target_keys,
          missing_actual_policy: "retain_partial",
        },
      }, rationale.trim() ? "local-researcher" : undefined);
      setCases((current) => [created, ...current.filter((item) => item.id !== created.id)]);
      setActiveCaseId(created.id);
      setRationale("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Decision Caseを保存できませんでした。");
    } finally {
      setSaving(false);
    }
  };

  const replay = async () => {
    if (!activeCase) return;
    setSaving(true);
    setError("");
    try {
      const run = await workbenchApi.runDecisionReplay(projectId, activeCase.id, {
        schema_version: "decision-replay-request/v1",
        alternative_policy: "primary-objective-point-estimate/v1",
      });
      setRuns((current) => [run, ...current.filter((item) => item.id !== run.id)]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Decision Replayを実行できませんでした。");
    } finally {
      setSaving(false);
    }
  };

  return <section className="decision-replay-panel" aria-labelledby="decision-replay-heading">
    <header>
      <div>
        <span className="overline">DECISION REPLAY</span>
        <h2 id="decision-replay-heading">この判断は、当時の証拠から見て妥当でしたか。後から何が分かりましたか？</h2>
      </div>
    </header>

    {loading && <p role="status">Decision Caseを読み込んでいます。</p>}
    {error && <div className="decision-replay-warning" role="alert"><strong>この操作だけ完了していません</strong><span>{error}</span></div>}

    {!loading && context && <details className="decision-replay-create">
      <summary>判断時点を固定する</summary>
      <div className="decision-replay-create-body">
        <label>判断時刻<input type="datetime-local" step="0.001" value={cutoff} onChange={(event) => setCutoff(event.target.value)} /></label>
        <label>当時の選択<select value={effectiveSelection} onChange={(event) => setSelectionKey(event.target.value)}>
          <option value="no_decision">選択しなかった</option>
          {evidence.map((item) => <option key={candidateKey(item.candidate)} value={candidateKey(item.candidate)}>{item.candidate_name} · r{item.candidate.candidate_revision}</option>)}
        </select></label>
        <label className="decision-replay-rationale">判断理由（任意）<textarea rows={2} maxLength={4000} value={rationale} onChange={(event) => setRationale(event.target.value)} /></label>
        <div className="decision-replay-fixed-evidence">
          <strong>この時点までの保存済みSnapshot</strong>
          {evidence.length === 0 ? <span>利用できるSnapshotがありません。</span> : evidence.map((item) => <span key={item.snapshot_id}>{item.candidate_name} · r{item.candidate.candidate_revision}</span>)}
          <small>後から得たActual: {laterActuals.length}件。Snapshotより後の情報は「当時」へ入りません。</small>
        </div>
        <button className="primary-button" type="button" disabled={saving || evidence.length === 0} onClick={() => void createCase()}>Decision Caseを固定</button>
      </div>
    </details>}

    {cases.length > 0 && <div className="decision-replay-history" aria-label="保存済みDecision Case">
      {cases.map((item) => <button type="button" aria-current={item.id === activeCaseId} className={item.id === activeCaseId ? "active" : ""} key={item.id} onClick={() => setActiveCaseId(item.id)}>
        {new Date(item.decision_timestamp).toLocaleString("ja-JP")} · {item.selection.status === "selected" ? "選択あり" : "no decision"}
      </button>)}
    </div>}

    {!loading && cases.length === 0 && <p className="decision-replay-empty">保存済みDecision Caseはありません。判断時刻とSnapshotを固定すると、後日の実測と分けて振り返れます。</p>}

    {activeCase && <div className="decision-replay-result">
      <section className="decision-replay-layer historical" aria-labelledby="decision-replay-historical">
        <header><span>当時</span><h3 id="decision-replay-historical">判断時点で利用できた証拠</h3></header>
        <p>{activeCase.selection.status === "selected" ? "候補を選択した記録" : "候補を選択しなかった記録"}{activeCase.rationale ? ` · ${activeCase.rationale.rationale}` : ""}</p>
        <div className="decision-replay-candidates">
          {activeCase.historical_evidence.map((item) => <article key={item.snapshot_id}>
            <header><button type="button" className="link-button" onClick={() => onSelectCandidate(item.candidate.candidate_id)}>{item.candidate_name}</button><small>r{item.candidate.candidate_revision}</small></header>
            <dl>{Object.entries(item.predictions).map(([target, prediction]) => <div key={target}><dt>{outputByKey.get(target)?.label ?? target}</dt><dd>{prediction.value.toLocaleString("ja-JP")} {prediction.unit === "1" ? "" : prediction.unit}</dd></div>)}</dl>
            {item.warnings.length > 0 && <small className="warning">{item.warnings.join(" / ")}</small>}
          </article>)}
        </div>
      </section>

      <section className="decision-replay-layer retrospective" aria-labelledby="decision-replay-retrospective">
        <header><span>後から</span><h3 id="decision-replay-retrospective">実測と現在の見方</h3></header>
        {!activeRun && <div className="decision-replay-run-prompt"><p>当時のSnapshotは変えず、後着Actualと現在Packageによる再評価を別レイヤーで作成します。</p><button type="button" className="primary-button" disabled={saving} onClick={() => void replay()}>Replayを実行</button></div>}
        {activeRun && <>
          {activeRun.result.warnings.length > 0 && <div className="decision-replay-warning"><strong>実測は部分的です</strong><span>{activeRun.result.warnings.join(" / ")}</span></div>}
          {activeRun.result.unobserved_targets.length > 0 && <p className="decision-replay-unobserved">未観測 target: {activeRun.result.unobserved_targets.map((target) => outputByKey.get(target)?.label ?? target).join(" / ")}</p>}
          <div className="decision-replay-outcomes">
            {activeRun.result.realized_outcomes.length === 0 ? <p>Actualはまだ到着していません。</p> : activeRun.result.realized_outcomes.map((item) => <article key={item.actual_id}>
              <strong>{outputByKey.get(item.target)?.label ?? item.target}</strong>
              <span>実測 {item.observed_label ?? item.observed_value.toLocaleString("ja-JP")}</span>
              <span>当時の予測 {item.predicted_value.toLocaleString("ja-JP")}</span>
              <small>差 {item.absolute_error.toLocaleString("ja-JP")}</small>
            </article>)}
          </div>
          <p className="decision-replay-policy">固定policy: {activeRun.result.alternative_selection ? `候補 ${activeRun.result.alternative_selection.candidate_id}` : "比較不能"} · {activeRun.result.alternative_selection_reason}</p>
          <details className="decision-replay-hindsight"><summary>現在Packageでの再評価（hindsight）</summary>
            {activeRun.result.current_package_reevaluation.map((item) => <div key={candidateKey(item.candidate)}><button type="button" className="link-button" onClick={() => onSelectCandidate(item.candidate.candidate_id)}>{item.candidate.candidate_id}</button><span>{Object.entries(item.predictions).map(([target, prediction]) => `${outputByKey.get(target)?.label ?? target} ${prediction.value.toLocaleString("ja-JP")}`).join(" · ")}</span></div>)}
          </details>
          {activeRun.result.similar_cases.length > 0 && <div className="decision-replay-similar"><strong>同じTask・Objective・targetのCase</strong>{activeRun.result.similar_cases.map((item) => <div key={item.case_id}>
            <a href={`?view=project&project=${encodeURIComponent(item.project_id)}`}>{new Date(item.decision_timestamp).toLocaleDateString("ja-JP")} · {item.selection_status}</a>
            {item.snapshot_ids.map((snapshotId, index) => <a key={snapshotId} href={`?view=project&project=${encodeURIComponent(item.project_id)}&snapshot=${encodeURIComponent(snapshotId)}`}>Snapshot {index + 1}</a>)}
            {item.actual_references.map((actual) => <a key={actual.actual.id} href={`?view=candidates&project=${encodeURIComponent(item.project_id)}&candidate=${encodeURIComponent(actual.actual.candidate_id)}&candidate_section=actuals`}>Actual · {actual.actual.property}</a>)}
          </div>)}</div>}
        </>}
      </section>

      <details className="activity-run-provenance"><summary>Case identity</summary><dl>
        <div><dt>Case</dt><dd>{activeCase.id}</dd></div>
        <div><dt>Task contract</dt><dd>{activeCase.task_contract_digest}</dd></div>
        <div><dt>Objective</dt><dd>{activeCase.objective_definition_digest ?? "未固定"}</dd></div>
      </dl></details>
    </div>}
  </section>;
}
