import type { components } from "../../generated/api-types";

export type CandidateInputs = components["schemas"]["CandidateInputs"];
export type NumericRange = components["schemas"]["NumericRange"];
export type TaskFieldDefinition = components["schemas"]["InputFieldDefinition"];
export type TaskInputGroup = components["schemas"]["InputGroupDefinition"];
export type TaskOutputDefinition = components["schemas"]["OutputDefinition"];
export type TaskDefinitionContract = components["schemas"]["TaskDefinition"];
export type RuntimeOperations = components["schemas"]["RuntimeOperationsCapability"];
export type ResolvedTaskDefinition = components["schemas"]["ResolvedTaskDefinition"];

export type NumericTaskInput = TaskFieldDefinition & {
  id: string;
  field: string;
  group: "composition" | "process";
  unit: string;
};

const candidateInputGroups = new Set<TaskInputGroup["key"]>([
  "composition",
  "process",
  "categorical",
  "heat_pattern",
]);

function pathSuffix(group: TaskInputGroup["key"], path: string): string {
  if (group === "heat_pattern") {
    if (path !== "heat_pattern") throw new Error(`TaskDefinition field path does not match group: ${group} / ${path}`);
    return path;
  }
  const prefix = `${group}.`;
  if (!path.startsWith(prefix) || path.length === prefix.length) {
    throw new Error(`TaskDefinition field path does not match group: ${group} / ${path}`);
  }
  return path.slice(prefix.length);
}

export function taskFieldName(group: TaskInputGroup["key"], path: string): string {
  return pathSuffix(group, path);
}

export function validateResolvedTaskDefinition(resolved: ResolvedTaskDefinition): ResolvedTaskDefinition {
  const definition = resolved.task_definition;
  if (resolved.runtime_capability.task_id !== definition.id) {
    throw new Error(`TaskDefinition/runtime capability task mismatch: ${definition.id} / ${resolved.runtime_capability.task_id}`);
  }
  for (const group of definition.input_groups) {
    if (!candidateInputGroups.has(group.key)) throw new Error(`Unsupported TaskDefinition input group: ${group.key}`);
    for (const field of group.fields) {
      pathSuffix(group.key, field.path);
      const expectedKind = group.key === "heat_pattern" ? "heat_pattern" : group.key === "categorical" ? "categorical" : "number";
      if (field.kind !== expectedKind) {
        throw new Error(`TaskDefinition field kind does not match group: ${group.key} / ${field.path} / ${field.kind}`);
      }
    }
  }
  return resolved;
}

export function orderedInputGroups(definition: TaskDefinitionContract): TaskInputGroup[] {
  return [...definition.input_groups]
    .sort((left, right) => left.order - right.order)
    .map((group) => ({ ...group, fields: [...group.fields].sort((left, right) => left.order - right.order) }));
}

export function numericTaskInputs(definition: TaskDefinitionContract | null): NumericTaskInput[] {
  if (!definition) return [];
  return orderedInputGroups(definition).flatMap((group) => {
    if (group.key !== "composition" && group.key !== "process") return [];
    const groupKey = group.key;
    return group.fields.map((field) => ({
      ...field,
      id: field.path,
      field: pathSuffix(groupKey, field.path),
      group: groupKey,
      unit: field.unit ?? "",
    }));
  });
}

export function getCandidateInputValue(inputs: CandidateInputs, path: string): number | string | CandidateInputs["heat_pattern"] | undefined {
  if (path === "heat_pattern") return inputs.heat_pattern;
  const separator = path.indexOf(".");
  if (separator < 1 || separator === path.length - 1) throw new Error(`Invalid candidate input path: ${path}`);
  const group = path.slice(0, separator);
  const field = path.slice(separator + 1);
  if (group === "composition" || group === "process") return inputs[group][field];
  if (group === "categorical") return inputs.categorical?.[field];
  throw new Error(`Unsupported candidate input path: ${path}`);
}

export function setCandidateInputValue(inputs: CandidateInputs, path: string, value: number | string | CandidateInputs["heat_pattern"]): CandidateInputs {
  if (path === "heat_pattern") return { ...inputs, heat_pattern: value as CandidateInputs["heat_pattern"] };
  const separator = path.indexOf(".");
  if (separator < 1 || separator === path.length - 1) throw new Error(`Invalid candidate input path: ${path}`);
  const group = path.slice(0, separator);
  const field = path.slice(separator + 1);
  if (group === "composition" || group === "process") {
    return { ...inputs, [group]: { ...inputs[group], [field]: Number(value) } };
  }
  if (group === "categorical") return { ...inputs, categorical: { ...inputs.categorical, [field]: String(value) } };
  throw new Error(`Unsupported candidate input path: ${path}`);
}
