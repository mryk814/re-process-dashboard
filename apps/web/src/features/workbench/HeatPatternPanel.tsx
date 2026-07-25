import { type PointerEvent, useState } from "react";
import { SvgChartTooltip } from "../../shared/ui/SvgChartTooltip";
import type { CandidateViewModel as Candidate, HeatTimeBasis } from "../candidates";

function number(value: number, digits = 0) {
  return value.toLocaleString("ja-JP", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

const CANDIDATE_COLORS = ["#d97706", "#0f766e", "#9333a8", "#dc2626", "#0891b2", "#4f46e5", "#65a30d", "#c2410c"];

function candidateColor(candidateId: string, selectedId: string) {
  if (candidateId === selectedId) return "#1f5fc4";
  let hash = 0;
  for (const character of candidateId) hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  return CANDIDATE_COLORS[hash % CANDIDATE_COLORS.length];
}

export function HeatPattern({
  candidates,
  candidate,
  onTimeBasisChange,
  onUpdate,
  onAdd,
  onDelete,
}: {
  candidates: Candidate[];
  candidate: Candidate;
  onTimeBasisChange: (basis: HeatTimeBasis) => void;
  onUpdate: (index: number, field: "time" | "temperature" | "stageName", raw: number | string) => void;
  onAdd: () => void;
  onDelete: (index: number) => void;
}) {
  const width = 440;
  const height = 228;
  const pad = { left: 48, right: 28, top: 18, bottom: 42 };
  const times = candidates.flatMap((item) => item.heat.map((point) => point.time));
  const rawMinTime = Math.min(...times);
  const rawMaxTime = Math.max(...times);
  const timePadding = Math.max((rawMaxTime - rawMinTime) * 0.08, 0.05);
  const minTime = Math.max(0, rawMinTime - timePadding);
  const maxTime = rawMaxTime + timePadding;
  const maxTemp = Math.max(
    1000,
    ...candidates.flatMap((item) =>
      item.heat.map((point) => point.temperature),
    ),
  );
  const x = (time: number) =>
    pad.left + ((time - minTime) / Math.max(0.001, maxTime - minTime)) * (width - pad.left - pad.right);
  const y = (temp: number) =>
    height - pad.bottom - (temp / maxTemp) * (height - pad.top - pad.bottom);
  const points = candidate.heat
    .map((point) => `${x(point.time)},${y(point.temperature)}`)
    .join(" ");
  const timeTicks = [minTime, (minTime + maxTime) / 2, maxTime];
  const timeSpan = maxTime - minTime;
  const timeTickDigits = timeSpan >= 100 ? 0 : timeSpan >= 10 ? 1 : 2;
  const [hoveredHeatPoint, setHoveredHeatPoint] = useState<{ x: number; y: number; lines: string[] } | null>(null);
  const dragPoint = (event: PointerEvent<SVGCircleElement>, index: number) => {
    const svg = event.currentTarget.ownerSVGElement;
    if (!svg) return;
    const bounds = svg.getBoundingClientRect();
    const temperature = Math.round(
      Math.max(
        0,
        Math.min(
          maxTemp,
          ((height -
            pad.bottom -
            ((event.clientY - bounds.top) / bounds.height) * height) /
            (height - pad.top - pad.bottom)) *
            maxTemp,
        ),
      ),
    );
    const point = candidate.heat[index];
    setHoveredHeatPoint({ x: x(point.time), y: y(temperature), lines: [candidate.label, point.stageName || point.stageCategory || `点 ${index + 1}`, `時間 ${number(point.time, 2)} min`, `温度 ${number(temperature, 0)} °C`] });
    onUpdate(index, "temperature", temperature);
  };
  return (
    <section className="chart-panel heat-panel">
      <div className="panel-title">
        <h2>
          ヒートパターン <span>（焼鈍温度・時間）</span>
        </h2>
        <div className="candidate-color-legend" aria-label="候補の色">
          {candidates.map((item) => <span className={item.id === candidate.id ? "selected" : ""} key={item.id}><i style={{ background: candidateColor(item.id, candidate.id) }} />{item.label}</span>)}
        </div>
      </div>
      <div className="heat-time-basis">
        <label>
          <span>時間基準</span>
          <select
            aria-label="ヒートパターンの時間基準"
            value={candidate.heatTimeBasis}
            onChange={(event) => onTimeBasisChange(event.target.value as HeatTimeBasis)}
          >
            <option
              value="line_speed"
              disabled={!Number.isFinite(candidate.raw.inputs.process.ls_mpm) || Number(candidate.raw.inputs.process.ls_mpm) <= 0}
            >
              ライン速度連動
            </option>
            <option value="elapsed_time">経過時間を直接指定</option>
          </select>
        </label>
        <small>
          {candidate.heatTimeBasis === "line_speed"
            ? "LSを変えると現在の各点を設備位置とみなし、全時刻を再計算します。"
            : "時間を直接編集します。ヒートパターンがあればLSは未設定でも予測できます。"}
        </small>
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="heat-chart"
        role="img"
        aria-label="候補を重ねたヒートパターン。選択候補の温度点をドラッグして編集できます。"
      >
        <g className="grid-lines">
          {[0, .25, .5, .75, 1].map((ratio) => Math.round(maxTemp * ratio / 50) * 50).map((value) => (
            <g key={value}>
              <line x1={pad.left} x2={width - pad.right} y1={y(value)} y2={y(value)} />
              <text x={pad.left - 6} y={y(value) + 4} textAnchor="end">
                {value}
              </text>
            </g>
          ))}
          {timeTicks.map((value, index) => (
            <g key={`time-${value}`}>
              <line x1={x(value)} x2={x(value)} y1={pad.top} y2={height - pad.bottom} />
              <text
                x={x(value)}
                y={height - 25}
                textAnchor={index === 0 ? "start" : index === timeTicks.length - 1 ? "end" : "middle"}
              >
                {number(value, timeTickDigits)}
              </text>
            </g>
          ))}
        </g>
        {candidates
          .filter((item) => item.id !== candidate.id)
          .map((item) => (
            <g key={item.id}>
              <polyline
                points={item.heat.map((point) => `${x(point.time)},${y(point.temperature)}`).join(" ")}
                fill="none"
                stroke={candidateColor(item.id, candidate.id)}
                strokeWidth="1.5"
                opacity=".62"
              />
              {item.heat.map((point, index) => <circle
                className="svg-chart-hit-target"
                tabIndex={-1}
                aria-label={`${item.label}: ${number(point.time, 2)}分, ${point.temperature}度`}
                key={`${item.id}-${point.time}-${index}`}
                cx={x(point.time)} cy={y(point.temperature)} r="7" fill="transparent"
                onMouseEnter={() => setHoveredHeatPoint({ x: x(point.time), y: y(point.temperature), lines: [item.label, `時間 ${number(point.time, 2)} min`, `温度 ${number(point.temperature, 0)} °C`] })}
                onMouseLeave={() => setHoveredHeatPoint(null)}
                onFocus={() => setHoveredHeatPoint({ x: x(point.time), y: y(point.temperature), lines: [item.label, `時間 ${number(point.time, 2)} min`, `温度 ${number(point.temperature, 0)} °C`] })}
                onBlur={() => setHoveredHeatPoint(null)}
              />)}
            </g>
          ))}
        <polyline
          points={points}
          fill="none"
          stroke={candidateColor(candidate.id, candidate.id)}
          strokeWidth="3"
        />
        {candidate.heat.map((point, index) => (
          <circle
            tabIndex={0}
            aria-label={`${number(point.time, 2)}分, ${point.temperature}度`}
            key={`${point.time}-${index}`}
            cx={x(point.time)}
            cy={y(point.temperature)}
            r="5"
            fill="#1F5FC4"
            onMouseEnter={() => setHoveredHeatPoint({ x: x(point.time), y: y(point.temperature), lines: [candidate.label, point.stageName || point.stageCategory || `点 ${index + 1}`, `時間 ${number(point.time, 2)} min`, `温度 ${number(point.temperature, 0)} °C`] })}
            onMouseLeave={() => setHoveredHeatPoint(null)}
            onFocus={() => setHoveredHeatPoint({ x: x(point.time), y: y(point.temperature), lines: [candidate.label, point.stageName || point.stageCategory || `点 ${index + 1}`, `時間 ${number(point.time, 2)} min`, `温度 ${number(point.temperature, 0)} °C`] })}
            onBlur={() => setHoveredHeatPoint(null)}
            onPointerDown={(event) => {
              event.currentTarget.setPointerCapture(event.pointerId);
              dragPoint(event, index);
            }}
            onPointerMove={(event) =>
              event.currentTarget.hasPointerCapture(event.pointerId) &&
              dragPoint(event, index)
            }
          />
        ))}
        {hoveredHeatPoint && <SvgChartTooltip {...hoveredHeatPoint} chartWidth={width} chartHeight={height} />}
        <text className="axis-title" x={pad.left} y="12">
          温度（°C）
        </text>
        <text
          className="axis-title"
          x={(pad.left + width - pad.right) / 2}
          y={height - 5}
          textAnchor="middle"
        >
          時間（min）
        </text>
      </svg>
      <div className="heat-edit">
        <div>
          <b>ヒートパターン編集</b>
          <span>点をドラッグ、または数値を編集</span>
          <button className="text-button" onClick={onAdd}>
            点を追加
          </button>
        </div>
        <div className="heat-point-table-wrap">
          <table className="heat-point-table">
            <thead>
              <tr><th>#</th><th>工程名</th><th>時間 <small>min</small></th><th>温度 <small>°C</small></th><th aria-label="操作" /></tr>
            </thead>
            <tbody>
              {candidate.heat.map((point, index) => (
                <tr key={`${point.time}-${index}`}>
                  <th scope="row">{index + 1}</th>
                  <td><input type="text" value={point.stageName ?? point.stageCategory ?? ""} aria-label={`点${index + 1}の工程名`} onChange={(event) => onUpdate(index, "stageName", event.target.value)} /></td>
                  <td><input type="number" step="0.01" disabled={candidate.heatTimeBasis === "line_speed"} value={Number(point.time.toFixed(3))} aria-label={`点${index + 1}の時間（分）`} onChange={(event) => onUpdate(index, "time", Number(event.target.value))} /></td>
                  <td><input type="number" value={point.temperature} aria-label={`点${index + 1}の温度（℃）`} onChange={(event) => onUpdate(index, "temperature", Number(event.target.value))} /></td>
                  <td><button className="icon-delete" aria-label={`点${index + 1}を削除`} disabled={candidate.heat.length <= 2} onClick={() => onDelete(index)}>×</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <small>RT = 室温（25°C）</small>
      </div>
    </section>
  );
}
