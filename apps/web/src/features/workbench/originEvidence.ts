import type { ApiLineage } from "../../shared/api/workbench-api";
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
  lineage: ApiLineage,
  outputs: TaskOutputDefinition[],
): OriginMeasurement[] {
  return outputs.flatMap((output) => {
    const summaryKey = [...(output.measurement_keys ?? []), output.key, output.label]
      .find((key) => lineage.node.property_summary[key]);
    const summary = summaryKey ? lineage.node.property_summary[summaryKey] : undefined;
    return summary ? [{
      key: output.key,
      label: output.key === "lambda" ? "λ" : output.key,
      mean: summary.mean,
      std: summary.std,
      count: summary.count,
      unit: output.unit,
    }] : [];
  });
}
