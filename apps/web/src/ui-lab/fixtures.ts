import type {
  LabCandidate,
  LabCurveSeries,
  LabFixture,
  LabPreview,
  LabTaskDefinition,
} from "./types";

const annealedTask: LabTaskDefinition = {
  id: "lab-annealed-properties",
  label: "焼鈍後特性",
  description: "組成・板厚・ライン速度・ヒートパターンから複数特性を比較するfixture",
  fields: [
    { path: "composition.C", label: "C", unit: "mass%", group: "composition", min: 0.01, max: 0.35, step: 0.005 },
    { path: "composition.Si", label: "Si", unit: "mass%", group: "composition", min: 0, max: 2, step: 0.02 },
    { path: "composition.Mn", label: "Mn", unit: "mass%", group: "composition", min: 0.2, max: 3, step: 0.02 },
    { path: "composition.Cr", label: "Cr", unit: "mass%", group: "composition", min: 0, max: 1.5, step: 0.01 },
    { path: "composition.Mo", label: "Mo", unit: "mass%", group: "composition", min: 0, max: 0.5, step: 0.005 },
    { path: "process.thickness_mm", label: "板厚", unit: "mm", group: "process", min: 0.4, max: 3.2, step: 0.05 },
    { path: "process.line_speed_m_min", label: "ライン速度", unit: "m/min", group: "process", min: 30, max: 180, step: 1 },
    { path: "process.peak_temperature_c", label: "最高温度", unit: "°C", group: "process", min: 650, max: 920, step: 2 },
    { path: "process.hold_time_s", label: "高温保持", unit: "s", group: "process", min: 0, max: 240, step: 5 },
    { path: "categorical.coating", label: "メッキ", group: "categorical", choices: ["なし", "GI", "GA"] },
  ],
  outputs: [
    { key: "TS", label: "引張強さ", unit: "MPa", goalDirection: "at_least" },
    { key: "YS", label: "降伏強さ", unit: "MPa", goalDirection: "at_least" },
    { key: "EL", label: "伸び", unit: "%", goalDirection: "at_least" },
    { key: "lambda", label: "穴広げ率 λ", unit: "%", goalDirection: "at_least" },
  ],
  capabilities: {
    uncertainty: true,
    support: true,
    responseCurves: true,
    similarity: true,
    detailedPrediction: true,
  },
};

const hotRollingTask: LabTaskDefinition = {
  id: "lab-hot-rolled-properties",
  label: "熱延後特性",
  description: "組成と熱延条件からTSを検討する単一出力fixture",
  fields: [
    { path: "composition.C", label: "C", unit: "mass%", group: "composition", min: 0.01, max: 0.35, step: 0.005 },
    { path: "composition.Mn", label: "Mn", unit: "mass%", group: "composition", min: 0.2, max: 3, step: 0.02 },
    { path: "composition.Nb", label: "Nb", unit: "mass%", group: "composition", min: 0, max: 0.12, step: 0.002 },
    { path: "composition.Ti", label: "Ti", unit: "mass%", group: "composition", min: 0, max: 0.12, step: 0.002 },
    { path: "process.reheat_temperature_c", label: "加熱温度", unit: "°C", group: "process", min: 950, max: 1300, step: 5 },
    { path: "process.finish_temperature_c", label: "仕上温度", unit: "°C", group: "process", min: 700, max: 1000, step: 5 },
    { path: "process.coiling_temperature_c", label: "巻取温度", unit: "°C", group: "process", min: 350, max: 750, step: 5 },
    { path: "process.cooling_rate_c_s", label: "冷却速度", unit: "°C/s", group: "process", min: 5, max: 120, step: 1 },
    { path: "process.reduction_percent", label: "圧下率", unit: "%", group: "process", min: 30, max: 95, step: 1 },
  ],
  outputs: [{ key: "TS", label: "引張強さ", unit: "MPa", goalDirection: "at_least" }],
  capabilities: {
    uncertainty: true,
    support: true,
    responseCurves: true,
    similarity: true,
    detailedPrediction: true,
  },
};

