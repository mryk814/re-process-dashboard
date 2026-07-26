import type { components } from "../generated/api-types";

export type TaskPresentationDefinition = Pick<
  components["schemas"]["TaskDefinition"],
  "display_decimals" | "outputs"
>;

type KeyedValue = { key: string };

export function taskDisplayDecimals(
  definition: TaskPresentationDefinition,
  key: string,
  overrides?: Record<string, number>,
): number {
  return overrides?.[key] ?? definition.display_decimals[key] ?? 1;
}

export function formatTaskNumber(
  value: number,
  definition: TaskPresentationDefinition,
  key: string,
  overrides?: Record<string, number>,
): string {
  const digits = taskDisplayDecimals(definition, key, overrides);
  return formatNumberAtDecimals(value, digits);
}

export function formatNumberAtDecimals(value: number, digits: number): string {
  return value.toLocaleString("ja-JP", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function orderedTaskEntries<T>(
  definition: TaskPresentationDefinition,
  values: Record<string, T>,
): Array<[string, T]> {
  const rank = new Map(definition.outputs.map((output, index) => [output.key, index]));
  return Object.entries(values).sort(([left], [right]) => {
    const leftRank = rank.get(left) ?? Number.MAX_SAFE_INTEGER;
    const rightRank = rank.get(right) ?? Number.MAX_SAFE_INTEGER;
    return leftRank - rightRank || left.localeCompare(right, "ja");
  });
}

export function orderedTaskItems<T extends KeyedValue>(
  definition: TaskPresentationDefinition,
  items: readonly T[],
): T[] {
  const rank = new Map(definition.outputs.map((output, index) => [output.key, index]));
  return [...items].sort((left, right) => {
    const leftRank = rank.get(left.key) ?? Number.MAX_SAFE_INTEGER;
    const rightRank = rank.get(right.key) ?? Number.MAX_SAFE_INTEGER;
    return leftRank - rightRank || left.key.localeCompare(right.key, "ja");
  });
}

export function taskOutputUnit(definition: TaskPresentationDefinition, key: string): string {
  const unit = definition.outputs.find((output) => output.key === key)?.unit?.trim();
  return unit && unit !== "1" ? unit : "";
}
