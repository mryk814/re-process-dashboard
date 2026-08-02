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
