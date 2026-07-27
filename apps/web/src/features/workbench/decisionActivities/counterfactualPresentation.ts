import type { components } from "../../../generated/api-types";
import {
  formatPredictionPoint,
  predictionHasInterval,
  predictionIntervalLabel,
} from "../../../shared/predictionPresentation";

type CounterfactualTargetEvaluation =
  components["schemas"]["CounterfactualTargetEvaluation"];

export function presentCounterfactualTarget(
  target: CounterfactualTargetEvaluation,
  label: string,
  formatNumber: (value: number) => string,
) {
  const prediction = target.prediction;
  const unit = target.unit && target.unit !== "1" ? ` ${target.unit}` : "";
  const plainValue = (value: number) => `${formatNumber(value)}${unit}`;
  const point = prediction
    ? formatPredictionPoint(prediction, formatNumber)
    : plainValue(target.predicted_value);
  const pointKind = prediction ? "点予測" : "旧記録の要約値";
  const shortfall = target.shortfall == null
    ? null
    : prediction?.target_kind === "binary"
      ? `${formatNumber(target.shortfall * 100)}パーセントポイント`
      : prediction?.target_kind === "ordinal"
        ? `カテゴリ差 ${formatNumber(target.shortfall)}`
        : plainValue(target.shortfall);
  const state = target.role === "reporting_only"
    ? "参考値"
    : target.achieved
      ? "✓ 達成"
      : shortfall
        ? `未達（あと ${shortfall}）`
        : "未達";
  const hasInterval = prediction != null && predictionHasInterval(prediction);
  const interval = hasInterval
    ? `${formatPredictionPoint({ ...prediction, value: prediction.lower }, formatNumber)}–${formatPredictionPoint({ ...prediction, value: prediction.upper }, formatNumber)}（${predictionIntervalLabel(prediction)}）`
    : "区間情報なし（この保存結果では利用不可）";

  return {
    point,
    pointKind,
    state,
    hasInterval,
    interval,
    accessibleName: `${label}、${pointKind} ${point}、${state}、${hasInterval ? `予測区間 ${interval}` : interval}`,
  };
}
