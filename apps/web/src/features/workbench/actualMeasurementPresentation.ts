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

export function actualMeasurementErrorMessage(cause: unknown, fallback: string): string {
  if (typeof cause !== "object" || cause === null || Reflect.get(cause, "name") !== "ApiClientError") {
    return fallback;
  }
  if (Reflect.get(cause, "kind") === "network") {
    return "APIへ接続できませんでした。接続状態を確認して、もう一度お試しください。";
  }
  const message = Reflect.get(cause, "message");
  return typeof message === "string" && message.trim() ? message : fallback;
}
