import { useEffect, useRef, useState } from "react";
import { fromApiCandidate, setCandidateInputValue, toApiCandidate, type CandidateViewModel as Candidate, type ResolvedTaskDefinition, type TaskDefinitionContract } from "../candidates";
import { workbenchApi, type ApiProject, type ApiProposalStrategyAvailability, type ApiScreeningRun } from "../../shared/api/workbench-api";
import { CandidateAddButton } from "../../shared/ui/CandidateAddButton";
import { SvgChartTooltip } from "../../shared/ui/SvgChartTooltip";
import { assessPrediction, clampToRange, resolveOutputDefinition } from "../../shared/outputPresentation";
import { ScreeningBaseEditor } from "./ScreeningBaseEditor";
import {
  emptyScreeningGoal,
  screeningGoalFromDraft,
  ScreeningGoalEditor,
  type ScreeningGoalDirection,
  type ScreeningGoalDraft,
  type ScreeningGoalPayload,
} from "./ScreeningGoalEditor";
import { ScreeningProposalSummary } from "./ScreeningProposalSummary";
import { ScreeningRepresentativeTable } from "./ScreeningRepresentativeTable";

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

const MAX_SCREENING_SEED = 2_147_483_647;

function nextScreeningSeed(current: number) {
  const value = crypto.getRandomValues(new Uint32Array(1))[0] % (MAX_SCREENING_SEED + 1);
  return value === current ? (value + 1) % (MAX_SCREENING_SEED + 1) : value;
}

type GoalEvaluation = ApiScreeningRun["points"][number]["goal_evaluation"];

function goalEvaluationLabel(evaluation: GoalEvaluation, primary: boolean) {
  // Older saved runs may contain achieved=false for absolute-distance ranking.
  // The method is the durable semantic: a point target has no pass/fail tolerance.
  if (evaluation.method === "absolute_distance") return "目標値への近さで順位付け（達成判定なし）";
  if (evaluation.achieved === true) return primary ? "選別基準を満たす" : "副条件を満たす";
  if (evaluation.achieved === false) return primary ? "選別基準を満たさない" : "副条件を満たさない";
  return "達成判定なし";
}

function outputGoalDirection(direction: string | undefined): ScreeningGoalDirection {
  return direction === "at_most" ? "at_most" : "at_least";
}

function draftFromGoal(
  goal: ScreeningGoalPayload | null | undefined,
  fallbackDirection: ScreeningGoalDirection,
  legacyValue?: number | null,
): ScreeningGoalDraft {
  if (goal) {
    return {
      direction: goal.direction,
      lower: goal.lower == null ? "" : String(goal.lower),
      upper: goal.upper == null ? "" : String(goal.upper),
    };
  }
  if (legacyValue != null) {
    return fallbackDirection === "at_most"
      ? { direction: fallbackDirection, lower: "", upper: String(legacyValue) }
      : { direction: fallbackDirection, lower: String(legacyValue), upper: "" };
  }
  return emptyScreeningGoal(fallbackDirection);
}

