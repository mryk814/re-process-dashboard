import type { PreparedProjectBinding } from "../../shared/preparedProjectBinding";

type PreparedDatasetIdentity = {
  revisionId: string;
  sourceSha256: string;
};

type PreparedPackageIdentity = {
  refId: string;
  taskId: string;
};

export function preparedBindingBlockers({
  binding,
  dataset,
  taskExists,
  modelPackage,
  taskCompatible,
  packageCompatible,
  estimatorCompatible,
}: {
  binding: PreparedProjectBinding;
  dataset?: PreparedDatasetIdentity;
  taskExists: boolean;
  modelPackage?: PreparedPackageIdentity;
  taskCompatible: boolean;
  packageCompatible: boolean;
  estimatorCompatible: boolean;
}): string[] {
  const blockers: string[] = [];
  if (!dataset) blockers.push(`Dataset view ${binding.datasetViewId} がありません。`);
  if (dataset && dataset.revisionId !== binding.datasetRevisionId) {
    blockers.push(`Dataset revision ${binding.datasetRevisionId} と現在のviewが一致しません。`);
  }
  if (dataset && dataset.sourceSha256 !== binding.sourceSha256) {
    blockers.push(`Source content ${binding.sourceSha256} と現在のDatasetが一致しません。`);
  }
  if (!taskExists) blockers.push(`Prediction Task ${binding.taskId} がありません。`);
  if (dataset && !taskCompatible) {
    blockers.push(`DatasetはPrediction Task ${binding.taskId} をsupportしていません。`);
  }
  if (!modelPackage) blockers.push(`Model Package ${binding.modelPackageRefId} がありません。`);
  if (modelPackage && modelPackage.taskId !== binding.taskId) {
    blockers.push(`Model PackageはPrediction Task ${binding.taskId} に対応していません。`);
  }
  if (modelPackage && !packageCompatible) {
    blockers.push(`Model Package ${binding.modelPackageRefId} はこのDataset bindingで利用できません。`);
  }
  if (modelPackage && !estimatorCompatible) {
    blockers.push(`Model PackageのEstimatorは ${binding.estimatorId} と一致しません。`);
  }
  return blockers;
}
