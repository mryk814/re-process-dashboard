import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  finalRunnerObservation,
  lastCompletedPytest,
  runnerObservationSchemaVersion,
  withRunnerObservation,
} from "./run-observed-ci-shard.mjs";


test("runner observation keeps the exact child exit, peak RSS, and last completed test", () => {
  const stdout = [
    "backend/tests/test_first.py::test_one PASSED [ 10%]",
    "[gw2] [ 11%] SKIPPED backend/tests/test_second.py::test_two",
  ].join("\n");
  assert.equal(
    lastCompletedPytest(stdout),
    "backend/tests/test_second.py::test_two",
  );

  const report = finalRunnerObservation({
    heartbeat: {
      peakTreeWorkingSetBytes: 3_221_225_472,
      peakProcessTree: [{ processId: 22, name: "python.exe" }],
      sampleCount: 41,
      totalPhysicalMemoryBytes: 7_500_000_000,
      lastCompletedTest: "backend/tests/test_second.py::test_two",
    },
    childExitCode: 137,
    childSignal: "SIGKILL",
    childError: null,
    stdout,
    shardId: "backend-science",
    childPid: 20,
    environment: {
      GITHUB_RUN_ID: "1234",
      GITHUB_RUN_ATTEMPT: "2",
      GITHUB_JOB: "verification-shards",
      RUNNER_NAME: "windows-runner",
      RUNNER_OS: "Windows",
      RUNNER_ARCH: "X64",
    },
  });

  assert.equal(report.schemaVersion, runnerObservationSchemaVersion);
  assert.equal(report.childExitCode, 137);
  assert.equal(report.childSignal, "SIGKILL");
  assert.equal(report.peakTreeWorkingSetBytes, 3_221_225_472);
  assert.equal(report.lastCompletedTest, "backend/tests/test_second.py::test_two");
  assert.equal(report.runnerIdentity.githubRunAttempt, "2");
  assert.equal(report.runnerIdentity.runnerOS, "Windows");
  assert.deepEqual(
    withRunnerObservation({ status: "passed" }, report),
    { status: "passed", runnerObservation: report },
  );
});


test("workflow supervises shard execution and full pytest exposes test identity", async () => {
  const [workflow, catalog, observer] = await Promise.all([
    readFile(new URL("../.github/workflows/verify.yml", import.meta.url), "utf8"),
    readFile(new URL("./verification-gates.json", import.meta.url), "utf8"),
    readFile(new URL("./observe-process-tree.ps1", import.meta.url), "utf8"),
  ]);
  assert.match(workflow, /node scripts\/run-observed-ci-shard\.mjs --plan/);
  assert.match(workflow, /backend-science[\s\S]+uv sync --frozen --extra dev --extra runtime-numpyro/);
  const fullPytest = JSON.parse(catalog).gates["full-pytest"];
  assert.ok(fullPytest.runner.args.includes("-vv"));
  assert.ok(fullPytest.runner.args.includes("runtime-numpyro"));
  assert.match(observer, /peakTreeWorkingSetBytes/);
  assert.match(observer, /lastCompletedTest/);
  assert.match(observer, /\[int\]\$PollMilliseconds = 10000/);
  assert.match(observer, /Move-Item .* -Force/);
});
