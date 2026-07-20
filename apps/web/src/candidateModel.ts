import type { ApiCandidate, ApiCandidateInput } from "./shared/api/workbench-api";

export type CandidateViewModel = {
  raw: ApiCandidate;
  id: string;
  label: string;
  heat: Array<{ time: number; temperature: number; segmentStart?: boolean }>;
};

export function fromApiCandidate(candidate: ApiCandidate): CandidateViewModel {
  return {
    raw: candidate,
    id: candidate.id,
    label: candidate.name,
    heat: (candidate.inputs.heat_pattern ?? []).map((point) => ({
      time: point.time_s / 60,
      temperature: point.temperature_c,
      segmentStart: point.segment_start,
    })),
  };
}

export function toApiCandidate(candidate: CandidateViewModel): ApiCandidateInput {
  return {
    name: candidate.label,
    inputs: {
      composition: candidate.raw.inputs.composition,
      process: candidate.raw.inputs.process,
      categorical: candidate.raw.inputs.categorical,
      ...(candidate.raw.inputs.heat_pattern === null && candidate.heat.length === 0
        ? { heat_pattern: null }
        : {
            heat_pattern: candidate.heat.map((point, index) => ({
              ...candidate.raw.inputs.heat_pattern?.[index],
              time_s: point.time * 60,
              temperature_c: point.temperature,
              segment_start: point.segmentStart ?? false,
            })),
          }),
    },
    provenance: candidate.raw.provenance,
  };
}