const annealedCandidates: LabCandidate[] = [
  {
    id: "anneal-a",
    name: "基準案",
    values: {
      "composition.C": 0.08,
      "composition.Si": 0.35,
      "composition.Mn": 1.45,
      "composition.Cr": 0.18,
      "composition.Mo": 0.03,
      "process.thickness_mm": 1.4,
      "process.line_speed_m_min": 103,
      "process.peak_temperature_c": 812,
      "process.hold_time_s": 62,
      "categorical.coating": "GI",
    },
  },
  {
    id: "anneal-b",
    name: "強度寄り",
    values: {
      "composition.C": 0.11,
      "composition.Si": 0.42,
      "composition.Mn": 1.72,
      "composition.Cr": 0.28,
      "composition.Mo": 0.05,
      "process.thickness_mm": 1.2,
      "process.line_speed_m_min": 96,
      "process.peak_temperature_c": 828,
      "process.hold_time_s": 74,
      "categorical.coating": "GA",
    },
  },
  {
    id: "anneal-c",
    name: "延性寄り",
    values: {
      "composition.C": 0.065,
      "composition.Si": 0.28,
      "composition.Mn": 1.22,
      "composition.Cr": 0.12,
      "composition.Mo": 0.02,
      "process.thickness_mm": 1.6,
      "process.line_speed_m_min": 112,
      "process.peak_temperature_c": 798,
      "process.hold_time_s": 84,
      "categorical.coating": "GI",
    },
  },
];

const hotRollingCandidates: LabCandidate[] = [
  {
    id: "hot-a",
    name: "基準条件",
    values: {
      "composition.C": 0.07,
      "composition.Mn": 1.35,
      "composition.Nb": 0.025,
      "composition.Ti": 0.018,
      "process.reheat_temperature_c": 1180,
      "process.finish_temperature_c": 875,
      "process.coiling_temperature_c": 590,
      "process.cooling_rate_c_s": 34,
      "process.reduction_percent": 78,
    },
  },
  {
    id: "hot-b",
    name: "高強度条件",
    values: {
      "composition.C": 0.095,
      "composition.Mn": 1.62,
      "composition.Nb": 0.045,
      "composition.Ti": 0.025,
      "process.reheat_temperature_c": 1210,
      "process.finish_temperature_c": 845,
      "process.coiling_temperature_c": 515,
      "process.cooling_rate_c_s": 55,
      "process.reduction_percent": 84,
    },
  },
  {
    id: "hot-c",
    name: "低温巻取",
    values: {
      "composition.C": 0.08,
      "composition.Mn": 1.48,
      "composition.Nb": 0.035,
      "composition.Ti": 0.02,
      "process.reheat_temperature_c": 1160,
      "process.finish_temperature_c": 860,
      "process.coiling_temperature_c": 455,
      "process.cooling_rate_c_s": 72,
      "process.reduction_percent": 80,
    },
  },
];

function numeric(candidate: LabCandidate, path: string, fallback = 0): number {
  const value = candidate.values[path];
  return typeof value === "number" ? value : fallback;
}

function supportFor(task: LabTaskDefinition, candidate: LabCandidate): Pick<LabPreview, "supportStatus" | "supportMessage" | "nearestLabel" | "warning"> {
  const firstNumeric = task.fields.find((field) => field.group !== "categorical");
  const fraction = firstNumeric && firstNumeric.group !== "categorical"
    ? Math.abs(numeric(candidate, firstNumeric.path) - (firstNumeric.min + firstNumeric.max) / 2) / (firstNumeric.max - firstNumeric.min)
    : 0;
  if (fraction > 0.46) {
    return {
      supportStatus: "extrapolated",
      supportMessage: "学習条件の密度が低い領域です。予測値は探索的に扱ってください。",
      nearestLabel: "近傍条件 3件",
      warning: "入力の一部が通常域から外れています",
    };
  }
  if (fraction > 0.28) {
    return {
      supportStatus: "caution",
      supportMessage: "近傍実験はありますが、条件の密度は高くありません。",
      nearestLabel: "近傍条件 7件",
    };
  }
  return {
    supportStatus: "supported",
    supportMessage: "独立した過去条件の近傍に実測があります。",
    nearestLabel: "近傍条件 14件",
  };
}

