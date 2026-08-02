import type {
  ApiPredictionGraphDefinition,
  ApiPredictionGraphDraftContent,
  ApiPredictionGraphDraftDocument,
} from "../../shared/api/workbench-api";

export const predictionGraphDraftStorageKey = "decision-workbench.prediction-graph-draft-id";

export function predictionGraphDraftContent(
  definition: ApiPredictionGraphDefinition,
  projectName: string,
): ApiPredictionGraphDraftContent {
  return {
    definition,
    project_name: projectName,
    schema_version: "prediction-graph-draft-content/v1",
  };
}

export function samePredictionGraphDraft(
  left: ApiPredictionGraphDraftContent | undefined,
  right: ApiPredictionGraphDraftContent,
): boolean {
  return left !== undefined && JSON.stringify(left) === JSON.stringify(right);
}

export function predictionGraphDraftSummary(document: ApiPredictionGraphDraftDocument) {
  return {
    version: document.version,
    graphLabel: document.content.definition.label || "表示名なし",
    projectName: document.content.project_name || "Project名なし",
    updatedAt: document.updated_at,
  };
}
