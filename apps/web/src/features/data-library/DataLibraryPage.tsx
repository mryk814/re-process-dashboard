import { useState } from "react";
import type { ApiProject } from "../../shared/api/workbench-api";
import type { PreparedCsvProjectBinding } from "./CsvTaskOnboarding";
import { DataLibraryShell } from "./DataLibraryShell";
import type { DataLibraryLocation } from "./location";
import { ResourceCatalogView } from "./ResourceCatalogView";
import { SourceLifecycleWorkspace } from "./SourceLifecycleWorkspace";
import { useDataLibraryResources } from "./useDataLibraryResources";

export function DataLibraryPage({
  projects,
  onAddDataset,
  onStartProject,
  onOpenTrainingData,
  onOpenStorage,
  location,
  onNavigate,
}: {
  projects: ApiProject[];
  onAddDataset: (
    mode?: "revision" | "mapping",
    baseDatasetRevisionId?: string,
  ) => void;
  onStartProject: (
    datasetViewRevisionId: string,
    binding?: Omit<PreparedCsvProjectBinding, "datasetViewId">,
  ) => void;
  onOpenTrainingData: (projectId: string) => void;
  onOpenStorage: () => void;
  location: DataLibraryLocation;
  onNavigate: (location: DataLibraryLocation, replace?: boolean) => void;
}) {
  const resources = useDataLibraryResources();
  const [compareOpen, setCompareOpen] = useState(false);
  const actions = location.tab === "browse" ? <div className="data-library-header-actions">
    <button className="primary-button" onClick={() => onAddDataset("mapping")}>データを追加</button>
    <button
      className="outline-button"
      aria-expanded={compareOpen}
      aria-controls="dataset-comparison-builder"
      onClick={() => setCompareOpen((value) => !value)}
    >＋ 比較セット</button>
  </div> : undefined;

  return <DataLibraryShell location={location} onNavigate={onNavigate} actions={actions}>
    <div hidden={location.tab !== "browse"}>
      <ResourceCatalogView
        projects={projects}
        onAddDataset={onAddDataset}
        onStartProject={onStartProject}
        onOpenTrainingData={onOpenTrainingData}
        onOpenStorage={onOpenStorage}
        location={location}
        onNavigate={onNavigate}
        resources={resources}
        compareOpen={compareOpen}
        onCompareOpenChange={setCompareOpen}
      />
    </div>
    {location.tab === "update" && <SourceLifecycleWorkspace
        datasets={resources.datasets}
        location={location}
        onNavigate={onNavigate}
      />}
  </DataLibraryShell>;
}
