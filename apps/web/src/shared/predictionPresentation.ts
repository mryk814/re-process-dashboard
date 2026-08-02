type PredictionSemantics = {
  target_kind: string;
  predictive_family: string;
  quantiles: Record<string, number>;
  interval_method?: "conformal" | "quantile" | "parametric" | "bayesian" | null;
  interval_coverage_level?: number | null;
  interval_calibration_dataset_digest?: string | null;
  interval_calibration_sample_count?: number | null;
};

type PredictionPoint = PredictionSemantics & { value: number; unit: string; categories?: string[] };

export function formatPredictionPoint(prediction: PredictionPoint, formatNumber: (value: number) => string): string {
  if (prediction.target_kind === "binary") return `${formatNumber(prediction.value * 100)}%`;
  if (prediction.target_kind === "ordinal") {
    const categories = prediction.categories ?? [];
    const nearest = categories[Math.max(0, Math.min(categories.length - 1, Math.round(prediction.value)))];
    return nearest ? `${nearest}（期待 ${formatNumber(prediction.value)}）` : `期待カテゴリ ${formatNumber(prediction.value)}`;
  }
  return `${formatNumber(prediction.value)} ${prediction.unit}`;
}

export function predictionIntervalLabel(prediction: PredictionSemantics): string {
  const coverage = prediction.interval_coverage_level == null
    ? "coverage未記録"
    : `${Math.round(prediction.interval_coverage_level * 100)}%`;
  if (prediction.interval_method === "conformal") return `Conformal予測区間（${coverage}）`;
  if (prediction.interval_method === "quantile") {
    const label = prediction.target_kind === "binary"
      ? "確率分位点区間"
      : prediction.target_kind === "ordinal"
        ? "カテゴリ分位点区間"
        : "予測分位点区間";
    return `${label}（${coverage}）`;
  }
  if (prediction.interval_method === "parametric") return `パラメトリック予測区間（${coverage}）`;
  if (prediction.interval_method === "bayesian") {
    return prediction.target_kind === "binary"
      ? `Bayesian確率区間（${coverage}）`
      : `Bayesian予測区間（${coverage}）`;
  }
  return predictionHasInterval(prediction) ? "区間の意味は未記録" : "利用不可";
}

export function predictionHasInterval(prediction: PredictionSemantics): boolean {
  return prediction.interval_method != null || Object.keys(prediction.quantiles ?? {}).length >= 2;
}

export function formatPredictionInterval(
  prediction: PredictionSemantics & { lower: number; upper: number },
  formatNumber: (value: number) => string,
): string | null {
  if (
    !predictionHasInterval(prediction)
    || !Number.isFinite(prediction.lower)
    || !Number.isFinite(prediction.upper)
  ) return null;
  return `${predictionIntervalLabel(prediction)} ${formatNumber(prediction.lower)}–${formatNumber(prediction.upper)}`;
}
