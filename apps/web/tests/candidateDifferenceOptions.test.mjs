import test from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import path from "node:path";

const sourceRoot = path.resolve(import.meta.dirname, "../src");
const bundle = await build({
  entryPoints: [path.join(
    sourceRoot,
    "features/workbench/decisionActivities/candidateDifferenceOptions.ts",
  )],
  bundle: true,
  format: "esm",
  platform: "node",
  write: false,
});
const source = bundle.outputFiles[0].text;
const encoded = Buffer.from(source).toString("base64");
const { candidateDifferenceOptions } = await import(`data:text/javascript;base64,${encoded}`);

const candidate = (id, label, revision) => ({
  id,
  label,
  raw: { revision },
});

test("candidate difference offers the current candidate history before other candidates", () => {
  const current = candidate("current", "現在候補", 3);
  const other = candidate("other", "比較候補", 2);

  assert.deepEqual(candidateDifferenceOptions(current, [current, other]), [
    {
      key: "current@2",
      candidateId: "current",
      revision: 2,
      label: "この候補の過去版 r2",
      kind: "history",
    },
    {
      key: "current@1",
      candidateId: "current",
      revision: 1,
      label: "この候補の過去版 r1",
      kind: "history",
    },
    {
      key: "other@2",
      candidateId: "other",
      revision: 2,
      label: "比較候補（r2）",
      kind: "candidate",
    },
  ]);
});

test("a first revision with no other candidate has no comparison option", () => {
  const current = candidate("current", "現在候補", 1);
  assert.deepEqual(candidateDifferenceOptions(current, [current]), []);
});
