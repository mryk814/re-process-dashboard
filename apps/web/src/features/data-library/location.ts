export type DataLibraryLocation = Readonly<{
  tab: "browse" | "update";
  connectorId?: string;
  stage?: "raw" | "curation" | "approval" | "training";
  revisionId?: string;
  onboardingMode?: "revision" | "mapping" | "new-task";
  datasetRevisionId?: string;
  packageReferenceId?: string;
}>;
