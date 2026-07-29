import { useEffect, useMemo, useState } from "react";
import { candidateInputIdentity } from "../../shared/api/inferenceRequestCache";
import {
  workbenchApi,
  type ApiResponseContour,
} from "../../shared/api/workbench-api";
import {
  getCandidateInputValue,
  numericTaskInputs,
  type CandidateViewModel,
  type TaskDefinitionContract,
} from "../candidates";
import type { components } from "../../generated/api-types";

type ResponseContourSurface =
  components["schemas"]["ResponseContourSurfaceDefinition"];

function format(value: number, digits = 2) {
  return value.toLocaleString("ja-JP", {
    maximumFractionDigits: digits,
  });
}

function color(value: number, min: number, max: number) {
  const ratio = Math.max(0, Math.min(1, (value - min) / Math.max(max - min, 1e-12)));
  const hue = 210 - ratio * 175;
  const lightness = 92 - ratio * 45;
  return `hsl(${hue} 72% ${lightness}%)`;
}

export function ResponseContourPanel({
  projectId,
  candidate,
  taskDefinition,
  surface,
  ready,
}: {
  projectId: string;
  candidate: CandidateViewModel;
  taskDefinition: TaskDefinitionContract;
  surface: ResponseContourSurface;
  ready: boolean;
}) {
  const axes = useMemo(() => {
    const allowed = new Set(surface.axis_paths);
    return numericTaskInputs(taskDefinition).filter((field) => allowed.has(field.path));
  }, [surface.axis_paths, taskDefinition]);
  const outputs = taskDefinition.outputs;
  const [xPath, setXPath] = useState(surface.axis_paths[0] ?? "");
  const [yPath, setYPath] = useState(surface.axis_paths[1] ?? "");
  const [target, setTarget] = useState(outputs[0]?.key ?? "");
  const [enabled, setEnabled] = useState(false);
  const [payload, setPayload] = useState<ApiResponseContour | null>(null);
  const [payloadIdentity, setPayloadIdentity] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const requestIdentity = [
    projectId,
    candidate.id,
    candidate.raw.revision,
    candidateInputIdentity(candidate.raw.inputs),
    target,
    xPath,
    yPath,
    surface.grid_size,
  ].join("\u001f");

  useEffect(() => {
    setPayload(null);
    setError("");
    if (!enabled || !ready || !target || !xPath || !yPath || xPath === yPath) {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setLoading(true);
      workbenchApi.responseContour(
        projectId,
        candidate.id,
        candidate.raw.revision,
        candidateInputIdentity(candidate.raw.inputs),
        target,
        xPath,
        yPath,
        surface.grid_size,
        controller.signal,
      ).then((result) => {
        if (controller.signal.aborted) return;
        setPayload(result);
        setPayloadIdentity(requestIdentity);
        setError("");
      }).catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        setPayload(null);
        setPayloadIdentity("");
        setError(cause instanceof Error ? cause.message : String(cause));
      }).finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    }, 240);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [requestIdentity, enabled, ready]);

  if (axes.length < 2 || !outputs.length) return null;
  const output = outputs.find((item) => item.key === target) ?? outputs[0];
  const visiblePayload = payloadIdentity === requestIdentity ? payload : null;
  const xCurrent = Number(getCandidateInputValue(candidate.raw.inputs, xPath));
  const yCurrent = Number(getCandidateInputValue(candidate.raw.inputs, yPath));
  return (
    <section className="response-contour-panel" aria-labelledby="response-contour-title">
      <div className="panel-title response-contour-header">
        <div>
          <h2 id="response-contour-title">2変数の予測地図</h2>
          <p>各軸は学習データの最小〜最大です。斜線は、固定した他の入力まで含めると既存実績から遠い条件です。</p>
        </div>
        {!enabled ? (
          <button type="button" className="primary-button" onClick={() => setEnabled(true)}>
            地図を表示
          </button>
        ) : null}
      </div>
      {enabled ? (
        <>
          <div className="response-contour-controls">
            <label>予測特性
              <select value={target} onChange={(event) => setTarget(event.target.value)}>
                {outputs.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
              </select>
            </label>
            <div className="contour-axis-controls" role="group" aria-label="表示軸">
              <label>横軸
                <select value={xPath} onChange={(event) => {
                  const next = event.target.value;
                  setXPath(next);
                  if (next === yPath) setYPath(xPath);
                }}>
                  {axes.map((item) => <option key={item.path} value={item.path}>{item.label}{item.unit ? ` (${item.unit})` : ""}</option>)}
                </select>
              </label>
              <button type="button" className="text-button contour-swap-button" onClick={() => {
                setXPath(yPath);
                setYPath(xPath);
              }} aria-label="横軸と縦軸を入れ替える">↔ 入替</button>
              <label>縦軸
                <select value={yPath} onChange={(event) => {
                  const next = event.target.value;
                  setYPath(next);
                  if (next === xPath) setXPath(yPath);
                }}>
                  {axes.map((item) => <option key={item.path} value={item.path}>{item.label}{item.unit ? ` (${item.unit})` : ""}</option>)}
                </select>
              </label>
            </div>
          </div>
          {!ready ? <p className="empty-evidence">入力を保存後に更新します。</p>
            : loading ? <p className="empty-evidence" role="status">予測地図を計算しています。</p>
              : error ? <p className="empty-evidence" role="alert">{error}</p>
                : visiblePayload ? <ContourFigure
                  payload={visiblePayload}
                  outputLabel={output.label}
                  outputUnit={output.unit}
                  xCurrent={xCurrent}
                  yCurrent={yCurrent}
                /> : null}
        </>
      ) : null}
    </section>
  );
}

