export type OutputRange = { min: number; max: number };

export type OutputDefinitionLike = {
  key: string;
  label: string;
  unit: string;
  measurement_keys?: string[];
  plausibility_range?: OutputRange | null;
  preferred_display_range?: OutputRange | null;
};

export type PredictionLike = {
  value: number;
  lower?: number;
  upper?: number;
  quantiles?: Record<string, number>;
};

export type OutputAssessment = {
  implausible: boolean;
  warning: string | null;
  outsideValues: number[];
};

export function resolveOutputDefinition(
  outputs: readonly OutputDefinitionLike[],
  key: string,
): OutputDefinitionLike | undefined {
  return outputs.find((output) => output.key === key || output.measurement_keys?.includes(key));
}

export function assessOutputValues(
  output: OutputDefinitionLike | undefined,
  values: readonly number[],
  subject = "値",
): OutputAssessment {
  const range = output?.plausibility_range;
  if (!output || !range) return { implausible: false, warning: null, outsideValues: [] };
  const outsideValues = [...new Set(values.filter((value) => Number.isFinite(value) && (value < range.min || value > range.max)))];
  if (!outsideValues.length) return { implausible: false, warning: null, outsideValues };
  return {
    implausible: true,
    warning: `${subject}が物理範囲外です（妥当範囲 ${range.min.toLocaleString("ja-JP")}–${range.max.toLocaleString("ja-JP")} ${output.unit}）`,
    outsideValues,
  };
}

export function assessPrediction(
  output: OutputDefinitionLike | undefined,
  prediction: PredictionLike | undefined,
): OutputAssessment {
  if (!prediction) return { implausible: false, warning: null, outsideValues: [] };
  const intervalValues = [prediction.lower, prediction.upper, ...Object.values(prediction.quantiles ?? {})]
    .filter((value): value is number => typeof value === "number");
  const point = assessOutputValues(output, [prediction.value], "予測値");
  if (point.implausible) return point;
  return assessOutputValues(output, intervalValues, "予測区間");
}

export function clampToRange(value: number, range: OutputRange): number {
  return Math.min(range.max, Math.max(range.min, value));
}

export function isOutsideRange(value: number, range: OutputRange): boolean {
  return Number.isFinite(value) && (value < range.min || value > range.max);
}
