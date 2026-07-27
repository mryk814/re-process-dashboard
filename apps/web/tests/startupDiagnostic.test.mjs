import assert from "node:assert/strict";
import test from "node:test";
import { parseStartupDiagnostic } from "../src/features/workbench/startupDiagnostic.ts";

const finding = {
  stage: "catalog",
  resource_id: "package-v1",
  cause: "contract digestが一致しません",
  impact: "catalog bootstrapが停止します",
  recovery_hint: "新しいPackage versionを作成してください",
};

test("accepts the API-independent workspace preflight envelope", () => {
  const diagnostic = parseStartupDiagnostic({
    schema_version: "startup-diagnostic/v1",
    source: "workspace_preflight",
    log_path: "npm run dev output",
    recovery_route: "docs/decisions/startup-failure-boundaries.md",
    report: { status: "error", findings: [finding] },
  });
  assert.deepEqual(diagnostic?.report.findings, [finding]);
});

test("rejects incomplete findings rather than hiding required recovery detail", () => {
  assert.equal(parseStartupDiagnostic({
    schema_version: "startup-diagnostic/v1",
    source: "workspace_preflight",
    log_path: "log",
    recovery_route: "recovery",
    report: { status: "error", findings: [{ ...finding, recovery_hint: undefined }] },
  }), null);
});
