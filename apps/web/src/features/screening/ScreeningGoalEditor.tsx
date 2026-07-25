export type ScreeningGoalDirection = "at_least" | "at_most" | "between";

export type ScreeningGoalDraft = {
  direction: ScreeningGoalDirection;
  lower: string;
  upper: string;
};

export type ScreeningGoalPayload = {
  direction: ScreeningGoalDirection;
  lower?: number | null;
  upper?: number | null;
};

export function emptyScreeningGoal(direction: ScreeningGoalDirection): ScreeningGoalDraft {
  return { direction, lower: "", upper: "" };
}

export function screeningGoalFromDraft(draft: ScreeningGoalDraft): ScreeningGoalPayload | null {
  const lower = draft.lower.trim();
  const upper = draft.upper.trim();
  if (!lower && !upper) return null;
  if (draft.direction === "at_least") {
    if (!lower || !Number.isFinite(Number(lower))) throw new Error("下限を数値で指定してください。");
    return { direction: "at_least", lower: Number(lower) };
  }
  if (draft.direction === "at_most") {
    if (!upper || !Number.isFinite(Number(upper))) throw new Error("上限を数値で指定してください。");
    return { direction: "at_most", upper: Number(upper) };
  }
  if (!lower || !upper || !Number.isFinite(Number(lower)) || !Number.isFinite(Number(upper))) {
    throw new Error("範囲の下限と上限を数値で指定してください。");
  }
  if (Number(lower) >= Number(upper)) throw new Error("範囲は下限より上限を大きくしてください。");
  return { direction: "between", lower: Number(lower), upper: Number(upper) };
}

export function ScreeningGoalEditor({
  label,
  unit,
  value,
  onChange,
}: {
  label: string;
  unit: string;
  value: ScreeningGoalDraft;
  onChange: (next: ScreeningGoalDraft) => void;
}) {
  const bound = (key: "lower" | "upper", next: string) => onChange({ ...value, [key]: next });
  return (
    <fieldset className="screening-goal-editor">
      <legend>{label}</legend>
      <select
        aria-label={`${label}の判定ルール`}
        value={value.direction}
        onChange={(event) => onChange({ ...value, direction: event.target.value as ScreeningGoalDirection })}
      >
        <option value="at_least">下限以上</option>
        <option value="at_most">上限以下</option>
        <option value="between">範囲内</option>
      </select>
      {value.direction !== "at_most" && (
        <label>
          下限{unit ? ` (${unit})` : ""}
          <input
            aria-label={`${label}の下限`}
            type="number"
            value={value.lower}
            placeholder="指定なし"
            onChange={(event) => bound("lower", event.target.value)}
          />
        </label>
      )}
      {value.direction !== "at_least" && (
        <label>
          上限{unit ? ` (${unit})` : ""}
          <input
            aria-label={`${label}の上限`}
            type="number"
            value={value.upper}
            placeholder="指定なし"
            onChange={(event) => bound("upper", event.target.value)}
          />
        </label>
      )}
    </fieldset>
  );
}
