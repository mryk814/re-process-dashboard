import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import { performance } from "node:perf_hooks";

const repositoryRoot = resolve(import.meta.dirname, "..");
const projectName = `decision-workbench-test-${process.pid}`;

function compose(args, stdio = "inherit") {
  return spawnSync("docker", [
    "compose",
    "--project-name",
    projectName,
    "--profile",
    "test",
    ...args,
  ], {
    cwd: repositoryRoot,
    stdio,
  });
}

const started = performance.now();
let status = 1;
try {
  const result = compose([
    "up",
    "--abort-on-container-exit",
    "--exit-code-from",
    "integration-smoke",
    "integration-smoke",
  ]);
  status = result.status ?? 1;
} finally {
  const cleanup = compose(["down", "--remove-orphans"]);
  if (cleanup.status !== 0 && status === 0) status = cleanup.status ?? 1;
}

const durationSeconds = Math.round((performance.now() - started) / 100) / 10;
if (status !== 0) {
  console.error(`Ephemeral Compose integration failed after ${durationSeconds}s.`);
  process.exitCode = status;
} else {
  console.log(`Ephemeral Compose integration passed and was removed in ${durationSeconds}s.`);
}
