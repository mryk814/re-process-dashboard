export type PlaygroundAttemptStatus = "running" | "completed" | "failed" | "interrupted";

export type PlaygroundMetricValue = number | string | null;

export type PlaygroundTargetResult = Readonly<{
  targetKey: string;
  metrics: Readonly<Record<string, PlaygroundMetricValue>>;
  inferenceLabel: string;
}>;

export type PlaygroundAttemptView = Readonly<{
  attemptId: string;
  recipeId: string;
  recipeLabel: string;
  sequence: number;
  status: PlaygroundAttemptStatus;
  buildSeconds?: number;
  peakMemoryBytes?: number;
  artifactSizeBytes?: number;
  predictionLatencyMs?: number | null;
  packagePath?: string;
  targets: readonly PlaygroundTargetResult[];
  failure?: Readonly<{
    message: string;
    recoveryHint: string;
  }>;
  registration?: Readonly<{
    referenceId: string;
    activePackageChanged: false;
  }>;
}>;

export type PlaygroundComparisonRow = Readonly<{
  metric: string;
  values: Readonly<Record<string, PlaygroundMetricValue>>;
}>;

const metricLabels: Readonly<Record<string, string>> = {
  mae: "MAE",
  rmse: "RMSE",
  median_absolute_error: "Median AE",
  mean_log_predictive_density: "Mean log density",
  interval_coverage_90: "90% coverage",
  mean_interval_width: "Interval width",
  extreme_residual_mae: "Extreme residual MAE",
};

export function latestAttempts(
  attempts: readonly PlaygroundAttemptView[],
): readonly PlaygroundAttemptView[] {
  const byRecipe = new Map<string, PlaygroundAttemptView>();
  for (const attempt of attempts) {
    const current = byRecipe.get(attempt.recipeId);
    if (!current || attempt.sequence > current.sequence) {
      byRecipe.set(attempt.recipeId, attempt);
    }
  }
  return [...byRecipe.values()];
}

export function comparisonRows(
  attempts: readonly PlaygroundAttemptView[],
  targetKey: string,
): readonly PlaygroundComparisonRow[] {
  const completed = latestAttempts(attempts).filter(
    (attempt) => attempt.status === "completed",
  );
  const metrics = new Set<string>();
  for (const attempt of completed) {
    const target = attempt.targets.find((item) => item.targetKey === targetKey);
    for (const metric of Object.keys(target?.metrics ?? {})) metrics.add(metric);
  }
  return [...metrics]
    .sort((left, right) => (metricLabels[left] ?? left).localeCompare(
      metricLabels[right] ?? right,
    ))
    .map((metric) => ({
      metric: metricLabels[metric] ?? metric,
      values: Object.fromEntries(completed.map((attempt) => [
        attempt.recipeId,
        attempt.targets.find((item) => item.targetKey === targetKey)?.metrics[metric] ?? null,
      ])),
    }));
}

export function formatMetric(value: PlaygroundMetricValue): string {
  if (value === null) return "—";
  if (typeof value === "string") return value;
  if (!Number.isFinite(value)) return "—";
  if (Math.abs(value) >= 1_000) return value.toLocaleString("ja-JP", { maximumFractionDigits: 1 });
  return value.toLocaleString("ja-JP", { maximumSignificantDigits: 5 });
}

export function formatBytes(value?: number): string {
  if (value === undefined) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / 1024 ** 2).toFixed(1)} MiB`;
}

export function latencyLabel(value?: number | null): string {
  return value === undefined || value === null
    ? "未計測"
    : `${value.toLocaleString("ja-JP", { maximumFractionDigits: 1 })} ms`;
}

