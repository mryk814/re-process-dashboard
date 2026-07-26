import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdir, readFile, readdir, rename, stat, writeFile } from "node:fs/promises";
import { basename, join, resolve } from "node:path";
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
let database;
let userData;

const PACKAGED_STARTUP_TIMEOUT_MS = 120_000;

const electronApp = await electron.launch({ executablePath, timeout: PACKAGED_STARTUP_TIMEOUT_MS });
try {
  const window = await electronApp.firstWindow({ timeout: PACKAGED_STARTUP_TIMEOUT_MS });
  await window
    .getByRole("heading", { name: "焼鈍条件の候補検討", level: 1 })
    .waitFor({ timeout: PACKAGED_STARTUP_TIMEOUT_MS });
  const secondInstance = spawn(executablePath, [], { env: process.env, stdio: "ignore" });
  const secondExit = await Promise.race([
    once(secondInstance, "exit"),
    new Promise((_, reject) => setTimeout(() => reject(new Error("second instance did not exit")), 30_000)),
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
  const authenticatedFetch = (path, init = {}) => fetch(`${runtime.apiBaseUrl}${path}`, {
    ...init,
    headers: {
      ...init.headers,
      "X-Workbench-Launch-Token": runtime.launchToken,
    },
  });
  const smokeProjectId = "default";
  const readSmokeProject = async () => {
    const response = await authenticatedFetch(`/api/projects/${smokeProjectId}`);
    assert.equal(response.status, 200);
    return response.json();
  };
  const updateSmokeProjectNotes = async (notes) => {
    const project = await readSmokeProject();
    const response = await authenticatedFetch(`/api/projects/${smokeProjectId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...project, notes }),
    });
    assert.equal(response.status, 200);
    assert.equal((await response.json()).notes, notes);
  };
  const transformsResponse = await authenticatedFetch("/api/transforms");
  assert.equal(transformsResponse.status, 200);
  const transforms = await transformsResponse.json();
  assert.equal(transforms.length, 1);
  const diagnosticsResponse = await authenticatedFetch("/api/developer/diagnostics");
  assert.equal(diagnosticsResponse.status, 200);
  const diagnostics = await diagnosticsResponse.json();
  const databaseCheck = diagnostics.checks.find((check) => check.id === "database");
  assert.equal(databaseCheck?.severity, "ok");
  assert.deepEqual(databaseCheck.details.policy, {
    foreign_keys: 1,
    busy_timeout: 5000,
    journal_mode: "delete",
    synchronous: 2,
    foreign_key_violations: 0,
  });
  const stageA = transforms[0];
  assert.equal(stageA.transform_id, "welding-stage-a-v1");
  assert.equal(stageA.outputs.length, 31);
  assert.equal(stageA.outputs.at(-1), "other");
  const editorResponse = await authenticatedFetch(
    `/api/transforms/${stageA.transform_id}/blend-editor`,
  );
  assert.equal(editorResponse.status, 200);
  const editor = await editorResponse.json();
  const scientificBlend = JSON.parse(await readFile(
    join(
      repositoryRoot,
      "models",
      "packages",
      "welding-stage-a-deterministic-v1",
      "smoke",
      "input.json",
    ),
    "utf8",
  ));
  const blend = {
    ...scientificBlend,
    schema_version: "sparse-blend/v1",
    balance_material_id: scientificBlend.items[0].material_id,
    commercial_catalog: editor.commercial_catalog,
    design_space: editor.design_space_ref,
  };
  const transformResponse = await authenticatedFetch(
    "/api/transforms/welding-stage-a-v1/execute",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ blend }),
    },
  );
  assert.equal(transformResponse.status, 200);
  const transformResult = await transformResponse.json();
  assert.equal(Object.keys(transformResult.material_composition).length, 31);
  const compositionTotal = Object.values(transformResult.material_composition)
    .reduce((total, value) => total + value, 0);
  // The bundled scientific golden rounds individual components to six
  // decimals, so use the same absolute tolerance as its contract test.
  assert(Math.abs(compositionTotal - 100) <= 2e-5);
  assert(transformResult.powder_blend_cost_yen_per_kg_core > 0);
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
    {
      projectId: "battery-degradation-v1-default",
      target: "capacity_percent",
      variable: "process.cycle_index",
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

  const backupMarker = `packaged-smoke-${mode}-before-backup`;
  await updateSmokeProjectNotes(backupMarker);
  const backupPath = join(artifacts, `packaged-${mode}-workspace.mdwb`);
  await electronApp.evaluate(async ({ dialog }, filePath) => {
    dialog.showSaveDialog = async () => ({ canceled: false, filePath });
  }, backupPath);
  await window.getByRole("button", { name: "ワークスペース" }).click();
  await window.getByRole("heading", { name: "ワークスペースの保管と復元" }).waitFor();
  await window.getByRole("button", { name: "保存先を選ぶ" }).click();
  const backupOutcome = await Promise.race([
    window.getByText(`${basename(backupPath)} を作成しました`).waitFor({
      timeout: PACKAGED_STARTUP_TIMEOUT_MS,
    }).then(() => ({ status: "created" })),
    window.getByRole("alert").waitFor({
      timeout: PACKAGED_STARTUP_TIMEOUT_MS,
    }).then(async () => ({
      status: "error",
      detail: await window.getByRole("alert").innerText(),
    })),
  ]);
  assert.equal(
    backupOutcome.status,
    "created",
    `Workspace backup failed: ${backupOutcome.detail ?? "unknown error"}`,
  );
  assert((await stat(backupPath)).size > 0);
  await window.getByRole("button", { name: "閉じる" }).click();
  const changedMarker = `packaged-smoke-${mode}-after-backup`;
  await updateSmokeProjectNotes(changedMarker);
  assert.equal((await readSmokeProject()).notes, changedMarker);

  const tamperedPath = join(artifacts, `packaged-${mode}-workspace-tampered.mdwb`);
  const tamperedBytes = await readFile(backupPath);
  tamperedBytes[Math.min(128, tamperedBytes.length - 1)] ^= 0xff;
  await writeFile(tamperedPath, tamperedBytes);
  await electronApp.evaluate(async ({ dialog }, filePath) => {
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [filePath] });
  }, tamperedPath);
  await window.getByRole("button", { name: "ワークスペース" }).click();
  await window.getByRole("button", { name: "ファイルを選ぶ" }).click();
  await window.getByRole("alert").waitFor({ timeout: PACKAGED_STARTUP_TIMEOUT_MS });
  assert(await window.getByRole("heading", { name: "焼鈍条件の候補検討", level: 1 }).isVisible());
  await window.getByRole("button", { name: "閉じる" }).click();

  const portableBackupPath = join(artifacts, "packaged-portable-workspace.mdwb");
  const restorePath = mode === "installed" ? portableBackupPath : backupPath;
  assert((await stat(restorePath)).size > 0);
  await electronApp.evaluate(async ({ dialog }, filePath) => {
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [filePath] });
  }, restorePath);
  await window.getByRole("button", { name: "ワークスペース" }).click();
  await window.getByRole("button", { name: "ファイルを選ぶ" }).click();
  await window.getByRole("button", { name: "この内容へ復元" }).waitFor({
    timeout: PACKAGED_STARTUP_TIMEOUT_MS,
  });
  await window.getByRole("button", { name: "この内容へ復元" }).click();
  await window.getByRole(
    "heading",
    { name: "焼鈍条件の候補検討", level: 1 },
  ).waitFor({ timeout: PACKAGED_STARTUP_TIMEOUT_MS });
  await window.getByText("Workspaceを復元し、APIの起動確認まで完了しました。")
    .waitFor({ timeout: PACKAGED_STARTUP_TIMEOUT_MS });
  const restoredMarker = mode === "installed"
    ? "packaged-smoke-portable-before-backup"
    : backupMarker;
  assert.equal((await readSmokeProject()).notes, restoredMarker);

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
  userData = mode === "portable"
    ? join(appRoot, "user-data")
    : join(process.env.LOCALAPPDATA, "Material Decision Workbench");
  database = join(userData, "workbench.db");
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

assert(database && userData);
const databaseFiles = await readdir(userData);
assert(!databaseFiles.includes("workbench.db-wal"));
assert(!databaseFiles.includes("workbench.db-shm"));
const movedDatabase = `${database}.move-check`;
await rename(database, movedDatabase);
await rename(movedDatabase, database);

const restartedApp = await electron.launch({
  executablePath,
  timeout: PACKAGED_STARTUP_TIMEOUT_MS,
});
try {
  const restartedWindow = await restartedApp.firstWindow({
    timeout: PACKAGED_STARTUP_TIMEOUT_MS,
  });
  await restartedWindow.getByRole(
    "heading",
    { name: "焼鈍条件の候補検討", level: 1 },
  ).waitFor({ timeout: PACKAGED_STARTUP_TIMEOUT_MS });
} finally {
  await restartedApp.close();
}
