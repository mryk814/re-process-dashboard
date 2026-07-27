import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import { performance } from "node:perf_hooks";

const repositoryRoot = resolve(import.meta.dirname, "..");

function compose(args) {
  return spawnSync("docker", ["compose", "--profile", "infra", ...args], {
    cwd: repositoryRoot,
    stdio: "inherit",
  });
}

function requireSuccess(label, args) {
  const result = compose(args);
  if (result.error) {
    console.error(`${label} could not start: ${result.error.message}`);
    process.exit(result.status ?? 1);
  }
  if (result.status !== 0) {
    console.error(`${label} failed. Infrastructure was left running for diagnostics.`);
    process.exit(result.status ?? 1);
  }
}

const started = performance.now();

// Wait only for long-running services. Compose treats a successful one-shot
// container as an error when it is included in `up -d --wait`.
requireSuccess("Infrastructure startup", [
  "up",
  "-d",
  "--wait",
  "postgres",
  "object-storage",
]);
requireSuccess("PostgreSQL migration", ["run", "--rm", "--no-deps", "migration"]);
requireSuccess("Object storage bucket initialization", ["run", "--rm", "--no-deps", "bucket-init"]);

const durationSeconds = Math.round((performance.now() - started) / 100) / 10;
console.log(`Compose infrastructure is healthy and initialized in ${durationSeconds}s.`);
