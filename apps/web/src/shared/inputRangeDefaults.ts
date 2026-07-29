import type { components } from "../generated/api-types";

type NumericRange = components["schemas"]["NumericRange"];
type InputRangeSource = Pick<
  components["schemas"]["InputFieldDefinition"],
  "path" | "allowed_range" | "default_range" | "training_range"
>;

function clampRange(range: NumericRange, allowed: NumericRange): NumericRange {
  return {
    min: Math.max(allowed.min, range.min),
    max: Math.min(allowed.max, range.max),
  };
}

export function suggestedInputRange(input: InputRangeSource): NumericRange {
  if (!input.allowed_range) {
    throw new Error(`数値fieldにallowed_rangeがありません: ${input.path}`);
  }
  if (input.default_range) {
    return clampRange(input.default_range, input.allowed_range);
  }
  if (!input.training_range) {
    return input.allowed_range;
  }

  const span = input.training_range.max - input.training_range.min;
  const reference = Math.max(
    Math.abs(input.training_range.min),
    Math.abs(input.training_range.max),
    1e-6,
  );
  const margin = span > 0 ? span * 0.1 : reference * 0.1;
  return clampRange({
    min: input.training_range.min - margin,
    max: input.training_range.max + margin,
  }, input.allowed_range);
}

export function defaultResponseCurveRange(
  input: InputRangeSource | undefined,
  projectRange: NumericRange | undefined,
): NumericRange | undefined {
  if (projectRange) return projectRange;
  return input ? suggestedInputRange(input) : undefined;
}
