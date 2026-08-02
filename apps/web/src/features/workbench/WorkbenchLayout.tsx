import { type KeyboardEvent, useRef } from "react";

export const workbenchLayoutStorage = {
  inspectorWidth: "material-workbench:layout:inspector-width:v1",
  inspectorCollapsed: "material-workbench:layout:inspector-collapsed:v1",
  candidateInspectorCollapsed: "material-workbench:layout:candidate-inspector-collapsed:v2",
  curveShare: "material-workbench:layout:curve-share:v1",
  comparisonHeight: "material-workbench:layout:comparison-height:v1",
  reviewComparisonHeight: "material-workbench:layout:review-comparison-height:v1",
  exploreComparisonHeight: "material-workbench:layout:explore-comparison-height:v1",
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

export function storedLayoutBoolean(key: string, fallback: boolean) {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    if (raw === "true") return true;
    if (raw === "false") return false;
    return fallback;
  } catch {
    return fallback;
  }
}

export function saveLayoutBoolean(key: string, value: boolean) {
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
  dragMin = min,
  max,
  step,
  orientation = "vertical",
  onChange,
  onDragChange,
  onDrag,
  onDragEnd,
  onDragCancel,
  onReset,
}: {
  className: string;
  label: string;
  value: number;
  min: number;
  dragMin?: number;
  max: number;
  step: number;
  orientation?: "vertical" | "horizontal";
  onChange: (value: number) => void;
  onDragChange?: (value: number) => void;
  onDrag: (startValue: number, delta: number) => number;
  onDragEnd?: (value: number) => void;
  onDragCancel?: () => void;
  onReset: () => void;
}) {
  const drag = useRef<{ pointerId: number; startPosition: number; startValue: number; latestValue: number } | null>(null);
  const changeByKeyboard = (event: KeyboardEvent<HTMLDivElement>) => {
    const amount = event.shiftKey ? step * 4 : step;
    const decreaseKey = orientation === "vertical" ? "ArrowLeft" : "ArrowUp";
    const increaseKey = orientation === "vertical" ? "ArrowRight" : "ArrowDown";
    const next = event.key === decreaseKey
      ? value - amount
      : event.key === increaseKey
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
      aria-orientation={orientation}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={Math.round(value)}
      title={`ドラッグで${orientation === "vertical" ? "幅" : "高さ"}を調整・ダブルクリックで初期${orientation === "vertical" ? "幅" : "高さ"}`}
      onDoubleClick={onReset}
      onKeyDown={changeByKeyboard}
      onPointerDown={(event) => {
        drag.current = {
          pointerId: event.pointerId,
          startPosition: orientation === "vertical" ? event.clientX : event.clientY,
          startValue: value,
          latestValue: value,
        };
        event.currentTarget.setPointerCapture(event.pointerId);
      }}
      onPointerMove={(event) => {
        const current = drag.current;
        if (!current || current.pointerId !== event.pointerId || !event.currentTarget.hasPointerCapture(event.pointerId)) return;
        const position = orientation === "vertical" ? event.clientX : event.clientY;
        const nextValue = clampLayoutValue(onDrag(current.startValue, position - current.startPosition), dragMin, max);
        current.latestValue = nextValue;
        (onDragChange ?? onChange)(nextValue);
      }}
      onPointerUp={(event) => {
        const current = drag.current;
        drag.current = null;
        if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
        if (current?.pointerId === event.pointerId) onDragEnd?.(current.latestValue);
      }}
      onPointerCancel={() => {
        if (drag.current) onDragCancel?.();
        drag.current = null;
      }}
      onLostPointerCapture={() => {
        if (drag.current) onDragCancel?.();
        drag.current = null;
      }}
    ><span aria-hidden="true" /></div>
  );
}
