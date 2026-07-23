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
  const runtimeTypes = new Set(records.map((item) => item.runtime_type).filter((item): item is string => typeof item === "string"));
  const architectureIds = new Set(records.map((item) => item.architecture_id).filter((item): item is string => typeof item === "string"));
  if (records.some((item) => item.runtime_type === "builtin.heteroscedastic_exact_gp.v1")) {
    return "異分散GP（個々値）";
  }
  if (records.some((item) => item.architecture_id === "hierarchical_parent_random_intercept_v1")) {
    return "階層Bayes（個々値・反復）";
  }
  if ([...architectureIds].some((item) => item.toLowerCase().includes("lightgbm"))) return "LightGBM";
  if (records.some((item) => asRecord(item.config)?.kernel === "ARD-RBF")) return "GP（安定ARD）";
  if (runtimeTypes.has("builtin.exact_gp.v1")) return "GP";
  if (runtimeTypes.has("builtin.posterior_linear.v1")) return "Bayes線形回帰";
  if (runtimeTypes.has("builtin.linear.v1")) return "線形回帰";
  return modelPackage.package_id;
}

export function modelPackageTrainedOnDataset(
  modelPackage: ApiModelPackageRef,
  dataset: ApiDataLibraryDataset | undefined,
): boolean {
  if (!dataset) return false;
  return (
    trainingDataSha(modelPackage) === dataset.data_asset.sha256
    && trainingProfileDigest(modelPackage) === dataset.profile_revision.profile_digest
  );
}

export function compatiblePackagesForDatasetTask(
  dataset: ApiDataLibraryDataset | undefined,
  taskId: string,
  options: Pick<ApiProjectCreationOptions, "model_packages" | "task_contract_digests">,
): ApiModelPackageRef[] {
  const currentDigest = options.task_contract_digests[taskId];
  if (!dataset || !currentDigest) return [];
  return options.model_packages.filter((item) => (
    item.task_id === taskId && item.task_contract_digest === currentDigest
    && modelPackageTrainedOnDataset(item, dataset)
  ));
}

export function compatibleTaskIdsForDataset(
  dataset: ApiDataLibraryDataset | undefined,
  options: Pick<ApiProjectCreationOptions, "model_packages" | "task_contract_digests">,
): string[] {
  return (dataset?.supported_task_ids ?? []).filter((taskId) => (
    compatiblePackagesForDatasetTask(dataset, taskId, options).length > 0
  ));
}
