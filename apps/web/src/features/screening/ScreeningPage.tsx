import { Fragment, useEffect, useRef, useState } from "react";
import { fromApiCandidate, toApiCandidate, type CandidateViewModel as Candidate, type ResolvedTaskDefinition, type TaskDefinitionContract } from "../candidates";
import {
  workbenchApi,
  type ApiCandidateCapacity,
  type ApiProject,
  type ApiProposalStrategyAvailability,
  type ApiScreeningRun,
  type ApiSimilarObservation,
} from "../../shared/api/workbench-api";
import { candidateInputIdentity } from "../../shared/api/inferenceRequestCache";
import { CandidateAddButton } from "../../shared/ui/CandidateAddButton";
import { SvgChartTooltip } from "../../shared/ui/SvgChartTooltip";
import { assessPrediction, clampToRange, resolveOutputDefinition } from "../../shared/outputPresentation";
import { formatTaskNumber } from "../../shared/taskPresentation";
import { supportStatusLabel } from "../../shared/supportPresentation";
import {
  emptyScreeningGoal,
  screeningGoalFromDraft,
  ScreeningGoalEditor,
  type ScreeningGoalDirection,
  type ScreeningGoalDraft,
  type ScreeningGoalPayload,
} from "./ScreeningGoalEditor";
import { ScreeningProposalSummary, ScreeningRunEvidence } from "./ScreeningProposalSummary";
import { safeExplorationRange } from "./screeningVariableRange";
import { screeningVariableError } from "./screeningVariableValidation";
import { ScreeningRepresentativeTable } from "./ScreeningRepresentativeTable";
import { initialScreeningMode, type ScreeningMode } from "./screeningInitialMode";
import { buildScreeningInterpolation } from "./screeningInterpolation";
import {
  ScreeningEvaluatedTable,
  ScreeningResultSurfaceTabs,
  type ScreeningResultSurface,
} from "./ScreeningResultSurfaces";
import { HistoricalEvidenceDrawer } from "../workbench";

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

const screeningModes: Array<{
  id: ScreeningMode;
  label: string;
  description: string;
}> = [
  {
    id: "landscape",
    label: "領域を見る",
    description: "入力を動かし、予測の分布と学習範囲からの外れ方を確認",
  },
  {
    id: "opportunity",
    label: "有望候補を探す",
    description: "目標に近い条件を順位付けして候補を見つける",
  },
  {
    id: "batch",
    label: "実験バッチを組む",
    description: "有望な結果から、重複を避けた複数の実験条件を選ぶ",
  },
];

function runHasObjective(run: ApiScreeningRun) {
  if (run.target_goal) return true;
  if (run.target_value != null) return true;
  if (Object.keys(run.secondary_targets ?? {}).length > 0) return true;
  return run.objective_definition?.terms.some(
    (term) => term.role === "primary_objective" && term.direction != null,
  ) ?? false;
}

