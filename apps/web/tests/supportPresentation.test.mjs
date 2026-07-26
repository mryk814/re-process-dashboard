import test from "node:test";
import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import { supportStatusLabel, supportStatusTone } from "../src/shared/supportPresentation.ts";

const source = (relativePath) => readFile(new URL(relativePath, import.meta.url), "utf8");

test("model support status has one Japanese label for every surface", () => {
  assert.equal(supportStatusLabel("supported"), "範囲内");
  assert.equal(supportStatusLabel("caution"), "要確認");
  assert.equal(supportStatusLabel("extrapolated"), "外挿");
  assert.equal(supportStatusLabel(undefined), "未計算");
  assert.equal(supportStatusLabel(null, "未確認"), "未確認");
});

test("support tone separates supported from every state that needs attention", () => {
  assert.equal(supportStatusTone("supported"), "success");
  assert.equal(supportStatusTone("caution"), "caution");
  assert.equal(supportStatusTone("extrapolated"), "caution");
  assert.equal(supportStatusTone(undefined), "unknown");
});

test("comparison table and screening results resolve support labels from the shared source", async () => {
  const comparison = await source("../src/features/candidates/CandidateUi.tsx");
  const screening = await source("../src/features/screening/ScreeningRepresentativeTable.tsx");
  assert.match(comparison, /supportStatusLabel\(value\)/);
  assert.match(screening, /supportStatusLabel\(status, "未確認"\)/);
});

test("counterfactual proposals show the support word instead of the raw contract value", async () => {
  const content = await source("../src/features/workbench/decisionActivities/CounterfactualActivityView.tsx");
  assert.match(content, /<SupportBadge status=\{proposal\.support\.status\}/);
  assert.doesNotMatch(content, /support-pill/);
  assert.doesNotMatch(content, /\{proposal\.support\.status\}<\/span>/);
});

test("support badge carries a word and a shape, never colour alone", async () => {
  const content = await source("../src/shared/ui/SupportBadge.tsx");
  assert.match(content, /適用範囲 \{label\}/);
  assert.match(content, /<i aria-hidden="true" \/>/);
});

test("candidate revision is called 編集版 everywhere in the product UI", async () => {
  const root = new URL("../src/", import.meta.url);
  const offenders = [];
  const walk = async (directory) => {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const child = new URL(`${entry.name}${entry.isDirectory() ? "/" : ""}`, directory);
      if (entry.isDirectory()) {
        if (entry.name !== "generated") await walk(child);
      } else if (/\.tsx?$/.test(entry.name) && (await readFile(child, "utf8")).includes("候補版")) {
        offenders.push(entry.name);
      }
    }
  };
  await walk(root);
  assert.deepEqual(offenders, [], "Candidate revision is labelled 編集版, never 候補版");
});

test("no surface renders a raw support status", async () => {
  const root = new URL("../src/", import.meta.url);
  const offenders = [];
  // Text content only: className templates use ${…} and props use status={…}.
  const rawStatus = /(?<![$=])\{[\w.?]*support\??\.status\}/;
  const walk = async (directory) => {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const child = new URL(`${entry.name}${entry.isDirectory() ? "/" : ""}`, directory);
      if (entry.isDirectory()) {
        if (entry.name !== "generated") await walk(child);
        continue;
      }
      if (!entry.name.endsWith(".tsx")) continue;
      if (rawStatus.test(await readFile(child, "utf8"))) offenders.push(entry.name);
    }
  };
  await walk(root);
  assert.deepEqual(offenders, [], "support status is shown through supportStatusLabel");
});
