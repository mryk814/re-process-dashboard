import type { components } from "../../generated/api-types";

export type CandidateInputs = components["schemas"]["CandidateInputs"];
export type NumericRange = components["schemas"]["NumericRange"];
export type TaskFieldDefinition = components["schemas"]["InputFieldDefinition"];
export type TaskInputGroup = components["schemas"]["InputGroupDefinition"];
export type TaskOutputDefinition = components["schemas"]["OutputDefinition"];
export type TaskDefinitionContract = components["schemas"]["TaskDefinition"];
export type RuntimeOperations = components["schemas"]["RuntimeOperationsCapability"];
export type ResolvedTaskDefinition = components["schemas"]["ResolvedTaskDefinition"];
export type ResponseCurveVariableOption = {
  id: string;
  requestVariable: string;
  label: string;
  unit: string;
  min: number;
  max: number;
  current: number;
  group: string;
  stageName?: string;
  stagePositionM?: number;
};

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

function stagePositionM(inputs: CandidateInputs, stageName: string): number | null {
  const points = inputs.heat_pattern ?? [];
  const speed = inputs.process.ls_mpm;
  if (!points.length || !Number.isFinite(speed) || speed <= 0) return null;
  const matches = points.filter((point) => point.stage_name?.trim() === stageName);
  if (!matches.length) return null;
  const meanTime = matches.reduce((sum, point) => sum + point.time_s, 0) / matches.length;
  return (meanTime - points[0].time_s) * speed / 60;
}

export function responseCurveVariables(
  definition: TaskDefinitionContract | null,
  candidateInputs: CandidateInputs,
  comparisonInputs: CandidateInputs[],
  configuredStagePositions: Record<string, number>,
): ResponseCurveVariableOption[] {
  if (!definition) return [];
  const numeric = new Map(numericTaskInputs(definition).map((field) => [field.path, field]));
  const declarations = [...(definition.response_curve_variables ?? [])].sort((left, right) => left.order - right.order);
  if (!declarations.length) {
    return [...numeric.values()].filter((field) => field.editable).map((field) => ({
      id: field.path,
      requestVariable: field.path,
      label: field.label,
      unit: field.unit,
      min: field.allowed_range!.min,
      max: field.allowed_range!.max,
      current: field.group === "composition" ? candidateInputs.composition[field.field] ?? 0 : candidateInputs.process[field.field] ?? 0,
      group: field.group === "composition" ? "成分" : "工程条件",
    }));
  }
  const variables: ResponseCurveVariableOption[] = [];
  for (const declaration of declarations) {
    if (declaration.kind === "numeric_input") {
      const field = numeric.get(declaration.path ?? "");
      if (!field?.allowed_range) continue;
      variables.push({
        id: field.path,
        requestVariable: field.path,
        label: declaration.label,
        unit: field.unit,
        min: field.allowed_range.min,
        max: field.allowed_range.max,
        current: field.group === "composition" ? candidateInputs.composition[field.field] ?? 0 : candidateInputs.process[field.field] ?? 0,
        group: field.group === "composition" ? "成分" : "工程条件",
      });
      continue;
    }
    const stageNames = [...new Set([
      ...comparisonInputs.flatMap((inputs) => (inputs.heat_pattern ?? []).map((point) => point.stage_name?.trim()).filter((name): name is string => Boolean(name))),
      ...Object.keys(configuredStagePositions).map((name) => name.trim()).filter(Boolean),
    ])];
    for (const stageName of stageNames) {
      const observedPositions = comparisonInputs.map((inputs) => stagePositionM(inputs, stageName)).filter((value): value is number => value !== null).sort((a, b) => a - b);
      const inferredPosition = observedPositions[Math.floor(observedPositions.length / 2)];
      const position = configuredStagePositions[stageName] ?? inferredPosition;
      if (!Number.isFinite(position)) continue;
      const ownPoints = (candidateInputs.heat_pattern ?? []).filter((point) => point.stage_name?.trim() === stageName);
      const current = ownPoints.length ? ownPoints.reduce((sum, point) => sum + point.temperature_c, 0) / ownPoints.length : 0;
      variables.push({
        id: `heat.stage_temperature_c:${stageName}`,
        requestVariable: "heat.stage_temperature_c",
        label: `${stageName} 温度`,
        unit: "°C",
        min: -273.15,
        max: 1800,
        current,
        group: declaration.label,
        stageName,
        stagePositionM: position,
      });
    }
  }
  return variables;
}

export type CategoricalTaskInput = TaskFieldDefinition & {
  id: string;
  field: string;
  choices: string[];
};

export function categoricalTaskInputs(definition: TaskDefinitionContract | null): CategoricalTaskInput[] {
  if (!definition) return [];
  return orderedInputGroups(definition).flatMap((group) => {
    if (group.key !== "categorical") return [];
    return group.fields.map((field) => ({
      ...field,
      id: field.path,
      field: pathSuffix("categorical", field.path),
      choices: [...field.choices],
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
