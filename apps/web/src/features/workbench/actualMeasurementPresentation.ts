export function actualDifference(actual: number, predicted: number): number {
  return actual - predicted;
}

export function signedDifference(value: number, format: (value: number) => string): string {
  if (Object.is(value, -0) || value === 0) return format(0);
  return `${value > 0 ? "+" : "−"}${format(Math.abs(value))}`;
}

export function measurementMetadata(actual: {
  experiment_no: string;
  measured_at?: string | null;
  replicates: number;
  std: number;
  note: string;
}): string[] {
  return [
    actual.experiment_no ? `実験 ${actual.experiment_no}` : "",
    actual.measured_at ? `測定日 ${actual.measured_at}` : "",
    `n=${actual.replicates}`,
    actual.std > 0 ? `標準偏差 ${actual.std}` : "",
    actual.note,
  ].filter(Boolean);
}
