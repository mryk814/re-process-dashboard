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
  if (prediction.interval_method === "conformal") return `Conformal予測区間（${Math.round((prediction.interval_coverage_level ?? 0) * 100)}%）`;
  if (prediction.interval_method === "quantile") return "予測分位点区間";
  if (prediction.interval_method === "parametric") return "パラメトリック予測区間";
  if (prediction.interval_method === "bayesian") return "Bayesian credible interval";
  const levels = Object.keys(prediction.quantiles ?? {}).map(Number).sort((left, right) => left - right);
  if (levels.length < 2 || levels.some((level) => !Number.isFinite(level))) return "利用不可";
  const low = levels[0]!;
  const high = levels.at(-1)!;
  const coverage = Math.round((high - low) * 100);
  if (prediction.target_kind === "binary") return `${Math.round(low * 100)}–${Math.round(high * 100)}%確率分位`;
  if (prediction.target_kind === "ordinal") return `${Math.round(low * 100)}–${Math.round(high * 100)}%カテゴリ分位`;
  return prediction.predictive_family === "empirical_quantiles"
    ? `${Math.round(low * 100)}–${Math.round(high * 100)}%分位`
    : `${coverage}%予測区間`;
}

export function predictionHasInterval(prediction: PredictionSemantics): boolean {
  return prediction.interval_method != null || Object.keys(prediction.quantiles ?? {}).length >= 2;
}
