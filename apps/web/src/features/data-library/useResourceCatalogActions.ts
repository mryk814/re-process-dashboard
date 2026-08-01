import { useState } from "react";
import {
  workbenchApi,
  type ApiDataLibraryDataset,
  type ApiModelPackageRef,
} from "../../shared/api/workbench-api";
import {
  datasetDisplayName,
  modelPackageDisplayName,
  trainingDataset,
} from "../../shared/dataLibraryPresentation";
import type { DataLibraryLocation } from "./location";
import type { DataLibraryResources } from "./useDataLibraryResources";

type UndoAction = {
  kind: "dataset" | "package";
  id: string;
  archived: boolean;
  label: string;
};

export type PackageTrainingSnapshotLink = {
  connectorId: string;
  snapshotId: string;
  snapshotDigest: string;
  selectionPolicyDigest: string;
};

export function packageTrainingSnapshotLink(
  item: ApiModelPackageRef,
): PackageTrainingSnapshotLink | null {
  const provenance = item.manifest_json.provenance;
  if (!provenance || typeof provenance !== "object") return null;
  const lifecycle = (provenance as Record<string, unknown>).source_lifecycle;
  if (!lifecycle || typeof lifecycle !== "object") return null;
  const identity = lifecycle as Record<string, unknown>;
  return typeof identity.connector_id === "string" && identity.connector_id.length > 0
    && typeof identity.training_snapshot_id === "string" && identity.training_snapshot_id.length > 0
    && typeof identity.training_snapshot_digest === "string" && identity.training_snapshot_digest.length > 0
    && typeof identity.training_selection_policy_digest === "string" && identity.training_selection_policy_digest.length > 0
    ? {
      connectorId: identity.connector_id,
      snapshotId: identity.training_snapshot_id,
      snapshotDigest: identity.training_snapshot_digest,
      selectionPolicyDigest: identity.training_selection_policy_digest,
    }
    : null;
}

