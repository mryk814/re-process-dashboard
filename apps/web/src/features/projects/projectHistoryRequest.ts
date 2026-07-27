export function isCurrentProjectHistoryRequest(
  expectedProjectId: string,
  currentProjectId: string,
  aborted = false,
): boolean {
  return !aborted && expectedProjectId === currentProjectId;
}