export function calculateLabPreview(task: LabTaskDefinition, candidate: LabCandidate): LabPreview {
  const c = numeric(candidate, "composition.C", 0.08);
  const mn = numeric(candidate, "composition.Mn", 1.4);
  const strengthProcess = task.id.includes("hot")
    ? numeric(candidate, "process.cooling_rate_c_s", 30) * 0.8 + (650 - numeric(candidate, "process.coiling_temperature_c", 560)) * 0.18
    : (numeric(candidate, "process.peak_temperature_c", 810) - 760) * 0.35 - numeric(candidate, "process.line_speed_m_min", 100) * 0.08;
  const baseStrength = 330 + c * 1450 + mn * 78 + strengthProcess;
  const support = supportFor(task, candidate);
  const uncertainty = support.supportStatus === "supported" ? 26 : support.supportStatus === "caution" ? 44 : 72;

  const predictions = task.outputs.map((output) => {
    let value = baseStrength;
    if (output.key === "YS") value = baseStrength * 0.74;
    if (output.key === "EL") value = 35 - c * 72 - mn * 2.8 + numeric(candidate, "process.hold_time_s", 60) * 0.018;
    if (output.key === "lambda") value = 92 - c * 140 - mn * 8 + numeric(candidate, "process.peak_temperature_c", 810) * 0.025;
    const goalValue = output.key === "TS" ? 590 : output.key === "YS" ? 420 : output.key === "EL" ? 18 : 45;
    const normalizedDistance = (value - goalValue) / Math.max(uncertainty, 1);
    const goalProbability = Math.max(0.02, Math.min(0.98, 0.5 + normalizedDistance * 0.23));
    return {
      outputKey: output.key,
      value,
      lower: task.capabilities.uncertainty ? value - uncertainty : undefined,
      upper: task.capabilities.uncertainty ? value + uncertainty : undefined,
      goalValue,
      goalProbability,
    };
  });

  return { candidateId: candidate.id, predictions, ...support };
}

export function calculateLabCurves(
  task: LabTaskDefinition,
  candidates: LabCandidate[],
  variablePath: string,
): LabCurveSeries[] {
  const field = task.fields.find((item) => item.path === variablePath && item.group !== "categorical");
  if (!field || field.group === "categorical") return [];
  const values = Array.from({ length: 9 }, (_, index) => field.min + ((field.max - field.min) * index) / 8);
  return candidates.flatMap((candidate) =>
    task.outputs.map((output) => ({
      candidateId: candidate.id,
      outputKey: output.key,
      points: values.map((x) => {
        const changed: LabCandidate = { ...candidate, values: { ...candidate.values, [variablePath]: x } };
        const prediction = calculateLabPreview(task, changed).predictions.find((item) => item.outputKey === output.key)!;
        return { x, value: prediction.value, lower: prediction.lower, upper: prediction.upper };
      }),
    })),
  );
}

function fixture(task: LabTaskDefinition, candidates: LabCandidate[]): LabFixture {
  return {
    task,
    candidates,
    previews: Object.fromEntries(candidates.map((candidate) => [candidate.id, calculateLabPreview(task, candidate)])),
  };
}

export const LAB_FIXTURES: Record<"annealed" | "hotRolling", LabFixture> = {
  annealed: fixture(annealedTask, annealedCandidates),
  hotRolling: fixture(hotRollingTask, hotRollingCandidates),
};
