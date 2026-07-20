import type { components } from "./generated/api-types";

export type NumericRange = components["schemas"]["NumericRange"];
export type TaskFieldDefinition = components["schemas"]["InputFieldDefinition"];
export type TaskInputGroup = components["schemas"]["InputGroupDefinition"];
export type TaskOutputDefinition = components["schemas"]["OutputDefinition"];
export type TaskDefinitionContract = components["schemas"]["TaskDefinition"];
export type RuntimeOperations = components["schemas"]["RuntimeOperationsCapability"];
export type ResolvedTaskDefinition = components["schemas"]["ResolvedTaskDefinition"];

export type TaskInputDefinition = TaskFieldDefinition & {
  id: string;
  field: string;
  group: "composition" | "process";
  unit: string;
};

export type TaskDefinitionView = {
  task_id: string;
  inputs: TaskInputDefinition[];
  outputs: TaskOutputDefinition[];
  operations: RuntimeOperations;
};

export function taskDefinitionView(resolved: ResolvedTaskDefinition): TaskDefinitionView {
  const definition = resolved.task_definition;
  return {
    task_id: definition.id,
    inputs: definition.input_groups.flatMap<TaskInputDefinition>((group) => {
      if (group.key !== "composition" && group.key !== "process") return [];
      const groupKey: "composition" | "process" = group.key;
      return group.fields
        .filter((field) => field.kind === "number")
        .map((field) => ({
          ...field,
          id: field.path,
          field: field.path.split(".", 2)[1],
          group: groupKey,
          unit: field.unit ?? "",
        }));
    }),
    outputs: definition.outputs,
    operations: resolved.runtime_capability.operations,
  };
}
