import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdir, readdir, stat } from "node:fs/promises";
import { join, resolve } from "node:path";
import { _electron as electron } from "playwright";


const repositoryRoot = resolve(import.meta.dirname, "..");
const appRoot = resolve(process.argv[2] ?? join(repositoryRoot, "release", "Material-Decision-Workbench-folder"));
const mode = process.argv[3] ?? "portable";
assert(["portable", "installed"].includes(mode));
const smokeRoot = join(repositoryRoot, "release", "smoke");
if (mode === "installed") process.env.LOCALAPPDATA = join(smokeRoot, "local-app-data");
const executablePath = join(appRoot, "Material Decision Workbench.exe");
const artifacts = join(repositoryRoot, "artifacts");
await mkdir(artifacts, { recursive: true });

const electronApp = await electron.launch({ executablePath, timeout: 60_000 });
try {
  const window = await electronApp.firstWindow({ timeout: 60_000 });
  await window.getByRole("heading", { name: "焼鈍条件の候補検討" }).waitFor({ timeout: 60_000 });
  const secondInstance = spawn(executablePath, [], { env: process.env, stdio: "ignore" });
  const secondExit = await Promise.race([
    once(secondInstance, "exit"),
    new Promise((_, reject) => setTimeout(() => reject(new Error("second instance did not exit")), 10_000)),
  ]);
  assert.equal(secondExit[0], 0);
  await window.getByRole("button", { name: "候補比較", exact: true }).click();
  const download = window.waitForResponse((response) => response.url().endsWith("/candidates/export.xlsx"));
  await window.getByRole("button", { name: "候補・予測をXLSX出力" }).click();
  assert.equal((await download).status(), 200);
  await window.getByRole("button", { name: "保存結果・履歴" }).click();
  await window.getByRole("heading", { name: "候補と判断履歴" }).waitFor();

  const runtime = await window.evaluate(() => window.workbenchDesktop);
  assert(runtime?.apiBaseUrl);
  assert(runtime.launchToken);
  const authenticatedFetch = (path) => fetch(`${runtime.apiBaseUrl}${path}`, {
    headers: { "X-Workbench-Launch-Token": runtime.launchToken },
  });
  const externalTasks = [
    {
      projectId: "heat-treatment-tradeoff-v1-default",
      target: "hardness_hv",
      variable: "composition.carbon_pct",
    },
    {
      projectId: "concrete-strength-v1-default",
      target: "compressive_strength_mpa",
      variable: "process.age_days",
    },
    {
      projectId: "wear-curve-v1-default",
      target: "wear_vb_um",
      variable: "process.cutting_distance_m",
    },
  ];
  for (const task of externalTasks) {
    const candidatesResponse = await authenticatedFetch(`/api/projects/${task.projectId}/candidates`);
    assert.equal(candidatesResponse.status, 200);
    const candidates = await candidatesResponse.json();
    assert.equal(candidates.length, 3);
    const candidate = candidates[0];
    const curveParams = new URLSearchParams({
      expected_revision: String(candidate.revision),
      target: task.target,
      variable: task.variable,
      points: "9",
    });
    const curveResponse = await authenticatedFetch(
      `/api/projects/${task.projectId}/candidates/${candidate.id}/response-curve?${curveParams}`,
    );
    assert.equal(curveResponse.status, 200);
    assert.equal((await curveResponse.json()).points.length, 9);
  }
  assert.equal((await fetch(`${runtime.apiBaseUrl}/health`)).status, 401);
  assert.equal((await fetch(`${runtime.apiBaseUrl}/health`, {
    headers: { "X-Workbench-Launch-Token": runtime.launchToken },
  })).status, 200);

  const layout = await window.evaluate(() => ({
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    scrollWidth: document.documentElement.scrollWidth,
    scrollHeight: document.documentElement.scrollHeight,
  }));
  assert(layout.innerWidth >= 1180);
  assert(layout.innerHeight >= 760);
  assert(layout.scrollWidth <= layout.innerWidth);

  const screenshot = join(artifacts, `packaged-${mode}-smoke.png`);
  await window.screenshot({ path: screenshot, scale: "css" });
  const userData = mode === "portable"
    ? join(appRoot, "user-data")
    : join(process.env.LOCALAPPDATA, "Material Decision Workbench");
  const database = join(userData, "workbench.db");
  const logDirectory = join(userData, "logs");
  const logName = (await readdir(logDirectory))
    .filter((name) => /^sidecar-.*\.log$/.test(name))
    .sort()
    .at(-1);
  assert(logName, `sidecar log was not created in ${logDirectory}`);
  const log = join(logDirectory, logName);
  assert((await stat(database)).size > 0);
  assert((await stat(log)).size > 0);

  console.log(JSON.stringify({ mode, executablePath, screenshot, database, log, layout }, null, 2));
} finally {
  await electronApp.close();
}
