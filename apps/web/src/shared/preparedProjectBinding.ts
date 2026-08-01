export type PreparedProjectBinding = {
  datasetViewId: string;
  datasetRevisionId: string;
  taskId: string;
  taskLabel: string;
  modelPackageRefId: string;
  sourceSha256: string;
  sourceFilename: string;
  estimatorId: string;
  estimatorLabel: string;
  preparationResult: "new" | "reused";
  workspaceKind: string;
  workspaceDatabasePath: string;
  reloaded: true;
};

export function preparedEstimatorCapabilities(estimatorId: string): string[] {
  return estimatorId === "ridge.v1" || estimatorId === "lightgbm-regression.v1"
    ? ["mean_point", "quantiles"]
    : [];
}
