import type {
  ApiModelExplorationRun,
  ApiModelPlaygroundPreview,
} from "../../shared/api/workbench-api";
import type {
  ModelPlaygroundPreviewView,
  ModelPlaygroundRunView,
  PlaygroundRecipeView,
} from "./ModelPlaygroundPage";
import type {
  PlaygroundAttemptView,
  PlaygroundTargetResult,
} from "./modelPlaygroundPresentation";

type ApiRecipe = ApiModelPlaygroundPreview["recipes"][number];
type ApiTargetResult = NonNullable<
  ApiModelExplorationRun["attempts"][number]["result"]
>["targets"][number];

function validationLabel(
  targets: ApiModelPlaygroundPreview["context"]["targets"],
): string {
  return [...new Set(targets.map((target) => {
    const plan = target.validation_plan;
    return `${plan.strategy} · ${plan.folds} folds`;
  }))].join(" / ");
}

function inferenceLabel(
  identity: ApiRecipe["inference_identity"] | ApiTargetResult["inference_identity"],
  unavailableReason?: string | null,
): string {
  if (!identity) return unavailableReason || "posterior inferenceなし";
  const effective = [
    identity.algorithm_id,
    identity.role,
    identity.draws ? `${identity.draws} draws` : null,
  ].filter(Boolean);
  return effective.join(" · ");
}

export function intervalSemantics(
  metrics: Readonly<Record<string, number | string | null>>,
): string {
  const method = metrics.interval_coverage_method;
  if (method === "posterior-predictive-interval") {
    return "新しい1観測のposterior predictive interval";
  }
  if (method === "cross-fitted-oof-residual-quantiles") {
    return "cross-fitted OOF残差によるpredictive interval";
  }
  if (method === "nested-grouped-oof-residual-quantiles") {
    return "nested grouped OOF残差分位点によるpredictive interval";
  }
  if (method === "grouped-fold-predictive-interval") {
    return "grouped outer-foldのposterior predictive interval";
  }
  if (method === "loo-predictive-interval") {
    return "leave-one-out predictive interval";
  }
  if (method === "temporal-holdout-predictive-interval") {
    return "temporal holdoutのpredictive interval";
  }
  if (method === "outer-fold-conditional-quantiles") {
    return "outer-fold conditional quantile interval";
  }
  if (method === "split-conformal-interval") {
    return "split conformal predictive interval";
  }
  if (method === "cross-fitted-oof-normal-scale") {
    return "cross-fitted OOF normal scaleによるpredictive interval";
  }
  if (method === "nested-grouped-oof-normal-scale") {
    return "nested grouped OOF normal scaleによるpredictive interval";
  }
  if (method === "temporal-holdout-residual-quantiles") {
    return "temporal holdout残差分位点によるpredictive interval";
  }
  if (method === "temporal-holdout-normal-scale") {
    return "temporal holdout normal scaleによるpredictive interval";
  }
  if (typeof method === "string" && method) return method;
  return "interval evidenceなし";
}

function recipeView(recipe: ApiRecipe): PlaygroundRecipeView {
  const targetReasons = recipe.target_readiness.flatMap((target) =>
    target.reasons.map((reason) => `${target.target_key}: ${reason}`),
  );
  return {
    recipeId: recipe.recipe_id,
    label: recipe.label,
    lifecycle: recipe.lifecycle,
    availability: recipe.availability,
    reasons: [...new Set([...recipe.reasons, ...targetReasons])],
    comparisonRole: recipe.comparison_role,
    requiredDependency: recipe.required_dependency,
    trainingCost: recipe.training_cost,
    capabilities: recipe.predictive_capabilities,
    taskStructure: recipe.task_structure,
    hypothesisLabel: recipe.hypothesis
      ? `${recipe.hypothesis.card_id} · ${recipe.hypothesis.card_version}`
      : undefined,
    inferenceLabel: inferenceLabel(
      recipe.inference_identity,
      recipe.inference_unavailable_reason,
    ),
    executable: recipe.availability === "ready"
      || recipe.availability === "ready_expensive",
  };
}

function targetResult(target: ApiTargetResult): PlaygroundTargetResult {
  return {
    targetKey: target.target_key,
    metrics: target.metrics,
    inferenceLabel: inferenceLabel(
      target.inference_identity,
      target.inference_unavailable_reason,
    ),
    intervalSemantics: intervalSemantics(target.metrics),
  };
}

export function presentModelPlaygroundPreview(
  preview: ApiModelPlaygroundPreview,
): ModelPlaygroundPreviewView {
  return {
    taskId: preview.context.task_id,
    taskLabel: preview.context.task_id,
    trainingSnapshotId: preview.context.training_snapshot_id,
    targets: preview.context.targets.map((target) => target.target_key),
    validationLabel: validationLabel(preview.context.targets),
    recipes: preview.recipes.map(recipeView),
  };
}

export function presentModelExplorationRun(
  run: ApiModelExplorationRun,
): ModelPlaygroundRunView {
  const recipeById = new Map(
    run.definition.selected_recipes.map((recipe) => [
      recipe.recipe_id,
      recipe,
    ]),
  );
  const attempts: PlaygroundAttemptView[] = run.attempts.map((attempt) => {
    const recipe = recipeById.get(attempt.recipe_id);
    return {
      attemptId: attempt.attempt_id,
      recipeId: attempt.recipe_id,
      recipeLabel: recipe?.label ?? attempt.recipe_id,
      sequence: attempt.sequence,
      status: attempt.status,
      buildSeconds: attempt.result?.build_seconds,
      peakMemoryBytes: attempt.result?.peak_memory_bytes,
      artifactSizeBytes: attempt.result?.artifact_size_bytes,
      predictionLatencyMs: attempt.result?.prediction_latency_ms,
      packagePath: attempt.result?.package_path,
      capabilities: attempt.result?.capabilities
        ?? recipe?.predictive_capabilities
        ?? [],
      targets: attempt.result?.targets.map(targetResult) ?? [],
      failure: attempt.failure
        ? {
          message: attempt.failure.message,
          recoveryHint: attempt.failure.recovery_hint,
        }
        : undefined,
      registration: attempt.registration
        ? {
          referenceId: attempt.registration.reference_id,
          activePackageChanged: attempt.registration.active_package_changed,
        }
        : undefined,
    };
  });
  return {
    runId: run.run_id,
    revision: run.execution_revision,
    taskId: run.definition.context.task_id,
    taskLabel: run.definition.context.task_id,
    trainingSnapshotId: run.definition.context.training_snapshot_id,
    contextDigest: run.definition.context_digest,
    validationLabel: validationLabel(run.definition.context.targets),
    targets: run.definition.context.targets.map((target) => target.target_key),
    computeBudget: run.definition.compute_budget,
    recipes: run.definition.selected_recipes.map(recipeView),
    attempts,
    warnings: run.definition.warnings,
    adoptionMemo: run.adoption_memo
      ? {
        decision: run.adoption_memo.decision,
        recipeId: run.adoption_memo.adopted_recipe_id,
        rationale: run.adoption_memo.rationale,
      }
      : undefined,
  };
}