function goalSummary(goal: ScreeningGoalPayload | null | undefined, legacyValue?: number | null) {
  if (!goal) return legacyValue == null ? "選別基準なし" : number(legacyValue, 1);
  if (goal.direction === "between") return `${number(goal.lower ?? 0, 1)}–${number(goal.upper ?? 0, 1)}`;
  if (goal.direction === "at_most") return `${number(goal.upper ?? 0, 1)} 以下`;
  return `${number(goal.lower ?? 0, 1)} 以上`;
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
  const [seed, setSeed] = useState(20260719);
  const [proposalStrategyId, setProposalStrategyId] = useState("latin_hypercube_v1");
  const [proposalStrategies, setProposalStrategies] = useState<ApiProposalStrategyAvailability[]>([]);
  const [explorationParameter, setExplorationParameter] = useState(2);
  const [incumbentValue, setIncumbentValue] = useState("");
  const [supportPolicy, setSupportPolicy] = useState<"supported_first" | "exclude_extrapolated" | "allow_with_warning">("supported_first");
  const [batchEnabled, setBatchEnabled] = useState(true);
  const [batchSize, setBatchSize] = useState(8);
  const [batchCandidatePoolSize, setBatchCandidatePoolSize] = useState(32);
  const [batchSelectorId, setBatchSelectorId] = useState<"ranked_top_k_v1" | "greedy_value_diversity_v1">("greedy_value_diversity_v1");
  const [diversityWeight, setDiversityWeight] = useState(0.75);
  const [nearDuplicateThreshold, setNearDuplicateThreshold] = useState(0.05);
  const [pendingCandidateIds, setPendingCandidateIds] = useState<string[]>([]);
  const [controlCandidateId, setControlCandidateId] = useState("");
  const [controlReplicates, setControlReplicates] = useState(1);
  const [maxBatchCost, setMaxBatchCost] = useState("");
  const [target, setTarget] = useState("TS");
  const [targetGoal, setTargetGoal] = useState<ScreeningGoalDraft>({ direction: "at_least", lower: "500", upper: "" });
  const [secondaryGoals, setSecondaryGoals] = useState<Record<string, ScreeningGoalDraft>>({});
  const [baseCandidateId, setBaseCandidateId] = useState(selectedId);
  const baseCandidateSource = candidates.find((candidate) => candidate.id === baseCandidateId);
  const [baseCandidate, setBaseCandidate] = useState<Candidate>();
  const [baseEditorVersion, setBaseEditorVersion] = useState(0);
  const pendingBaseInputs = useRef<ApiScreeningRun["base_inputs"]>(undefined);
  const balancePaths = new Set(
    resolvedTaskDefinition?.task_definition.composition_totals
      ?.map((constraint) => constraint.balance_path)
      .filter((path): path is string => path != null) ?? [],
  );
  const compositionBalanceNotice = resolvedTaskDefinition?.task_definition.composition_totals
    ?.map((constraint) => {
      const balance = resolvedTaskDefinition.task_definition.input_groups
        .flatMap((group) => group.fields)
        .find((field) => field.path === constraint.balance_path);
      return balance ? `${balance.label.replace("（balance）", "")}は残量として自動配分（合計 ${constraint.total} ${constraint.unit}）` : null;
    })
    .filter((message): message is string => message != null)
    .join(" / ");
  const optionGroups = resolvedTaskDefinition
    ? resolvedTaskDefinition.task_definition.input_groups.map((group) => ({
        key: group.key,
        label: group.label,
        options: group.fields.flatMap((field) => {
          if (!field.editable || balancePaths.has(field.path)) return [];
          if (field.kind !== "heat_pattern") return [{
            value: field.path,
            label: `${field.label}${field.unit ? ` (${field.unit})` : ""}`,
            kind: field.kind,
            choices: field.choices,
            defaultRange: field.default_range,
            trainingRange: field.training_range,
          }];
          return (baseCandidate?.raw.inputs.heat_pattern ?? []).flatMap((point, index) => [
            {
              value: `heat_pattern.${index}.temperature_c`,
              label: `点${index + 1} 温度 (°C)`,
              kind: "number" as const,
              choices: [] as string[],
              defaultRange: { min: Math.max(0, point.temperature_c - 50), max: point.temperature_c + 50 },
              trainingRange: undefined,
            },
            {
              value: `heat_pattern.${index}.time_s`,
              label: `点${index + 1} 時刻 (s)`,
              kind: "number" as const,
              choices: [] as string[],
              defaultRange: { min: Math.max(0, point.time_s - 10), max: point.time_s + 10 },
              trainingRange: undefined,
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
  const fixedObjective = project?.objective_definition;
  const fixedObjectivePrimary = fixedObjective?.terms.find((term) => term.role === "primary_objective")
    ?? fixedObjective?.terms.find((term) => term.role === "reporting_only");
  const unsupportedObjectiveReason = fixedObjective?.optimization_kind === "pareto_multi_objective"
    ? "Pareto Objectiveは現在の提案方法ではまだ実行できません。"
    : fixedObjective?.terms.some((term) => term.role === "soft_preference")
      ? "soft preferenceは現在の提案方法ではまだ実行できません。"
      : fixedObjective?.terms.some((term) => term.role !== "reporting_only" && !["at_least", "at_most", "between"].includes(term.direction ?? ""))
        ? "このObjective方向は現在の提案方法ではまだ実行できません。"
        : "";
  const defaultGoalDraft = (output: TaskDefinitionContract["outputs"][number]): ScreeningGoalDraft => {
    const configured = project?.target_values?.[output.key];
    if (typeof configured === "number") {
      return draftFromGoal(undefined, outputGoalDirection(output.goal_direction), configured);
    }
    if (configured && typeof configured === "object" && "lower" in configured && "upper" in configured) {
      return draftFromGoal(
        { direction: "between", lower: configured.lower, upper: configured.upper },
        outputGoalDirection(output.goal_direction),
      );
    }
    return emptyScreeningGoal(outputGoalDirection(output.goal_direction));
  };
  useEffect(() => {
    const defaults = options.filter((option) => option.kind === "number").slice(0, 2).map((option) => ({
      field: option.value,
      mode: "range" as const,
      first: String(option.defaultRange?.min ?? ""),
      second: String(option.defaultRange?.max ?? ""),
    }));
    setVariables(defaults);
    const fixedPrimaryOutput = outputs.find((output) => output.key === fixedObjectivePrimary?.output_key);
    if (fixedObjectivePrimary && fixedPrimaryOutput) {
      setTarget(fixedObjectivePrimary.output_key);
      setTargetGoal(fixedObjectivePrimary.direction && ["at_least", "at_most", "between"].includes(fixedObjectivePrimary.direction)
        ? {
            direction: fixedObjectivePrimary.direction as ScreeningGoalDraft["direction"],
            lower: fixedObjectivePrimary.lower == null ? "" : String(fixedObjectivePrimary.lower),
            upper: fixedObjectivePrimary.upper == null ? "" : String(fixedObjectivePrimary.upper),
          }
        : emptyScreeningGoal(outputGoalDirection(fixedPrimaryOutput.goal_direction)));
      setSecondaryGoals(Object.fromEntries(
        (fixedObjective?.terms ?? [])
          .filter((term) => term.role === "hard_outcome_constraint" && term.direction && ["at_least", "at_most", "between"].includes(term.direction))
          .map((term) => [term.output_key, {
            direction: term.direction as ScreeningGoalDraft["direction"],
            lower: term.lower == null ? "" : String(term.lower),
            upper: term.upper == null ? "" : String(term.upper),
          }]),
      ));
    } else if (outputs[0]) {
      setTarget(outputs[0].key);
      setTargetGoal(defaultGoalDraft(outputs[0]));
      setSecondaryGoals({});
    }
    setBatchEnabled(true);
    setBatchSize(8);
    setBatchCandidatePoolSize(32);
    setBatchSelectorId("greedy_value_diversity_v1");
    setDiversityWeight(0.75);
    setNearDuplicateThreshold(0.05);
    setPendingCandidateIds([]);
    setControlCandidateId("");
    setControlReplicates(1);
    setMaxBatchCost("");
    setResult(null);
    setSelectedPointIndices([]);
    setFocusedPointIndex(null);
    setDraftDirty(false);
  }, [resolvedTaskDefinition?.task_definition.id, project?.id, project?.objective_definition_digest]);
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
  useEffect(() => {
    let active = true;
    workbenchApi.proposalStrategies(projectId, target)
      .then((items) => {
        if (!active) return;
        setProposalStrategies(items);
        if (!items.some((item) => item.available && item.definition.strategy_id === proposalStrategyId)) {
          setProposalStrategyId(items.find((item) => item.available)?.definition.strategy_id ?? "latin_hypercube_v1");
        }
      })
      .catch(() => { if (active) setProposalStrategies([]); });
    return () => { active = false; };
  }, [projectId, target]);
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
  const run = async (requestedSeed = seed) => {
    if (!baseCandidate) return setError("基準条件を読み込めませんでした。");
    const controlCandidate = controlCandidateId
      ? candidates.find((candidate) => candidate.id === controlCandidateId)
      : null;
    if (controlCandidateId && !controlCandidate) {
      return setError("Control候補を読み直してから実行してください。");
    }
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
        seed: requestedSeed,
        target,
        target_goal: screeningGoalFromDraft(targetGoal),
        secondary_goals: Object.fromEntries(
          Object.entries(secondaryGoals)
            .map(([key, draft]) => [key, screeningGoalFromDraft(draft)] as const)
            .filter((entry): entry is readonly [string, ScreeningGoalPayload] => entry[1] != null),
        ),
        proposal: {
          strategy_id: proposalStrategyId,
          exploration_parameter: explorationParameter,
          pool_multiplier: 4,
          support_policy: supportPolicy,
          fallback_policy: "reject",
          incumbent_value: incumbentValue === "" ? null : Number(incumbentValue),
        },
        batch_definition: batchEnabled ? {
          schema_version: "batch-proposal-definition/v1",
          selector_id: batchSelectorId,
          batch_size: batchSize,
          candidate_pool_size: batchCandidatePoolSize,
          diversity_weight: diversityWeight,
          near_duplicate_threshold: nearDuplicateThreshold,
          pending_candidate_ids: pendingCandidateIds,
          pending_policy: "avoid",
          pending_penalty: 1,
          controls: controlCandidateId
            ? [{
                candidate_id: controlCandidateId,
                candidate_revision: controlCandidate!.raw.revision,
                replicates: controlReplicates,
              }]
            : [],
          category_quotas: [],
          resources: {
            default_candidate_cost: 1,
            max_total_cost: maxBatchCost === "" ? null : Number(maxBatchCost),
            setup_group_path: null,
            max_setup_groups: null,
            setup_change_penalty: 0,
            cost_rules: [],
          },
        } : null,
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
    const primaryDefinition = outputs.find((output) => output.key === run.target);
    setTargetGoal(draftFromGoal(
      run.target_goal,
      outputGoalDirection(primaryDefinition?.goal_direction),
      run.target_value,
    ));
    const legacySecondaryTargets = run.secondary_targets ?? {};
    setSecondaryGoals(Object.fromEntries(
      outputs
        .filter((output) => output.key !== run.target)
        .flatMap((output) => {
          const goal = run.secondary_goals?.[output.key];
          const legacy = legacySecondaryTargets[output.key];
          if (!goal && legacy == null) return [];
          return [[
            output.key,
            draftFromGoal(goal, outputGoalDirection(output.goal_direction), legacy),
          ] as const];
        }),
    ));
    setSamples(run.samples);
    setSeed(run.seed);
    if (run.proposal_strategy) {
      setProposalStrategyId(run.proposal_strategy.id);
      setExplorationParameter(run.proposal_strategy.exploration_parameter ?? 2);
      setSupportPolicy(run.proposal_strategy.support_policy);
    }
    if (run.batch_proposal) {
      const definition = run.batch_proposal.definition;
      setBatchEnabled(true);
      setBatchSize(definition.batch_size);
      setBatchCandidatePoolSize(definition.candidate_pool_size);
      setBatchSelectorId(
        definition.selector_id === "greedy_value_diversity_v1"
          ? definition.selector_id
          : "ranked_top_k_v1",
      );
      setDiversityWeight(definition.diversity_weight);
      setNearDuplicateThreshold(definition.near_duplicate_threshold);
      setPendingCandidateIds([...definition.pending_candidate_ids]);
      setControlCandidateId(definition.controls[0]?.candidate_id ?? "");
      setControlReplicates(definition.controls[0]?.replicates ?? 1);
      setMaxBatchCost(definition.resources.max_total_cost == null ? "" : String(definition.resources.max_total_cost));
    } else {
      setBatchEnabled(false);
    }
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
  const remainingCandidateCapacity = Math.max(0, 100 - candidates.length);
  const addableSelectedCount = selectedNewPointIndices.length <= remainingCandidateCapacity
    ? selectedNewPointIndices.length
    : 0;
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
  const batchPointIndices = Array.from(new Set(
    result?.batch_proposal?.selected.flatMap((item) => (
      item.source === "acquisition_ranked" && item.point_index != null
        ? [item.point_index]
        : []
    )) ?? [],
  ));
  const newBatchPointIndices = batchPointIndices.filter((index) => !stockedPointIndices.has(index));
  const persistBatch = async () => {
    if (!result || !newBatchPointIndices.length) return;
    if (newBatchPointIndices.length > remainingCandidateCapacity) {
      setError(`提案batchを保存するには候補枠があと${newBatchPointIndices.length}件必要です。`);
      return;
    }
    try {
      const response = await workbenchApi.candidatesFromScreening(
        projectId,
        result.id,
        newBatchPointIndices,
      );
      response.candidates.forEach((candidate) => onCandidate(fromApiCandidate(candidate)));
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "提案batchを候補へ保存できませんでした。");
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
            指定範囲に点を分散して生成し、制約を満たす点から候補を集めます。
          </p>
        </div>
        <button
          className="primary-button"
          disabled={!baseCandidateId || !baseCandidate || Boolean(unsupportedObjectiveReason)}
          title={unsupportedObjectiveReason || (baseCandidateId ? "選択した候補を基準に探索します" : "基準候補が必要です")}
          onClick={() => {
            void run();
          }}
        >
          探索を実行
        </button>
      </div>
      {compositionBalanceNotice && <p className="screening-balance-notice">組成制約: {compositionBalanceNotice}</p>}
      {unsupportedObjectiveReason && <p className="error-banner">{unsupportedObjectiveReason}</p>}
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
                <b>{outputs.find((output) => output.key === run.target)?.label ?? run.target}</b> → {goalSummary(run.target_goal, run.target_value)} /{" "}
                 {run.samples}点{" "}
                <small>
                  基準: {candidates.find((candidate) => candidate.id === run.base_candidate_id)?.label ?? run.base_candidate_id?.slice(0, 8) ?? "旧保存データ"} ·{" "}
                  seed {run.seed} ·{" "}
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
              onChange={(event) => {
                const nextSamples = Number(event.target.value);
                setSamples(nextSamples);
                setBatchCandidatePoolSize((current) => Math.min(current, nextSamples, 128));
                setDraftDirty(true);
              }}
            />
          </label>
          <label>
            乱数seed
            <input
              type="number"
              min="0"
              max={MAX_SCREENING_SEED}
              value={seed}
              onChange={(event) => { setSeed(Number(event.target.value)); setDraftDirty(true); }}
            />
          </label>
          <label>
            候補の提案方法
            <select
              value={proposalStrategyId}
              onChange={(event) => { setProposalStrategyId(event.target.value); setDraftDirty(true); }}
            >
              {!proposalStrategies.length && (
                <option value="latin_hypercube_v1">Latin hypercube・目標基準</option>
              )}
              {proposalStrategies.map((item) => {
                const needsIncumbent = item.reasons.length === 1
                  && item.reasons[0].includes("incumbent");
                return (
                <option
                  key={item.definition.strategy_id}
                  value={item.definition.strategy_id}
                  disabled={!item.available && !needsIncumbent}
                  title={item.reasons.join(" / ")}
                >
                  {item.definition.label}{item.available ? "" : needsIncumbent ? "（現在の最良値を入力）" : `（利用不可: ${item.reasons.join(" / ")}）`}
                </option>
              );})}
            </select>
          </label>
          {(proposalStrategyId === "sobol_ucb_v1" || proposalStrategyId === "sobol_ei_v1") && (
            <label>
              {proposalStrategyId === "sobol_ei_v1"
                ? "改善余裕 ξ"
                : "探索の強さ（σ倍率）"}
              <input
                type="number"
                min="0.01"
                step="0.1"
                value={explorationParameter}
                onChange={(event) => { setExplorationParameter(Number(event.target.value)); setDraftDirty(true); }}
              />
              <small>
                {proposalStrategyId === "sobol_ei_v1"
                  ? "現在の最良値を、この値以上改善する余地を評価します"
                  : "予測平均へ加減する標準偏差の倍率です"}
              </small>
            </label>
          )}
          {proposalStrategyId === "sobol_ei_v1" && (
            <label>
              現在の最良値
              <input
                type="number"
                value={incumbentValue}
                placeholder="Project判断から自動"
                onChange={(event) => { setIncumbentValue(event.target.value); setDraftDirty(true); }}
              />
            </label>
          )}
          <label>
            学習範囲外の扱い
            <select
              value={supportPolicy}
              onChange={(event) => { setSupportPolicy(event.target.value as typeof supportPolicy); setDraftDirty(true); }}
            >
              <option value="supported_first">範囲内を優先</option>
              <option value="exclude_extrapolated">外挿を除外</option>
              <option value="allow_with_warning">警告付きで含める</option>
            </select>
          </label>
          <label>
            選別する特性
            <select
              value={target}
              disabled={Boolean(fixedObjective)}
              onChange={(event) => {
                const next = event.target.value;
                const definition = outputs.find((output) => output.key === next);
                setTarget(next);
                setTargetGoal(definition ? defaultGoalDraft(definition) : emptyScreeningGoal("at_least"));
                setSecondaryGoals((current) => {
                  const updated = { ...current };
                  delete updated[next];
                  return updated;
                });
                setDraftDirty(true);
              }}
            >
              {outputs.map((output) => <option key={output.key} value={output.key}>{output.label} ({output.unit})</option>)}
            </select>
          </label>
        </div>
        <details className="screening-batch-settings">
          <summary>
            <label onClick={(event) => event.stopPropagation()}>
              <input
                type="checkbox"
                checked={batchEnabled}
                onChange={(event) => { setBatchEnabled(event.target.checked); setDraftDirty(true); }}
              />
              実験バッチを提案
            </label>
            <small>獲得順位価値に多様性・pending・exact Control・コストを加えて選抜</small>
          </summary>
          {batchEnabled && (
            <div className="screening-batch-grid">
              <label>
                バッチ件数
                <input
                  type="number"
                  min="1"
                  max={Math.min(32, samples)}
                  value={batchSize}
                  onChange={(event) => {
                    const nextBatchSize = Number(event.target.value);
                    setBatchSize(nextBatchSize);
                    setBatchCandidatePoolSize((current) => Math.max(current, nextBatchSize));
                    setDraftDirty(true);
                  }}
                />
              </label>
              <label>
                バッチ候補pool
                <input
                  type="number"
                  min={batchSize}
                  max={Math.min(samples, 128)}
                  value={batchCandidatePoolSize}
                  onChange={(event) => { setBatchCandidatePoolSize(Number(event.target.value)); setDraftDirty(true); }}
                />
                <small>獲得順位の上位から選抜に渡す件数</small>
              </label>
              <label>
                バッチ選抜
                <select
                  value={batchSelectorId}
                  onChange={(event) => { setBatchSelectorId(event.target.value as typeof batchSelectorId); setDraftDirty(true); }}
                >
                  <option value="greedy_value_diversity_v1">獲得順位価値 + 多様性</option>
                  <option value="ranked_top_k_v1">獲得順位価値の上位</option>
                </select>
              </label>
              {batchSelectorId === "greedy_value_diversity_v1" && (
                <label>
                  多様性の重み
                  <input
                    type="number"
                    min="0"
                    max="10"
                    step="0.05"
                    value={diversityWeight}
                    onChange={(event) => { setDiversityWeight(Number(event.target.value)); setDraftDirty(true); }}
                  />
                </label>
              )}
              <label>
                近接とみなす距離
                <input
                  type="number"
                  min="0"
                  max="1"
                  step="0.01"
                  value={nearDuplicateThreshold}
                  onChange={(event) => { setNearDuplicateThreshold(Number(event.target.value)); setDraftDirty(true); }}
                />
                <small>Design Spaceの各範囲で正規化</small>
              </label>
              <label>
                Control条件
                <select
                  value={controlCandidateId}
                  onChange={(event) => { setControlCandidateId(event.target.value); setDraftDirty(true); }}
                >
                  <option value="">指定なし</option>
                  {candidates.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.label}</option>)}
                </select>
              </label>
              {controlCandidateId && (
                <label>
                  Control反復数
                  <input
                    type="number"
                    min="1"
                    max="8"
                    value={controlReplicates}
                    onChange={(event) => { setControlReplicates(Number(event.target.value)); setDraftDirty(true); }}
                  />
                </label>
              )}
              {controlCandidateId && <small>指定候補の現在revisionをDesign Spaceで再検証し、その条件自体をControlとして固定します。</small>}
              <label>
                最大実験コスト
                <input
                  type="number"
                  min="0.01"
                  value={maxBatchCost}
                  placeholder="制約なし"
                  onChange={(event) => { setMaxBatchCost(event.target.value); setDraftDirty(true); }}
                />
                <small>既定は1条件=1</small>
              </label>
              <fieldset>
                <legend>実験予定・測定中</legend>
                {candidates.map((candidate) => (
                  <label key={candidate.id}>
                    <input
                      type="checkbox"
                      checked={pendingCandidateIds.includes(candidate.id)}
                      onChange={(event) => {
                        setPendingCandidateIds((current) => event.target.checked
                          ? [...current, candidate.id]
                          : current.filter((id) => id !== candidate.id));
                        setDraftDirty(true);
                      }}
                    />
                    {candidate.label}
                  </label>
                ))}
              </fieldset>
            </div>
          )}
        </details>
        {fixedObjective
          ? <section className="screening-goals" aria-label="Project固定Objective">
              <h3>{fixedObjective.name} · r{fixedObjective.revision}</h3>
              <p>Projectに固定した判断基準をそのまま提案計算へ使います。</p>
              <ul>
                {fixedObjective.terms.map((term) => (
                  <li key={term.output_key}>
                    {outputs.find((output) => output.key === term.output_key)?.label ?? term.output_key}
                    {" · "}{term.role}
                    {" · "}{term.direction ?? "表示のみ"}
                    {term.lower != null ? ` ${term.lower}以上` : ""}
                    {term.upper != null ? ` ${term.upper}以下` : ""}
                  </li>
                ))}
              </ul>
            </section>
          : targetDefinition && (
          <section className="screening-goals" aria-label="選別基準">
            <ScreeningGoalEditor
              label={`主目標: ${targetDefinition.label}`}
              unit={targetDefinition.unit}
              value={targetGoal}
              onChange={(next) => { setTargetGoal(next); setDraftDirty(true); }}
            />
            {outputs.filter((output) => output.key !== target).map((output) => (
              <ScreeningGoalEditor
                key={output.key}
                label={`副条件: ${output.label}`}
                unit={output.unit}
                value={secondaryGoals[output.key] ?? emptyScreeningGoal(outputGoalDirection(output.goal_direction))}
                onChange={(next) => {
                  setSecondaryGoals((current) => ({ ...current, [output.key]: next }));
                  setDraftDirty(true);
                }}
              />
            ))}
          </section>
        )}
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
            {variables.map((row, index) => {
              const option = options.find((item) => item.value === row.field);
              const first = Number(row.first);
              const second = Number(row.second);
              const outsideTraining = row.mode === "range"
                && option?.trainingRange
                && Number.isFinite(first)
                && Number.isFinite(second)
                && (first < option.trainingRange.min || second > option.trainingRange.max);
              return <tr key={`${row.field}-${index}`}>
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
                  {option?.trainingRange && <small className={outsideTraining ? "screening-variable-range outside" : "screening-variable-range"}>
                    学習範囲 {number(option.trainingRange.min, 3)}–{number(option.trainingRange.max, 3)}
                    {outsideTraining && <b> · 範囲外を含む</b>}
                  </small>}
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
              </tr>;
            })}
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
          <ScreeningProposalSummary
            result={result}
            batchSaveCount={newBatchPointIndices.length}
            onSaveBatch={() => { void persistBatch(); }}
            onAnotherSample={() => {
              const nextSeed = nextScreeningSeed(seed);
              setSeed(nextSeed);
              void run(nextSeed);
            }}
          />
          <div className="screening-display-controls">
            <label>X軸<select value={xAxis} onChange={(event) => setXAxis(event.target.value)}>{confirmedVaryingFields.map((field) => <option key={field} value={field}>{axisLabel(field)}</option>)}</select></label>
            <label>Y軸<select value={yAxis} onChange={(event) => setYAxis(event.target.value)}><option value="">点番号</option>{confirmedVaryingFields.filter((field) => field !== xAxis).map((field) => <option key={field} value={field}>{axisLabel(field)}</option>)}</select></label>
            <label>色<select value={colorMetric} onChange={(event) => setColorMetric(event.target.value)}><option value="score">目標に対する有望度</option>{outputs.map((output) => <option key={output.key} value={output.key}>{output.label}</option>)}</select></label>
          </div>
          {hiddenVaryingFields.length > 0 && <p className="screening-hidden-variables"><b>図に出ていない変動条件:</b> {hiddenVaryingFields.map(axisLabel).join(" / ")}。各点の詳細で実値を確認できます。</p>}
          <div className="screening-action-bar" role="status">
            <dl className="screening-selection-summary">
              <div><dt>選択</dt><dd>{selectedPointIndices.length}件</dd><small>図・表で選んだ点</small></div>
              <div><dt>新規</dt><dd>{selectedNewPointIndices.length}件</dd><small>まだ候補にない選択点</small></div>
              <div><dt>今回追加可能</dt><dd>{addableSelectedCount}件</dd><small>候補枠の空き {remainingCandidateCapacity}件</small></div>
            </dl>
            {selectedPointIndices.some((index) => stockedPointIndices.has(index)) && <small>stock済みの点は再追加しません。</small>}
            {selectedNewPointIndices.length > remainingCandidateCapacity && <small className="screening-capacity-warning">新規選択が候補枠を超えています。選択を{remainingCandidateCapacity}件以下に減らしてください。</small>}
            <CandidateAddButton disabled={!addableSelectedCount} onClick={() => void persistSelected()}>{addableSelectedCount}件を候補へ追加</CandidateAddButton>
            <button className="outline-button" disabled={!candidates.length} onClick={onCompare}>候補比較へ</button>
          </div>
          <div className="screen-legend">
            <span className="opportunity-scale" />
            {colorMetric === "score" ? result.score_contract?.display_label ?? "目標に対して有望" : outputs.find((output) => output.key === colorMetric)?.label ?? colorMetric} <span className="support-key supported" />
            範囲内 <span className="support-key caution" />
            要確認 <span className="support-key extrapolated" />
            外挿 <span className="selection-key" />
            選択中
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
              const selected = selectedPointIndices.includes(point.index);
              return (
                <g key={point.index} className="screen-map-point">
                  {selected && <circle className="screen-map-selection-ring" cx={cx} cy={cy} r="12" aria-hidden="true" />}
                  <circle
                  className={selectedPointIndices.includes(point.index) ? "selected" : ""}
                  cx={cx}
                  cy={cy}
                  r="7"
                  fill={opportunity(point)}
                  stroke={supportStroke(point.support.status)}
                  strokeWidth="3"
                  opacity={
                    point.support.status === "extrapolated" ? ".55" : ".9"
                  }
                  role="button"
                  aria-pressed={selected}
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
                </g>
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
            <div className="screening-point-predictions">
              {Object.entries({ [result.target]: focusedPoint.prediction, ...(focusedPoint.predictions ?? {}) }).map(([key, prediction]) => {
                const output = resolveOutputDefinition(outputs, key);
                const assessment = assessPrediction(output, prediction);
                const evaluation = key === result.target
                  ? focusedPoint.goal_evaluation
                  : focusedPoint.secondary_goal_evaluations?.[key];
                return <div className={assessment.implausible ? "implausible-output" : undefined} title={assessment.warning ?? undefined} key={key}>
                  <b>{output?.label ?? key}</b>
                  <strong>{number(prediction.value, 1)} {prediction.unit}</strong>
                  <small>{number(prediction.lower, 1)}–{number(prediction.upper, 1)}{prediction.goal_probability != null ? ` / 達成確率 ${Math.round(prediction.goal_probability * 100)}%` : ""}</small>
                  {assessment.implausible && <em className="output-warning-badge">⚠ 物理範囲外</em>}
                  {evaluation && <em>{goalEvaluationLabel(evaluation, key === result.target)}</em>}
                </div>;
              })}
            </div>
            <p><b>全変動条件:</b> {Object.entries(focusedPoint.inputs).map(([key, value]) => `${axisLabel(key)} ${typeof value === "number" ? number(value, 3) : value}`).join(" / ")}</p>
            <p><b>支持度:</b> {focusedPoint.support.status} / percentile {number(focusedPoint.support.percentile, 1)} / 参照{focusedPoint.support.reference_count}件</p>
            {focusedPoint.warnings?.map((warning) => <p className="warning" key={warning}>{warning}</p>)}
            {(focusedPoint.similar ?? []).length > 0 && <p><b>近い実績:</b> {(focusedPoint.similar ?? []).slice(0, 3).map((item) => `${item.observation_id || item.parent_key} (距離 ${number(item.distance, 2)})`).join(" / ")}</p>}
          </section>}
          <ScreeningRepresentativeTable
            result={result}
            outputs={outputs}
            options={options}
            baseCandidateLabel={candidates.find((candidate) => candidate.id === result.base_candidate_id)?.label ?? "基準候補"}
            selectedPointIndices={selectedPointIndices}
            stockedPointIndices={stockedPointIndices}
            onToggle={togglePoint}
          />
        </>
      )}
    </div>
  );
}
