import { useEffect, useMemo, useRef, useState } from "react";
import {
  getCandidateInputValue,
  numericTaskInputs,
  type CandidateViewModel,
  type TaskDefinitionContract,
} from "../candidates";
import {
  workbenchApi,
  type ApiDecisionActivityAvailability,
  type ApiDecisionActivityRun,
  type ApiDecisionActivityRunRequest,
} from "../../shared/api/workbench-api";
import {
  acceptsDecisionActivityResponse,
  decisionActivityIdentity,
} from "./decisionActivityState";

const numberFormat = new Intl.NumberFormat("ja-JP", { maximumFractionDigits: 3 });
const percentFormat = new Intl.NumberFormat("ja-JP", { maximumFractionDigits: 0 });

function defaultTolerance(
  current: number,
  range: { min: number; max: number },
  practicalRange?: { min: number; max: number } | null,
): number {
  const practicalWidth = practicalRange ? practicalRange.max - practicalRange.min : range.max - range.min;
  const proposed = Math.max(Math.abs(current) * 0.02, practicalWidth * 0.01, 0.0001);
  const symmetricRoom = Math.min(current - range.min, range.max - current);
  return Math.min(proposed, symmetricRoom * 0.5);
}

export function DecisionActivityPanel({
  projectId,
  candidate,
  taskDefinition,
  ready,
  onClose,
}: {
  projectId: string;
  candidate: CandidateViewModel;
  taskDefinition: TaskDefinitionContract;
  ready: boolean;
  onClose: () => void;
}) {
  const identity = decisionActivityIdentity(projectId, candidate.id, candidate.raw.revision);
  const identityRef = useRef(identity);
  identityRef.current = identity;
  const requestControllerRef = useRef<AbortController | null>(null);
  const fields = useMemo(
    () => {
      const balancePaths = new Set(taskDefinition.composition_totals.map((item) => item.balance_path).filter(Boolean));
      return numericTaskInputs(taskDefinition).filter((field) => {
        const value = getCandidateInputValue(candidate.raw.inputs, field.path);
        return field.editable
          && field.allowed_range
          && typeof value === "number"
          && value > field.allowed_range.min
          && value < field.allowed_range.max
          && !balancePaths.has(field.path);
      });
    },
    [taskDefinition, candidate.raw.inputs],
  );
  const fieldByPath = useMemo(() => new Map(fields.map((field) => [field.path, field])), [fields]);
  const [availability, setAvailability] = useState<ApiDecisionActivityAvailability | null>(null);
  const [runs, setRuns] = useState<ApiDecisionActivityRun[]>([]);
  const [activeRun, setActiveRun] = useState<ApiDecisionActivityRun | null>(null);
  const [tolerances, setTolerances] = useState<Record<string, number>>({});
  const [nextField, setNextField] = useState("");
  const [sampleCount, setSampleCount] = useState(64);
  const [seed, setSeed] = useState(0);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    requestControllerRef.current?.abort();
    setRunning(false);
    const first = fields[0];
    if (!first?.allowed_range) {
      setTolerances({});
      setNextField("");
      return;
    }
    const current = Number(getCandidateInputValue(candidate.raw.inputs, first.path));
    setTolerances({
      [first.path]: defaultTolerance(current, first.allowed_range, first.default_range),
    });
    setNextField(fields[1]?.path ?? "");
  }, [identity]);

  useEffect(() => {
    const controller = new AbortController();
    const requestedIdentity = identity;
    setLoading(true);
    setError("");
    Promise.all([
      workbenchApi.decisionActivities(projectId, candidate.id, candidate.raw.revision, controller.signal),
      workbenchApi.decisionActivityRuns(projectId, candidate.id, controller.signal),
    ]).then(([items, savedRuns]) => {
      if (!acceptsDecisionActivityResponse(identityRef.current, requestedIdentity)) return;
      const robustness = items.find((item) => item.definition.activity_id === "robustness-analysis-v1") ?? null;
      setAvailability(robustness);
      setRuns(savedRuns);
      setActiveRun(savedRuns[0] ?? null);
    }).catch((cause: unknown) => {
      if (controller.signal.aborted || !acceptsDecisionActivityResponse(identityRef.current, requestedIdentity)) return;
      setError(cause instanceof Error ? cause.message : "検討アクティビティを取得できませんでした。");
    }).finally(() => {
      if (!controller.signal.aborted && acceptsDecisionActivityResponse(identityRef.current, requestedIdentity)) setLoading(false);
    });
    return () => controller.abort();
  }, [identity, projectId, candidate.id, candidate.raw.revision]);

  useEffect(() => () => requestControllerRef.current?.abort(), [identity]);

  const remainingFields = fields.filter((field) => tolerances[field.path] === undefined);
  const validTolerances = Object.values(tolerances).every((value) => Number.isFinite(value) && value > 0);
  const canRun = ready
    && availability?.available === true
    && Object.keys(tolerances).length > 0
    && validTolerances
    && Number.isInteger(sampleCount)
    && sampleCount >= 8
    && sampleCount <= 500
    && Number.isInteger(seed)
    && seed >= 0
    && !running;

  async function runActivity() {
    if (!canRun) return;
    const controller = new AbortController();
    requestControllerRef.current?.abort();
    requestControllerRef.current = controller;
    const requestedIdentity = identity;
    setRunning(true);
    setError("");
    const body: ApiDecisionActivityRunRequest = {
      expected_revision: candidate.raw.revision,
      parameters: {
        schema_version: "robustness-parameters/v1",
        sample_count: sampleCount,
        seed,
        tolerance_profile: {
          fields: Object.fromEntries(
            Object.entries(tolerances).map(([path, amount]) => [
              path,
              { kind: "absolute" as const, amount },
            ]),
          ),
        },
      },
    };
    try {
      const result = await workbenchApi.runDecisionActivity(
        projectId,
        candidate.id,
        "robustness-analysis-v1",
        body,
        controller.signal,
      );
      if (!acceptsDecisionActivityResponse(identityRef.current, requestedIdentity)) return;
      setRuns((current) => [result, ...current.filter((item) => item.id !== result.id)]);
      setActiveRun(result);
    } catch (cause) {
      if (!controller.signal.aborted && acceptsDecisionActivityResponse(identityRef.current, requestedIdentity)) {
        setError(cause instanceof Error ? cause.message : "ロバストネス解析を実行できませんでした。");
      }
    } finally {
      if (requestControllerRef.current === controller) requestControllerRef.current = null;
      if (!controller.signal.aborted && acceptsDecisionActivityResponse(identityRef.current, requestedIdentity)) setRunning(false);
    }
  }

  function addTolerance() {
    const field = fieldByPath.get(nextField);
    if (!field?.allowed_range) return;
    const current = Number(getCandidateInputValue(candidate.raw.inputs, field.path));
    setTolerances((values) => ({
      ...values,
      [field.path]: defaultTolerance(current, field.allowed_range!, field.default_range),
    }));
    setNextField(remainingFields.find((item) => item.path !== field.path)?.path ?? "");
  }

  const outputLabels = new Map(taskDefinition.outputs.map((output) => [output.key, output.label]));
  return <aside className="decision-activity-panel" aria-label="検討アクティビティ">
    <header>
      <div><span className="overline">DECISION ACTIVITY</span><h2>ロバストネス／公差解析</h2></div>
      <button type="button" className="outline-button" onClick={onClose}>閉じる</button>
    </header>
    <p className="activity-question">製造ばらつきがあっても目標を安定して満たすか</p>
    {loading ? <p className="empty-evidence">利用条件を確認しています。</p> : availability && !availability.available ? (
      <div className="activity-unavailable"><strong>現在は利用できません</strong>{availability.reasons.map((reason) => <span key={reason}>{reason}</span>)}</div>
    ) : null}
    {error && <p className="panel-error" role="alert">{error}</p>}

    <section className="activity-settings">
      <div className="panel-title"><h3>入力の公差</h3><span>現在値を中心とした±幅</span></div>
      <div className="activity-tolerance-list">
        {Object.entries(tolerances).map(([path, amount]) => {
          const field = fieldByPath.get(path);
          return <div key={path}>
            <span><b>{field?.label ?? path}</b><small>{field?.unit}</small></span>
            <label>± <input aria-label={`${field?.label ?? path}の公差幅`} type="number" min="0" step="any" value={amount} onChange={(event) => setTolerances((current) => ({ ...current, [path]: Number(event.target.value) }))} /></label>
            <button type="button" className="text-button" aria-label={`${field?.label ?? path}を公差対象から外す`} onClick={() => setTolerances((current) => {
              const { [path]: _removed, ...remaining } = current;
              return remaining;
            })}>外す</button>
          </div>;
        })}
      </div>
      {remainingFields.length > 0 && <div className="activity-add-tolerance">
        <select aria-label="追加する公差対象" value={nextField} onChange={(event) => setNextField(event.target.value)}>
          {remainingFields.map((field) => <option value={field.path} key={field.path}>{field.label}</option>)}
        </select>
        <button type="button" className="outline-button" onClick={addTolerance}>公差を追加</button>
      </div>}
      <div className="activity-run-settings">
        <label>サンプル数<input type="number" min={8} max={500} value={sampleCount} onChange={(event) => setSampleCount(Number(event.target.value))} /></label>
        <label>乱数シード（seed）<input type="number" min={0} value={seed} onChange={(event) => setSeed(Number(event.target.value))} /></label>
        <button type="button" className="primary-button" disabled={!canRun} onClick={() => void runActivity()}>{running ? "解析中…" : "公差内を解析"}</button>
      </div>
      {!ready && <small>候補の入力を保存すると実行できます。</small>}
    </section>

    {runs.length > 0 && <nav className="activity-run-history" aria-label="保存済みロバストネス解析">
      <span>保存済み</span>
      {runs.slice(0, 5).map((run) => <button type="button" className={activeRun?.id === run.id ? "active" : ""} onClick={() => setActiveRun(run)} key={run.id}>{new Date(run.created_at).toLocaleString("ja-JP")}</button>)}
    </nav>}

    {activeRun && <section className="activity-result">
      <div className="activity-result-meta"><span>候補版 {activeRun.provenance.candidate_revision}</span><span>{activeRun.result.accepted_samples}/{activeRun.result.requested_samples}件を評価</span></div>
      <div className="activity-targets">{activeRun.result.target_summaries.map((summary) => <article key={summary.target}>
        <header><strong>{outputLabels.get(summary.target) ?? summary.target}</strong>{summary.goal_achievement_rate != null && <b>目標達成 {percentFormat.format(summary.goal_achievement_rate * 100)}%</b>}</header>
        <dl>
          <div><dt>基準予測</dt><dd>{numberFormat.format(summary.base_prediction.value)} {summary.unit}</dd></div>
          <div><dt>入力ばらつき</dt><dd>{numberFormat.format(summary.input_variation.lower)}–{numberFormat.format(summary.input_variation.upper)} {summary.unit}</dd></div>
          <div><dt>モデル不確実性</dt><dd>{numberFormat.format(summary.model_uncertainty.lower)}–{numberFormat.format(summary.model_uncertainty.upper)} {summary.unit}</dd></div>
          <div><dt>公差内の最悪値</dt><dd>{numberFormat.format(summary.worst_observed)} {summary.unit}</dd></div>
        </dl>
      </article>)}</div>
      <div className="activity-support-summary">
        <span>モデル支持範囲外 <b>{percentFormat.format(activeRun.result.extrapolated_rate * 100)}%</b></span>
        <span>注意域 <b>{percentFormat.format(activeRun.result.caution_rate * 100)}%</b></span>
      </div>
      {activeRun.result.critical_inputs.length > 0 && <details className="activity-evidence">
        <summary>ばらつきと結び付きが強い入力</summary>
        {activeRun.result.critical_inputs.map((item) => <div key={`${item.target}-${item.path}`}><span>{fieldByPath.get(item.path)?.label ?? item.path} → {outputLabels.get(item.target) ?? item.target}</span><b>相関の強さ |r| {numberFormat.format(item.absolute_correlation)}</b></div>)}
        <small>局所サンプル内の相関であり、因果効果ではありません。</small>
      </details>}
      {activeRun.result.failure_examples.length > 0 && <details className="activity-evidence">
        <summary>代表的な未達・支持範囲外条件（{activeRun.result.failure_examples.length}件）</summary>
        {activeRun.result.failure_examples.map((example) => <div key={example.sample_index}><span>{Object.entries(example.varied_inputs).map(([path, value]) => `${fieldByPath.get(path)?.label ?? path} ${numberFormat.format(value)}`).join(" / ")}</span><b>{example.failed_targets.length ? `${example.failed_targets.join(", ")} 未達` : "支持範囲外"}</b></div>)}
      </details>}
      <ul className="activity-warnings">{activeRun.result.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
    </section>}
  </aside>;
}
