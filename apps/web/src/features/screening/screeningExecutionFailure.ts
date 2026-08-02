export type ScreeningExecutionFailure = {
  kind: "validation" | "network" | "execution";
  title: string;
  message: string;
  fieldErrors: Array<{ path: string; message: string }>;
  persistence: "not_saved" | "unknown";
};

function property(value: unknown, key: string): unknown {
  return typeof value === "object" && value !== null ? Reflect.get(value, key) : undefined;
}

export function screeningExecutionFailure(cause: unknown): ScreeningExecutionFailure {
  const isApiError = property(cause, "name") === "ApiClientError";
  const kind = property(cause, "kind");
  const message = property(cause, "message");
  const fieldErrors = property(cause, "fieldErrors");

  if (isApiError && kind === "network") {
    return {
      kind: "network",
      title: "APIに接続できませんでした",
      message: "応答を受け取る前にRunが保存された可能性があります。先に保存済みRunを確認してください。",
      fieldErrors: [],
      persistence: "unknown",
    };
  }

  if (isApiError && (kind === "validation" || kind === "conflict")) {
    return {
      kind: "validation",
      title: "入力条件を確認してください",
      message: typeof message === "string" && message.trim()
        ? message
        : "入力条件に実行できない項目があります。",
      fieldErrors: Array.isArray(fieldErrors)
        ? fieldErrors.filter(
            (item): item is { path: string; message: string } =>
              typeof item === "object"
              && item !== null
              && typeof Reflect.get(item, "path") === "string"
              && typeof Reflect.get(item, "message") === "string",
          )
        : [],
      persistence: "not_saved",
    };
  }

  return {
    kind: "execution",
    title: "範囲探索を完了できませんでした",
    message: "Runが保存されたか確認できません。先に保存済みRunを確認してください。",
    fieldErrors: [],
    persistence: "unknown",
  };
}

export function screeningFailureFieldLabel(
  path: string,
  inputLabels: ReadonlyMap<string, string>,
): string {
  const matchedInput = [...inputLabels.entries()]
    .sort(([left], [right]) => right.length - left.length)
    .find(([key]) => (
      path === key
      || path.startsWith(`${key}.`)
      || path.endsWith(`.${key}`)
      || path.includes(`.${key}.`)
    ));
  if (matchedInput) return `探索変数「${matchedInput[1]}」`;
  if (path.includes("target_goal")) return "主目標";
  if (path.includes("secondary_goals")) return "副条件";
  if (path.includes("proposal")) return "提案設定";
  if (path.includes("batch_definition")) return "実験バッチ設定";
  if (path.includes("base_")) return "探索の基準候補";
  return "入力条件";
}
