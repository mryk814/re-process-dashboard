import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  copyFileSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import test from "node:test";
import { tmpdir } from "node:os";
import { resolve } from "node:path";

const repositoryRoot = resolve(import.meta.dirname, "..");
const acceptanceRunner = resolve(import.meta.dirname, "run-main-acceptance.ps1");
const catalogPath = resolve(import.meta.dirname, "verification-gates.json");
const powershellExecutable = process.platform === "win32" ? "powershell.exe" : "pwsh";

function selectionFor(...extraArguments) {
  const result = spawnSync(
    powershellExecutable,
    [
      "-NoProfile",
      "-ExecutionPolicy",
      "Bypass",
      "-File",
      acceptanceRunner,
      "-SelectionOnly",
      ...extraArguments,
    ],
    {
      cwd: repositoryRoot,
      encoding: "utf8",
    },
  );
  assert.equal(result.status, 0, result.stderr || result.stdout);
  return JSON.parse(result.stdout.trim());
}

test("release selection uses catalog-declared absorption", () => {
  const selection = selectionFor();

  assert.equal(selection.absorbedGates["security-boundary-tests"], "full-pytest");
  assert.equal(selection.absorbedGates["model-package-contract-tests"], "full-pytest");
  assert.equal(selection.absorbedGates["legacy-workspace"], "full-pytest");
  assert.ok(selection.selectedGates.includes("security-boundary-tests"));
  assert.ok(!selection.executionGates.includes("security-boundary-tests"));
  assert.ok(selection.executionGates.includes("full-pytest"));
});

test("release selection does not infer absorption from runner shape", () => {
  const temporaryDirectory = mkdtempSync(resolve(tmpdir(), "acceptance-catalog-"));
  const temporaryCatalogPath = resolve(temporaryDirectory, "verification-gates.json");
  try {
    const catalog = JSON.parse(readFileSync(catalogPath, "utf8"));
    catalog.gates["full-pytest"].absorbs = ["security-boundary-tests"];
    writeFileSync(temporaryCatalogPath, JSON.stringify(catalog));

    const selection = selectionFor(
      "-VerificationCatalogPath",
      temporaryCatalogPath,
    );

    assert.equal(selection.absorbedGates["security-boundary-tests"], "full-pytest");
    assert.equal(selection.absorbedGates["model-package-contract-tests"], undefined);
    assert.ok(selection.executionGates.includes("model-package-contract-tests"));
  } finally {
    rmSync(temporaryDirectory, { recursive: true, force: true });
  }
});

test("release selection keeps failure-state coverage while default Playwright owns its standard specs", () => {
  const selection = selectionFor();

  assert.ok(selection.executionGates.includes("default-playwright"));
  assert.ok(selection.executionGates.includes("failure-state-e2e"));
  assert.deepEqual(selection.gateEnvironment["failure-state-e2e"], {
    VERIFICATION_SKIP_STANDARD_FAILURE_SPECS: "1",
  });
});

test("release execution reports absorbed evidence and scopes failure-state environment", () => {
  const temporaryRepository = mkdtempSync(resolve(tmpdir(), "acceptance-runner-"));
  const scriptsDirectory = resolve(temporaryRepository, "scripts");
  const temporaryRunner = resolve(scriptsDirectory, "run-main-acceptance.ps1");
  const temporaryCatalog = resolve(scriptsDirectory, "verification-gates.json");
  const reportPath = resolve(temporaryRepository, "artifacts", "report.json");
  try {
    mkdirSync(scriptsDirectory, { recursive: true });
    copyFileSync(acceptanceRunner, temporaryRunner);
    writeFileSync(
      resolve(temporaryRepository, "package.json"),
      JSON.stringify({ version: "0.0.0-test" }),
    );
    writeFileSync(
      resolve(scriptsDirectory, "fake-gate.mjs"),
      `const mode = process.argv[2];
const skip = process.env.VERIFICATION_SKIP_STANDARD_FAILURE_SPECS;
if (mode === "absorbed") process.exit(91);
if (mode === "failure-state" && skip !== "1") process.exit(92);
if (mode !== "failure-state" && skip !== undefined) process.exit(93);
console.log("passed " + mode);
`,
    );
    writeFileSync(
      temporaryCatalog,
      JSON.stringify({
        levels: [{
          id: "release",
          gates: [
            "full-pytest",
            "security-boundary-tests",
            "default-playwright",
            "failure-state-e2e",
            "after-environment",
          ],
        }],
        gates: {
          "full-pytest": {
            command: "node scripts/fake-gate.mjs owner",
            platform: "any",
            absorbs: ["security-boundary-tests"],
            runner: { executable: "node", args: ["scripts/fake-gate.mjs", "owner"] },
          },
          "security-boundary-tests": {
            command: "node scripts/fake-gate.mjs absorbed",
            platform: "any",
            runner: { executable: "node", args: ["scripts/fake-gate.mjs", "absorbed"] },
          },
          "default-playwright": {
            command: "node scripts/fake-gate.mjs default",
            platform: "any",
            runner: { executable: "node", args: ["scripts/fake-gate.mjs", "default"] },
          },
          "failure-state-e2e": {
            command: "node scripts/fake-gate.mjs failure-state",
            platform: "any",
            runner: { executable: "node", args: ["scripts/fake-gate.mjs", "failure-state"] },
          },
          "after-environment": {
            command: "node scripts/fake-gate.mjs after",
            platform: "any",
            runner: { executable: "node", args: ["scripts/fake-gate.mjs", "after"] },
          },
        },
      }),
    );
    for (const args of [
      ["init"],
      ["config", "user.email", "acceptance-test@example.invalid"],
      ["config", "user.name", "Acceptance Test"],
      ["add", "."],
      ["commit", "-m", "fixture"],
    ]) {
      const git = spawnSync("git", args, {
        cwd: temporaryRepository,
        encoding: "utf8",
      });
      assert.equal(git.status, 0, git.stderr || git.stdout);
    }

    const result = spawnSync(
      powershellExecutable,
      [
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        temporaryRunner,
        "-ReportPath",
        reportPath,
        "-VerificationCatalogPath",
        temporaryCatalog,
      ],
      {
        cwd: temporaryRepository,
        encoding: "utf8",
      },
    );
    assert.equal(result.status, 0, result.stderr || result.stdout);

    const report = JSON.parse(readFileSync(reportPath, "utf8").replace(/^\uFEFF/, ""));
    const absorbed = report.gates.find(
      (gate) => gate.name === "security-boundary-tests",
    );
    assert.equal(absorbed.status, "passed");
    assert.equal(absorbed.evidenceSource, "full-pytest");
    assert.ok(
      !report.omittedGates.some(
        (gate) => gate.id === "security-boundary-tests",
      ),
    );
    assert.ok(
      report.gates.every((gate) => gate.status === "passed"),
      JSON.stringify(report.gates),
    );
  } finally {
    rmSync(temporaryRepository, { recursive: true, force: true });
  }
});
