export type NumericRange = { min: number; max: number };

export type TaskFieldDefinition = {
  path: string;
  kind: "number" | "categorical" | "heat_pattern";
  order: number;
  label: string;
  unit: string | null;
  required: boolean;
  editable: boolean;
  default_range: NumericRange | null;
  allowed_range: NumericRange | null;
  training_range: NumericRange | null;
  choices: string[];
};

export type TaskInputGroup = {
  key: "composition" | "process" | "heat_pattern" | "categorical";
  order: number;
  label: string;
  fields: TaskFieldDefinition[];
};

export type TaskOutputDefinition = {
  key: string;
  label: string;
  unit: string;
  goal_direction: "at_least" | "at_most" | "target";
};

export type TaskDefinitionContract = {
  schema_version: "task-definition/v1";
  id: string;
  label: string;
  canonical_candidate_schema_version: "canonical-candidate/v1";
  input_groups: TaskInputGroup[];
  outputs: TaskOutputDefinition[];
  fixed_context: Array<{ path: string; order: number; label: string; value: string | number | boolean }>;
};

export type RuntimeOperations = {
  preview: boolean;
  detailed_prediction: boolean;
  response_curve: boolean;
  similarity: boolean;
  snapshot: boolean;
  actual_measurement: boolean;
};

export type ResolvedTaskDefinition = {
  task_definition: TaskDefinitionContract;
  runtime_capability: { task_id: string; operations: RuntimeOperations };
};

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
