import { useEffect, useRef, useState } from "react";
import { fromApiCandidate, setCandidateInputValue, toApiCandidate, type CandidateViewModel as Candidate, type ResolvedTaskDefinition, type TaskDefinitionContract } from "../candidates";
import { workbenchApi, type ApiProject, type ApiScreeningRun } from "../../shared/api/workbench-api";
import { CandidateAddButton } from "../../shared/ui/CandidateAddButton";
import { SvgChartTooltip } from "../../shared/ui/SvgChartTooltip";
import { assessPrediction, clampToRange, resolveOutputDefinition } from "../../shared/outputPresentation";
import { ScreeningBaseEditor } from "./ScreeningBaseEditor";

function cloneScreeningCandidate(candidate: Candidate): Candidate {
  return {
    ...candidate,
    raw: {
      ...candidate.raw,
      inputs: {
        ...candidate.raw.inputs,
        composition: { ...candidate.raw.inputs.composition },
        process: { ...candidate.raw.inputs.process },
        categorical: candidate.raw.inputs.categorical ? { ...candidate.raw.inputs.categorical } : candidate.raw.inputs.categorical,
        heat_pattern: candidate.raw.inputs.heat_pattern === null
          ? null
          : candidate.raw.inputs.heat_pattern?.map((point) => ({ ...point })),
      },
    },
    heat: candidate.heat.map((point) => ({ ...point })),
  };
}

