import { useEffect, useMemo, useState } from "react";

import {
  workbenchApi,
  type ApiCandidate,
  type ApiPredictionGraphDefinition,
  type ApiPredictionGraphDesignSpace,
  type ApiPredictionGraphGoalSearchRun,
  type ApiPredictionGraphObjective,
} from "../../shared/api/workbench-api";

type DecisionOutput = ApiPredictionGraphDefinition["decision_outputs"][number];

type Props = {
  projectId: string;
  candidate: ApiCandidate;
  decisionOutputs: DecisionOutput[];
  onPromoted: (candidate: ApiCandidate) => Promise<void>;
};

const scoreText = (score: number | null) => (
  score === null ? "—" : score.toLocaleString("ja-JP", { maximumFractionDigits: 3 })
);

export function GraphGoalSearchPanel({
  projectId,
  candidate,
  decisionOutputs,
  onPromoted,
}: Props) {
  const [designSpace, setDesignSpace] = useState<ApiPredictionGraphDesignSpace | null>(null);
  const [objectives, setObjectives] = useState<ApiPredictionGraphObjective[]>([]);
  const [runs, setRuns] = useState<ApiPredictionGraphGoalSearchRun[]>([]);
  const [objectiveId, setObjectiveId] = useState("");
  const [primaryOutputId, setPrimaryOutputId] = useState("");
  const [constraintOutputId, setConstraintOutputId] = useState("");
  const [primaryThreshold, setPrimaryThreshold] = useState("");
  const [constraintThreshold, setConstraintThreshold] = useState("");
  const [objectiveName, setObjectiveName] = useState("強度と制約の探索");
  const [sampleCount, setSampleCount] = useState(2);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");

  const primaryOutputs = useMemo(() => decisionOutputs.filter((output) => (
    output.role === "primary_objective"
    && ["at_least", "at_most"].includes(output.evidence?.goal_direction ?? "")
  )), [decisionOutputs]);
  const constraintOutputs = useMemo(() => decisionOutputs.filter((output) => (
    output.role === "hard_constraint"
    && ["at_least", "at_most"].includes(output.evidence?.goal_direction ?? "")
  )), [decisionOutputs]);
  const selectedObjective = objectives.find((item) => item.objective_id === objectiveId) ?? null;
  const latestRun = runs.find((item) => item.objective.objective_id === objectiveId) ?? runs[0] ?? null;
  const rankedPoints = useMemo(() => (
    latestRun
      ? [...latestRun.points].sort((left, right) => (
        Number(right.feasible) - Number(left.feasible)
        || (right.score ?? Number.NEGATIVE_INFINITY) - (left.score ?? Number.NEGATIVE_INFINITY)
        || left.point_index - right.point_index
      ))
      : []
  ), [latestRun]);

  useEffect(() => {
    setPrimaryOutputId((current) => (
      primaryOutputs.some((item) => item.output_id === current)
        ? current
        : primaryOutputs[0]?.output_id ?? ""
    ));
    setConstraintOutputId((current) => (
      constraintOutputs.some((item) => item.output_id === current)
        ? current
        : constraintOutputs[0]?.output_id ?? ""
    ));
  }, [constraintOutputs, primaryOutputs]);

  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([
      workbenchApi.predictionGraphGoalSearchDesignSpace(projectId, controller.signal),
      workbenchApi.listPredictionGraphObjectives(projectId, controller.signal),
      workbenchApi.listPredictionGraphGoalSearchRuns(projectId, controller.signal),
    ]).then(([space, savedObjectives, savedRuns]) => {
      if (controller.signal.aborted) return;
      setDesignSpace(space);
      setObjectives(savedObjectives);
      setRuns(savedRuns);
      setObjectiveId(savedObjectives[0]?.objective_id ?? "");
    }).catch((cause) => {
      if (!controller.signal.aborted) {
        setMessage(cause instanceof Error ? cause.message : "Graph探索を読み込めませんでした");
      }
    });
    return () => controller.abort();
  }, [projectId]);

  const perform = async (label: string, operation: () => Promise<void>) => {
    setBusy(label);
    setMessage("");
    try {
      await operation();
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : `${label}に失敗しました`);
    } finally {
      setBusy("");
    }
  };

  const primary = primaryOutputs.find((item) => item.output_id === primaryOutputId);
  const constraint = constraintOutputs.find((item) => item.output_id === constraintOutputId);
  const formReady = Boolean(
    primary
    && constraint
    && objectiveName.trim()
    && primaryThreshold.trim()
    && constraintThreshold.trim()
    && Number.isFinite(Number(primaryThreshold))
    && Number.isFinite(Number(constraintThreshold)),
  );

  return <section className="graph-decision-card graph-goal-search" aria-labelledby="graph-goal-search-title">
    <div className="graph-decision-card-heading">
      <div>
        <span className="overline">GOAL SEARCH</span>
        <h3 id="graph-goal-search-title">Objectiveから候補を探索</h3>
      </div>
      {designSpace && <span className="graph-goal-search-count">{designSpace.variables.length}変数</span>}
    </div>
    {message && <div className="connection-banner" role="alert"><strong>{message}</strong></div>}

    <details open>
      <summary>安全なDesign Space</summary>
      <div className="graph-goal-design-space">
        {designSpace?.variables.map((variable) => <div key={variable.input_id}>
          <strong>{variable.label}</strong>
          <span>{variable.kind === "number" && variable.numeric_range
            ? `${variable.numeric_range.min}〜${variable.numeric_range.max} ${variable.unit ?? ""}`
            : variable.choices.join(" / ")}</span>
          <small>{variable.sampling_policy} · 影響 {variable.affected_output_ids.join(" / ")}</small>
        </div>)}
      </div>
      <p className="secondary-text">scenario context・fixed parameter・疎配合は自動探索しません。</p>
      <p className="secondary-text">現在のGraph evidenceはdemonstration用途です。保存したObjectiveも同じ利用境界を引き継ぎます。</p>
    </details>

    <div className="graph-goal-form">
      <label>
        <span>Objective名</span>
        <input value={objectiveName} onChange={(event) => setObjectiveName(event.target.value)} />
      </label>
      <label>
        <span>Primary output</span>
        <select value={primaryOutputId} onChange={(event) => setPrimaryOutputId(event.target.value)}>
          {primaryOutputs.map((output) => <option key={output.output_id} value={output.output_id}>{output.label}</option>)}
        </select>
      </label>
      <label>
        <span>Primary threshold {primary?.evidence?.unit_or_scale && <small>{primary.evidence.unit_or_scale}</small>}</span>
        <input aria-label="Primary threshold" type="number" value={primaryThreshold} onChange={(event) => setPrimaryThreshold(event.target.value)} />
      </label>
      <label>
        <span>Hard constraint</span>
        <select value={constraintOutputId} onChange={(event) => setConstraintOutputId(event.target.value)}>
          {constraintOutputs.map((output) => <option key={output.output_id} value={output.output_id}>{output.label}</option>)}
        </select>
      </label>
      <label>
        <span>Constraint threshold {constraint?.evidence?.unit_or_scale && <small>{constraint.evidence.unit_or_scale}</small>}</span>
        <input aria-label="Constraint threshold" type="number" value={constraintThreshold} onChange={(event) => setConstraintThreshold(event.target.value)} />
      </label>
      <button type="button" disabled={!formReady || Boolean(busy)} onClick={() => void perform("Objective保存", async () => {
        if (!primary || !constraint) return;
        const created = await workbenchApi.createPredictionGraphObjective(projectId, {
          name: objectiveName.trim(),
          primary: {
            output_id: primary.output_id,
            direction: primary.evidence!.goal_direction as "at_least" | "at_most",
            threshold: Number(primaryThreshold),
          },
          hard_constraint: {
            output_id: constraint.output_id,
            direction: constraint.evidence!.goal_direction as "at_least" | "at_most",
            threshold: Number(constraintThreshold),
          },
          incumbent_candidate_id: candidate.id,
          incumbent_candidate_revision: candidate.revision,
          use_context: "demonstration",
        });
        setObjectives((current) => [created, ...current]);
        setObjectiveId(created.objective_id);
        setMessage("Objectiveを現在のCandidate revisionへ固定しました。");
      })}>Objectiveを保存</button>
    </div>

    {objectives.length > 0 && <div className="graph-goal-run-controls">
      <label>
        <span>保存済みObjective</span>
        <select value={objectiveId} onChange={(event) => setObjectiveId(event.target.value)}>
          {objectives.map((objective) => <option key={objective.objective_id} value={objective.objective_id}>
            {objective.name} · incumbent r{objective.incumbent_candidate_revision}
          </option>)}
        </select>
      </label>
      <label>
        <span>候補数</span>
        <input type="number" min={2} max={32} value={sampleCount} onChange={(event) => setSampleCount(Number(event.target.value))} />
      </label>
      <button type="button" className="primary-button" disabled={!selectedObjective || Boolean(busy)} onClick={() => void perform("Goal search", async () => {
        if (!selectedObjective) return;
        const created = await workbenchApi.createPredictionGraphGoalSearchRun(projectId, {
          objective_id: selectedObjective.objective_id,
          base_candidate_id: selectedObjective.incumbent_candidate_id,
          base_candidate_revision: selectedObjective.incumbent_candidate_revision,
          sample_count: sampleCount,
          seed: 20260803,
        });
        setRuns((current) => [created, ...current]);
        setMessage(`Goal searchを保存しました。選択候補 ${created.selected_point_indices.length}件。`);
      })}>Goal searchを実行</button>
    </div>}

    {latestRun && <div className="graph-goal-results">
      <div className="graph-goal-run-meta">
        <strong>Run {latestRun.run_id.slice(0, 8)}</strong>
        <span>seed {latestRun.seed} · {latestRun.points.length}点 · 選択 {latestRun.selected_point_indices.length}件</span>
      </div>
      <div className="graph-goal-result-list">
        {rankedPoints.map((point, rank) => {
          const selected = latestRun.selected_point_indices.includes(point.point_index);
          const primaryResult = point.outputs[latestRun.objective.primary.output_id];
          const constraintResult = point.outputs[latestRun.objective.hard_constraint.output_id];
          return <article key={point.point_index} className={selected ? "selected" : ""}>
            <div>
              <strong>#{rank + 1} · 点{point.point_index + 1}</strong>
              <span>{selected ? "昇格候補" : point.rejection_reason ?? "評価済み"}</span>
            </div>
            <dl>
              <div><dt>score</dt><dd>{scoreText(point.score)}</dd></div>
              <div><dt>primary</dt><dd>{primaryResult?.status ?? "unknown"} · {String(primaryResult?.value ?? "—")}</dd></div>
              <div><dt>constraint</dt><dd>{constraintResult?.status ?? "unknown"} · {String(constraintResult?.value ?? "—")}</dd></div>
            </dl>
            {selected && <button type="button" disabled={Boolean(busy)} onClick={() => void perform("候補昇格", async () => {
              const promoted = await workbenchApi.promotePredictionGraphGoalSearchResult(
                projectId,
                latestRun.run_id,
                {
                  point_index: point.point_index,
                  name: `${latestRun.objective.name} 候補${rank + 1}`,
                },
              );
              await onPromoted(promoted);
              setMessage("選択結果を通常のGraph Candidateへ昇格しました。");
            })}>候補へ昇格</button>}
          </article>;
        })}
      </div>
    </div>}
  </section>;
}
