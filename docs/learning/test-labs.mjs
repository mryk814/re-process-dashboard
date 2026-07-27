import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { validateLabs } from "./check-labs.mjs";
import {
  assertAllowedWritePath,
  calculateWeightedMean,
  repositoryRoot as actualRepositoryRoot,
} from "./labs/scripts/toy-weighted-mean.mjs";

const learningRoot = path.dirname(fileURLToPath(import.meta.url));
const schemaSource = fs.readFileSync(
  path.join(learningRoot, "labs", "lab.schema.json"),
  "utf8",
);
const sha = "0123456789abcdef0123456789abcdef01234567";
const protectedPaths = [
  "data/source",
  "data/workbench.db",
  "models/active-packages.json",
  "models/active-transforms.json",
  "models/packages",
  "apps/web/src/generated",
];

function qmd(id, minutes = 10) {
  return `---
chapter_id: "${id}"
estimated_minutes: ${minutes}
verified_commit: "${sha}"
---

# ${id}
`;
}

function lab(id, overrides = {}) {
  return {
    lab_id: id,
    document: `labs/${id}.qmd`,
    mode: "guided",
    category: "contract",
    title: id,
    verified_commit: sha,
    expected_minutes: 10,
    requires: [],
    fixtures: [],
    commands: {
      setup: null,
      run: null,
      verify: null,
      reset: null,
    },
    writes: [],
    must_not_write: [...protectedPaths],
    network: "forbidden",
    secrets: "forbidden",
    timeout_seconds: 30,
    expected_outcomes: ["境界を説明する"],
    ...overrides,
  };
}

function manifest(labs, overrides = {}) {
  return {
    $schema: "./lab.schema.json",
    schema_version: "learning-labs/v1",
    allowed_write_root: "artifacts/learning-labs",
    protected_paths: [...protectedPaths],
    labs,
    ...overrides,
  };
}

function withFixture(files, operation) {
  const repositoryRoot = fs.mkdtempSync(path.join(os.tmpdir(), "learning-labs-"));
  const fixtureLearningRoot = path.join(repositoryRoot, "docs", "learning");
  try {
    fs.mkdirSync(path.join(fixtureLearningRoot, "labs"), { recursive: true });
    fs.writeFileSync(
      path.join(fixtureLearningRoot, "labs", "lab.schema.json"),
      schemaSource,
    );
    for (const [relative, content] of Object.entries(files)) {
      const filename = path.join(repositoryRoot, ...relative.split("/"));
      fs.mkdirSync(path.dirname(filename), { recursive: true });
      fs.writeFileSync(filename, content);
    }
    return operation({ repositoryRoot, learningRoot: fixtureLearningRoot });
  } finally {
    fs.rmSync(repositoryRoot, { recursive: true, force: true });
  }
}

withFixture(
  {
    "docs/learning/labs/guided.qmd": qmd("guided"),
    "docs/learning/labs/executable.qmd": qmd("executable"),
    "docs/learning/labs/fixtures/input.json": "{}\n",
    "docs/learning/labs/scripts/executable.mjs": "export {};\n",
    "docs/learning/labs/manifest.json": JSON.stringify(
      manifest([
        lab("guided"),
        lab("executable", {
          mode: "executable",
          category: "math",
          requires: ["node"],
          fixtures: ["docs/learning/labs/fixtures/input.json"],
          commands: {
            setup: "node docs/learning/labs/scripts/executable.mjs setup",
            run: "node docs/learning/labs/scripts/executable.mjs run",
            verify: "node docs/learning/labs/scripts/executable.mjs verify",
            reset: "node docs/learning/labs/scripts/executable.mjs reset",
          },
          writes: ["artifacts/learning-labs/executable"],
        }),
      ]),
      null,
      2,
    ),
  },
  ({ repositoryRoot, learningRoot: fixtureLearningRoot }) => {
    const result = validateLabs({
      learningRoot: fixtureLearningRoot,
      repositoryRoot,
    });
    assert.deepEqual(result.errors, []);
    assert.equal(result.labCount, 2);
  },
);

withFixture(
  {
    "docs/learning/labs/unsafe.qmd": qmd("unsafe"),
    "docs/learning/labs/unregistered.qmd": qmd("unregistered"),
    "docs/learning/labs/scripts/unsafe.mjs": "export {};\n",
    "docs/learning/labs/manifest.json": JSON.stringify(
      manifest([
        lab("unsafe", {
          mode: "executable",
          fixtures: ["docs/learning/labs/fixtures/missing.json"],
          commands: {
            setup: "node docs/learning/labs/scripts/unsafe.mjs setup data/source",
            run: "curl https://example.invalid",
            verify: "node docs/learning/labs/scripts/unsafe.mjs verify",
            reset: null,
          },
          writes: ["data/source"],
          must_not_write: ["data/source"],
        }),
        lab("unsafe", { document: "labs/missing.qmd" }),
      ]),
      null,
      2,
    ),
  },
  ({ repositoryRoot, learningRoot: fixtureLearningRoot }) => {
    const errors = validateLabs({
      learningRoot: fixtureLearningRoot,
      repositoryRoot,
    }).errors.join("\n");
    assert.match(errors, /duplicate lab_id unsafe/);
    assert.match(errors, /fixture does not exist/);
    assert.match(errors, /command names protected path data\/source/);
    assert.match(errors, /network command is forbidden/);
    assert.match(errors, /executable lab needs setup, run, verify, and reset commands/);
    assert.match(errors, /write path is outside lab sandbox/);
    assert.match(errors, /write path overlaps protected path data\/source/);
    assert.match(errors, /must_not_write is missing models\/active-packages\.json/);
    assert.match(errors, /labs\/unregistered\.qmd: Lab document is not registered/);
    assert.match(errors, /document does not exist: labs\/missing\.qmd/);
  },
);

const toyResult = calculateWeightedMean({
  schema_version: "toy-weighted-mean/v1",
  value_unit: "MPa",
  measurements: [
    { id: "M-01", value: 10, weight: 1 },
    { id: "M-02", value: 20, weight: 2 },
    { id: "M-03", value: 40, weight: 1 },
  ],
});
assert.equal(toyResult.total_weight, 4);
assert.equal(toyResult.weighted_sum, 90);
assert.equal(toyResult.weighted_mean, 22.5);
assert.throws(
  () => assertAllowedWritePath(path.join(actualRepositoryRoot, "data", "source", "forbidden.txt")),
  /outside artifacts\/learning-labs|overlaps protected path/,
);
assert.doesNotThrow(() =>
  assertAllowedWritePath(
    path.join(actualRepositoryRoot, "artifacts", "learning-labs", "fixture-test"),
  ),
);

console.log(
  "Lab fixture tests passed: manifest contract, registration, sandbox writes, protected paths, commands, and toy calculation.",
);