function number(value: number, digits = 0) {
  return value.toLocaleString("ja-JP", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function chartDigits(min: number, max: number) {
  const span = Math.abs(max - min);
  if (span < 0.001) return 6;
  if (span < 0.01) return 5;
  if (span < 0.1) return 4;
  if (span < 1) return 3;
  if (span < 10) return 2;
  if (span < 100) return 1;
  return 0;
}

export function ScreeningPage({
  projectId,
  project,
  candidates,
  selectedId,
  taskDefinition,
  resolvedTaskDefinition,
  initialRunId,
  onRunChange,
  onCandidate,
  onCompare,
  onCreateStarter,
}: {
  projectId: string;
  project: ApiProject | undefined;
  candidates: Candidate[];
  selectedId: string;
  taskDefinition: TaskDefinitionContract | null;
  resolvedTaskDefinition: ResolvedTaskDefinition | null;
  initialRunId?: string;
  onRunChange: (runId: string) => void;
  onCandidate: (candidate: Candidate) => void;
  onCompare: () => void;
  onCreateStarter: () => void;
}) {
  type VariableRow = {
    field: string;
    mode: "fixed" | "range" | "list";
    first: string;
    second: string;
  };
  type ScreenPoint = ApiScreeningRun["points"][number];
  type ScreenResult = ApiScreeningRun;
  const [variables, setVariables] = useState<VariableRow[]>([]);
  const [samples, setSamples] = useState(64);
  const [target, setTarget] = useState("TS");
  const [targetValue, setTargetValue] = useState("500");
  const [secondaryTargets, setSecondaryTargets] = useState<Record<string, string>>({});
  const [baseCandidateId, setBaseCandidateId] = useState(selectedId);
  const baseCandidateSource = candidates.find((candidate) => candidate.id === baseCandidateId);
  const [baseCandidate, setBaseCandidate] = useState<Candidate>();
  const [baseEditorVersion, setBaseEditorVersion] = useState(0);
  const pendingBaseInputs = useRef<ApiScreeningRun["base_inputs"]>(undefined);
  const optionGroups = resolvedTaskDefinition
    ? resolvedTaskDefinition.task_definition.input_groups.map((group) => ({
        key: group.key,
        label: group.label,
        options: group.fields.flatMap((field) => {
          if (!field.editable) return [];
          if (field.kind !== "heat_pattern") return [{
            value: field.path,
            label: `${field.label}${field.unit ? ` (${field.unit})` : ""}`,
            kind: field.kind,
            choices: field.choices,
            defaultRange: field.default_range,
          }];
          return (baseCandidate?.raw.inputs.heat_pattern ?? []).flatMap((point, index) => [
            {
              value: `heat_pattern.${index}.temperature_c`,
              label: `点${index + 1} 温度 (°C)`,
              kind: "number" as const,
              choices: [] as string[],
              defaultRange: { min: Math.max(0, point.temperature_c - 50), max: point.temperature_c + 50 },
            },
            {
              value: `heat_pattern.${index}.time_s`,
              label: `点${index + 1} 時刻 (s)`,
              kind: "number" as const,
              choices: [] as string[],
              defaultRange: { min: Math.max(0, point.time_s - 10), max: point.time_s + 10 },
            },
          ]);
        }),
      })).filter((group) => group.options.length)
    : [];
  const options = optionGroups.flatMap((group) => group.options);
  const [result, setResult] = useState<ScreenResult | null>(null);
  const [savedRuns, setSavedRuns] = useState<ScreenResult[]>([]);
  const [error, setError] = useState("");
  const [draftDirty, setDraftDirty] = useState(false);
  const [xAxis, setXAxis] = useState("");
  const [yAxis, setYAxis] = useState("");
  const [colorMetric, setColorMetric] = useState("score");
  const [selectedPointIndices, setSelectedPointIndices] = useState<number[]>([]);
  const [focusedPointIndex, setFocusedPointIndex] = useState<number | null>(null);
  const [hoveredScreenPoint, setHoveredScreenPoint] = useState<{ x: number; y: number; lines: string[] } | null>(null);
  const runRequestSequence = useRef(0);
  const activeProjectRef = useRef(projectId);
  activeProjectRef.current = projectId;
  const outputs = taskDefinition?.outputs ?? [];
  const targetDefinition = outputs.find((output) => output.key === target);
  useEffect(() => {
    const defaults = options.filter((option) => option.kind === "number").slice(0, 2).map((option) => ({
      field: option.value,
      mode: "range" as const,
      first: String(option.defaultRange?.min ?? ""),
      second: String(option.defaultRange?.max ?? ""),
    }));
    setVariables(defaults);
    setSecondaryTargets({});
    setResult(null);
    setSelectedPointIndices([]);
    setFocusedPointIndex(null);
    setDraftDirty(false);
  }, [resolvedTaskDefinition?.task_definition.id]);
  useEffect(() => {
    if (outputs.length && !outputs.some((output) => output.key === target)) {
      setTarget(outputs[0].key);
    }
  }, [outputs, target]);
  useEffect(() => {
    if (candidates.some((candidate) => candidate.id === selectedId)) setBaseCandidateId(selectedId);
  }, [selectedId]);
  useEffect(() => {
    if (!candidates.some((candidate) => candidate.id === baseCandidateId)) setBaseCandidateId(candidates[0]?.id ?? "");
  }, [candidates, baseCandidateId]);
  useEffect(() => {
    if (!baseCandidateSource) {
      pendingBaseInputs.current = undefined;
      return setBaseCandidate(undefined);
    }
    const inputs = pendingBaseInputs.current;
    pendingBaseInputs.current = undefined;
    setBaseCandidate(inputs
      ? fromApiCandidate({ ...baseCandidateSource.raw, inputs })
      : cloneScreeningCandidate(baseCandidateSource));
  }, [baseCandidateId, baseCandidateSource?.id]);
  useEffect(() => {
    const requestProjectId = projectId;
    runRequestSequence.current += 1;
    setResult(null);
    setSavedRuns([]);
    setSelectedPointIndices([]);
    setFocusedPointIndex(null);
    workbenchApi.listScreeningRuns(requestProjectId)
      .then((runs) => { if (activeProjectRef.current === requestProjectId) setSavedRuns(runs); })
      .catch(() => undefined);
  }, [projectId]);
  const updateVariable = (index: number, patch: Partial<VariableRow>) =>
    (setDraftDirty(true), setVariables((rows) =>
      rows.map((row, rowIndex) =>
        rowIndex === index ? { ...row, ...patch } : row,
      ),
    ));
  const updateBaseInput = (path: string, value: number | string) => {
    setBaseCandidate((current) => current ? { ...current, raw: { ...current.raw, inputs: setCandidateInputValue(current.raw.inputs, path, value) } } : current);
    setDraftDirty(true);
  };
  const updateBaseHeat = (index: number, field: "time" | "temperature" | "stageName", raw: number | string) => {
    setBaseCandidate((current) => {
      if (!current) return current;
      const next = { ...current, heat: current.heat.map((point, pointIndex) => pointIndex === index ? { ...point, [field]: raw } : point) };
      return { ...next, raw: { ...next.raw, inputs: toApiCandidate(next).inputs } };
    });
    setDraftDirty(true);
  };
  const applyResult = (run: ScreenResult) => {
    setResult(run);
    const varying = Object.entries(run.variables).filter(([, spec]) => spec.mode !== "fixed").map(([field]) => field);
    setXAxis(varying[0] ?? "");
    setYAxis(varying[1] ?? "");
    setColorMetric("score");
    setSelectedPointIndices([]);
    setFocusedPointIndex(run.representative_points[0]?.index ?? null);
    setDraftDirty(false);
  };
  const run = async () => {
    if (!baseCandidate) return setError("基準条件を読み込めませんでした。");
    const sequence = ++runRequestSequence.current;
    const requestProjectId = projectId;
    try {
      setError("");
      const specs = Object.fromEntries(
        variables.map((row) => {
          const categorical = options.find((option) => option.value === row.field)?.kind === "categorical";
          if (row.mode === "range")
            return [
              row.field,
              {
                mode: row.mode,
                min: Number(row.first),
                max: Number(row.second),
              },
            ];
          if (row.mode === "list")
            return [
              row.field,
              {
                mode: row.mode,
                values: row.first
                  .split(",")
                  .map((value) =>
                    categorical ? value.trim() : Number(value.trim()),
                  ),
              },
            ];
          return [
            row.field,
            {
              mode: row.mode,
              value: categorical ? row.first.trim() : Number(row.first),
            },
          ];
        }),
      );
      const created = await workbenchApi.createScreeningRun(requestProjectId, {
        base_candidate_id: baseCandidateId,
        base_inputs: toApiCandidate(baseCandidate).inputs,
        variables: specs,
        samples,
        target,
        target_value: targetValue.trim() === "" ? null : Number(targetValue),
        secondary_targets: Object.fromEntries(Object.entries(secondaryTargets).filter(([, value]) => value.trim() !== "").map(([key, value]) => [key, Number(value)])),
      });
      if (sequence !== runRequestSequence.current || activeProjectRef.current !== requestProjectId) return;
      applyResult(created);
      setSavedRuns((runs) => [created, ...runs]);
      onRunChange(created.id);
    } catch (cause) {
      if (sequence !== runRequestSequence.current || activeProjectRef.current !== requestProjectId) return;
      setError(
        `範囲探索を実行できませんでした。${cause instanceof Error && cause.message ? ` ${cause.message}` : ""}`,
      );
    }
  };
  const loadRun = async (runId: string) => {
    const sequence = ++runRequestSequence.current;
    const requestProjectId = projectId;
    setError("");
    let run: ScreenResult;
    try {
      run = await workbenchApi.screeningRun(requestProjectId, runId);
    } catch {
      if (sequence !== runRequestSequence.current || activeProjectRef.current !== requestProjectId) return;
      return setError("作成元の探索は削除済みか、このプロジェクトから参照できません。");
    }
    if (sequence !== runRequestSequence.current || activeProjectRef.current !== requestProjectId) return;
    applyResult(run);
    if (run.base_candidate_id) {
      const source = candidates.find((candidate) => candidate.id === run.base_candidate_id);
      if (!source) {
        pendingBaseInputs.current = undefined;
      } else if (run.base_candidate_id === baseCandidateId) {
        setBaseCandidate(run.base_inputs
          ? fromApiCandidate({ ...source.raw, inputs: run.base_inputs })
          : cloneScreeningCandidate(source));
        setBaseEditorVersion((version) => version + 1);
      } else if (source) {
        pendingBaseInputs.current = run.base_inputs;
        setBaseEditorVersion((version) => version + 1);
        setBaseCandidateId(run.base_candidate_id);
      }
    }
    setTarget(run.target);
    setTargetValue(run.target_value == null ? "" : String(run.target_value));
    setSecondaryTargets(Object.fromEntries(Object.entries(run.secondary_targets ?? {}).map(([key, value]) => [key, String(value)])));
    setSamples(run.samples);
    if (run.variables)
      setVariables(
        Object.entries(run.variables).map(([field, spec]) => ({
          field,
          mode: spec.mode,
          first:
            spec.mode === "fixed"
              ? String(spec.value ?? "")
              : spec.mode === "list"
                ? (spec.values ?? []).join(",")
                : String(spec.min ?? ""),
          second: spec.mode === "range" ? String(spec.max ?? "") : "",
        })),
      );
    onRunChange(run.id);
  };
  useEffect(() => {
    if (initialRunId && result?.id !== initialRunId) void loadRun(initialRunId);
    return () => {
      runRequestSequence.current += 1;
    };
  }, [initialRunId, projectId]);
  const stockedPointIndices = new Set(candidates.flatMap((candidate) => {
    const provenance = candidate.raw.provenance;
    if (!provenance || provenance.source_kind !== "screening" || !provenance.source_ref || provenance.source_ref.run_id !== result?.id) return [];
    return typeof provenance.source_ref.point_index === "number" ? [provenance.source_ref.point_index] : [];
  }));
  const selectedNewPointIndices = selectedPointIndices.filter((index) => !stockedPointIndices.has(index));
  const remainingCandidateCapacity = Math.max(0, 10 - candidates.length);
  const persistSelected = async () => {
    if (!result || !selectedNewPointIndices.length) return;
    const requestProjectId = projectId;
    const requestRunId = result.id;
    if (selectedNewPointIndices.length > remainingCandidateCapacity) {
      setError(`追加できるのは残り${remainingCandidateCapacity}件です。選択を減らしてください。`);
      return;
    }
    try {
      const response = await workbenchApi.candidatesFromScreening(requestProjectId, requestRunId, selectedNewPointIndices);
      if (activeProjectRef.current !== requestProjectId) return;
      response.candidates.forEach((candidate) => onCandidate(fromApiCandidate(candidate)));
      setSelectedPointIndices([]);
      setError("");
    } catch (cause) {
      if (activeProjectRef.current !== requestProjectId) return;
      setError(cause instanceof Error ? cause.message : "候補を作成できませんでした。");
    }
  };
  const confirmedVaryingFields = result ? Object.entries(result.variables)
    .filter(([field, spec]) => spec.mode !== "fixed" && result.points.some((point) => typeof point.inputs[field] === "number"))
    .map(([field]) => field) : [];
  const axes = [xAxis, yAxis].filter(Boolean);
  const numeric = (axis: string) =>
    result?.points
      .map((point) => Number(point.inputs[axis]))
      .filter(Number.isFinite) ?? [];
  const xValues = numeric(axes[0]);
  const yValues = numeric(axes[1] ?? axes[0]);
  const xDigits = xValues.length ? chartDigits(Math.min(...xValues), Math.max(...xValues)) : 2;
  const yDigits = yValues.length ? chartDigits(Math.min(...yValues), Math.max(...yValues)) : 2;
  const scale = (
    value: number,
    values: number[],
    start: number,
    span: number,
  ) =>
    start +
    ((value - Math.min(...values)) /
      Math.max(1e-9, Math.max(...values) - Math.min(...values))) *
      span;
  const screenX = (value: number) => scale(value, xValues, 35, 530);
  const screenY = (value: number) => 270 - scale(value, yValues, 0, 235);
  const tickValues = (values: number[]) => {
    if (!values.length) return [];
    const min = Math.min(...values);
    const max = Math.max(...values);
    return Array.from({ length: 5 }, (_, index) => min + ((max - min) * index) / 4);
  };
  const xTicks = tickValues(xValues);
  const yTicks = tickValues(yValues);
  const scores = result?.points.map((point) => point.score).filter((score): score is number => score != null) ?? [];
  const colorValues = colorMetric === "score" ? scores : result?.points.map((point) => (point.predictions?.[colorMetric] ?? (colorMetric === result.target ? point.prediction : undefined))?.value).filter((value): value is number => typeof value === "number") ?? [];
  const colorOutput = outputs.find((output) => output.key === colorMetric);
  const colorRange = colorMetric === "score" ? undefined : colorOutput?.preferred_display_range ?? undefined;
  const opportunity = (point: ScreenPoint) => {
    const value = colorMetric === "score" ? point.score : (point.predictions?.[colorMetric] ?? (colorMetric === result?.target ? point.prediction : undefined))?.value;
    if (value == null || colorValues.length === 0) return "hsl(215 18% 72%)";
    const domainValues = colorRange ? [colorRange.min, colorRange.max] : colorValues;
    const displayValue = colorRange ? clampToRange(value, colorRange) : value;
    const normalized = (displayValue - Math.min(...domainValues)) / Math.max(1e-9, Math.max(...domainValues) - Math.min(...domainValues));
    const strength = colorMetric === "score" ? 1 - normalized : normalized;
    return `hsl(215 78% ${82 - strength * 42}%)`;
  };
  const axisLabel = (axis: string | undefined) => options.find((option) => option.value === axis)?.label ?? axis ?? "";
  const supportStroke = (status: string) =>
    status === "supported"
      ? "#15936a"
      : status === "caution"
        ? "#ee9200"
        : "#c43d3d";
  const focusedPoint = result?.points.find((point) => point.index === focusedPointIndex) ?? null;
  const hiddenVaryingFields = result ? Object.entries(result.variables).filter(([field, spec]) => spec.mode !== "fixed" && field !== xAxis && field !== yAxis).map(([field]) => field) : [];
  const togglePoint = (index: number) => {
    setFocusedPointIndex(index);
    setSelectedPointIndices((current) => current.includes(index) ? current.filter((item) => item !== index) : [...current, index]);
  };
  if (!candidates.length) return <div className="page-panel explore-page"><div className="page-intro"><div><h2>範囲探索</h2><p>探索の基準になる候補を1件作ると、TaskDefinitionの入力範囲から条件を検討できます。</p></div></div><div className="project-empty-state"><p>まだ基準候補がありません。</p><CandidateAddButton onClick={onCreateStarter}>基準候補を作って探索を始める</CandidateAddButton></div></div>;
  return (
    <div className="page-panel explore-page">
      <div className="page-intro">
        <div>
          <h2>範囲探索</h2>
          <p>
            指定範囲を偏りなく確認し、有望領域から複数候補を集めます。
          </p>
        </div>
        <button
          className="primary-button"
          disabled={!baseCandidateId || !baseCandidate}
          title={baseCandidateId ? "選択した候補を基準に探索します" : "基準候補が必要です"}
          onClick={() => {
            void run();
          }}
        >
          探索を実行
        </button>
      </div>
      {draftDirty && result && <p className="screening-draft-notice">未実行の条件変更があります。図と点詳細は最後に実行した条件のままです。</p>}
      {savedRuns.length > 0 && (
        <section className="saved-runs">
          <h3>保存済み探索</h3>
          <div>
            {savedRuns.slice(0, 8).map((run) => (
              <button
                className={result?.id === run.id ? "active" : ""}
                key={run.id}
                onClick={() => {
                  void loadRun(run.id);
                }}
              >
                <b>{outputs.find((output) => output.key === run.target)?.label ?? run.target}</b> → {run.target_value == null ? "目標なし" : number(run.target_value, 1)} /{" "}
                 {run.samples}点{" "}
                <small>
                  基準: {candidates.find((candidate) => candidate.id === run.base_candidate_id)?.label ?? run.base_candidate_id?.slice(0, 8) ?? "旧保存データ"} ·{" "}
                  {Object.entries(run.variables).map(([field, spec]) => `${axisLabel(field)}=${spec.mode === "range" ? `${number(spec.min ?? 0, 3)}–${number(spec.max ?? 0, 3)}` : spec.mode === "list" ? (spec.values ?? []).join("/") : String(spec.value ?? "")}`).join(" / ")} ·{" "}
                  {run.created_at
                    ? new Date(run.created_at).toLocaleString("ja-JP")
                    : "保存済み"}
                </small>
              </button>
            ))}
          </div>
        </section>
      )}
      <div className="screening-settings">
        <div className="screening-target">
          <div className="screening-base-candidate">
            <label>
              基準候補
              <select value={baseCandidateId} onChange={(event) => { setBaseCandidateId(event.target.value); setDraftDirty(true); }}>
                {candidates.map((candidate) => (
                  <option key={candidate.id} value={candidate.id}>{candidate.label}</option>
                ))}
              </select>
            </label>
          </div>
          <label>
            評価点数
            <input
              type="number"
              min="48"
              max="128"
              value={samples}
              onChange={(event) => { setSamples(Number(event.target.value)); setDraftDirty(true); }}
            />
          </label>
          <label>
            目標特性
            <select
              value={target}
              onChange={(event) => { const next = event.target.value; setTarget(next); setSecondaryTargets((current) => { const updated = { ...current }; delete updated[next]; return updated; }); setDraftDirty(true); }}
            >
              {outputs.map((output) => <option key={output.key} value={output.key}>{output.label} ({output.unit})</option>)}
            </select>
          </label>
          <label>
            目標値 {targetDefinition?.goal_direction === "at_most" ? "（以下）" : targetDefinition?.goal_direction === "at_least" ? "（以上）" : ""}
            <input
              type="number"
              value={targetValue}
              onChange={(event) => { setTargetValue(event.target.value); setDraftDirty(true); }}
            />
          </label>
          {outputs.filter((output) => output.key !== target).map((output) => <label key={output.key}>副条件: {output.label}（{output.goal_direction === "at_most" ? "以下" : "以上"}）<input type="number" value={secondaryTargets[output.key] ?? ""} placeholder="指定なし" onChange={(event) => { setSecondaryTargets((current) => ({ ...current, [output.key]: event.target.value })); setDraftDirty(true); }} /></label>)}
        </div>
        {baseCandidate && taskDefinition && (
            <ScreeningBaseEditor key={`${baseCandidate.id}:${baseEditorVersion}`} candidate={baseCandidate} taskDefinition={taskDefinition} displayDecimalOverrides={project?.display_decimals} onInput={updateBaseInput} onHeat={updateBaseHeat} />
        )}
        <section className="screening-variable-editor" aria-label="探索で動かす項目">
          <div className="screening-variable-heading">
            <h3>探索で動かす項目</h3>
            <small>ここで指定した項目だけ、上の基準値から動かします。</small>
          </div>
          <div className="screening-variable-table-scroll">
            <table className="quality-table variable-table">
          <thead>
            <tr>
              <th>変数</th>
              <th>指定</th>
              <th>値 / 最小</th>
              <th>最大</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {variables.map((row, index) => (
              <tr key={`${row.field}-${index}`}>
                <td>
                  <select
                    value={row.field}
                    onChange={(event) => {
                      const option = options.find((item) => item.value === event.target.value);
                      updateVariable(index, option?.kind === "categorical"
                        ? { field: event.target.value, mode: "list", first: option.choices.join(","), second: "" }
                        : { field: event.target.value, mode: "range", first: String(option?.defaultRange?.min ?? ""), second: String(option?.defaultRange?.max ?? "") });
                    }}
                  >
                    {optionGroups.map((group) => <optgroup key={group.key} label={group.label}>{group.options.map((option) => <option key={option.value} value={option.value} disabled={variables.some((item, rowIndex) => rowIndex !== index && item.field === option.value)}>{option.label}</option>)}</optgroup>)}
                  </select>
                </td>
                <td>
                  <select
                    value={row.mode}
                    onChange={(event) =>
                      updateVariable(index, {
                        mode: event.target.value as VariableRow["mode"],
                      })
                    }
                  >
                    <option value="fixed">固定</option>
                    <option value="range" disabled={options.find((option) => option.value === row.field)?.kind === "categorical"}>範囲</option>
                    <option value="list">列挙</option>
                  </select>
                </td>
                <td>
                  <input
                    value={row.first}
                    placeholder={row.mode === "list" ? "例: GI,GA" : "値"}
                    onChange={(event) =>
                      updateVariable(index, { first: event.target.value })
                    }
                  />
                </td>
                <td>
                  {row.mode === "range" ? (
                    <input
                      value={row.second}
                      onChange={(event) =>
                        updateVariable(index, { second: event.target.value })
                      }
                    />
                  ) : (
                    "—"
                  )}
                </td>
                <td>
                  <button
                    className="icon-delete"
                    disabled={variables.length === 1}
                    onClick={() => {
                      setDraftDirty(true);
                      setVariables((rows) =>
                        rows.filter((_, rowIndex) => rowIndex !== index),
                      );
                    }}
                  >
                    ×
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
            </table>
          </div>
          <button
          className="outline-button"
          disabled={!options.some((option) => !variables.some((row) => row.field === option.value))}
          onClick={() => {
            const option = options.find((item) => !variables.some((row) => row.field === item.value));
            if (!option) return;
            setDraftDirty(true);
            setVariables((rows) => [...rows, { field: option.value, mode: option.kind === "categorical" ? "list" : "range", first: option.kind === "categorical" ? option.choices.join(",") : String(option.defaultRange?.min ?? ""), second: option.kind === "categorical" ? "" : String(option.defaultRange?.max ?? "") }]);
          }}
        >
          変数を追加
          </button>
        </section>
      </div>
      {error && <p className="warning">{error}</p>}
      {result && (
        <>
          <div className="screening-display-controls">
            <label>X軸<select value={xAxis} onChange={(event) => setXAxis(event.target.value)}>{confirmedVaryingFields.map((field) => <option key={field} value={field}>{axisLabel(field)}</option>)}</select></label>
            <label>Y軸<select value={yAxis} onChange={(event) => setYAxis(event.target.value)}><option value="">点番号</option>{confirmedVaryingFields.filter((field) => field !== xAxis).map((field) => <option key={field} value={field}>{axisLabel(field)}</option>)}</select></label>
            <label>色<select value={colorMetric} onChange={(event) => setColorMetric(event.target.value)}><option value="score">目標に対する有望度</option>{outputs.map((output) => <option key={output.key} value={output.key}>{output.label}</option>)}</select></label>
          </div>
          {hiddenVaryingFields.length > 0 && <p className="screening-hidden-variables"><b>図に出ていない変動条件:</b> {hiddenVaryingFields.map(axisLabel).join(" / ")}。各点の詳細で実値を確認できます。</p>}
          <div className="screening-action-bar" role="status">
            <span><b>{selectedPointIndices.length}</b>件選択 / 新規{selectedNewPointIndices.length}件 / 追加可能{remainingCandidateCapacity}件</span>
            {selectedPointIndices.some((index) => stockedPointIndices.has(index)) && <small>stock済みの点は再追加しません。</small>}
            <CandidateAddButton disabled={!selectedNewPointIndices.length || selectedNewPointIndices.length > remainingCandidateCapacity} onClick={() => void persistSelected()}>{selectedNewPointIndices.length}件を候補へ追加</CandidateAddButton>
            <button className="outline-button" disabled={!candidates.length} onClick={onCompare}>候補比較へ</button>
          </div>
          <div className="screen-legend">
            <span className="opportunity-scale" />
            {colorMetric === "score" ? result.score_contract?.display_label ?? "目標に対して有望" : outputs.find((output) => output.key === colorMetric)?.label ?? colorMetric} <span className="support-key supported" />
            範囲内 <span className="support-key caution" />
            要確認 <span className="support-key extrapolated" />
            外挿
          </div>
          <svg
            className="screen-map"
            viewBox="0 0 600 300"
            role="img"
            aria-label={`${axes.map(axisLabel).join(" × ")} の探索結果。色が濃いほど目標方向に有望で、枠線が学習範囲を示します。`}
          >
            {axes.length > 0 && xTicks.map((tick) => <g key={`x-${tick}`} className="screen-map-grid"><line x1={screenX(tick)} x2={screenX(tick)} y1="35" y2="270" /><text x={screenX(tick)} y="284" textAnchor="middle">{number(tick, xDigits)}</text></g>)}
            {axes.length > 1 && yTicks.map((tick) => <g key={`y-${tick}`} className="screen-map-grid"><line x1="35" x2="565" y1={screenY(tick)} y2={screenY(tick)} /><text x="31" y={screenY(tick) + 3} textAnchor="end">{number(tick, yDigits)}</text></g>)}
            {result.points.map((point, index) => {
              const cx = axes.length
                ? screenX(Number(point.inputs[axes[0]]))
                : 35 + (index % 12) * 46;
              const cy =
                axes.length > 1
                  ? screenY(Number(point.inputs[axes[1]]))
                  : 35 + Math.floor(index / 12) * 50;
              const targetOutput = resolveOutputDefinition(outputs, result.target);
              const targetAssessment = assessPrediction(targetOutput, point.prediction);
              const tooltipLines = [
                `点 ${point.index + 1}`,
                ...axes.map((axis, axisIndex) => `${axisLabel(axis)} ${number(Number(point.inputs[axis]), axisIndex === 0 ? xDigits : yDigits)}`),
                `${outputs.find((output) => output.key === result.target)?.label ?? result.target} ${number(point.prediction.value, 1)} ${point.prediction.unit}`,
                `90%区間 ${number(point.prediction.lower, 1)}–${number(point.prediction.upper, 1)}`,
                ...(targetAssessment.warning ? [`⚠ ${targetAssessment.warning}`] : []),
                point.support.message,
              ];
              return (
                <circle
                  key={point.index}
                  className={selectedPointIndices.includes(point.index) ? "selected" : ""}
                  cx={cx}
                  cy={cy}
                  r={selectedPointIndices.includes(point.index) ? "9" : "7"}
                  fill={opportunity(point)}
                  stroke={supportStroke(point.support.status)}
                  strokeWidth="3"
                  opacity={
                    point.support.status === "extrapolated" ? ".55" : ".9"
                  }
                  role="button"
                  tabIndex={focusedPointIndex === point.index || (focusedPointIndex === null && index === 0) ? 0 : -1}
                  aria-label={tooltipLines.join("、")}
                  onMouseEnter={() => setHoveredScreenPoint({ x: cx, y: cy, lines: tooltipLines })}
                  onMouseLeave={() => setHoveredScreenPoint(null)}
                  onFocus={() => {
                    setFocusedPointIndex(point.index);
                    setHoveredScreenPoint({ x: cx, y: cy, lines: tooltipLines });
                  }}
                  onBlur={() => setHoveredScreenPoint(null)}
                  onKeyDown={(event) => {
                    if (event.key !== "Enter" && event.key !== " ") return;
                    event.preventDefault();
                    togglePoint(point.index);
                  }}
                  onClick={() => {
                    togglePoint(point.index);
                  }}
                />
              );
            })}
            {hoveredScreenPoint && <SvgChartTooltip {...hoveredScreenPoint} chartWidth={600} chartHeight={300} />}
            <text x="300" y="296" textAnchor="middle">
              {axisLabel(axes[0])}
            </text>
            <text x="8" y="16">
              {axisLabel(axes[1])}
            </text>
          </svg>
          {focusedPoint && <section className="screening-point-detail" aria-label="選択した探索点の詳細">
            <div className="panel-title"><h3>点 {focusedPoint.index + 1}</h3><span className={`support-badge ${focusedPoint.support.status}`}>{focusedPoint.support.message}</span></div>
            <div className="screening-point-predictions">{Object.entries({ [result.target]: focusedPoint.prediction, ...(focusedPoint.predictions ?? {}) }).map(([key, prediction]) => { const output = resolveOutputDefinition(outputs, key); const assessment = assessPrediction(output, prediction); return <div className={assessment.implausible ? "implausible-output" : undefined} title={assessment.warning ?? undefined} key={key}><b>{output?.label ?? key}</b><strong>{number(prediction.value, 1)} {prediction.unit}</strong><small>{number(prediction.lower, 1)}–{number(prediction.upper, 1)}{prediction.goal_probability != null ? ` / 達成確率 ${Math.round(prediction.goal_probability * 100)}%` : ""}</small>{assessment.implausible && <em className="output-warning-badge">⚠ 物理範囲外</em>}{focusedPoint.secondary_goal_evaluations?.[key]?.achieved != null && <em>{focusedPoint.secondary_goal_evaluations[key].achieved ? "副条件を満たす" : "副条件を満たさない"}</em>}</div>; })}</div>
            <p><b>全変動条件:</b> {Object.entries(focusedPoint.inputs).map(([key, value]) => `${axisLabel(key)} ${typeof value === "number" ? number(value, 3) : value}`).join(" / ")}</p>
            <p><b>支持度:</b> {focusedPoint.support.status} / percentile {number(focusedPoint.support.percentile, 1)} / 参照{focusedPoint.support.reference_count}件</p>
            {focusedPoint.warnings?.map((warning) => <p className="warning" key={warning}>{warning}</p>)}
            {(focusedPoint.similar ?? []).length > 0 && <p><b>近い実績:</b> {(focusedPoint.similar ?? []).slice(0, 3).map((item) => `${item.observation_id || item.parent_key} (距離 ${number(item.distance, 2)})`).join(" / ")}</p>}
          </section>}
          <table className="quality-table">
            <thead>
              <tr>
                <th>選択</th>
                <th>代表点</th>
                <th>条件</th>
                <th>全予測 / 支持度</th>
              </tr>
            </thead>
            <tbody>
              {result.representative_points.map((point) => (
                <tr key={point.index}>
                  <td><input type="checkbox" aria-label={`点 ${point.index + 1}を選択`} checked={selectedPointIndices.includes(point.index)} disabled={stockedPointIndices.has(point.index)} onChange={() => togglePoint(point.index)} /></td>
                  <td>{point.index + 1}</td>
                  <td>
                    {Object.entries(point.inputs)
                      .map(
                        ([key, value]) =>
                          `${key}: ${typeof value === "number" ? number(value, 3) : value}`,
                      )
                      .join(" / ")}
                  </td>
                  <td>
                    {Object.entries({ [result.target]: point.prediction, ...(point.predictions ?? {}) }).map(([key, prediction]) => { const output = resolveOutputDefinition(outputs, key); const assessment = assessPrediction(output, prediction); return <span className={assessment.implausible ? "implausible-output" : undefined} title={assessment.warning ?? undefined} key={key}>{output?.label ?? key} {number(prediction.value, 1)} {prediction.unit}{assessment.implausible && <small className="output-warning-badge">⚠ 物理範囲外</small>}</span>; })}<br /><small>{point.support.message}{stockedPointIndices.has(point.index) ? " / stock済み" : ""}</small>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