export function useResourceCatalogActions({
  resources,
  onNavigate,
}: {
  resources: DataLibraryResources;
  onNavigate: (location: DataLibraryLocation, replace?: boolean) => void;
}) {
  const [error, setError] = useState("");
  const [changingResourceId, setChangingResourceId] = useState("");
  const [openingTrainingSnapshotId, setOpeningTrainingSnapshotId] = useState("");
  const [undoAction, setUndoAction] = useState<UndoAction | null>(null);
  const [refreshingPackages, setRefreshingPackages] = useState(false);
  const [refreshMessage, setRefreshMessage] = useState("");
  const [refreshWarnings, setRefreshWarnings] = useState<Array<{
    source: string;
    reference?: string | null;
    message: string;
  }>>([]);

  async function openTrainingSnapshot(link: PackageTrainingSnapshotLink) {
    setOpeningTrainingSnapshotId(link.snapshotId);
    setError("");
    try {
      const [snapshotDetail, connectorDetail] = await Promise.all([
        workbenchApi.approvedTrainingSnapshot(link.snapshotId),
        workbenchApi.sourceConnectorDetail(link.connectorId),
      ]);
      const belongsToConnector = connectorDetail.training_snapshots.some(
        (item) => item.id === link.snapshotId,
      );
      const snapshotDigestMatches = snapshotDetail.snapshot.snapshot_digest === link.snapshotDigest;
      const policyDigestMatches = snapshotDetail.snapshot.selection_policy_digest === link.selectionPolicyDigest;
      if (!belongsToConnector || !snapshotDigestMatches || !policyDigestMatches) {
        throw new Error("Model Packageが固定した学習Snapshotの識別情報が一致しません。");
      }
      onNavigate({
        tab: "update",
        connectorId: link.connectorId,
        stage: "training",
        revisionId: link.snapshotId,
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "固定した学習Snapshotを確認できませんでした。");
    } finally {
      setOpeningTrainingSnapshotId("");
    }
  }

  async function changeDatasetState(item: ApiDataLibraryDataset) {
    const id = item.dataset_revision.id;
    const archived = !item.dataset_revision.archived_at;
    setChangingResourceId(id);
    setError("");
    try {
      await workbenchApi.setDatasetArchived(id, archived);
      setUndoAction({ kind: "dataset", id, archived: !archived, label: datasetDisplayName(item) });
      await resources.loadResources(["options", "datasets"]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : archived ? "Datasetを利用停止できませんでした。" : "Datasetを復元できませんでした。");
    } finally {
      setChangingResourceId("");
    }
  }

  async function changeModelPackageState(item: ApiModelPackageRef) {
    const archived = !item.archived_at;
    setChangingResourceId(item.id);
    setError("");
    try {
      await workbenchApi.setModelPackageArchived(item.id, archived);
      setUndoAction({ kind: "package", id: item.id, archived: !archived, label: modelPackageDisplayName(item) });
      await resources.loadResources(["modelPackages"]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : archived ? "Model Packageを利用停止できませんでした。" : "Model Packageを復元できませんでした。");
    } finally {
      setChangingResourceId("");
    }
  }

  async function undoLastChange() {
    if (!undoAction) return;
    setChangingResourceId(undoAction.id);
    setError("");
    try {
      if (undoAction.kind === "dataset") {
        await workbenchApi.setDatasetArchived(undoAction.id, undoAction.archived);
      } else {
        await workbenchApi.setModelPackageArchived(undoAction.id, undoAction.archived);
      }
      setUndoAction(null);
      await resources.loadResources(
        undoAction.kind === "dataset" ? ["options", "datasets"] : ["modelPackages"],
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "直前の操作を元に戻せませんでした。");
    } finally {
      setChangingResourceId("");
    }
  }

  async function createComparison(name: string, selectedIds: string[]) {
    if (!resources.options || selectedIds.length < 2 || !name.trim()) {
      setError("比較名と2件以上のDatasetを選んでください。");
      return false;
    }
    try {
      await workbenchApi.createDatasetView({
        view_id: `cohort-${crypto.randomUUID()}`,
        revision: 1,
        name: name.trim(),
        kind: "cohort_comparison",
        members: selectedIds.map((dataset_revision_id, ordinal) => {
          const dataset = resources.options!.datasets.find(
            (item) => item.dataset_revision.id === dataset_revision_id,
          );
          return {
            dataset_revision_id,
            ordinal,
            cohort_key: `cohort-${ordinal + 1}`,
            cohort_label: dataset ? datasetDisplayName(dataset) : `Cohort ${ordinal + 1}`,
            provenance_json: {},
          };
        }),
      });
      await resources.loadResources(["options", "datasets"]);
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "比較セットを作成できませんでした。");
      return false;
    }
  }

  async function refreshTaskResources() {
    setRefreshingPackages(true);
    setRefreshMessage("");
    setRefreshWarnings([]);
    setError("");
    try {
      const result = await workbenchApi.refreshTaskResources();
      const warnings = result.warnings ?? [];
      const refreshedLibrary = await resources.loadResources(["options", "modelPackages"]);
      setRefreshWarnings(warnings);
      const addedTaskIds = result.added_task_ids ?? [];
      const addedModelPackageIds = result.added_model_package_ids ?? [];
      const added = [
        ...(addedTaskIds.length > 0 ? [`新しいTask ${addedTaskIds.length}件`] : []),
        ...(addedModelPackageIds.length > 0 ? [`新しいModel Package ${addedModelPackageIds.length}件`] : []),
      ];
      const selectablePackageIds = new Set(
        addedModelPackageIds.filter((packageId) => {
          const modelPackage = refreshedLibrary.modelPackages?.find((item) => item.id === packageId);
          const dataset = trainingDataset(modelPackage, resources.datasets);
          return Boolean(
            modelPackage
            && dataset?.dataset_views?.some((view) => view.kind === "single")
            && dataset.supported_task_ids.includes(modelPackage.task_id)
            && refreshedLibrary.options?.task_contract_digests[modelPackage.task_id] === modelPackage.task_contract_digest,
          );
        }),
      );
      setRefreshMessage(warnings.length > 0
        ? `${added.length > 0 ? `${added.join("・")}を反映。` : ""}${warnings.length}件は検証で除外されました。`
        : !refreshedLibrary.options || !refreshedLibrary.modelPackages
          ? "再読込は完了しましたが、一部のresourceを確認できませんでした。失敗した項目を再試行してからProject作成を確認してください。"
          : selectablePackageIds.size > 0
            ? `${added.join("・")}を反映しました。Project作成で選べます。`
            : added.length > 0
              ? `${added.join("・")}を再読込しましたが、対応するDatasetが登録されていないためProject作成にはまだ使えません。`
              : "再読込は完了しました。新しく反映するTask／Model Packageはありません。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "個人Model Packageを再読込できませんでした。");
    } finally {
      setRefreshingPackages(false);
    }
  }

  return {
    error,
    changingResourceId,
    openingTrainingSnapshotId,
    undoAction,
    refreshingPackages,
    refreshMessage,
    refreshWarnings,
    openTrainingSnapshot,
    changeDatasetState,
    changeModelPackageState,
    undoLastChange,
    dismissUndo: () => setUndoAction(null),
    createComparison,
    refreshTaskResources,
    reportError: setError,
  };
}
