import type {
  ApiCandidate,
  ApiPredictionGraphSnapshot,
} from "../../shared/api/workbench-api";

export function resolvePredictionGraphActualOutputId(
  current: string,
  snapshot: ApiPredictionGraphSnapshot | null,
): string {
  const outputIds = snapshot?.terminal_outputs
    .filter((output) => output.status === "latest")
    .map((output) => output.output_id) ?? [];
  return outputIds.includes(current) ? current : outputIds[0] ?? "";
}

export function isPredictionGraphActualWritable(
  candidate: ApiCandidate | null,
  snapshot: ApiPredictionGraphSnapshot | null,
): boolean {
  return Boolean(
    candidate
      && snapshot
      && snapshot.identity.candidate_id === candidate.id
      && snapshot.identity.candidate_revision === candidate.revision,
  );
}
