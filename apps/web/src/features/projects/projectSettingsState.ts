export type ProjectSettingsAvailability = {
  open: boolean;
  hasProject: boolean;
  loading: boolean;
  disabled: boolean;
  projectName: string;
  invalidTargetRange: boolean;
};

export function shouldShowProjectSettings({
  open,
  hasProject,
}: Pick<ProjectSettingsAvailability, "open" | "hasProject">): boolean {
  return open && hasProject;
}

export function projectSettingsSaveDisabled({
  loading,
  disabled,
  projectName,
  invalidTargetRange,
}: Pick<
  ProjectSettingsAvailability,
  "loading" | "disabled" | "projectName" | "invalidTargetRange"
>): boolean {
  return loading || disabled || !projectName.trim() || invalidTargetRange;
}

export function projectSettingsControlsDisabled({
  loading,
  disabled,
}: Pick<ProjectSettingsAvailability, "loading" | "disabled">): boolean {
  return loading || disabled;
}

export function isCurrentProjectSettingsRequest(
  expectedProjectId: string,
  currentProjectId: string,
): boolean {
  return expectedProjectId === currentProjectId;
}

export const ungroupedMembershipValue = "__ungrouped__";

export function projectGroupMembershipState({
  selectedSeriesId,
  currentSeriesId,
  currentSeriesProjectCount,
}: {
  selectedSeriesId: string;
  currentSeriesId: string | null;
  currentSeriesProjectCount: number;
}) {
  const targetSeriesId = selectedSeriesId === ungroupedMembershipValue
    ? null
    : selectedSeriesId;
  const changed = Boolean(selectedSeriesId) && targetSeriesId !== currentSeriesId;
  return {
    targetSeriesId,
    changed,
    emptiesCurrentSeries: changed
      && currentSeriesId != null
      && currentSeriesProjectCount === 1,
    showUngroupOption: currentSeriesId != null,
  };
}

export function projectScientificSettingsReadOnly(
  taskUnavailable: boolean,
  settingsReadOnly: boolean,
): boolean {
  return taskUnavailable || settingsReadOnly;
}
