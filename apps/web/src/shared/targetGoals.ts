export type TargetRange = { lower: number; upper: number };
export type TargetGoal = number | TargetRange;

export function isTargetRange(goal: TargetGoal | undefined): goal is TargetRange {
  return typeof goal === "object" && goal !== null && "lower" in goal && "upper" in goal;
}

export function hasValidTargetGoal(goal: TargetGoal | undefined): boolean {
  if (isTargetRange(goal)) return Number.isFinite(goal.lower) && Number.isFinite(goal.upper) && goal.lower < goal.upper;
  return typeof goal === "number" && Number.isFinite(goal);
}

export function targetGoalText(
  goal: TargetGoal,
  direction: "at_least" | "at_most" | "target",
  format: (value: number) => string,
): string {
  if (isTargetRange(goal)) return `${format(goal.lower)}–${format(goal.upper)}`;
  const relation = direction === "at_most" ? "≤" : direction === "target" ? "≈" : "≥";
  return `${relation} ${format(goal)}`;
}
