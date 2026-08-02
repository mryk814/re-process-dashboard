import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

const scratch = mkdtempSync(join(tmpdir(), "decision-workbench-degraded-e2e-"));
const database = join(scratch, "workbench.db");
const missingPackage = join(scratch, "missing-heat-treatment-package");
const projectIdFile = join(scratch, "broken-project-id.txt");
const playwrightCli = join(
  process.cwd(),
  "node_modules",
  "@playwright",
  "test",
  "cli.js",
);
const environment = {
  ...process.env,
  PYTHONPATH: join(process.cwd(), "backend", "src"),
  PLAYWRIGHT_DB_PATH: database,
  PLAYWRIGHT_BROKEN_TASK_PACKAGE: missingPackage,
  PLAYWRIGHT_REUSE_SERVER: "0",
};

function run(executable, args) {
  const result = spawnSync(executable, args, {
    cwd: process.cwd(),
    env: environment,
    stdio: "inherit",
  });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exitCode = result.status ?? 1;
  return result.status === 0;
}

try {
  const seeded = run("uv", [
    "run",
    "python",
    "e2e/helpers/seed-degraded-task.py",
    "--db",
    database,
    "--project-id-file",
    projectIdFile,
  ]);
  if (seeded) {
    const brokenProjectId = readFileSync(projectIdFile, "utf8").trim();
    if (!brokenProjectId) throw new Error("degraded-task seed did not report its Project ID");
    environment.PLAYWRIGHT_BROKEN_PROJECT_ID = brokenProjectId;
    run(process.execPath, [playwrightCli, "test", "e2e/degraded-task.spec.ts"]);
  }
} finally {
  rmSync(scratch, { recursive: true, force: true });
}
