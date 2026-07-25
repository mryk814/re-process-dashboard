import type { TaskDefinitionContract } from "./taskDefinition";
import { formatTaskNumber, taskDisplayDecimals } from "../../shared/taskPresentation";

export type DisplayDecimalOverrides = Record<string, number> | undefined;

export function displayDecimals(
  definition: TaskDefinitionContract,
  key: string,
  overrides?: DisplayDecimalOverrides,
) {
  return taskDisplayDecimals(definition, key, overrides);
}

export function formatDisplayNumber(
  value: number,
  definition: TaskDefinitionContract,
  key: string,
  overrides?: DisplayDecimalOverrides,
) {
  return formatTaskNumber(value, definition, key, overrides);
}

export function formatInputNumber(
  value: number,
  definition: TaskDefinitionContract,
  key: string,
  overrides?: DisplayDecimalOverrides,
) {
  return value.toFixed(displayDecimals(definition, key, overrides));
}
