import type { TaskDefinitionContract } from "./taskDefinition";

export type DisplayDecimalOverrides = Record<string, number> | undefined;

export function displayDecimals(
  definition: TaskDefinitionContract,
  key: string,
  overrides?: DisplayDecimalOverrides,
) {
  return overrides?.[key] ?? definition.display_decimals[key];
}

export function formatDisplayNumber(
  value: number,
  definition: TaskDefinitionContract,
  key: string,
  overrides?: DisplayDecimalOverrides,
) {
  const digits = displayDecimals(definition, key, overrides);
  return value.toLocaleString("ja-JP", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function formatInputNumber(
  value: number,
  definition: TaskDefinitionContract,
  key: string,
  overrides?: DisplayDecimalOverrides,
) {
  return value.toFixed(displayDecimals(definition, key, overrides));
}
