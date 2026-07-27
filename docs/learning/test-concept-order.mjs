import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  parseChapterFrontMatter,
  parseChapterOrder,
  validateLearningRoot,
  validateProfileOrder,
} from "./check-concept-order.mjs";

function chapter(
  chapterPath,
  {
    id = path.basename(chapterPath, path.extname(chapterPath)),
    prerequisites = [],
    introduced = [],
    bridged = [],
  } = {},
) {
  return {
    id,
    path: chapterPath,
    prerequisiteConcepts: prerequisites,
    introducedConcepts: introduced,
    bridgedConcepts: bridged,
  };
}

function qmd({ id, prerequisites = [], introduced = [], bridged }) {
  const list = (key, values) =>
    values.length === 0
      ? `${key}: []`
      : `${key}:\n${values.map((value) => `  - "${value}"`).join("\n")}`;
  return [
    "---",
    `chapter_id: "${id}"`,
    list("prerequisite_concepts", prerequisites),
    list("introduced_concepts", introduced),
    ...(bridged === undefined ? [] : [list("bridged_concepts", bridged)]),
    "---",
    "",
    `# ${id}`,
    "",
  ].join("\n");
}

function withFixture(files, operation) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "concept-order-"));
  try {
    for (const [relative, content] of Object.entries(files)) {
      const filename = path.join(root, ...relative.split("/"));
      fs.mkdirSync(path.dirname(filename), { recursive: true });
      fs.writeFileSync(filename, content, "utf8");
    }
    return operation(root);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

{
  const order = parseChapterOrder(`
book:
  chapters:
    - index.qmd
    - part: "教材"
      chapters:
        - chapters/first.qmd
        - "chapters/second.qmd"
`);
  assert.deepEqual(order, [
    "index.qmd",
    "chapters/first.qmd",
    "chapters/second.qmd",
  ]);
}

{
  const violations = validateProfileOrder("_quarto-reader.yml", [
    chapter("chapters/decision-safety.qmd", {
      id: "decision-safety",
      prerequisites: ["predictive-summary"],
    }),
    chapter("chapters/prediction-calibration-support.qmd", {
      id: "prediction-calibration-support",
      introduced: ["predictive-summary"],
    }),
  ]);
  assert.deepEqual(violations, [
    '_quarto-reader.yml: chapters/decision-safety.qmd (decision-safety) at position 1 requires "predictive-summary" before it is learned; first introduced by chapters/prediction-calibration-support.qmd at position 2',
  ]);
}

{
  const bridged = validateProfileOrder("_quarto-reader.yml", [
    chapter("chapters/self-contained.qmd", {
      prerequisites: ["contract"],
      bridged: ["contract"],
    }),
  ]);
  assert.deepEqual(bridged, []);

  const bridgeDoesNotTeachLaterChapters = validateProfileOrder("_quarto-reader.yml", [
    chapter("chapters/self-contained.qmd", {
      prerequisites: ["contract"],
      bridged: ["contract"],
    }),
    chapter("chapters/later.qmd", { prerequisites: ["contract"] }),
  ]);
  assert.match(
    bridgeDoesNotTeachLaterChapters.join("\n"),
    /chapters\/later\.qmd.*no introducing chapter exists/,
  );
}

{
  const invalidBridge = validateProfileOrder("_quarto-reader.yml", [
    chapter("chapters/invalid-bridge.qmd", {
      introduced: ["contract"],
      bridged: ["contract"],
    }),
  ]);
  assert.match(
    invalidBridge.join("\n"),
    /bridged concept "contract" must also be listed in prerequisite_concepts/,
  );
  assert.match(
    invalidBridge.join("\n"),
    /bridged concept "contract" cannot also be listed in introduced_concepts/,
  );
}

assert.throws(
  () =>
    parseChapterFrontMatter(
      `---
chapter_id: "invalid"
prerequisite_concepts: []
introduced_concepts: []
bridged_concepts: "contract"
---
`,
      "chapters/invalid.qmd",
    ),
  /bridged_concepts must be a YAML list/,
);

withFixture(
  {
    "_quarto-reader.yml": `
book:
  chapters:
    - index.qmd
    - chapters/introduction.qmd
    - glossary.qmd
    - chapters/use.qmd
`,
    "_quarto-site.yml": `
book:
  chapters:
    - index.qmd
    - part: "教材"
      chapters:
        - chapters/use.qmd
        - chapters/introduction.qmd
    - glossary.qmd
`,
    "index.qmd": "# Index\n",
    "glossary.qmd": "# Glossary\n\nThe concept exists here, but has not been taught.\n",
    "chapters/introduction.qmd": qmd({
      id: "introduction",
      introduced: ["support"],
    }),
    "chapters/use.qmd": qmd({
      id: "use",
      prerequisites: ["support", "glossary-only"],
    }),
  },
  (root) => {
    const result = validateLearningRoot(root);
    assert.equal(result.profiles.length, 2);
    assert.match(
      result.violations.join("\n"),
      /_quarto-reader\.yml: chapters\/use\.qmd.*"glossary-only".*no introducing chapter exists/,
    );
    assert.doesNotMatch(
      result.violations.join("\n"),
      /_quarto-reader\.yml: chapters\/use\.qmd.*"support"/,
    );
    assert.match(
      result.violations.join("\n"),
      /_quarto-site\.yml: chapters\/use\.qmd.*"support".*first introduced by chapters\/introduction\.qmd/,
    );
  },
);

console.log(
  "Concept order fixture tests passed: profile parsing, violation detail, glossary exclusion, and bridge contract.",
);
