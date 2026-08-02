import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(
  new URL("../src/features/screening/ScreeningPage.tsx", import.meta.url),
  "utf8",
);

test("Screening reads as question, current evidence, then optional edit", () => {
  const question = source.indexOf('<section className="screening-mode-picker"');
  const evidence = source.indexOf("<ScreeningProposalSummary", question);
  const editor = source.indexOf('className="screening-editor-disclosure"', evidence);
  const detailedEvidence = source.indexOf("<ScreeningResultSurfaceTabs", editor);

  assert.ok(question >= 0);
  assert.ok(evidence > question);
  assert.ok(editor > evidence);
  assert.ok(detailedEvidence > editor);
});

test("successful Run closes editing and field diagnosis reopens it", () => {
  assert.match(source, /const applyResult = \(run: ScreenResult\) => \{[\s\S]*?setEditorOpen\(false\)/);
  assert.match(source, /setScreeningMode\(mode\);\s*setEditorOpen\(true\)/);
  assert.match(source, /if \(failure\.fieldErrors\.length > 0\) setEditorOpen\(true\)/);
});

test("the page header no longer duplicates the Run action", () => {
  const pageIntro = source.match(/<div className="page-intro">([\s\S]*?)<\/div>\s*\{compositionBalanceNotice/);
  assert.ok(pageIntro);
  assert.doesNotMatch(pageIntro[1], /onClick=\{\(\) => \{\s*void run\(\)/);
});
