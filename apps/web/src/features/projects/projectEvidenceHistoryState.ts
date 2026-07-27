export type ProjectEvidenceHistoryViewState = "loading" | "error" | "empty" | "ready";

export function projectEvidenceHistoryViewState({
  loading,
  error,
  candidateCount,
}: {
  loading: boolean;
  error: boolean;
  candidateCount: number;
}): ProjectEvidenceHistoryViewState {
  if (error) return "error";
  if (loading) return "loading";
  return candidateCount === 0 ? "empty" : "ready";
}

type ChainStageLike = {
  output_definitions: readonly unknown[];
  result?: unknown;
};

export type ChainHistoryPrediction = {
  value?: number;
  std?: number;
  lower?: number;
  upper?: number;
};

export function chainHistoryPredictions(
  result: unknown,
): Record<string, ChainHistoryPrediction> {
  if (!result || typeof result !== "object") return {};
  const predictions = (result as { predictions?: unknown }).predictions;
  return predictions && typeof predictions === "object"
    ? predictions as Record<string, ChainHistoryPrediction>
    : {};
}

export function terminalHistoryStage<TStage extends ChainStageLike>(
  stages: readonly TStage[],
): TStage | undefined {
  return [...stages].reverse().find((stage) => (
    stage.output_definitions.length > 0
    && Object.keys(chainHistoryPredictions(stage.result)).length > 0
  ));
}
