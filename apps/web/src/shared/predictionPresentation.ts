type PredictionSemantics = {
  target_kind: string;
  predictive_family: string;
  quantiles: Record<string, number>;
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
  return Object.keys(prediction.quantiles ?? {}).length >= 2;
}
