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

export type AllowedRangeDefinition = {
  unit?: string | null;
  display_decimals?: number | null;
  allowed_range?: { min: number; max: number } | null;
};

/**
 * 許容範囲はその入力の表示桁数へ丸め、単位を添えて出す。生の浮動小数
 * （0.6316999999999999）を画面に出さないため。
 *
 * 丸めは内側へ寄せる。表示を四捨五入すると、表示上は範囲内に見える値が
 * 実際には範囲外で弾かれることがあるため、下限は切り上げ、上限は切り捨てる。
 * 丸めた結果が反転するほど狭い範囲は、丸めずにそのまま出す。
 */
export function formatAllowedRange(definition: AllowedRangeDefinition): string {
  const range = definition.allowed_range;
  if (!range) return "";
  const digits = definition.display_decimals ?? 1;
  const step = 10 ** digits;
  const min = Math.ceil(range.min * step) / step;
  const max = Math.floor(range.max * step) / step;
  const bounds = min <= max
    ? `${formatNumberAtDecimals(min, digits)}〜${formatNumberAtDecimals(max, digits)}`
    : `${range.min}〜${range.max}`;
  const unit = (definition.unit ?? "").trim();
  return unit ? `${bounds} ${unit}` : bounds;
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
