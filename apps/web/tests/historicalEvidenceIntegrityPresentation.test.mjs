import test from "node:test";
import assert from "node:assert/strict";
import {
  historicalEvidenceWarning,
  storedHistoricalMeasurements,
} from "../src/features/workbench/historicalEvidenceIntegrity.ts";

const outputs = [{ key: "TS", label: "引張強さ", unit: "MPa" }];

test("historical evidence classification preserves saved actuals for integrity failures", () => {
  const measurements = storedHistoricalMeasurements({ TS: 500 }, outputs);
  assert.deepEqual(measurements, [{ key: "TS", label: "引張強さ", mean: 500, std: 0, count: 1, unit: "MPa" }]);

  const mismatch = historicalEvidenceWarning({ name: "ApiClientError", kind: "validation", status: 422 });
  const missing = historicalEvidenceWarning({ name: "ApiClientError", kind: "not_found", status: 404 });
  assert.equal(mismatch.kind, "integrity");
  assert.match(mismatch.message, /Dataset Revisionと一致しません/);
  assert.equal(missing.kind, "integrity");
  assert.match(missing.message, /見つかりません/);
});

test("historical evidence keeps saved actuals while transient retrieval failures stay distinct", () => {
  const measurements = storedHistoricalMeasurements({ TS: 500 }, outputs);
  const network = historicalEvidenceWarning({ name: "ApiClientError", kind: "network", status: 0, message: "Failed to fetch" });
  const server = historicalEvidenceWarning({ name: "ApiClientError", kind: "server", status: 500 });

  assert.equal(measurements[0].mean, 500);
  assert.equal(network.kind, "transient");
  assert.equal(server.kind, "transient");
  assert.match(network.message, /一時的に取得できません/);
  assert.doesNotMatch(network.message, /Failed to fetch/);
});
