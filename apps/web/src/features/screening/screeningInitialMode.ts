type TargetGoal = number | { lower: number; upper: number };

export type ScreeningMode = "landscape" | "opportunity" | "batch";

export function initialScreeningMode(
  targetValues: Record<string, TargetGoal | null> | null | undefined,
): ScreeningMode {
  const hasValidGoal = Object.values(targetValues ?? {}).some((goal) => (
    typeof goal === "number"
      ? Number.isFinite(goal)
      : goal !== null
        && Number.isFinite(goal.lower)
        && Number.isFinite(goal.upper)
        && goal.lower < goal.upper
  ));
  return hasValidGoal
    ? "opportunity"
    : "landscape";
}
