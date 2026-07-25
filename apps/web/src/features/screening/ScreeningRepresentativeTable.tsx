import type { ApiScreeningRun } from "../../shared/api/workbench-api";
import { assessOutputValues } from "../../shared/outputPresentation";
import type { TaskDefinitionContract } from "../candidates";

type ScreeningDisplayOption = {
  value: string;
  label: string;
};

type ScreeningOutput = TaskDefinitionContract["outputs"][number];
type ScreeningPoint = ApiScreeningRun["representative_points"][number];

function number(value: number, digits = 0) {
  return value.toLocaleString("ja-JP", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function supportLabel(status: string) {
  return status === "supported" ? "範囲内" : status === "caution" ? "要確認" : status === "extrapolated" ? "外挿" : "未確認";
}

function pointPredictions(point: ScreeningPoint, target: string) {
  return {
    ...(point.predictions ?? {}),
    [target]: point.prediction,
  };
}

function predictionPointText(prediction: ScreeningPoint["prediction"], digits: number) {
  if (prediction.target_kind === "binary") return `${number(prediction.value * 100, 1)}%`;
  const unit = prediction.unit === "1" ? "" : ` ${prediction.unit}`;
  return `${number(prediction.value, digits)}${unit}`;
}

function predictionIntervalText(prediction: ScreeningPoint["prediction"], digits: number) {
  if (prediction.target_kind === "binary") return null;
  if (!Number.isFinite(prediction.lower) || !Number.isFinite(prediction.upper) || prediction.lower === prediction.upper) return null;
  const unit = prediction.unit === "1" ? "" : ` ${prediction.unit}`;
  return `${number(prediction.lower, digits)}–${number(prediction.upper, digits)}${unit}`;
}

export function ScreeningRepresentativeTable({
  result,
  outputs,
  options,
  baseCandidateLabel,
  selectedPointIndices,
  stockedPointIndices,
  onToggle,
}: {
  result: ApiScreeningRun;
  outputs: ScreeningOutput[];
  options: ScreeningDisplayOption[];
  baseCandidateLabel: string;
  selectedPointIndices: number[];
  stockedPointIndices: Set<number>;
  onToggle: (index: number) => void;
}) {
  const optionByPath = new Map(options.map((option) => [option.value, option]));
  const varyingFields = Object.entries(result.variables)
    .filter(([, spec]) => spec.mode !== "fixed")
    .map(([field]) => field);
  const fixedFields = Object.entries(result.variables)
    .filter(([, spec]) => spec.mode === "fixed");
  const orderedOutputs = [
    ...outputs.filter((output) => output.key === result.target),
    ...outputs.filter((output) => output.key !== result.target),
  ];
  const commonSupportMessage = result.representative_points.length > 0
    && result.representative_points.every((point) => point.support.message === result.representative_points[0].support.message)
    ? result.representative_points[0].support.message
    : null;
  const commonSupportStatus = result.representative_points.length > 0
    && result.representative_points.every((point) => point.support.status === result.representative_points[0].support.status)
    ? result.representative_points[0].support.status
    : null;

  return (
    <section className="screening-results" aria-labelledby="screening-results-title">
      <div className="screening-results-heading">
        <div>
          <h3 id="screening-results-title">代表点</h3>
          <small>変えた条件と予測を比較</small>
        </div>
        <div className="screening-result-context" aria-label="探索の固定条件">
          <span><small>基準候補</small><b>{baseCandidateLabel}</b></span>
          <span><small>固定条件</small><b>基準候補の入力を使用</b></span>
          {fixedFields.map(([field, spec]) => (
            <span key={field}>
              <small>{optionByPath.get(field)?.label ?? "固定入力"}</small>
              <b>{String(spec.value ?? "—")}</b>
            </span>
          ))}
        </div>
      </div>
      {commonSupportMessage && (
        <div className={`screening-common-support ${commonSupportStatus ?? ""}`} role="note">
          <span className={`support-badge ${commonSupportStatus ?? ""}`}>{supportLabel(commonSupportStatus ?? "")}</span>
          <p>{commonSupportMessage}</p>
          <small>代表点に共通</small>
        </div>
      )}
      <div className="screening-results-scroll">
        <table className="quality-table screening-results-table">
          <thead>
            <tr>
              <th className="screening-select-column">選択</th>
              <th className="screening-point-column">点</th>
              {varyingFields.map((field, index) => (
                <th key={field}>{optionByPath.get(field)?.label ?? `変動条件 ${index + 1}`}</th>
              ))}
              {orderedOutputs.map((output) => <th key={output.key}>{output.label}<small>{output.unit === "1" ? "" : output.unit}</small></th>)}
              <th className="screening-support-column">支持範囲</th>
            </tr>
          </thead>
          <tbody>
            {result.representative_points.map((point) => {
              const predictions = pointPredictions(point, result.target);
              return (
                <tr key={point.index}>
                  <td className="screening-select-column">
                    <input type="checkbox" aria-label={`点 ${point.index + 1}を選択`} checked={selectedPointIndices.includes(point.index)} disabled={stockedPointIndices.has(point.index)} onChange={() => onToggle(point.index)} />
                    {stockedPointIndices.has(point.index) && <small>追加済み</small>}
                  </td>
                  <th className="screening-point-column" scope="row">{point.index + 1}</th>
                  {varyingFields.map((field) => {
                    const value = point.inputs[field];
                    return <td className="screening-variable-value" key={field}>{typeof value === "number" ? number(value, 3) : String(value ?? "—")}</td>;
                  })}
                  {orderedOutputs.map((output) => {
                    const prediction = predictions[output.key];
                    if (!prediction) return <td key={output.key}>—</td>;
                    const pointAssessment = assessOutputValues(output, [prediction.value], "予測値");
                    const intervalAssessment = assessOutputValues(
                      output,
                      [prediction.lower, prediction.upper, ...Object.values(prediction.quantiles ?? {})],
                      "予測区間",
                    );
                    const interval = predictionIntervalText(prediction, 1);
                    return (
                      <td className={pointAssessment.implausible ? "implausible-output screening-prediction-cell" : "screening-prediction-cell"} title={pointAssessment.warning ?? intervalAssessment.warning ?? undefined} key={output.key}>
                        <strong>{predictionPointText(prediction, 1)}</strong>
                        {pointAssessment.implausible && <small className="output-warning-badge">⚠ 物理範囲外</small>}
                        {interval && <small className={intervalAssessment.implausible ? "implausible-output" : undefined}>{interval}{intervalAssessment.implausible && " · ⚠ 範囲外含む"}</small>}
                      </td>
                    );
                  })}
                  <td className="screening-support-column">
                    <span className={`support-badge ${point.support.status}`} title={commonSupportMessage ? undefined : point.support.message}>{supportLabel(point.support.status)}</span>
                    {!commonSupportMessage && <small>{point.support.message}</small>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
