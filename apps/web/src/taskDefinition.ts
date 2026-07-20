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

export type FixedTaskContext = {
  path: string;
  order: number;
  label: string;
  value: string | number | boolean;
};

export type TaskDefinitionContract = {
  schema_version: "task-definition/v1";
  id: string;
  label: string;
  canonical_candidate_schema_version: "canonical-candidate/v1";
  input_groups: TaskInputGroup[];
  outputs: TaskOutputDefinition[];
  fixed_context: FixedTaskContext[];
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

export type UnsupportedTaskInput = {
  group: TaskInputGroup["key"];
  field: TaskFieldDefinition;
  reason: "unsupported_group" | "unsupported_kind";
};

/**
 * Temporary projection consumed by the pre-#7 renderer.
 *
 * This is deliberately not the canonical frontend contract. Fields that the
 * current renderer cannot draw are retained in unsupported_inputs instead of
 * disappearing silently, so #7 can migrate them explicitly.
 */
export type TaskDefinitionView = {
  task_id: string;
  inputs: TaskInputDefinition[];
  unsupported_inputs: UnsupportedTaskInput[];
  fixed_context: FixedTaskContext[];
  outputs: TaskOutputDefinition[];
  operations: RuntimeOperations;
};

function fieldName(group: "composition" | "process", path: string): string {
  const prefix = `${group}.`;
  if (!path.startsWith(prefix) || path.length === prefix.length) {
    throw new Error(`TaskDefinition field path does not match group: ${group} / ${path}`);
  }
  return path.slice(prefix.length);
}

export function taskDefinitionView(resolved: ResolvedTaskDefinition): TaskDefinitionView {
  const definition = resolved.task_definition;
  if (resolved.runtime_capability.task_id !== definition.id) {
    throw new Error(
      `TaskDefinition/runtime capability task mismatch: ${definition.id} / ${resolved.runtime_capability.task_id}`,
    );
  }

  const inputs: TaskInputDefinition[] = [];
  const unsupportedInputs: UnsupportedTaskInput[] = [];

  for (const group of [...definition.input_groups].sort((left, right) => left.order - right.order)) {
    for (const field of [...group.fields].sort((left, right) => left.order - right.order)) {
      if (group.key !== "composition" && group.key !== "process") {
        unsupportedInputs.push({ group: group.key, field, reason: "unsupported_group" });
        continue;
      }
      if (field.kind !== "number") {
        unsupportedInputs.push({ group: group.key, field, reason: "unsupported_kind" });
        continue;
      }
      inputs.push({
        ...field,
        id: field.path,
        field: fieldName(group.key, field.path),
        group: group.key,
        unit: field.unit ?? "",
      });
    }
  }

  return {
    task_id: definition.id,
    inputs,
    unsupported_inputs: unsupportedInputs,
    fixed_context: [...definition.fixed_context].sort((left, right) => left.order - right.order),
    outputs: definition.outputs,
    operations: resolved.runtime_capability.operations,
  };
}