function modeFromRun(run: ApiScreeningRun): ScreeningMode {
  if (run.purpose === "design_space_map") return "landscape";
  if (run.purpose === "goal_search") return "opportunity";
  if (run.purpose === "experiment_batch") return "batch";
  if (run.batch_proposal) return "batch";
  return runHasObjective(run) ? "opportunity" : "landscape";
}

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
  onSelectCandidate,
  onCandidate,
  onCompare,
  onConfigureGoals,
}: {
  projectId: string;
  project: ApiProject | undefined;
  candidates: Candidate[];
  selectedId: string;
  taskDefinition: TaskDefinitionContract | null;
  resolvedTaskDefinition: ResolvedTaskDefinition | null;
  initialRunId?: string;
  onRunChange: (runId: string) => void;
  onSelectCandidate: (candidateId: string) => void;
  onCandidate: (candidate: Candidate) => void;
  onCompare: () => void;
  onConfigureGoals: () => void;
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
  const [proposalCount, setProposalCount] = useState(5);
  const [proposalSelectionPolicy, setProposalSelectionPolicy] = useState<
    "ranked_top_k_v1" | "greedy_value_diversity_v1"
  >("ranked_top_k_v1");
  const [proposalDiversityWeight, setProposalDiversityWeight] = useState(0.75);
  const [explorationParameter, setExplorationParameter] = useState(2);
  const [incumbentValue, setIncumbentValue] = useState("");
  const [supportPolicy, setSupportPolicy] = useState<"supported_first" | "exclude_extrapolated" | "allow_with_warning">("supported_first");
  const [screeningMode, setScreeningMode] = useState<ScreeningMode>(
    () => initialScreeningMode(project?.target_values),
  );
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
  const baseCandidate = candidates.find((candidate) => candidate.id === baseCandidateId);
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
            defaultRange: safeExplorationRange(field.default_range, field.training_range),
            trainingRange: field.training_range,
            allowedRange: field.allowed_range,
          }];
          return (baseCandidate?.raw.inputs.heat_pattern ?? []).flatMap((point, index) => [
            {
              value: `heat_pattern.${index}.temperature_c`,
              label: `点${index + 1} 温度 (°C)`,
              kind: "number" as const,
              choices: [] as string[],
              defaultRange: { min: Math.max(0, point.temperature_c - 50), max: point.temperature_c + 50 },
              trainingRange: undefined,
              allowedRange: undefined,
            },
            {
              value: `heat_pattern.${index}.time_s`,
              label: `点${index + 1} 時刻 (s)`,
              kind: "number" as const,
              choices: [] as string[],
              defaultRange: { min: Math.max(0, point.time_s - 10), max: point.time_s + 10 },
              trainingRange: undefined,
              allowedRange: undefined,
            },
          ]);
        }),
      })).filter((group) => group.options.length)
    : [];
  const options = optionGroups.flatMap((group) => group.options);
  const [result, setResult] = useState<ScreenResult | null>(null);
  const [savedRuns, setSavedRuns] = useState<ScreenResult[]>([]);
  const [pendingDeleteRunId, setPendingDeleteRunId] = useState("");
  const [deletingRunId, setDeletingRunId] = useState("");
  const [showAllSavedRuns, setShowAllSavedRuns] = useState(false);
  const [detailItem, setDetailItem] = useState<ApiSimilarObservation | null>(null);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const [candidateCapacity, setCandidateCapacity] = useState<ApiCandidateCapacity | null>(null);
  const [candidateCapacityError, setCandidateCapacityError] = useState("");
  const [draftDirty, setDraftDirty] = useState(false);
  const [xAxis, setXAxis] = useState("");
  const [yAxis, setYAxis] = useState("");
  const [colorMetric, setColorMetric] = useState("score");
  const [chartExpanded, setChartExpanded] = useState(false);
  const [resultSurface, setResultSurface] = useState<ScreeningResultSurface>("map");
  const [selectedPointIndices, setSelectedPointIndices] = useState<number[]>([]);
  const [focusedPointIndex, setFocusedPointIndex] = useState<number | null>(null);
  const [hoveredScreenPoint, setHoveredScreenPoint] = useState<{ x: number; y: number; lines: string[] } | null>(null);
  const variableSelectRefs = useRef<Array<HTMLSelectElement | null>>([]);
  const pendingVariableFocusIndex = useRef<number | null>(null);
  const runRequestSequence = useRef(0);
  const activeProjectRef = useRef(projectId);
  activeProjectRef.current = projectId;
  const outputs = taskDefinition?.outputs ?? [];
  // Predicted values follow the task contract, not a per-panel digit count.
  const outputNumber = (key: string, value: number) => taskDefinition
    ? formatTaskNumber(value, taskDefinition, `output.${key}`, project?.display_decimals)
    : number(value, 1);
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
    setScreeningMode(initialScreeningMode(project?.target_values));
    setBatchSize(8);
    setBatchCandidatePoolSize(32);
    setBatchSelectorId("greedy_value_diversity_v1");
    setDiversityWeight(0.75);
    setNearDuplicateThreshold(0.05);
    setPendingCandidateIds([]);
    setControlCandidateId("");
    setControlReplicates(1);
    setMaxBatchCost("");
    setProposalCount(5);
    setProposalSelectionPolicy("ranked_top_k_v1");
    setProposalDiversityWeight(0.75);
    setResult(null);
    setSelectedPointIndices([]);
    setFocusedPointIndex(null);
    setDetailItem(null);
    setResultSurface("map");
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
    const requestProjectId = projectId;
    runRequestSequence.current += 1;
    setResult(null);
    setSavedRuns([]);
    setPendingDeleteRunId("");
    setDeletingRunId("");
    setShowAllSavedRuns(false);
    setDetailItem(null);
    setRunning(false);
    setSelectedPointIndices([]);
    setFocusedPointIndex(null);
    workbenchApi.listScreeningRuns(requestProjectId)
      .then((runs) => { if (activeProjectRef.current === requestProjectId) setSavedRuns(runs); })
      .catch(() => undefined);
  }, [projectId]);
  useEffect(() => {
    const index = pendingVariableFocusIndex.current;
    if (index == null) return;
    pendingVariableFocusIndex.current = null;
    variableSelectRefs.current[index]?.focus();
  }, [variables.length]);
  useEffect(() => {
    let active = true;
    setCandidateCapacity(null);
    setCandidateCapacityError("");
    workbenchApi.candidateCapacity(projectId)
      .then((capacity) => {
        if (active) setCandidateCapacity(capacity);
      })
      .catch((cause) => {
        if (!active) return;
        setCandidateCapacityError(
          cause instanceof Error ? cause.message : "候補枠を取得できませんでした。",
        );
      });
    return () => {
      active = false;
    };
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
  const applyResult = (run: ScreenResult) => {
    setResult(run);
    const varying = Object.entries(run.variables).filter(([, spec]) => spec.mode !== "fixed").map(([field]) => field);
    setXAxis(varying[0] ?? "");
    setYAxis(varying[1] ?? "");
    setColorMetric("score");
    setChartExpanded(false);
    const proposedIndices = run.proposal_selection?.selected.map(
      (item) => item.point_index,
    ) ?? [];
    setSelectedPointIndices(proposedIndices);
    setFocusedPointIndex(
      proposedIndices[0] ?? run.representative_points[0]?.index ?? null,
    );
    setDetailItem(null);
    setResultSurface(
      run.proposal_selection != null || run.batch_proposal != null
        ? "proposals"
        : "map",
    );
    setDraftDirty(false);
  };
  const candidateContextDirty = Boolean(
    result
    && (
      result.base_candidate_id !== baseCandidateId
      || (
        result.base_inputs
        && baseCandidate
        && candidateInputIdentity(toApiCandidate(baseCandidate).inputs) !== candidateInputIdentity(result.base_inputs)
      )
    ),
  );
  const effectiveDraftDirty = draftDirty || candidateContextDirty;
  const displayedOpportunityRun = result && modeFromRun(result) === "opportunity"
    ? result
    : result?.source_run_id
      ? savedRuns.find((run) => run.id === result.source_run_id)
      : undefined;
  const opportunitySourceRun = displayedOpportunityRun
    && !effectiveDraftDirty
    && displayedOpportunityRun.proposal_strategy != null
    && displayedOpportunityRun.project_id === projectId
    && displayedOpportunityRun.project_design_space_digest === project?.design_space_digest
    && (
      !project?.objective_definition_digest
      || displayedOpportunityRun.objective_definition_digest === project.objective_definition_digest
    )
    ? displayedOpportunityRun
    : undefined;
  const run = async (requestedSeed = seed) => {
    if (running) return;
    if (!baseCandidate) return setError("基準条件を読み込めませんでした。");
    if (screeningMode !== "landscape" && !fixedObjective && !screeningGoalFromDraft(targetGoal)) {
      return setError("有望候補を探すには主目標を入力してください。");
    }
    if (screeningMode === "batch" && !opportunitySourceRun) {
      return setError("先に「有望候補を探す」を実行してください。");
    }
    const controlCandidate = controlCandidateId
      ? candidates.find((candidate) => candidate.id === controlCandidateId)
      : null;
    if (controlCandidateId && !controlCandidate) {
      return setError("Control候補を読み直してから実行してください。");
    }
    const sequence = ++runRequestSequence.current;
    const requestProjectId = projectId;
    try {
      setRunning(true);
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
        purpose: screeningMode === "landscape"
          ? "design_space_map"
          : screeningMode === "opportunity"
            ? "goal_search"
            : "experiment_batch",
        source_run_id: screeningMode === "batch" ? opportunitySourceRun?.id ?? null : null,
        base_candidate_id: baseCandidateId,
        base_inputs: toApiCandidate(baseCandidate).inputs,
        variables: specs,
        samples,
        seed: requestedSeed,
        target,
        target_goal: screeningMode === "landscape" ? null : screeningGoalFromDraft(targetGoal),
        secondary_goals: screeningMode === "landscape"
          ? {}
          : Object.fromEntries(
              Object.entries(secondaryGoals)
                .map(([key, draft]) => [key, screeningGoalFromDraft(draft)] as const)
                .filter((entry): entry is readonly [string, ScreeningGoalPayload] => entry[1] != null),
            ),
        proposal: {
          strategy_id: screeningMode === "landscape"
            ? "latin_hypercube_v1"
            : proposalStrategyId,
          exploration_parameter: explorationParameter,
          pool_multiplier: 4,
          support_policy: screeningMode === "landscape" ? "allow_with_warning" : supportPolicy,
          fallback_policy: "reject",
          incumbent_value: screeningMode === "landscape" || screeningMode === "batch" || incumbentValue === ""
            ? null
            : Number(incumbentValue),
          proposal_count: proposalCount,
          selection_policy: proposalSelectionPolicy,
          diversity_weight: proposalDiversityWeight,
        },
        batch_definition: screeningMode === "batch" ? {
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
    } finally {
      if (sequence === runRequestSequence.current && activeProjectRef.current === requestProjectId) {
        setRunning(false);
      }
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
      if (source && run.base_candidate_id !== baseCandidateId) {
        setBaseCandidateId(run.base_candidate_id);
        onSelectCandidate(run.base_candidate_id);
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
    setScreeningMode(modeFromRun(run));
    if (run.proposal_strategy) {
      setProposalStrategyId(run.proposal_strategy.id);
      setExplorationParameter(run.proposal_strategy.exploration_parameter ?? 2);
      setSupportPolicy(run.proposal_strategy.support_policy);
    }
    if (run.proposal_selection) {
      setProposalCount(run.proposal_selection.requested_count);
      setProposalSelectionPolicy(run.proposal_selection.policy_id);
      setProposalDiversityWeight(
        run.proposal_selection.requested_diversity_weight,
      );
    }
    if (run.batch_proposal) {
      const definition = run.batch_proposal.definition;
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
  const deleteRun = async (runId: string) => {
    if (deletingRunId) return;
    setDeletingRunId(runId);
    setError("");
    try {
      await workbenchApi.deleteScreeningRun(projectId, runId);
      setSavedRuns((runs) => runs.filter((run) => run.id !== runId));
      setPendingDeleteRunId("");
      if (result?.id === runId) {
        setResult(null);
        setSelectedPointIndices([]);
        setFocusedPointIndex(null);
        setDetailItem(null);
        setResultSurface("map");
        onRunChange("");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存済み探索を削除できませんでした。");
    } finally {
      setDeletingRunId("");
    }
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
  const proposedPointIndices = new Set(
    result?.proposal_selection?.selected.map((item) => item.point_index) ?? [],
  );
  const selectedNewPointIndices = selectedPointIndices.filter((index) => !stockedPointIndices.has(index));
  const remainingCandidateCapacity = candidateCapacity
    ? Math.max(0, candidateCapacity.limit - candidates.length)
    : 0;
  const candidateCapacityLabel = candidateCapacity
    ? `候補枠 ${candidates.length} / ${candidateCapacity.limit}`
    : candidateCapacityError || "候補枠を確認中";
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
  const movedConditionFields = result
    ? Object.entries(result.variables)
      .filter(([, spec]) => spec.mode !== "fixed")
      .map(([field]) => field)
    : [];
  const confirmedVaryingFields = result
    ? movedConditionFields.filter((field) => result.points.some((point) => typeof point.inputs[field] === "number"))
    : [];
  const axes = [xAxis, yAxis].filter(Boolean);
  const numeric = (axis: string) => {
    const complete = (result?.proposal_pool ?? [])
      .map((point) => Number(point.inputs[axis]))
      .filter(Number.isFinite);
    return complete.length > 0
      ? complete
      : result?.points
        .map((point) => Number(point.inputs[axis]))
        .filter(Number.isFinite) ?? [];
  };
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
  const metricColor = (value: number | null, values = colorValues) => {
    if (value == null || values.length === 0) return "hsl(215 18% 72%)";
    const domainValues = colorRange ? [colorRange.min, colorRange.max] : values;
    const displayValue = colorRange ? clampToRange(value, colorRange) : value;
    const normalized = (displayValue - Math.min(...domainValues)) / Math.max(1e-9, Math.max(...domainValues) - Math.min(...domainValues));
    const strength = colorMetric === "score" ? 1 - normalized : normalized;
    return `hsl(215 78% ${82 - strength * 42}%)`;
  };
  const opportunity = (point: ScreenPoint) => metricColor(
    colorMetric === "score"
      ? point.score
      : (point.predictions?.[colorMetric] ?? (
          colorMetric === result?.target ? point.prediction : undefined
        ))?.value ?? null,
  );
  const axisLabel = (axis: string | undefined) => options.find((option) => option.value === axis)?.label ?? axis ?? "";
  const focusedPoint = result?.points.find((point) => point.index === focusedPointIndex) ?? null;
  // The server states what the score means for this run; the UI must not upgrade it.
  const scoreLabel = result?.score_contract?.display_label ?? "探索スコア";
  const colorMetricLabel = colorMetric === "score"
    ? scoreLabel
    : outputs.find((output) => output.key === colorMetric)?.label ?? colorMetric;
  const interpolation = result
    ? buildScreeningInterpolation(result, xAxis, yAxis, colorMetric)
    : null;
  const hiddenVaryingFields = result ? Object.entries(result.variables).filter(([field, spec]) => spec.mode !== "fixed" && field !== xAxis && field !== yAxis).map(([field]) => field) : [];
  const togglePoint = (index: number) => {
    setFocusedPointIndex(index);
    if (result && modeFromRun(result) === "landscape") return;
    setSelectedPointIndices((current) => {
      if (current.includes(index)) return current.filter((item) => item !== index);
      const selectedNewCount = current.filter(
        (selectedIndex) => !stockedPointIndices.has(selectedIndex),
      ).length;
      if (
        stockedPointIndices.has(index)
        || !candidateCapacity
        || selectedNewCount >= remainingCandidateCapacity
      ) {
        return current;
      }
      return [...current, index];
    });
  };
  const actionLabel = screeningMode === "landscape"
    ? `${samples}点を評価`
    : screeningMode === "opportunity"
      ? "有望候補を探す"
      : "実験バッチを作成";
  const variableErrors = variables.map((row) => {
    const option = options.find((item) => item.value === row.field);
    return screeningVariableError(row, {
      categorical: option?.kind === "categorical",
      choices: option?.choices ?? [],
      allowedRange: option?.allowedRange ?? undefined,
    });
  });
  const activeVariableError = screeningMode === "batch"
    ? null
    : variableErrors.find((message) => message != null) ?? null;
  const primaryGoalReady = Boolean(
    fixedObjective?.terms.some(
      (term) => term.role === "primary_objective" && term.direction != null,
    )
    || screeningGoalFromDraft(targetGoal),
  );
  const activeObjectiveUnsupportedReason = screeningMode === "landscape"
    ? ""
    : unsupportedObjectiveReason;
  const proposalCountError = screeningMode === "opportunity"
    && (!Number.isInteger(proposalCount) || proposalCount < 1 || proposalCount > 10)
    ? "提案件数は1〜10件で入力してください"
    : "";
  const actionDisabled = running
    || !baseCandidateId
    || !baseCandidate
    || Boolean(activeVariableError)
    || Boolean(activeObjectiveUnsupportedReason)
    || Boolean(proposalCountError)
    || (screeningMode !== "landscape" && !primaryGoalReady)
    || (screeningMode === "batch" && !opportunitySourceRun);
  const actionTitle = activeVariableError
    || activeObjectiveUnsupportedReason
    || proposalCountError
    || (screeningMode !== "landscape" && !primaryGoalReady
      ? "主目標を入力してください"
      : screeningMode === "batch" && !opportunitySourceRun
        ? "先に有望候補を探してください"
        : `${actionLabel}を実行します`);
  const selectScreeningMode = async (mode: ScreeningMode) => {
    if (mode === "batch") {
      if (!opportunitySourceRun) return;
      if (result?.id !== opportunitySourceRun.id) await loadRun(opportunitySourceRun.id);
    }
    setScreeningMode(mode);
    setError("");
  };
  return (
    <div className="explore-page">
      <div className="page-intro">
        <div>
          <h2>範囲探索</h2>
        </div>
        <div className="screening-page-actions">
          <span className="screening-capacity" role="status">{candidateCapacityLabel}</span>
          <button
            className="primary-button"
            disabled={actionDisabled}
            title={actionTitle}
            aria-busy={running}
            onClick={() => {
              void run();
            }}
          >
            {running ? "計算中…" : actionLabel}
          </button>
        </div>
      </div>
      {compositionBalanceNotice && <p className="screening-balance-notice">組成制約: {compositionBalanceNotice}</p>}
      {activeObjectiveUnsupportedReason && <p className="error-banner">{activeObjectiveUnsupportedReason}</p>}
      <section className="screening-mode-picker" aria-labelledby="screening-mode-heading">
        <div>
          <h3 id="screening-mode-heading">何をしたいですか？</h3>
        </div>
        <div className="screening-mode-options">
          {screeningModes.map((mode) => {
            const unavailable = mode.id === "batch" && !opportunitySourceRun;
            return (
              <button
                type="button"
                key={mode.id}
                className={screeningMode === mode.id ? "active" : ""}
                aria-pressed={screeningMode === mode.id}
                disabled={unavailable}
                title={unavailable ? "先に「有望候補を探す」を実行すると選べます" : mode.description}
                onClick={() => { void selectScreeningMode(mode.id); }}
              >
                <b>{mode.label}</b>
                <span>{mode.description}</span>
                {unavailable && <small>有望候補の結果が必要</small>}
              </button>
            );
          })}
        </div>
      </section>
      {effectiveDraftDirty && result && <p className="screening-draft-notice">未実行の条件変更があります。図と点詳細は最後に実行した条件のままです。</p>}
      {savedRuns.length > 0 && (
        <section className="saved-runs">
          <h3>保存済み探索</h3>
          <div>
            {(showAllSavedRuns ? savedRuns : savedRuns.slice(0, 8)).map((run) => (
              <article className="saved-run-item" key={run.id}>
                <button
                  className={`saved-run-open${result?.id === run.id ? " active" : ""}`}
                  onClick={() => {
                    setPendingDeleteRunId("");
                    void loadRun(run.id);
                  }}
                >
                  <b>{outputs.find((output) => output.key === run.target)?.label ?? run.target}</b> → {goalSummary(run.target_goal, run.target_value)} /{" "}
                  {run.samples}点表示
                  {run.proposal_diagnostics?.proposed_count != null
                    ? ` · ${run.proposal_diagnostics.proposed_count}件提案`
                    : ""}{" "}
                  <small>
                    基準: {candidates.find((candidate) => candidate.id === run.base_candidate_id)?.label ?? run.base_candidate_id?.slice(0, 8) ?? "旧保存データ"} ·{" "}
                    {Object.entries(run.variables).filter(([, spec]) => spec.mode !== "fixed").map(([field, spec]) => `${axisLabel(field)}=${spec.mode === "range" ? `${number(spec.min ?? 0, 3)}–${number(spec.max ?? 0, 3)}` : (spec.values ?? []).join("/")}`).join(" / ") || "変更なし"} ·{" "}
                    {run.created_at ? new Date(run.created_at).toLocaleString("ja-JP") : "保存済み"}
                  </small>
                </button>
                <div className="saved-run-actions">
                  {pendingDeleteRunId === run.id
                    ? <>
                        <button className="danger-button" disabled={deletingRunId === run.id} onClick={() => { void deleteRun(run.id); }}>
                          {deletingRunId === run.id ? "削除中…" : "削除する"}
                        </button>
                        <button className="text-button" onClick={() => setPendingDeleteRunId("")}>やめる</button>
                      </>
                    : <button className="text-button" onClick={() => setPendingDeleteRunId(run.id)}>削除</button>}
                </div>
              </article>
            ))}
          </div>
          {savedRuns.length > 8 && (
            <button className="saved-runs-expand text-button" onClick={() => setShowAllSavedRuns((shown) => !shown)}>
              {showAllSavedRuns ? "直近8件に戻す" : `残り${savedRuns.length - 8}件を表示`}
            </button>
          )}
        </section>
      )}
      <div className="screening-settings">
        {screeningMode !== "landscape" && <div className="screening-primary-settings">
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
          {screeningMode === "batch" && (
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
                }}
              />
            </label>
          )}
          {screeningMode === "opportunity" && (
            <label>
              提案件数
              <input
                type="number"
                min="1"
                max="10"
                value={proposalCount}
                onChange={(event) => {
                  setProposalCount(Number(event.target.value));
                  setDraftDirty(true);
                }}
              />
            </label>
          )}
        </div>}
        {screeningMode === "batch" && opportunitySourceRun && (
          <section className="screening-batch-source" aria-label="バッチ元の有望候補Run">
            <div>
              <span>バッチ元</span>
              <b>{opportunitySourceRun.id.slice(0, 8)}</b>
            </div>
            <div>
              <span>目標</span>
              <b>{outputs.find((output) => output.key === opportunitySourceRun.target)?.label ?? opportunitySourceRun.target} {goalSummary(opportunitySourceRun.target_goal, opportunitySourceRun.target_value)}</b>
            </div>
            <div>
              <span>探索条件</span>
              <b>{Object.entries(opportunitySourceRun.variables).filter(([, spec]) => spec.mode !== "fixed").length}変数 · {opportunitySourceRun.samples}点 · seed {opportunitySourceRun.seed}</b>
            </div>
          </section>
        )}
        <details className="screening-advanced-settings">
          <summary>
            <span>詳細設定</span>
            <small>
              {screeningMode === "batch"
                ? `候補pool ${batchCandidatePoolSize}点 · ${batchSelectorId === "ranked_top_k_v1" ? "順位を優先" : "順位と多様性"}`
                : `${samples}点を表示${screeningMode === "opportunity" ? ` · ${proposalCount}件提案` : ""} · ${screeningMode === "landscape" ? "学習範囲外も表示" : supportPolicy === "supported_first" ? "範囲内を優先" : supportPolicy === "exclude_extrapolated" ? "外挿を除外" : "外挿を警告表示"} · 再現用seed固定`}
            </small>
          </summary>
          {screeningMode !== "batch" && (
          <div className="screening-target">
          <label>
            表示点数
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
            <small>制約内の全点を評価し、ここで指定した件数を図に表示します</small>
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
            外挿候補の扱い
            <select
              value={supportPolicy}
              onChange={(event) => { setSupportPolicy(event.target.value as typeof supportPolicy); setDraftDirty(true); }}
            >
              <option value="supported_first">近い学習実績を優先</option>
              <option value="exclude_extrapolated">外挿を除外</option>
              <option value="allow_with_warning">警告付きで含める</option>
            </select>
          </label>
          {screeningMode === "opportunity" && (
            <label>
              提案の選び方
              <select
                value={proposalSelectionPolicy}
                onChange={(event) => {
                  setProposalSelectionPolicy(
                    event.target.value as typeof proposalSelectionPolicy,
                  );
                  setDraftDirty(true);
                }}
              >
                <option value="ranked_top_k_v1">上位順</option>
                <option value="greedy_value_diversity_v1">
                  条件が重ならないよう選ぶ
                </option>
              </select>
              <small>
                {proposalSelectionPolicy === "ranked_top_k_v1"
                  ? "最良付近へ集中する場合があります"
                  : "有望度に加え、Taskの距離contractで条件間の違いを確保します"}
              </small>
            </label>
          )}
          {screeningMode === "opportunity"
            && proposalSelectionPolicy === "greedy_value_diversity_v1" && (
            <label>
              条件の違いの重み
              <input
                type="number"
                min="0"
                max="10"
                step="0.05"
                value={proposalDiversityWeight}
                onChange={(event) => {
                  setProposalDiversityWeight(Number(event.target.value));
                  setDraftDirty(true);
                }}
              />
            </label>
          )}
          </div>
          )}
        {screeningMode === "batch" && (
          <section className="screening-batch-settings" aria-label="バッチの詳細設定">
            <h4>バッチの詳細</h4>
            <div className="screening-batch-grid">
              <label>
                バッチ候補pool
                <input
                  type="number"
                  min={batchSize}
                  max={Math.min(samples, 128)}
                  value={batchCandidatePoolSize}
                  onChange={(event) => { setBatchCandidatePoolSize(Number(event.target.value)); }}
                />
                <small>獲得順位の上位から選抜に渡す件数</small>
              </label>
              <label>
                バッチ選抜
                <select
                  value={batchSelectorId}
                  onChange={(event) => { setBatchSelectorId(event.target.value as typeof batchSelectorId); }}
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
                    onChange={(event) => { setDiversityWeight(Number(event.target.value)); }}
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
                  onChange={(event) => { setNearDuplicateThreshold(Number(event.target.value)); }}
                />
                <small>Design Spaceの各範囲で正規化</small>
              </label>
              <label>
                Control条件
                <select
                  value={controlCandidateId}
                  onChange={(event) => { setControlCandidateId(event.target.value); }}
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
                    onChange={(event) => { setControlReplicates(Number(event.target.value)); }}
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
                  onChange={(event) => { setMaxBatchCost(event.target.value); }}
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
                      }}
                    />
                    {candidate.label}
                  </label>
                ))}
              </fieldset>
            </div>
          </section>
        )}
        </details>
        {screeningMode === "opportunity" && (fixedObjective
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
        ))}
        {screeningMode === "opportunity" && !primaryGoalReady && (
          <div className="screening-missing-goal" role="status">
            <span>主目標を入力するか、Projectの目標値を設定してください。</span>
            <button type="button" className="text-button" onClick={onConfigureGoals}>Projectの目標値を設定</button>
          </div>
        )}
        {screeningMode !== "batch" && (
        <section className="screening-variable-editor" aria-label="探索で動かす項目">
          <div className="screening-variable-heading">
            <h3>探索で動かす項目</h3>
            <small>ここで指定した項目だけ、選択中の候補から動かします。</small>
          </div>
          <div className="screening-variable-table-scroll">
            <table className="quality-table variable-table">
          <thead>
            <tr>
              <th>変数</th>
              <th>指定</th>
              <th>値 / 最小</th>
              <th>最大</th>
              <th>学習範囲</th>
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
              const errorId = `screening-variable-error-${index}`;
              return <Fragment key={`${row.field}-${index}`}>
              <tr className={variableErrors[index] ? "invalid" : ""}>
                <td>
                  <select
                    ref={(element) => { variableSelectRefs.current[index] = element; }}
                    aria-label={`${index + 1}行目の探索変数`}
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
                    aria-label={`${option?.label ?? row.field}の指定方法`}
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
                    aria-label={`${option?.label ?? row.field}の${
                      row.mode === "fixed"
                        ? "値"
                        : row.mode === "range"
                          ? "最小"
                          : "列挙値"
                    }`}
                    aria-describedby={variableErrors[index] ? errorId : undefined}
                    aria-invalid={Boolean(variableErrors[index])}
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
                      aria-label={`${option?.label ?? row.field}の最大`}
                      aria-describedby={variableErrors[index] ? errorId : undefined}
                      aria-invalid={Boolean(variableErrors[index])}
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
                  {option?.trainingRange
                    ? <small className={outsideTraining ? "screening-variable-range outside" : "screening-variable-range"}>
                        {number(option.trainingRange.min, 3)}–{number(option.trainingRange.max, 3)}
                        {outsideTraining && <b> · 学習範囲外を含む</b>}
                      </small>
                    : "—"}
                </td>
                <td>
                  <button
                    className="icon-delete"
                    type="button"
                    aria-label={`${option?.label ?? row.field}を削除`}
                    disabled={variables.length === 1}
                    onClick={() => {
                      pendingVariableFocusIndex.current = Math.min(index, variables.length - 2);
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
              {variableErrors[index] && <tr className="screening-variable-error-row">
                <td colSpan={6}><span id={errorId} role="alert">{variableErrors[index]}</span></td>
              </tr>}
              </Fragment>;
            })}
          </tbody>
            </table>
          </div>
          <button
          className="outline-button screening-add-variable"
          type="button"
          disabled={!options.some((option) => !variables.some((row) => row.field === option.value))}
          onClick={() => {
            const option = options.find((item) => !variables.some((row) => row.field === item.value));
            if (!option) return;
            pendingVariableFocusIndex.current = variables.length;
            setDraftDirty(true);
            setVariables((rows) => [...rows, { field: option.value, mode: option.kind === "categorical" ? "list" : "range", first: option.kind === "categorical" ? option.choices.join(",") : String(option.defaultRange?.min ?? ""), second: option.kind === "categorical" ? "" : String(option.defaultRange?.max ?? "") }]);
          }}
        >
          変数を追加
          </button>
        </section>
        )}
        <div className="screening-run-footer">
          <span>
            <b>{screeningModes.find((mode) => mode.id === screeningMode)?.label}</b>
            {screeningMode === "batch" && opportunitySourceRun
              ? ` · 元の有望候補Run ${opportunitySourceRun.id.slice(0, 8)}`
              : ` · ${variables.filter((row) => row.mode !== "fixed").length}変数`}
          </span>
          <button
            type="button"
            className="primary-button"
            disabled={actionDisabled}
            title={actionTitle}
            aria-busy={running}
            onClick={() => { void run(); }}
          >
            {running ? "計算中…" : actionLabel}
          </button>
        </div>
      </div>
      {error && <p className="warning">{error}</p>}
      {result && (
        <>
          <ScreeningProposalSummary
            result={result}
            targetLabel={outputs.find((output) => output.key === result.target)?.label}
            showAnotherSample={screeningMode !== "batch"}
            batchSaveCount={newBatchPointIndices.length}
            onSaveBatch={() => { void persistBatch(); }}
            onAnotherSample={() => {
              if (screeningMode === "batch") return;
              const nextSeed = nextScreeningSeed(seed);
              setSeed(nextSeed);
              void run(nextSeed);
            }}
          />
          <ScreeningResultSurfaceTabs
            value={resultSurface}
            onChange={(surface) => {
              setResultSurface(surface);
              setHoveredScreenPoint(null);
            }}
            proposalCount={
              result.proposal_diagnostics?.proposed_count
              ?? result.proposal_selection?.selected.length
              ?? 0
            }
            evaluatedCount={
              result.proposal_diagnostics?.evaluated_count
              ?? result.proposal_pool?.length
              ?? result.points.length
            }
            proposalsAvailable={
              modeFromRun(result) !== "landscape"
              && result.representative_points.length > 0
            }
          />
          {resultSurface === "map" && (
          <section
            id="screening-result-panel-map"
            className="screening-map-surface"
            role="region"
            aria-label="探索領域の地図"
          >
          <div className="screening-surface-header">
            <div className="screening-surface-title">
              <b>入力空間の地図</b>
              <small>{axes.map(axisLabel).join(" × ")} · 色: {colorMetricLabel}</small>
            </div>
            <div className="screening-display-controls">
              <label>横軸<select value={xAxis} onChange={(event) => setXAxis(event.target.value)}>{confirmedVaryingFields.map((field) => <option key={field} value={field}>{axisLabel(field)}</option>)}</select></label>
              <label>縦軸<select value={yAxis} onChange={(event) => setYAxis(event.target.value)}><option value="">点番号</option>{confirmedVaryingFields.filter((field) => field !== xAxis).map((field) => <option key={field} value={field}>{axisLabel(field)}</option>)}</select></label>
              <label>色<select value={colorMetric} onChange={(event) => setColorMetric(event.target.value)}><option value="score">{scoreLabel}</option>{outputs.map((output) => <option key={output.key} value={output.key}>{output.label}</option>)}</select></label>
            </div>
            <button
              type="button"
              className="outline-button screening-map-size-button"
              aria-expanded={chartExpanded}
              onClick={() => setChartExpanded((expanded) => !expanded)}
            >
              {chartExpanded ? "図を元の大きさに戻す" : "図を拡大"}
            </button>
            <div className="screen-legend">
              <span className="opportunity-scale" />
              {colorMetricLabel}{" "}
              <span className="evaluated-key" />
              評価点{" "}
              {result.proposal_selection && <><span className="proposal-key" />提案候補{" "}</>}
              <span className="selection-key" />
              選択中
            </div>
          </div>
          {hiddenVaryingFields.length > 0
            ? <p className="screening-slice-context warning">
                <b>{axes.map(axisLabel).join(" × ")}を表示</b>
                <span>
                  {hiddenVaryingFields.map(axisLabel).join(" / ")}も同時に変動しており、固定断面ではありません。
                  残りを固定して再実行すると面で表示できます。
                </span>
              </p>
            : <p className="screening-slice-context">
                <b>{axes.map(axisLabel).join(" × ")}の固定断面</b>
                <span>その他の入力は基準候補の条件に固定</span>
              </p>}
          {interpolation?.available
            ? <p className="screening-interpolation-status">
                <b>表示補間</b>
                {interpolation.method} v{interpolation.version} · grid {interpolation.columns}×{interpolation.rows}
                <span>modelの追加予測ではありません。近くに評価点がないセルは斜線のままです。</span>
              </p>
            : <p className="screening-interpolation-status unavailable" role="note">
                <b>点表示</b>
                {interpolation?.message ?? "補間の可否を確認できません。"}
              </p>}
          {modeFromRun(result) !== "landscape" && <div className="screening-action-bar" role="status">
            <dl className="screening-selection-summary">
              <div><dt>選択</dt><dd>{selectedPointIndices.length}件</dd><small>図・表で選んだ点</small></div>
              <div><dt>新規</dt><dd>{selectedNewPointIndices.length}件</dd><small>まだ候補にない選択点</small></div>
              <div><dt>今回追加可能</dt><dd>{addableSelectedCount}件</dd><small>候補枠の空き {remainingCandidateCapacity}件</small></div>
            </dl>
            {selectedPointIndices.some((index) => stockedPointIndices.has(index)) && <small>stock済みの点は再追加しません。</small>}
            {selectedNewPointIndices.length > remainingCandidateCapacity && <small className="screening-capacity-warning">新規選択が候補枠を超えています。選択を{remainingCandidateCapacity}件以下に減らしてください。</small>}
            <CandidateAddButton disabled={!addableSelectedCount} onClick={() => void persistSelected()}>{addableSelectedCount}件を候補へ追加</CandidateAddButton>
            <button className="outline-button" disabled={!candidates.length} onClick={onCompare}>候補比較へ</button>
          </div>}
          <div className="screening-map-layout">
          <div className="screening-map-chart">
          <svg
            className={`screen-map${chartExpanded ? " expanded" : ""}`}
            viewBox="0 0 600 300"
            role="group"
            aria-label={`${axes.map(axisLabel).join(" × ")} の探索結果。色は「${colorMetricLabel}」、実線の輪は提案候補、破線の輪は選択中の点を表します。`}
          >
            <defs>
              <pattern id="screening-uninterpolated" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                <line x1="0" y1="0" x2="0" y2="8" stroke="#cad5e2" strokeWidth="2" />
              </pattern>
            </defs>
            <rect x="35" y="35" width="530" height="235" fill="url(#screening-uninterpolated)" />
            {interpolation?.available && interpolation.cells.map((cell) => (
              <rect
                key={`${cell.column}-${cell.row}`}
                className="screen-map-interpolation-cell"
                x={35 + (cell.column * 530) / interpolation.columns}
                y={35 + ((interpolation.rows - cell.row - 1) * 235) / interpolation.rows}
                width={530 / interpolation.columns + 0.5}
                height={235 / interpolation.rows + 0.5}
                fill={metricColor(
                  cell.value,
                  [interpolation.valueMin, interpolation.valueMax],
                )}
                aria-hidden="true"
              />
            ))}
            {axes.length > 0 && xTicks.map((tick) => <g key={`x-${tick}`} className="screen-map-grid"><line x1={screenX(tick)} x2={screenX(tick)} y1="35" y2="270" /><text x={screenX(tick)} y="284" textAnchor="middle">{number(tick, xDigits)}</text></g>)}
            {axes.length > 1 && yTicks.map((tick) => <g key={`y-${tick}`} className="screen-map-grid"><line x1="35" x2="565" y1={screenY(tick)} y2={screenY(tick)} /><text x="31" y={screenY(tick) + 3} textAnchor="end">{number(tick, yDigits)}</text></g>)}
            {interpolation?.available && (result.proposal_pool ?? [])
              .filter((point) => point.selected_rank == null)
              .map((point) => {
                const x = Number(point.inputs[axes[0]]);
                const y = Number(point.inputs[axes[1]]);
                if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
                return (
                  <circle
                    key={`evaluated-${point.pool_index}`}
                    className="screen-map-evaluated-marker"
                    cx={screenX(x)}
                    cy={screenY(y)}
                    r="2"
                    aria-hidden="true"
                  />
                );
              })}
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
                `${outputs.find((output) => output.key === result.target)?.label ?? result.target} ${outputNumber(result.target, point.prediction.value)} ${point.prediction.unit}`,
                `90%区間 ${outputNumber(result.target, point.prediction.lower)}–${outputNumber(result.target, point.prediction.upper)}`,
                ...(targetAssessment.warning ? [`⚠ ${targetAssessment.warning}`] : []),
                ...(proposedPointIndices.has(point.index) ? ["提案候補"] : []),
                point.support.message,
              ];
              const selected = selectedPointIndices.includes(point.index);
              return (
                <g key={point.index} className="screen-map-point">
                  {proposedPointIndices.has(point.index) && (
                    <circle className="screen-map-proposal-marker" cx={cx} cy={cy} r="11" aria-hidden="true" />
                  )}
                  {selected && <circle className="screen-map-selection-ring" cx={cx} cy={cy} r="12" aria-hidden="true" />}
                  <circle
                  className={selectedPointIndices.includes(point.index) ? "selected" : ""}
                  cx={cx}
                  cy={cy}
                  r="7"
                  fill={interpolation?.available ? "#fff" : opportunity(point)}
                  stroke="#345b85"
                  strokeWidth="1.5"
                  opacity=".9"
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
          </div>
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
                  <strong>{outputNumber(key, prediction.value)} {prediction.unit}</strong>
                  <small>{outputNumber(key, prediction.lower)}–{outputNumber(key, prediction.upper)}{prediction.goal_probability != null ? ` / 達成確率 ${Math.round(prediction.goal_probability * 100)}%` : ""}</small>
                  {assessment.implausible && <em className="output-warning-badge">⚠ 物理範囲外</em>}
                  {evaluation && <em>{goalEvaluationLabel(evaluation, key === result.target)}</em>}
                </div>;
              })}
            </div>
            <p>
              <b>動かした条件:</b>{" "}
              {movedConditionFields.map((key) => {
                const value = focusedPoint.inputs[key];
                return `${axisLabel(key)} ${typeof value === "number" ? number(value, 3) : value}`;
              }).join(" / ") || "基準候補と同じ"}
            </p>
            <p><b>適用範囲:</b> {supportStatusLabel(focusedPoint.support.status)} / percentile {number(focusedPoint.support.percentile, 1)} / 参照{focusedPoint.support.reference_count}件</p>
            {focusedPoint.warnings?.map((warning) => <p className="warning" key={warning}>{warning}</p>)}
            {(focusedPoint.similar ?? []).length > 0 && (
              <div className="screening-similar-evidence">
                <b>近い実績</b>
                {(focusedPoint.similar ?? []).slice(0, 3).map((item) => (
                  <button
                    className="text-button"
                    key={`${item.process_key ?? item.parent_key}-${item.observation_id}`}
                    onClick={() => setDetailItem(item)}
                  >
                    実績を見る
                    <small>{item.observation_id || item.process_key || item.parent_key} · 距離 {number(item.distance, 2)}</small>
                  </button>
                ))}
              </div>
            )}
          </section>}
          </div>
          </section>
          )}
          {resultSurface === "proposals" && modeFromRun(result) !== "landscape" && (
          <section
            id="screening-result-panel-proposals"
            className="screening-proposal-surface"
            role="region"
            aria-label="提案候補"
          >
          <div className="screening-action-bar" role="status">
            <dl className="screening-selection-summary">
              <div><dt>選択</dt><dd>{selectedPointIndices.length}件</dd><small>地図・表で選んだ点</small></div>
              <div><dt>新規</dt><dd>{selectedNewPointIndices.length}件</dd><small>まだ候補にない選択点</small></div>
              <div><dt>今回追加可能</dt><dd>{addableSelectedCount}件</dd><small>候補枠の空き {remainingCandidateCapacity}件</small></div>
            </dl>
            {selectedPointIndices.some((index) => stockedPointIndices.has(index)) && <small>stock済みの点は再追加しません。</small>}
            {selectedNewPointIndices.length > remainingCandidateCapacity && <small className="screening-capacity-warning">新規選択が候補枠を超えています。選択を{remainingCandidateCapacity}件以下に減らしてください。</small>}
            <CandidateAddButton disabled={!addableSelectedCount} onClick={() => void persistSelected()}>{addableSelectedCount}件を候補へ追加</CandidateAddButton>
            <button className="outline-button" disabled={!candidates.length} onClick={onCompare}>候補比較へ</button>
          </div>
          <ScreeningRepresentativeTable
            result={result}
            outputs={outputs}
            taskDefinition={taskDefinition}
            displayDecimalOverrides={project?.display_decimals}
            options={options}
            baseCandidateLabel={candidates.find((candidate) => candidate.id === result.base_candidate_id)?.label ?? "基準候補"}
            selectedPointIndices={selectedPointIndices}
            stockedPointIndices={stockedPointIndices}
            selectionLimitReached={
              !candidateCapacity
              || selectedNewPointIndices.length >= remainingCandidateCapacity
            }
            selectionEnabled={modeFromRun(result) !== "landscape"}
            onToggle={togglePoint}
          />
          </section>
          )}
          {resultSurface === "evaluated" && (
            <ScreeningEvaluatedTable
              result={result}
              axisLabel={axisLabel}
              scoreLabel={scoreLabel}
              targetLabel={
                outputs.find((output) => output.key === result.target)?.label
                ?? result.target
              }
            />
          )}
          <ScreeningRunEvidence result={result} />
        </>
      )}
      <HistoricalEvidenceDrawer
        open={detailItem != null}
        projectId={projectId}
        reference={detailItem ? {
          processKey: detailItem.process_key ?? detailItem.parent_key,
          compositionKey: detailItem.melt_key,
          relationContextIds: detailItem.relation_context_ids,
          observationIds: detailItem.observation_ids ?? (detailItem.observation_id ? [detailItem.observation_id] : []),
          repeatSummary: detailItem.repeat_summary,
          observedOutputs: detailItem.outputs,
          measurementState: "ready",
          distance: detailItem.distance,
          source: detailItem.source,
        } : null}
        outputs={outputs}
        taskDefinition={taskDefinition}
        displayDecimalOverrides={project?.display_decimals}
        onClose={() => setDetailItem(null)}
      />
    </div>
  );
}
