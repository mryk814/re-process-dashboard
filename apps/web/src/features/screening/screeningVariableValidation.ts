export type ScreeningVariableDraft = {
  mode: "fixed" | "range" | "list";
  first: string;
  second: string;
};

export type ScreeningVariableConstraint = {
  categorical: boolean;
  choices: string[];
  allowedRange?: { min: number; max: number };
};

export function screeningVariableError(
  row: ScreeningVariableDraft,
  constraint: ScreeningVariableConstraint,
) {
  const values = row.first.split(",").map((value) => value.trim()).filter(Boolean);
  if (!row.first.trim()) {
    return row.mode === "list" ? "値を1つ以上入力してください。" : "値を入力してください。";
  }
  if (row.mode === "list" && values.length === 0) return "値を1つ以上入力してください。";

  if (constraint.categorical) {
    const unknown = values.find((value) => !constraint.choices.includes(value));
    return unknown ? `選択肢にない値「${unknown}」が含まれています。` : null;
  }

  if (row.mode === "list" && values.some((value) => !Number.isFinite(Number(value)))) {
    return "列挙値はカンマ区切りの数値で入力してください。";
  }
  if (row.mode !== "list" && !Number.isFinite(Number(row.first))) return "数値を入力してください。";
  if (row.mode === "range") {
    if (!row.second.trim() || !Number.isFinite(Number(row.second))) {
      return "最大値を数値で入力してください。";
    }
    if (Number(row.first) >= Number(row.second)) return "最小値は最大値より小さくしてください。";
  }

  if (constraint.allowedRange) {
    const checked = row.mode === "range"
      ? [Number(row.first), Number(row.second)]
      : values.map(Number);
    if (checked.some((value) => value < constraint.allowedRange!.min || value > constraint.allowedRange!.max)) {
      return `許容範囲 ${constraint.allowedRange.min}–${constraint.allowedRange.max} 内で入力してください。`;
    }
  }
  return null;
}
