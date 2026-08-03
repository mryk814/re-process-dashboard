export function resolveObservationAuthoringTaskId(
  current: string,
  tasks: ReadonlyArray<{ task_id: string }>,
): string {
  return tasks.some((task) => task.task_id === current)
    ? current
    : tasks[0]?.task_id ?? "";
}
