export type ProjectCreationMode = "empty" | "copy";
export type ProjectGroupChoice = "none" | "existing" | "new";

export type ProjectCreationSubmitState = {
  loading: boolean;
  disabled: boolean;
  projectName: string;
  datasetViewId: string;
  mode: ProjectCreationMode;
  copyTaskId?: string;
  taskId: string;
  modelPackageRefId: string;
  chainId: string;
  chainRevisionId: string;
  groupChoice: ProjectGroupChoice;
  projectSeriesId: string;
  projectSeriesName: string;
};

/**
 * This is intentionally presentation-only validation. The submit callback still
 * owns the authoritative API validation and error messages.
 */
export function projectCreationSubmitDisabled(state: ProjectCreationSubmitState): boolean {
  if (state.loading || state.disabled || !state.projectName.trim() || !state.datasetViewId) return true;
  if (state.chainId) {
    if (!state.chainRevisionId) return true;
  } else if (
    !(state.mode === "copy" ? state.copyTaskId : state.taskId)
    || !state.modelPackageRefId
  ) {
    return true;
  }
  if (state.groupChoice === "existing" && !state.projectSeriesId) return true;
  return state.groupChoice === "new" && !state.projectSeriesName.trim();
}
