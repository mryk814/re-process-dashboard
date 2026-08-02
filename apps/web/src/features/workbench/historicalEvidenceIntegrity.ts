import type { TaskOutputDefinition } from "../candidates";
import type { OriginMeasurement } from "./originEvidence";

type HistoricalOutput = Pick<TaskOutputDefinition, "key" | "label" | "unit">;

export type HistoricalEvidenceWarning = {
  kind: "integrity" | "transient";
  message: string;
};

export function storedHistoricalMeasurements(
  actualOutputs: Readonly<Record<string, unknown>>,
  outputs: readonly HistoricalOutput[],
): OriginMeasurement[] {
  return outputs.flatMap((output) => {
    const value = actualOutputs[output.key];
    return typeof value === "number" ? [{
      key: output.key,
      label: output.label,
      mean: value,
      std: 0,
      count: 1,
      unit: output.unit,
    }] : [];
  });
}

export function historicalEvidenceWarning(cause: unknown): HistoricalEvidenceWarning {
  const apiError = typeof cause === "object" && cause !== null
    && Reflect.get(cause, "name") === "ApiClientError"
    && typeof Reflect.get(cause, "kind") === "string"
    && typeof Reflect.get(cause, "status") === "number";
  const status = apiError ? Reflect.get(cause, "status") as number : 0;
  const kind = apiError ? Reflect.get(cause, "kind") as string : "";
  if ((kind === "not_found" && status === 404) || (kind === "validation" && status === 422)) {
    return {
      kind: "integrity",
      message: status === 404
        ? "保存時の実測recordが現在のDataset Revisionで見つかりません。候補の再現性は確認できません。"
        : "保存時の実測recordが現在のDataset Revisionと一致しません。候補の再現性は確認できません。",
    };
  }
  return {
    kind: "transient",
    message: "現在のsource identityを一時的に取得できません。接続回復後に再確認してください。",
  };
}
