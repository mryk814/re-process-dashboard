import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(
  new URL("../src/features/screening/ScreeningPage.tsx", import.meta.url),
  "utf8",
);

test("Screening composition renders judgment, history, detailed evidence, then optional edit", () => {
  const layout = source.slice(
    source.indexOf("function ScreeningQuestionFirstLayout"),
    source.indexOf("function number("),
  );
  const judgmentSlot = layout.indexOf("{judgment}");
  const historySlot = layout.indexOf("{history}");
  const evidenceSlot = layout.indexOf("{evidence}");
  const editorSlot = layout.indexOf("{editor}");

  assert.ok(judgmentSlot >= 0);
  assert.ok(historySlot > judgmentSlot);
  assert.ok(evidenceSlot > historySlot);
  assert.ok(editorSlot > evidenceSlot);

  const question = source.indexOf('<section className="screening-mode-picker"');
  const action = source.indexOf('className="screening-question-action"', question);
  const layoutUse = source.indexOf("<ScreeningQuestionFirstLayout", action);
  assert.ok(question >= 0 && action > question && layoutUse > action);
});

test("successful Run closes editing and field diagnosis reopens it", () => {
  assert.match(source, /const applyResult = \(run: ScreenResult\) => \{[\s\S]*?setEditorOpen\(false\)/);
  assert.match(source, /setScreeningMode\(mode\);\s*setEditorOpen\(true\)/);
  assert.match(source, /if \(failure\.fieldErrors\.length > 0\) setEditorOpen\(true\)/);
});

test("the question area owns the only primary Run action", () => {
  assert.equal(source.match(/className="screening-question-action"/g)?.length, 1);
  assert.doesNotMatch(source, /screening-run-footer/);
  assert.match(
    source,
    /className="screening-question-action"[\s\S]*?className="primary-button"[\s\S]*?void run\(\)/,
  );
});
