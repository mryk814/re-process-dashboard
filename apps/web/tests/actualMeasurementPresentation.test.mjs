import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
  actualDifference,
  actualMeasurementErrorMessage,
  measurementMetadata,
  signedDifference,
} from "../src/features/workbench/actualMeasurementPresentation.ts";

test("prediction and actual remain directionally distinct", () => {
  assert.equal(actualDifference(510, 500), 10);
  assert.equal(actualDifference(490, 500), -10);
  assert.equal(signedDifference(10, String), "+10");
  assert.equal(signedDifference(-10, String), "−10");
});

test("measurement metadata preserves experiment identity and repetition", () => {
  assert.deepEqual(measurementMetadata({
    experiment_no: "EXP-42",
    measured_at: "2026-07-25",
    replicates: 3,
    std: 2.5,
    note: "再試験",
  }), [
    "実験 EXP-42",
    "測定日 2026-07-25",
    "n=3",
    "標準偏差 2.5",
    "再試験",
  ]);
});

test("actual measurement errors preserve API meaning but never expose transport text", () => {
  assert.equal(actualMeasurementErrorMessage({
    name: "ApiClientError",
    kind: "validation",
    message: "単位は MPa です",
  }, "登録できませんでした。"), "単位は MPa です");
  assert.match(actualMeasurementErrorMessage({
    name: "ApiClientError",
    kind: "network",
    message: "Failed to fetch",
  }, "登録できませんでした。"), /APIへ接続できませんでした/);
  assert.equal(
    actualMeasurementErrorMessage(new TypeError("Failed to fetch"), "登録できませんでした。"),
    "登録できませんでした。",
  );
});

test("candidate workbench exposes the actual panel through the declared Surface", async () => {
  const [page, registry] = await Promise.all([
    readFile(new URL("../src/features/workbench/WorkbenchPage.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/features/workbench/workbenchSurfaceRegistry.ts", import.meta.url), "utf8"),
  ]);
  assert.match(registry, /actual_measurement: \{ zone: "before_activity"/);
  assert.match(page, /beforeActivitySurfaces\.map/);
  assert.match(page, /case "actual_measurement"/);
  assert.match(page, /<ActualMeasurementPanel/);
});

test("actual panel compares against the immutable registered snapshot", async () => {
  const source = await readFile(
    new URL("../src/features/workbench/ActualMeasurementPanel.tsx", import.meta.url),
    "utf8",
  );
  assert.match(source, /comparison\.prediction\.predictions\[actual\.property\]/);
  assert.doesNotMatch(source, /previewsByCandidate|previewCandidate/);
  assert.match(source, /現在の候補やPackageが変わっても自動更新しません/);
});

test("actual panel asks for task-declared event and ordinal labels, not a regression number", async () => {
  const source = await readFile(
    new URL("../src/features/workbench/ActualMeasurementPanel.tsx", import.meta.url),
    "utf8",
  );
  assert.match(source, /targetKind === "binary"/);
  assert.match(source, /selectedOutput\?\.binary\?\.event_label/);
  assert.match(source, /targetKind === "ordinal"/);
  assert.match(source, /selectedOutput\?\.ordinal\?\.categories/);
  assert.match(source, /targetKind === "count" \? "1" : "any"/);
  assert.match(source, /actual\.value_label/);
});
