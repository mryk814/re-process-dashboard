import { createServer } from "node:net";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { isolatedSpecs } from "../e2e/suite-inventory.mjs";

const root = process.cwd();
const playwrightCli = join(root, "node_modules", "@playwright", "test", "cli.js");
const maxWorkers = Number(process.env.PLAYWRIGHT_ISOLATED_WORKERS ?? "2");
if (!Number.isInteger(maxWorkers) || maxWorkers < 1) {
  throw new Error("PLAYWRIGHT_ISOLATED_WORKERS must be a positive integer");
}
const retries = Number(process.env.PLAYWRIGHT_ISOLATED_RETRIES ?? "0");
if (!Number.isInteger(retries) || retries !== 0) {
  throw new Error(
    "PLAYWRIGHT_ISOLATED_RETRIES must be 0: mutable E2E attempts are never retried against the same resource identity",
  );
}

function reservePort() {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        reject(new Error("could not reserve a TCP port"));
        return;
      }
      server.close((error) => error ? reject(error) : resolve(address.port));
    });
  });
}

async function runSpec(spec, index, reportDirectory) {
  // Ask the OS for separate API and Web ports for every fresh process. They are
  // passed through the same environment that e2e/helpers.ts resolves.
  const [apiPort, webPort] = await Promise.all([reservePort(), reservePort()]);
  if (apiPort === webPort) throw new Error(`duplicate isolated ports for ${spec}`);
  const outputDir = join(reportDirectory, spec.replace(/\.spec\.ts$/, ""));
  const runId = `${Date.now()}-${process.pid}-${index}-${randomUUID()}`;
  mkdirSync(outputDir, { recursive: true });
  const startedAt = new Date().toISOString();
  return await new Promise((resolve) => {
    const child = spawn(
      process.execPath,
      [playwrightCli, "test", `e2e/${spec}`, "--workers=1", "--retries=0"],
      {
        cwd: root,
        env: {
          ...process.env,
          PLAYWRIGHT_API_PORT: String(apiPort),
          PLAYWRIGHT_WEB_PORT: String(webPort),
          PLAYWRIGHT_OUTPUT_DIR: outputDir,
          PLAYWRIGHT_E2E_RUN_ID: runId,
        },
        stdio: "inherit",
      },
    );
    child.once("exit", (code, signal) => resolve({
      spec,
      index,
      apiPort,
      webPort,
      runId,
      startedAt,
      finishedAt: new Date().toISOString(),
      code: code ?? 1,
      signal,
      outputDir,
    }));
  });
}

const runId = `isolated-e2e-${Date.now()}-${process.pid}`;
const reportDirectory = join(root, "test-results", runId);
mkdirSync(reportDirectory, { recursive: true });
const pending = [...isolatedSpecs].map((spec, index) => ({ spec, index }));
const results = [];
const active = new Set();

while (pending.length || active.size) {
  while (pending.length && active.size < maxWorkers) {
    const next = pending.shift();
    const task = runSpec(next.spec, next.index, reportDirectory)
      .then((result) => results.push(result))
      .finally(() => active.delete(task));
    active.add(task);
  }
  await Promise.race(active);
}

results.sort((left, right) => left.index - right.index);
const report = {
  schema_version: "isolated-e2e-run/v1",
  workers: maxWorkers,
  retries,
  specs: results,
};
const reportPath = join(reportDirectory, "report.json");
writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`);
process.stdout.write(`Isolated E2E report: ${reportPath}\n`);
if (results.some((result) => result.code !== 0)) process.exitCode = 1;
