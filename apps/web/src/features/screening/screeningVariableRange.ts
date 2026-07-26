export type ScreeningNumericRange = Readonly<{ min: number; max: number }>;

const usable = (range: ScreeningNumericRange | undefined | null): range is ScreeningNumericRange => (
  range != null && Number.isFinite(range.min) && Number.isFinite(range.max) && range.min < range.max
);

/**
 * The default exploration range must not start outside what the model has seen.
 * Practical range and training range are both legitimate, so the default is their
 * overlap; when they do not overlap, the training range wins and the user has to
 * widen it explicitly.
 */
export function safeExplorationRange(
  practicalRange: ScreeningNumericRange | undefined | null,
  trainingRange: ScreeningNumericRange | undefined | null,
): ScreeningNumericRange | undefined {
  if (!usable(trainingRange)) return usable(practicalRange) ? practicalRange : undefined;
  if (!usable(practicalRange)) return trainingRange;
  const overlap = {
    min: Math.max(practicalRange.min, trainingRange.min),
    max: Math.min(practicalRange.max, trainingRange.max),
  };
  return usable(overlap) ? overlap : trainingRange;
}
