import test from "node:test";
import assert from "node:assert/strict";
import {
  screeningExecutionFailure,
  screeningFailureFieldLabel,
} from "../src/features/screening/screeningExecutionFailure.ts";

test("screening validation failures retain actionable API details", () => {
  const failure = screeningExecutionFailure({
    name: "ApiClientError",
    kind: "validation",
    message: "最小値は学習可能な範囲にありません",
    fieldErrors: [
      { path: "body.variables.composition.C.min", message: "0以上にしてください" },
    ],
  });

  assert.equal(failure.kind, "validation");
  assert.equal(failure.title, "入力条件を確認してください");
  assert.equal(failure.message, "最小値は学習可能な範囲にありません");
  assert.equal(failure.persistence, "not_saved");
  assert.deepEqual(failure.fieldErrors, [
    { path: "body.variables.composition.C.min", message: "0以上にしてください" },
  ]);
  assert.equal(
    screeningFailureFieldLabel(
      failure.fieldErrors[0].path,
      new Map([["composition.C", "炭素量 (mass%)"]]),
    ),
    "探索変数「炭素量 (mass%)」",
  );
});

test("screening failures never expose transport or unexpected exception text", () => {
  assert.deepEqual(screeningExecutionFailure({
    name: "ApiClientError",
    kind: "network",
    message: "Failed to fetch 127.0.0.1:8765",
  }), {
    kind: "network",
    title: "APIに接続できませんでした",
    message: "応答を受け取る前にRunが保存された可能性があります。先に保存済みRunを確認してください。",
    fieldErrors: [],
    persistence: "unknown",
  });

  const unexpected = screeningExecutionFailure(new Error("sqlite path C:\\private\\workbench.db"));
  assert.equal(unexpected.kind, "execution");
  assert.doesNotMatch(unexpected.message, /sqlite|private|workbench\.db/);
  assert.equal(unexpected.persistence, "unknown");
});

test("screening field errors match complete input paths before overlapping prefixes", () => {
  const labels = new Map([
    ["composition.C", "炭素"],
    ["composition.Cr", "クロム"],
  ]);
  assert.equal(
    screeningFailureFieldLabel("body.variables.composition.Cr.min", labels),
    "探索変数「クロム」",
  );
});
