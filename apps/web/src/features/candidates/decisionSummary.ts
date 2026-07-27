export type DecisionSupportStatus = "supported" | "caution" | "extrapolated" | "unknown";

type DecisionPrediction = Readonly<{
  lower: number;
  upper: number;
}>;

type DecisionPreview = Readonly<{
  predictions: Readonly<Record<string, DecisionPrediction>>;
  model_support?: Readonly<Record<string, Readonly<{ status?: string }>>>;
}>;

export type CandidateDecisionSummary = Readonly<{
  loadedCandidateCount: number;
  supportCounts: Readonly<Record<DecisionSupportStatus, number>>;
  uniformSupportStatus: DecisionSupportStatus | null;
  assessableOutputKeys: readonly string[];
  overlappingOutputKeys: readonly string[];
}>;

function candidateSupportStatus(
  outputKeys: readonly string[],
  preview: DecisionPreview,
): DecisionSupportStatus {
  const statuses = outputKeys.map((key) => preview.model_support?.[key]?.status);
  if (statuses.includes("extrapolated")) return "extrapolated";
  if (statuses.includes("caution")) return "caution";
  if (statuses.length > 0 && statuses.every((status) => status === "supported")) return "supported";
  return "unknown";
}

export function buildCandidateDecisionSummary({
  candidateIds,
  outputKeys,
  previewsByCandidate,
}: {
  candidateIds: readonly string[];
  outputKeys: readonly string[];
  previewsByCandidate: Readonly<Record<string, DecisionPreview | undefined>>;
}): CandidateDecisionSummary {
  const loaded = candidateIds.flatMap((candidateId) => {
    const preview = previewsByCandidate[candidateId];
    return preview ? [preview] : [];
  });
  const supportCounts: Record<DecisionSupportStatus, number> = {
    supported: 0,
    caution: 0,
    extrapolated: 0,
    unknown: 0,
  };
  for (const preview of loaded) {
    supportCounts[candidateSupportStatus(outputKeys, preview)] += 1;
  }

  const assessableOutputKeys: string[] = [];
  const overlappingOutputKeys: string[] = [];
  if (candidateIds.length >= 2) {
    for (const outputKey of outputKeys) {
      const intervals = candidateIds.map((candidateId) => {
        const prediction = previewsByCandidate[candidateId]?.predictions[outputKey];
        return prediction
          && Number.isFinite(prediction.lower)
          && Number.isFinite(prediction.upper)
          && prediction.lower <= prediction.upper
          ? prediction
          : null;
      });
      if (intervals.some((interval) => interval === null)) continue;
      const completeIntervals = intervals.filter((interval): interval is DecisionPrediction => interval !== null);
      assessableOutputKeys.push(outputKey);
      const latestLower = Math.max(...completeIntervals.map((interval) => interval.lower));
      const earliestUpper = Math.min(...completeIntervals.map((interval) => interval.upper));
      if (latestLower <= earliestUpper) overlappingOutputKeys.push(outputKey);
    }
  }

  return {
    loadedCandidateCount: loaded.length,
    supportCounts,
    uniformSupportStatus: candidateIds.length > 0 && loaded.length === candidateIds.length
      ? (Object.entries(supportCounts).find(([, count]) => count === candidateIds.length)?.[0] as DecisionSupportStatus | undefined) ?? null
      : null,
    assessableOutputKeys,
    overlappingOutputKeys,
  };
}
