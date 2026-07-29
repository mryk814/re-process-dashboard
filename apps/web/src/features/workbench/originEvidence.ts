import type { ApiCandidateOriginEvidence } from "../../shared/api/workbench-api";
import type { TaskOutputDefinition } from "../candidates";

export type OriginMeasurement = {
  key: string;
  label: string;
  mean: number;
  std: number;
  count: number;
  unit: string;
};

export function originMeasurements(
  evidence: ApiCandidateOriginEvidence,
  outputs: TaskOutputDefinition[],
): OriginMeasurement[] {
  const summaries = evidence.repeat_summary ?? {};
  return outputs.flatMap((output) => {
    const summaryKey = [...(output.measurement_keys ?? []), output.key, output.label]
      .find((key) => summaries[key]);
    const summary = summaryKey ? summaries[summaryKey] : undefined;
    return summary ? [{
      key: output.key,
      label: output.key === "lambda" ? "λ" : output.key,
      mean: summary.mean,
      std: summary.std,
      count: summary.n,
      unit: output.unit,
    }] : [];
  });
}
