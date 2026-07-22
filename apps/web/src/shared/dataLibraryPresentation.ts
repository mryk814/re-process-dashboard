import type {
  ApiDataLibraryDataset,
  ApiModelPackageRef,
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
