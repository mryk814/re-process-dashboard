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

export type ModelLibraryStudioIntent = Readonly<{
  graphId: string;
  definitionId: string;
  revisionId: string;
}>;

export type ModelLibraryDataIntent = Readonly<{
  datasetRevisionId?: string;
  packageReferenceId?: string;
}>;
