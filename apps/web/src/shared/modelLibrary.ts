import type { ApiPredictionGraphDefinition } from "./api/workbench-api";

export type ModelLibraryTab = "tasks" | "packages" | "transforms" | "graphs";

export type ModelLibraryProjectIntent =
  | Readonly<{
      kind: "single_task";
      datasetViewRevisionId: string;
      datasetRevisionId: string;
      taskId: string;
      packageReferenceId: string;
      packageManifestDigest: string;
    }>
  | Readonly<{
      kind: "graph";
      graphId: string;
      definitionId: string;
      revisionId: string;
      revisionDigest: string;
      datasetViewRevisionId?: string;
    }>;

export type ModelLibraryDataIntent = Readonly<{
  datasetRevisionId?: string;
  packageReferenceId?: string;
}>;

export function packagePredictiveMeaning(
  predictors: ReadonlyArray<{
    runtime_type: string;
    predictive_family: string;
  }>,
): string | null {
  if (predictors.some((item) => item.runtime_type === "builtin.quantile_linear.v1")) {
    return "中央値とq05／q95を直接学習 · q05–q95は正規分布の90%区間ではありません · 分位点交差は補正せず利用不能";
  }
  if (predictors.some((item) => (
    item.runtime_type === "numpyro.dense_posterior.v1"
    && item.predictive_family === "student_t"
  ))) {
    return "Student-t heavy-tail likelihood · 妥当な大残差の影響を抑えるmodelであり、入力ミスや単位不整合を許容しません · dfは2.1–30に制約したposterior · q05–q95は新観測の事後予測区間（潜在平均の信用区間ではありません）";
  }
  return null;
}

/**
 * A package handoff is focused at most once while its URL identity is active.
 * Changing identity clears the completed marker so browser back can focus the
 * earlier package again after an unresolved or incompatible handoff.
 */
export function clearFocusedPackageIntentOnChange(
  focusedIdentity: string | undefined,
  currentIdentity: string | undefined,
): string | undefined {
  return focusedIdentity === currentIdentity ? focusedIdentity : undefined;
}

/**
 * A Studio draft may be mutable, but the evidence boundary of the selected
 * published Graph remains part of the starting scientific context.  It must
 * stay visible and travel with the draft until the author explicitly changes
 * the Graph.
 */
export function draftDefinitionFromCatalog(
  definition: ApiPredictionGraphDefinition,
): ApiPredictionGraphDefinition {
  return {
    ...definition,
    decision_outputs: definition.decision_outputs.map((output) => ({ ...output })),
  };
}
