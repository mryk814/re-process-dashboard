import { useEffect, useMemo, useState } from "react";
import { getCandidateInputValue, numericTaskInputs } from "../../candidates";
import type { ApiDecisionActivityRun } from "../../../shared/api/workbench-api";
import type { DecisionActivityViewProps } from "./types";

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

function robustnessResult(run: ApiDecisionActivityRun) {
  return run.result.schema_version === "robustness-summary/v1" ? run.result : null;
}

export function RobustnessActivityView({
  candidate,
  taskDefinition,
  ready,
  availability,
  runs,
  running,
  onRun,
}: DecisionActivityViewProps) {
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
  const [tolerances, setTolerances] = useState<Record<string, number>>({});
  const [nextField, setNextField] = useState("");
  const [sampleCount, setSampleCount] = useState(64);
  const [seed, setSeed] = useState(0);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);

  useEffect(() => {
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
  }, [candidate.id, candidate.raw.revision]);

  const activeRun = runs.find((run) => run.id === activeRunId) ?? runs[0] ?? null;
  const result = activeRun ? robustnessResult(activeRun) : null;
  const remainingFields = fields.filter((field) => tolerances[field.path] === undefined);
  const validTolerances = Object.values(tolerances).every((value) => Number.isFinite(value) && value > 0);
  const canRun = ready
    && availability.available
    && Object.keys(tolerances).length > 0
    && validTolerances
    && Number.isInteger(sampleCount)
    && sampleCount >= 8
    && sampleCount <= 500
    && Number.isInteger(seed)
    && seed >= 0
    && !running;

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
  return <>
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
        <button type="button" className="primary-button" disabled={!canRun} onClick={() => void onRun({
          schema_version: "robustness-parameters/v1",
          sample_count: sampleCount,
          seed,
          tolerance_profile: {
            fields: Object.fromEntries(
              Object.entries(tolerances).map(([path, amount]) => [path, { kind: "absolute" as const, amount }]),
            ),
          },
        })}>{running ? "解析中…" : "公差内を解析"}</button>
      </div>
      {!ready && <small>候補の入力を保存すると実行できます。</small>}
    </section>

    {runs.length > 0 && <nav className="activity-run-history" aria-label="保存済みロバストネス解析">
      <span>保存済み</span>
      {runs.slice(0, 5).map((run) => <button type="button" className={activeRun?.id === run.id ? "active" : ""} onClick={() => setActiveRunId(run.id)} key={run.id}>{new Date(run.created_at).toLocaleString("ja-JP")}</button>)}
    </nav>}

    {activeRun && result && <section className="activity-result">
      <div className="activity-result-meta"><span>候補版 {activeRun.provenance.candidate_revision}</span><span>{result.accepted_samples}/{result.requested_samples}件を評価</span></div>
      <div className="activity-targets">{result.target_summaries.map((summary) => <article key={summary.target}>
        <header><strong>{outputLabels.get(summary.target) ?? summary.target}</strong>{summary.goal_achievement_rate != null && <b>目標達成 {percentFormat.format(summary.goal_achievement_rate * 100)}%</b>}</header>
        <dl>
          <div><dt>基準予測</dt><dd>{numberFormat.format(summary.base_prediction.value)} {summary.unit}</dd></div>
          <div><dt>入力ばらつき</dt><dd>{numberFormat.format(summary.input_variation.lower)}–{numberFormat.format(summary.input_variation.upper)} {summary.unit}</dd></div>
          <div><dt>モデル不確実性</dt><dd>{numberFormat.format(summary.model_uncertainty.lower)}–{numberFormat.format(summary.model_uncertainty.upper)} {summary.unit}</dd></div>
          <div><dt>公差内の最悪値</dt><dd>{numberFormat.format(summary.worst_observed)} {summary.unit}</dd></div>
        </dl>
      </article>)}</div>
      <div className="activity-support-summary">
        <span>モデル支持範囲外 <b>{percentFormat.format(result.extrapolated_rate * 100)}%</b></span>
        <span>注意域 <b>{percentFormat.format(result.caution_rate * 100)}%</b></span>
      </div>
      {result.critical_inputs.length > 0 && <details className="activity-evidence">
        <summary>ばらつきと結び付きが強い入力</summary>
        {result.critical_inputs.map((item) => <div key={`${item.target}-${item.path}`}><span>{fieldByPath.get(item.path)?.label ?? item.path} → {outputLabels.get(item.target) ?? item.target}</span><b>相関の強さ |r| {numberFormat.format(item.absolute_correlation)}</b></div>)}
        <small>局所サンプル内の相関であり、因果効果ではありません。</small>
      </details>}
      {result.failure_examples.length > 0 && <details className="activity-evidence">
        <summary>代表的な未達・支持範囲外条件（{result.failure_examples.length}件）</summary>
        {result.failure_examples.map((example) => <div key={example.sample_index}><span>{Object.entries(example.varied_inputs).map(([path, value]) => `${fieldByPath.get(path)?.label ?? path} ${numberFormat.format(value)}`).join(" / ")}</span><b>{example.failed_targets.length ? `${example.failed_targets.join(", ")} 未達` : "支持範囲外"}</b></div>)}
      </details>}
      <ul className="activity-warnings">{result.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
    </section>}
  </>;
}
