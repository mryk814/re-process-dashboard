import type {
  ApiDataLibraryDataset,
  ApiModelPackageRef,
  ApiProjectCreationOptions,
} from "./api/workbench-api";

const asRecord = (value: unknown): Record<string, unknown> | null => (
  value != null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
);

export function trainingDataSha(modelPackage: ApiModelPackageRef): string | null {
  const provenance = asRecord(asRecord(modelPackage.manifest_json)?.provenance);
  const trainingDataId = provenance?.training_data_id;
  if (typeof trainingDataId !== "string" || !trainingDataId.startsWith("sha256:")) return null;
  return trainingDataId.slice("sha256:".length);
}

export function trainingProfileDigest(modelPackage: ApiModelPackageRef): string | null {
  const provenance = asRecord(asRecord(modelPackage.manifest_json)?.provenance);
  return typeof provenance?.dataset_profile_id === "string"
    ? provenance.dataset_profile_id
    : null;
}

export function trainingDataset(
  modelPackage: ApiModelPackageRef | undefined,
  datasets: ApiDataLibraryDataset[],
): ApiDataLibraryDataset | undefined {
  if (!modelPackage) return undefined;
  const sha = trainingDataSha(modelPackage);
  const profileDigest = trainingProfileDigest(modelPackage);
  return sha ? datasets.find((item) => (
    item.data_asset.sha256 === sha
    && (profileDigest == null || item.profile_revision.profile_digest === profileDigest)
  )) : undefined;
}

export function datasetDisplayName(dataset: ApiDataLibraryDataset | undefined): string {
  return dataset?.data_asset.original_filename.replace(/\.xlsx$/i, "") ?? "Dataset未解決";
}

export function modelPackageDisplayName(modelPackage: ApiModelPackageRef | undefined): string {
  if (!modelPackage) return "—";
  const manifest = asRecord(modelPackage.manifest_json);
  const predictors = Array.isArray(manifest?.predictors) ? manifest.predictors : [];
  const records = predictors.map(asRecord).filter((item): item is Record<string, unknown> => item != null);
  if (records.some((item) => item.runtime_type === "builtin.heteroscedastic_exact_gp.v1")) {
    return "個々値・異分散GP（試験）";
  }
  if (records.some((item) => item.architecture_id === "hierarchical_parent_random_intercept_v1")) {
    return "個々値・反復階層Bayes（試験）";
  }
  return modelPackage.package_id;
}

export function compatiblePackagesForTask(
  taskId: string,
  options: Pick<ApiProjectCreationOptions, "model_packages" | "task_contract_digests">,
): ApiModelPackageRef[] {
  const currentDigest = options.task_contract_digests[taskId];
  if (!currentDigest) return [];
  return options.model_packages.filter((item) => (
    item.task_id === taskId && item.task_contract_digest === currentDigest
  ));
}

export function compatibleTaskIdsForDataset(
  dataset: ApiDataLibraryDataset | undefined,
  options: Pick<ApiProjectCreationOptions, "model_packages" | "task_contract_digests">,
): string[] {
  return (dataset?.supported_task_ids ?? []).filter((taskId) => (
    compatiblePackagesForTask(taskId, options).length > 0
  ));
}

export function initialProjectBindingForDataset(
  dataset: ApiDataLibraryDataset | undefined,
  options: Pick<ApiProjectCreationOptions, "model_packages" | "task_contract_digests">,
): { taskId: string; modelPackageRefId: string } {
  const taskIds = compatibleTaskIdsForDataset(dataset, options);
  if (taskIds.length !== 1) return { taskId: "", modelPackageRefId: "" };
  const packages = compatiblePackagesForTask(taskIds[0], options);
  return {
    taskId: taskIds[0],
    modelPackageRefId: packages.length === 1 ? packages[0].id : "",
  };
}
