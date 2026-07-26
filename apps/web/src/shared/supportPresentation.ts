/**
 * Model support status is a decision-relevant state, not an internal enum.
 * Every surface that shows it uses these labels so the same status never
 * appears as raw English in one panel and as Japanese in another.
 */
export type SupportStatus = "supported" | "caution" | "extrapolated";

export function supportStatusLabel(
  status: string | null | undefined,
  unknownLabel = "未計算",
): string {
  return status === "supported"
    ? "範囲内"
    : status === "caution"
      ? "要確認"
      : status === "extrapolated"
        ? "外挿"
        : unknownLabel;
}

export function supportStatusTone(status: string | null | undefined): "success" | "caution" | "unknown" {
  return status === "supported" ? "success" : status ? "caution" : "unknown";
}
