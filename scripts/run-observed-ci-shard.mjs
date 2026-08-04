import {
  mkdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { pathToFileURL } from "node:url";


export const runnerObservationSchemaVersion = "verification-runner-observation/v1";

export function lastCompletedPytest(output) {
  let lastCompletedTest = null;
  for (const line of output.split(/\r?\n/)) {
    const direct = line.match(
      /^(backend\/tests\/\S+::\S+)\s+(?:PASSED|FAILED|SKIPPED|XFAIL|XPASS)(?:\s|$)/,
    );
    const xdist = line.match(
      /^\[gw\d+\]\s+\[\s*\d+%\]\s+(?:PASSED|FAILED|SKIPPED|XFAIL|XPASS)\s+(backend\/tests\/\S+::\S+)(?:\s|$)/,
    );
    lastCompletedTest = direct?.[1] ?? xdist?.[1] ?? lastCompletedTest;
  }
  return lastCompletedTest;
}

function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return null;
  }
}

function readText(path) {
  try {
    return readFileSync(path, "utf8");
  } catch {
    return "";
  }
}

export function finalRunnerObservation({
  heartbeat,
  childExitCode,
  childSignal,
  childError,
  stdout,
  shardId,
  childPid,
  environment = process.env,
}) {
  return {
    schemaVersion: runnerObservationSchemaVersion,
    shardId,
    runnerIdentity: {
      platform: process.platform,
      architecture: process.arch,
      nodeVersion: process.version,
      githubRunId: environment.GITHUB_RUN_ID ?? null,
      githubRunAttempt: environment.GITHUB_RUN_ATTEMPT ?? null,
      githubJob: environment.GITHUB_JOB ?? null,
      runnerName: environment.RUNNER_NAME ?? null,
      runnerOS: environment.RUNNER_OS ?? null,
      runnerArchitecture: environment.RUNNER_ARCH ?? null,
    },
    supervisorPid: process.pid,
    childPid,
    childExitCode,
    childSignal,
    childError,
    finalized: true,
    observedAt: new Date().toISOString(),
    peakTreeWorkingSetBytes: heartbeat?.peakTreeWorkingSetBytes ?? null,
    peakProcessTree: heartbeat?.peakProcessTree ?? [],
    sampleCount: heartbeat?.sampleCount ?? 0,
    totalPhysicalMemoryBytes: heartbeat?.totalPhysicalMemoryBytes ?? null,
    lastCompletedTest: heartbeat?.lastCompletedTest ?? lastCompletedPytest(stdout),
    heartbeat: heartbeat ?? null,
  };
}

export function withRunnerObservation(report, observation) {
  return {
    ...report,
    runnerObservation: observation,
  };
}

function option(args, name) {
  const index = args.indexOf(name);
  return index >= 0 ? args[index + 1] : undefined;
}

function waitFor(child) {
  return new Promise((done) => {
    child.once("error", (error) => done({ code: null, signal: null, error }));
    child.once("close", (code, signal) => done({ code, signal, error: null }));
  });
}

async function observeBackendShard(args) {
  const shardId = option(args, "--shard");
  const diagnosticsDirectory = resolve(
    `artifacts/verification/diagnostics/${shardId}`,
  );
  mkdirSync(diagnosticsDirectory, { recursive: true });
  const heartbeatPath = resolve(diagnosticsDirectory, "runner-observation-heartbeat.json");
  const observationPath = resolve(diagnosticsDirectory, "runner-observation.json");
  const shardReportPath = resolve(option(args, "--output"));
  const stopPath = resolve(diagnosticsDirectory, "runner-observation.stop");
  const stdoutPath = resolve(diagnosticsDirectory, "full-pytest.stdout.log");
  const child = spawn(
    process.execPath,
    [resolve("scripts/verification-ci.mjs"), "run-shard", ...args],
    { stdio: "inherit", env: process.env },
  );
  const observer = spawn(
    "pwsh",
    [
      "-NoProfile",
      "-File",
      resolve("scripts/observe-process-tree.ps1"),
      "-ParentProcessId",
      String(child.pid),
      "-StdoutPath",
      stdoutPath,
      "-OutputPath",
      heartbeatPath,
      "-StopPath",
      stopPath,
    ],
    { stdio: "ignore", windowsHide: true },
  );
  const result = await waitFor(child);
  writeFileSync(stopPath, "stop\n", "utf8");
  await Promise.race([
    waitFor(observer),
    new Promise((done) => setTimeout(done, 5_000)),
  ]);
  if (observer.exitCode === null) observer.kill();
  const heartbeat = readJson(heartbeatPath);
  const observation = finalRunnerObservation({
    heartbeat,
    childExitCode: result.code,
    childSignal: result.signal,
    childError: result.error?.message ?? null,
    stdout: readText(stdoutPath),
    shardId,
    childPid: child.pid,
  });
  mkdirSync(dirname(observationPath), { recursive: true });
  writeFileSync(observationPath, `${JSON.stringify(observation, null, 2)}\n`, "utf8");
  const shardReport = readJson(shardReportPath);
  if (shardReport) {
    writeFileSync(
      shardReportPath,
      `${JSON.stringify(withRunnerObservation(shardReport, observation), null, 2)}\n`,
      "utf8",
    );
  }
  return result.code ?? 1;
}

async function run() {
  const args = process.argv.slice(2);
  const shardId = option(args, "--shard");
  if (!shardId) throw new Error("--shard is required");
  if (shardId === "backend-science" && process.platform === "win32") {
    return observeBackendShard(args);
  }
  const child = spawn(
    process.execPath,
    [resolve("scripts/verification-ci.mjs"), "run-shard", ...args],
    { stdio: "inherit", env: process.env },
  );
  const result = await waitFor(child);
  return result.code ?? 1;
}

if (
  process.argv[1]
  && import.meta.url === pathToFileURL(resolve(process.argv[1])).href
) {
  run().then(
    (exitCode) => { process.exitCode = exitCode; },
    (error) => {
      process.stderr.write(`${error.stack ?? error.message}\n`);
      process.exitCode = 2;
    },
  );
}
