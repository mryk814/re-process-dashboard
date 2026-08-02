import type {
  ApiPredictionGraphCatalog,
  ApiPredictionGraphDefinition,
  ApiPredictionGraphDraftContent,
  ApiPredictionGraphDraftDocument,
} from "../../shared/api/workbench-api";

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

export function unavailablePredictionGraphReferences(
  definition: ApiPredictionGraphDefinition,
  catalog: ApiPredictionGraphCatalog,
) {
  return definition.stages.flatMap((stage) => {
    const catalogItem = catalog.stages.find((item) => (
      item.stage_kind === stage.stage_kind && item.contract_id === stage.contract_id
    ));
    if (catalogItem?.status === "available" && catalogItem.surface) return [];
    return [{
      stage,
      reason: catalogItem?.reason
        || (catalogItem ? "Node契約を現在利用できません" : "現在のcatalogに参照がありません"),
      inboundBindingCount: definition.bindings.filter(
        (binding) => binding.target_stage_id === stage.stage_id,
      ).length,
      outboundBindingCount: definition.bindings.filter(
        (binding) => binding.source.source_kind === "stage_output"
          && binding.source.stage_id === stage.stage_id,
      ).length,
      decisionOutputCount: definition.decision_outputs.filter(
        (output) => output.source_stage_id === stage.stage_id,
      ).length,
    }];
  });
}
