import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  markdownFigureReferences,
  validateFigures,
} from "./check-figures.mjs";

const sha = "0123456789abcdef0123456789abcdef01234567";

function svg({ raster = false, title = "Flow", description = "A to B." } = {}) {
  return `<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="title desc">
  <title id="title">${title}</title>
  <desc id="desc">${description}</desc>
  ${raster ? '<image href="data:image/png;base64,AA=="/>' : '<path d="M0 0H10"/>'}
</svg>
`;
}

function figure(overrides = {}) {
  return {
    id: "flow",
    kind: "architecture-flow",
    source: "figures/flow.svg",
    chapter: "chapters/flow.qmd",
    verified_commit: sha,
    alt: "AからBへの流れ",
    long_description: "AからBへ進む。",
    drift_refs: ["src/flow.ts"],
    ...overrides,
  };
}

function registry(figures) {
  return JSON.stringify({ schema_version: 1, figures }, null, 2);
}

function withFixture(operation) {
  const repositoryRoot = fs.mkdtempSync(path.join(os.tmpdir(), "figure-check-"));
  const learningRoot = path.join(repositoryRoot, "docs", "learning");
  try {
    fs.mkdirSync(path.join(learningRoot, "figures"), { recursive: true });
    fs.mkdirSync(path.join(learningRoot, "chapters"), { recursive: true });
    fs.mkdirSync(path.join(repositoryRoot, "src"), { recursive: true });
    fs.writeFileSync(path.join(repositoryRoot, "src", "flow.ts"), "export {};\n");
    return operation({ repositoryRoot, learningRoot });
  } finally {
    fs.rmSync(repositoryRoot, { recursive: true, force: true });
  }
}

{
  const references = markdownFigureReferences(
    [
      '![短い説明](../figures/flow.svg){fig-alt="長い説明" width="100%"}',
      "```markdown",
      "![code example](../figures/not-a-reference.svg)",
      "```",
    ].join("\n"),
    "chapters/flow.qmd",
  );
  assert.deepEqual(references, [
    {
      chapter: "chapters/flow.qmd",
      source: "figures/flow.svg",
      alt: "短い説明",
      longDescription: "長い説明",
    },
  ]);
}

withFixture(({ repositoryRoot, learningRoot }) => {
  fs.writeFileSync(path.join(learningRoot, "figures", "flow.svg"), svg());
  fs.writeFileSync(
    path.join(learningRoot, "chapters", "flow.qmd"),
    '![AからBへの流れ](../figures/flow.svg){fig-alt="AからBへ進む。"}\n',
  );
  fs.writeFileSync(
    path.join(learningRoot, "figures", "registry.json"),
    registry([figure()]),
  );
  const result = validateFigures({ learningRoot, repositoryRoot });
  assert.deepEqual(result.errors, []);
  assert.equal(result.figureCount, 1);
  assert.equal(result.referenceCount, 1);
});

withFixture(({ repositoryRoot, learningRoot }) => {
  fs.writeFileSync(path.join(learningRoot, "figures", "flow.svg"), svg());
  fs.writeFileSync(path.join(learningRoot, "figures", "orphan.svg"), svg());
  fs.writeFileSync(path.join(learningRoot, "figures", "raster.svg"), svg({ raster: true }));
  fs.writeFileSync(
    path.join(learningRoot, "figures", "no-a11y.svg"),
    '<svg xmlns="http://www.w3.org/2000/svg" role="img"><path d="M0 0H10"/></svg>\n',
  );
  fs.writeFileSync(
    path.join(learningRoot, "chapters", "flow.qmd"),
    [
      "![](../figures/flow.svg)",
      "![未登録](../figures/orphan.svg)",
      "![異なるalt](../figures/raster.svg)",
    ].join("\n"),
  );
  fs.writeFileSync(
    path.join(learningRoot, "figures", "registry.json"),
    registry([
      figure({ alt: "", long_description: "" }),
      figure({
        id: "flow",
        source: "figures/missing.svg",
        chapter: "chapters/missing.qmd",
        drift_refs: ["src/missing.ts"],
      }),
      figure({
        id: "raster",
        source: "figures/raster.svg",
        alt: "Raster",
        long_description: "",
      }),
      figure({
        id: "no-a11y",
        source: "figures/no-a11y.svg",
        alt: "No accessibility metadata",
        long_description: "",
        drift_refs: [],
      }),
    ]),
  );
  const errors = validateFigures({ learningRoot, repositoryRoot }).errors.join("\n");
  assert.match(errors, /duplicate id flow/);
  assert.match(errors, /source does not exist: figures\/missing\.svg/);
  assert.match(errors, /chapter does not exist: chapters\/missing\.qmd/);
  assert.match(errors, /alt or long_description must be non-empty/);
  assert.match(errors, /path does not exist: src\/missing\.ts/);
  assert.match(errors, /has empty Markdown alt/);
  assert.match(errors, /unregistered figure reference figures\/orphan\.svg/);
  assert.match(errors, /figures\/orphan\.svg: SVG source is not registered/);
  assert.match(errors, /figures\/raster\.svg: SVG must not embed a raster image/);
  assert.match(errors, /figures\/raster\.svg alt differs from registry/);
  assert.match(errors, /figures\/no-a11y\.svg: SVG title is empty or missing/);
  assert.match(errors, /figures\/no-a11y\.svg: SVG desc is empty or missing/);
  assert.match(errors, /drift_refs must be a non-empty array/);
  assert.match(errors, /not referenced by registered chapter/);
});

console.log(
  "Figure fixture tests passed: registry schema, references, alt metadata, drift paths, SVG accessibility, and raster rejection.",
);
