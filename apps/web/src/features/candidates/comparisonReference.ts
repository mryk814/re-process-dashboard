export function comparisonValuesDiffer(left: unknown, right: unknown): boolean {
  return JSON.stringify(left ?? null) !== JSON.stringify(right ?? null);
}
