import type { ApiScreeningRun } from "../../shared/api/workbench-api";

export const SCREENING_INTERPOLATION_METHOD = "inverse_distance_weighted_display";
export const SCREENING_INTERPOLATION_VERSION = "1.0.0";
export const SCREENING_INTERPOLATION_COLUMNS = 18;
export const SCREENING_INTERPOLATION_ROWS = 12;

type PoolPoint = NonNullable<ApiScreeningRun["proposal_pool"]>[number];

export type ScreeningInterpolationCell = {
  column: number;
  row: number;
  value: number;
  neighborCount: number;
};

export type ScreeningInterpolationResult =
  | {
      available: true;
      method: typeof SCREENING_INTERPOLATION_METHOD;
      version: typeof SCREENING_INTERPOLATION_VERSION;
      columns: number;
      rows: number;
      xMin: number;
      xMax: number;
      yMin: number;
      yMax: number;
      valueMin: number;
      valueMax: number;
      coveredCellCount: number;
      cells: ScreeningInterpolationCell[];
    }
  | {
      available: false;
      reason:
        | "legacy_evidence"
        | "requires_two_numeric_ranges"
        | "requires_fixed_slice"
        | "constraint_holes"
        | "incomplete_evaluated_pool"
        | "metric_not_in_evaluated_pool"
        | "sparse_evaluated_pool";
      message: string;
    };

type NumericPoolPoint = {
  x: number;
  y: number;
  value: number;
};

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function poolMetric(
  point: PoolPoint,
  metric: string,
  target: string,
): number | null {
  if (metric === "score") return finiteNumber(point.acquisition_score);
  if (metric === target) return finiteNumber(point.acquisition_components.mean);
  return null;
}

function normalizedDistance(
  point: NumericPoolPoint,
  x: number,
  y: number,
  xSpan: number,
  ySpan: number,
) {
  return Math.hypot((point.x - x) / xSpan, (point.y - y) / ySpan);
}

export function buildScreeningInterpolation(
  run: ApiScreeningRun,
  xAxis: string,
  yAxis: string,
  metric: string,
): ScreeningInterpolationResult {
  const diagnostics = run.proposal_diagnostics;
  const pool = run.proposal_pool ?? [];
  const rejections = run.proposal_rejections ?? [];
  if (!diagnostics) {
    return {
      available: false,
      reason: "legacy_evidence",
      message: "この保存結果には、補間範囲を判定する全評価点の記録がありません。",
    };
  }

  const varying = Object.entries(run.variables)
    .filter(([, spec]) => spec.mode !== "fixed");
  if (
    varying.length !== 2
    || xAxis === yAxis
    || !varying.every(([, spec]) => spec.mode === "range")
    || !varying.every(([field]) => field === xAxis || field === yAxis)
  ) {
    return {
      available: false,
      reason: varying.length > 2
        ? "requires_fixed_slice"
        : "requires_two_numeric_ranges",
      message: varying.length > 2
        ? "3変数以上を同時に動かした結果は固定断面ではありません。残りの変数を固定して再実行すると地図を描けます。"
        : "地図は、範囲指定した2つの数値変数だけを動かした結果で表示できます。",
    };
  }

  if (diagnostics.rejected_count > 0 || rejections.length > 0) {
    return {
      available: false,
      reason: "constraint_holes",
      message: "制約で除外された領域の位置を安全に復元できないため、補間せず評価点を表示します。",
    };
  }

  if (
    pool.length !== diagnostics.evaluated_count
    || diagnostics.evaluated_count !== diagnostics.valid_count
  ) {
    return {
      available: false,
      reason: "incomplete_evaluated_pool",
      message: "全評価点が保存されていないため、補間せず評価点を表示します。",
    };
  }

  const points = pool.flatMap((point) => {
    const x = finiteNumber(point.inputs[xAxis]);
    const y = finiteNumber(point.inputs[yAxis]);
    const value = poolMetric(point, metric, run.target);
    return x == null || y == null || value == null ? [] : [{ x, y, value }];
  });
  if (points.length !== pool.length) {
    return {
      available: false,
      reason: "metric_not_in_evaluated_pool",
      message: "選んだ色の値は全評価点に保存されていないため、点表示で確認します。",
    };
  }

  const xValues = points.map((point) => point.x);
  const yValues = points.map((point) => point.y);
  const xMin = Math.min(...xValues);
  const xMax = Math.max(...xValues);
  const yMin = Math.min(...yValues);
  const yMax = Math.max(...yValues);
  const xSpan = xMax - xMin;
  const ySpan = yMax - yMin;
  const uniqueX = new Set(xValues.map((value) => value.toPrecision(12))).size;
  const uniqueY = new Set(yValues.map((value) => value.toPrecision(12))).size;
  if (
    points.length < 24
    || uniqueX < 4
    || uniqueY < 4
    || xSpan <= 0
    || ySpan <= 0
  ) {
    return {
      available: false,
      reason: "sparse_evaluated_pool",
      message: "評価点が疎なため、面を補わず点表示で確認します。",
    };
  }

  const cells: ScreeningInterpolationCell[] = [];
  for (let row = 0; row < SCREENING_INTERPOLATION_ROWS; row += 1) {
    const y = yMin + (ySpan * (row + 0.5)) / SCREENING_INTERPOLATION_ROWS;
    for (let column = 0; column < SCREENING_INTERPOLATION_COLUMNS; column += 1) {
      const x = xMin + (xSpan * (column + 0.5)) / SCREENING_INTERPOLATION_COLUMNS;
      const neighbors = points
        .map((point) => ({
          point,
          distance: normalizedDistance(point, x, y, xSpan, ySpan),
        }))
        .sort((left, right) => left.distance - right.distance)
        .slice(0, 4);
      if (
        neighbors.length < 4
        || neighbors[0].distance > 0.18
        || neighbors[3].distance > 0.34
      ) {
        continue;
      }
      const exact = neighbors.find((neighbor) => neighbor.distance < 1e-9);
      const weighted = exact
        ? exact.point.value
        : neighbors.reduce(
            (accumulator, neighbor) => {
              const weight = 1 / Math.max(1e-9, neighbor.distance ** 2);
              return {
                sum: accumulator.sum + neighbor.point.value * weight,
                weight: accumulator.weight + weight,
              };
            },
            { sum: 0, weight: 0 },
          );
      cells.push({
        column,
        row,
        value: typeof weighted === "number" ? weighted : weighted.sum / weighted.weight,
        neighborCount: neighbors.length,
      });
    }
  }

  const minimumCoveredCells = Math.ceil(
    SCREENING_INTERPOLATION_COLUMNS * SCREENING_INTERPOLATION_ROWS * 0.55,
  );
  if (cells.length < minimumCoveredCells) {
    return {
      available: false,
      reason: "sparse_evaluated_pool",
      message: "評価点の間隔が広く、安全に補間できる面積が少ないため点表示に戻しました。",
    };
  }

  const values = cells.map((cell) => cell.value);
  return {
    available: true,
    method: SCREENING_INTERPOLATION_METHOD,
    version: SCREENING_INTERPOLATION_VERSION,
    columns: SCREENING_INTERPOLATION_COLUMNS,
    rows: SCREENING_INTERPOLATION_ROWS,
    xMin,
    xMax,
    yMin,
    yMax,
    valueMin: Math.min(...values),
    valueMax: Math.max(...values),
    coveredCellCount: cells.length,
    cells,
  };
}
