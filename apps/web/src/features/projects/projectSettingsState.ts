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
