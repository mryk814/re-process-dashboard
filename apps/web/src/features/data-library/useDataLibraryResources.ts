import { useCallback, useEffect, useRef, useState } from "react";
import {
  workbenchApi,
  type ApiDataLibraryDataset,
  type ApiModelPackageRef,
  type ApiProjectCreationOptions,
} from "../../shared/api/workbench-api";
import {
  beginResourceLoad,
  initialResourceLoadState,
  rejectResourceLoad,
  resolveResourceLoad,
  type ResourceLoadState,
} from "./resourceLoadState";

export type DataLibraryResourceFamily = "options" | "datasets" | "modelPackages";
export type DataLibraryResourceStates = Record<DataLibraryResourceFamily, ResourceLoadState>;
export type DataLibraryLoadResult = {
  options?: ApiProjectCreationOptions;
  datasets?: ApiDataLibraryDataset[];
  modelPackages?: ApiModelPackageRef[];
};

export const dataLibraryResourceFamilies: DataLibraryResourceFamily[] = [
  "options",
  "datasets",
  "modelPackages",
];

export const resourceLabels: Record<DataLibraryResourceFamily, string> = {
  options: "予測タスクとプロジェクト作成条件",
  datasets: "Datasetとsource／revision",
  modelPackages: "Model Package",
};

const initialResourceStates = (): DataLibraryResourceStates => ({
  options: initialResourceLoadState(),
  datasets: initialResourceLoadState(),
  modelPackages: initialResourceLoadState(),
});

export function useDataLibraryResources() {
  const [options, setOptions] = useState<ApiProjectCreationOptions | null>(null);
  const [datasets, setDatasets] = useState<ApiDataLibraryDataset[]>([]);
  const [modelPackages, setModelPackages] = useState<ApiModelPackageRef[]>([]);
  const [resourceStates, setResourceStates] = useState<DataLibraryResourceStates>(
    initialResourceStates,
  );
  const requestVersions = useRef<Record<DataLibraryResourceFamily, number>>({
    options: 0,
    datasets: 0,
    modelPackages: 0,
  });

  const loadResources = useCallback(async (
    families: DataLibraryResourceFamily[] = dataLibraryResourceFamilies,
  ): Promise<DataLibraryLoadResult> => {
    const versions = Object.fromEntries(families.map((family) => [
      family,
      requestVersions.current[family] + 1,
    ])) as Partial<Record<DataLibraryResourceFamily, number>>;
    for (const family of families) requestVersions.current[family] = versions[family]!;
    setResourceStates((current) => {
      const next = { ...current };
      for (const family of families) next[family] = beginResourceLoad(current[family]);
      return next;
    });

    const result: DataLibraryLoadResult = {};
    await Promise.allSettled(families.map(async (family) => {
      try {
        if (family === "options") {
          const data = await workbenchApi.projectCreationOptions();
          if (requestVersions.current.options !== versions.options) return;
          result.options = data;
          setOptions(data);
        } else if (family === "datasets") {
          const data = await workbenchApi.listDataLibraryDatasets(true);
          if (requestVersions.current.datasets !== versions.datasets) return;
          result.datasets = data;
          setDatasets(data);
        } else {
          const data = await workbenchApi.listModelPackageRefs(true);
          if (requestVersions.current.modelPackages !== versions.modelPackages) return;
          result.modelPackages = data;
          setModelPackages(data);
        }
        setResourceStates((current) => ({
          ...current,
          [family]: resolveResourceLoad(),
        }));
      } catch (cause) {
        if (requestVersions.current[family] !== versions[family]) return;
        const message = cause instanceof Error
          ? cause.message
          : `${resourceLabels[family]}を取得できませんでした。`;
        setResourceStates((current) => ({
          ...current,
          [family]: rejectResourceLoad(current[family], message),
        }));
      }
    }));
    return result;
  }, []);

  useEffect(() => {
    void loadResources();
  }, [loadResources]);

  return {
    options,
    datasets,
    modelPackages,
    resourceStates,
    loadResources,
    retryResource: (family: DataLibraryResourceFamily) => loadResources([family]),
  };
}

export type DataLibraryResources = ReturnType<typeof useDataLibraryResources>;
