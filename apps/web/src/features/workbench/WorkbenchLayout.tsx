import { type KeyboardEvent, useRef } from "react";

export const workbenchLayoutStorage = {
  inspectorWidth: "material-workbench:layout:inspector-width:v1",
  curveShare: "material-workbench:layout:curve-share:v1",
} as const;

export function clampLayoutValue(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

export function storedLayoutNumber(key: string, fallback: number) {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    if (raw === null) return fallback;
    const value = Number(raw);
    return Number.isFinite(value) ? value : fallback;
  } catch {
    return fallback;
  }
}

export function saveLayoutNumber(key: string, value: number) {
  try {
    window.localStorage.setItem(key, String(value));
  } catch {
    // Layout persistence is optional when local storage is unavailable.
  }
}

export function SplitResizer({
  className,
  label,
  value,
  min,
  max,
  step,
  onChange,
  onDrag,
  onReset,
}: {
  className: string;
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
  onDrag: (startValue: number, deltaX: number) => number;
  onReset: () => void;
}) {
  const drag = useRef<{ pointerId: number; startX: number; startValue: number } | null>(null);
  const changeByKeyboard = (event: KeyboardEvent<HTMLDivElement>) => {
    const amount = event.shiftKey ? step * 4 : step;
    const next = event.key === "ArrowLeft"
      ? value - amount
      : event.key === "ArrowRight"
        ? value + amount
        : event.key === "Home"
          ? min
          : event.key === "End"
            ? max
            : null;
    if (next === null) return;
    event.preventDefault();
    onChange(clampLayoutValue(next, min, max));
  };
  return (
    <div
      className={`split-resizer ${className}`}
      role="separator"
      tabIndex={0}
      aria-label={label}
      aria-orientation="vertical"
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={Math.round(value)}
      title="ドラッグで幅を調整・ダブルクリックで初期幅"
      onDoubleClick={onReset}
      onKeyDown={changeByKeyboard}
      onPointerDown={(event) => {
        drag.current = { pointerId: event.pointerId, startX: event.clientX, startValue: value };
        event.currentTarget.setPointerCapture(event.pointerId);
      }}
      onPointerMove={(event) => {
        const current = drag.current;
        if (!current || current.pointerId !== event.pointerId || !event.currentTarget.hasPointerCapture(event.pointerId)) return;
        onChange(clampLayoutValue(onDrag(current.startValue, event.clientX - current.startX), min, max));
      }}
      onPointerUp={(event) => {
        if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
        drag.current = null;
      }}
      onPointerCancel={() => { drag.current = null; }}
      onLostPointerCapture={() => { drag.current = null; }}
    ><span aria-hidden="true" /></div>
  );
}