function ContourFigure({
  payload,
  outputLabel,
  outputUnit,
  xCurrent,
  yCurrent,
}: {
  payload: ApiResponseContour;
  outputLabel: string;
  outputUnit: string;
  xCurrent: number;
  yCurrent: number;
}) {
  const width = 680;
  const height = 390;
  const left = 76;
  const top = 22;
  const plotWidth = 560;
  const plotHeight = 300;
  const rows = payload.grid_shape[0];
  const columns = payload.grid_shape[1];
  const cellWidth = plotWidth / Math.max(columns - 1, 1);
  const cellHeight = plotHeight / Math.max(rows - 1, 1);
  const values = payload.cells
    .filter((cell) => cell.displayable && cell.prediction)
    .map((cell) => cell.prediction!.value);
  const fallbackValues = payload.cells
    .filter((cell) => cell.prediction)
    .map((cell) => cell.prediction!.value);
  const min = payload.output_range?.min ?? Math.min(...fallbackValues, 0);
  const max = payload.output_range?.max ?? Math.max(...fallbackValues, 1);
  const xMin = payload.x_axis.min;
  const xMax = payload.x_axis.max;
  const yMin = payload.y_axis.min;
  const yMax = payload.y_axis.max;
  const markerX = left + ((xCurrent - xMin) / Math.max(xMax - xMin, 1e-12)) * plotWidth;
  const markerY = top + plotHeight - ((yCurrent - yMin) / Math.max(yMax - yMin, 1e-12)) * plotHeight;
  const markerVisible = xCurrent >= xMin && xCurrent <= xMax && yCurrent >= yMin && yCurrent <= yMax;
  const xTicks = [xMin, (xMin + xMax) / 2, xMax];
  const yTicks = [yMin, (yMin + yMax) / 2, yMax];
  const invalidCount = payload.cells.filter((cell) => cell.invalid_reason).length;
  const cautionCount = payload.cells.filter((cell) => cell.support?.status === "caution").length;
  const extrapolatedCount = payload.cells.filter((cell) => cell.support?.status === "extrapolated").length;
  return (
    <div className="response-contour-content">
      <div className="response-contour-figure">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={`${outputLabel}を${payload.x_axis.label}と${payload.y_axis.label}で見た予測地図`}
          aria-describedby="response-contour-description"
        >
          <desc id="response-contour-description">色は既存実績に近い条件の予測値だけを表します。斜線は、軸ごとの範囲内でも、固定した他の入力まで含めると既存実績から遠い条件です。</desc>
          <defs>
            <clipPath id="response-contour-clip">
              <rect x={left} y={top} width={plotWidth} height={plotHeight} />
            </clipPath>
            <pattern id="contour-extrapolated" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <rect width="8" height="8" fill="#eef1f4" />
              <line x1="0" y1="0" x2="0" y2="8" stroke="#8995a5" strokeWidth="2" />
            </pattern>
            <pattern id="contour-invalid" width="6" height="6" patternUnits="userSpaceOnUse">
              <rect width="6" height="6" fill="#f5f6f8" />
              <path d="M0 0L6 6M6 0L0 6" stroke="#b6bec8" strokeWidth="1" />
            </pattern>
          </defs>
          {payload.cells.map((cell, index) => {
            const row = Math.floor(index / columns);
            const column = index % columns;
            const x = left + column * cellWidth - cellWidth / 2;
            const y = top + plotHeight - row * cellHeight - cellHeight / 2;
            const fill = cell.invalid_reason
              ? "url(#contour-invalid)"
              : !cell.displayable
                ? "url(#contour-extrapolated)"
                : color(cell.prediction!.value, min, max);
            const title = cell.invalid_reason
              ? cell.invalid_reason
              : !cell.displayable
                ? `${payload.x_axis.label} ${format(cell.x)}, ${payload.y_axis.label} ${format(cell.y)}, 既存実績から遠い（予測値は非表示）`
                : `${payload.x_axis.label} ${format(cell.x)}, ${payload.y_axis.label} ${format(cell.y)}, ${outputLabel} ${format(cell.prediction!.value)} ${outputUnit}, ${cell.support?.status === "caution" ? "既存実績からやや遠い" : "近い実績あり"}`;
            return <rect
              key={`${cell.x}-${cell.y}`}
              x={x}
              y={y}
              width={cellWidth + 0.25}
              height={cellHeight + 0.25}
              clipPath="url(#response-contour-clip)"
              fill={fill}
              stroke={cell.support?.status === "caution" ? "#b45309" : "#ffffff"}
              strokeWidth={cell.support?.status === "caution" ? 1.5 : 0.4}
            ><title>{title}</title></rect>;
          })}
          <rect x={left} y={top} width={plotWidth} height={plotHeight} fill="none" stroke="#607087" />
          {xTicks.map((tick) => <g key={`x-${tick}`}>
            <line x1={left + ((tick - xMin) / (xMax - xMin)) * plotWidth} y1={top + plotHeight} x2={left + ((tick - xMin) / (xMax - xMin)) * plotWidth} y2={top + plotHeight + 5} stroke="#607087" />
            <text x={left + ((tick - xMin) / (xMax - xMin)) * plotWidth} y={top + plotHeight + 18} textAnchor="middle">{format(tick)}</text>
          </g>)}
          {yTicks.map((tick) => <g key={`y-${tick}`}>
            <line x1={left - 5} y1={top + plotHeight - ((tick - yMin) / (yMax - yMin)) * plotHeight} x2={left} y2={top + plotHeight - ((tick - yMin) / (yMax - yMin)) * plotHeight} stroke="#607087" />
            <text x={left - 9} y={top + plotHeight - ((tick - yMin) / (yMax - yMin)) * plotHeight + 4} textAnchor="end">{format(tick)}</text>
          </g>)}
          {markerVisible ? <g aria-label="現在の候補">
            <circle cx={markerX} cy={markerY} r="8" fill="#fff" stroke="#132f54" strokeWidth="3" />
            <circle cx={markerX} cy={markerY} r="2.5" fill="#132f54" />
          </g> : null}
          <text x={left + plotWidth / 2} y={height - 20} textAnchor="middle">{payload.x_axis.label}{payload.x_axis.unit ? ` (${payload.x_axis.unit})` : ""}</text>
          <text transform={`translate(18 ${top + plotHeight / 2}) rotate(-90)`} textAnchor="middle">{payload.y_axis.label}{payload.y_axis.unit ? ` (${payload.y_axis.unit})` : ""}</text>
        </svg>
        <div className="response-contour-legend" aria-label="予測地図の凡例">
          <span className="contour-scale" aria-hidden="true" />
          <span>{values.length ? `${format(min)}–${format(max)} ${outputUnit}` : "表示できる支持範囲内セルなし"}</span>
          <span><i className="contour-caution-swatch" />既存実績からやや遠い {cautionCount}</span>
          <span><i className="contour-extrapolated-swatch" />既存実績から遠い（予測非表示） {extrapolatedCount}</span>
          {invalidCount ? <span><i className="contour-invalid-swatch" />制約外 {invalidCount}</span> : null}
          <span><i className="contour-current-swatch" />現在の候補</span>
        </div>
      </div>
      <details className="response-contour-table">
        <summary>数値で確認</summary>
        <div className="table-scroll">
          <table>
            <thead><tr><th>{payload.x_axis.label}</th><th>{payload.y_axis.label}</th><th>{outputLabel}</th><th>学習実績</th></tr></thead>
            <tbody>{payload.cells.map((cell) => <tr key={`${cell.x}-${cell.y}`}>
              <td>{format(cell.x)}</td>
              <td>{format(cell.y)}</td>
              <td>{cell.displayable && cell.prediction ? `${format(cell.prediction.value)} ${outputUnit}` : "—"}</td>
              <td>{cell.invalid_reason || (cell.support?.status === "supported" ? "近い実績あり" : cell.support?.status === "caution" ? "既存実績からやや遠い" : "既存実績から遠い")}</td>
            </tr>)}</tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
