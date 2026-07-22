type SvgChartTooltipProps = {
  x: number;
  y: number;
  lines: string[];
  chartWidth: number;
  chartHeight: number;
};

export function SvgChartTooltip({ x, y, lines, chartWidth, chartHeight }: SvgChartTooltipProps) {
  const groupRef = useRef<SVGGElement>(null);
  const [scale, setScale] = useState({ x: 1, y: 1 });
  useLayoutEffect(() => {
    const svg = groupRef.current?.closest("svg");
    if (!svg) return;
    const updateScale = () => {
      const matrix = svg.getScreenCTM();
      if (!matrix) return;
      setScale({
        x: Math.max(Math.hypot(matrix.a, matrix.b), 0.001),
        y: Math.max(Math.hypot(matrix.c, matrix.d), 0.001),
      });
    };
    const observer = new ResizeObserver(updateScale);
    observer.observe(svg);
    updateScale();
    return () => observer.disconnect();
  }, []);
  const displayLines = lines.map((line) => {
    const characters = Array.from(line);
    return characters.length > 31 ? `${characters.slice(0, 30).join("")}…` : line;
  });
  const width = Math.min(210, Math.max(88, ...displayLines.map((line) => Array.from(line).length * 6.4 + 16)));
  const height = displayLines.length * 14 + 10;
  const anchorX = x * scale.x;
  const anchorY = y * scale.y;
  const chartPixelWidth = chartWidth * scale.x;
  const chartPixelHeight = chartHeight * scale.y;
  const left = Math.min(Math.max(4 - anchorX, 9), chartPixelWidth - anchorX - width - 4);
  const preferredTop = -height - 9;
  const top = anchorY + preferredTop >= 4
    ? preferredTop
    : Math.min(chartPixelHeight - anchorY - height - 4, 9);
  return (
    <g
      ref={groupRef}
      className="svg-chart-tooltip"
      aria-hidden="true"
      pointerEvents="none"
      transform={`translate(${x} ${y}) scale(${1 / scale.x} ${1 / scale.y})`}
    >
      <rect x={left} y={top} width={width} height={height} rx="4" />
      {displayLines.map((line, index) => (
        <text key={`${index}-${line}`} x={left + 8} y={top + 15 + index * 14}>{line}</text>
      ))}
    </g>
  );
}
import { useLayoutEffect, useRef, useState } from "react";
