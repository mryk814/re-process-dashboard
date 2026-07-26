import { useEffect, useState } from "react";
import { workbenchApi } from "./api/workbench-api";

/**
 * Prediction Task ids are internal identifiers. Every surface that names a task
 * resolves the contract label from the same catalog, so the Data Library and the
 * project screens cannot disagree about what a task is called.
 */
export function useTaskLabels(): (taskId: string) => string {
  const [labels, setLabels] = useState<Map<string, string>>(() => new Map());
  useEffect(() => {
    let active = true;
    workbenchApi.listTaskDefinitions()
      .then((items) => {
        if (!active) return;
        setLabels(new Map(items.map((item) => [
          item.definition.task_definition.id,
          item.definition.task_definition.label,
        ])));
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);
  // The id remains the fallback: an unknown task is still identifiable.
  return (taskId: string) => labels.get(taskId) ?? taskId;
}
