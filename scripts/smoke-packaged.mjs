import assert from "node:assert/strict";
import { execFileSync, spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { once } from "node:events";
import { mkdir, readFile, readdir, rename, stat, writeFile } from "node:fs/promises";
import { basename, join, resolve } from "node:path";
import { performance } from "node:perf_hooks";
import { _electron as electron } from "playwright";


const repositoryRoot = resolve(import.meta.dirname, "..");
const appRoot = resolve(process.argv[2] ?? join(repositoryRoot, "release", "Evidence-Decision-Workbench-folder"));
const mode = process.argv[3] ?? "portable";
assert(["portable", "installed"].includes(mode));
const smokeRoot = join(repositoryRoot, "release", "smoke");
if (mode === "installed") process.env.LOCALAPPDATA = join(smokeRoot, "local-app-data");
const executablePath = join(appRoot, process.argv[4] ?? "Evidence Decision Workbench.exe");
const artifacts = join(repositoryRoot, "artifacts");
await mkdir(artifacts, { recursive: true });
let database;
let userData;
let lifecycleBenchmark;

const PACKAGED_STARTUP_TIMEOUT_MS = 120_000;
const sha256File = async (path) => createHash("sha256")
  .update(await readFile(path))
  .digest("hex");

const readProcessTreeMemory = (rootPid) => {
  const script = [
    "$ErrorActionPreference='Stop'",
    `$rootPid=${rootPid}`,
    "$all=Get-CimInstance Win32_Process",
    "$ids=@($rootPid)",
    "do {",
    "  $children=@($all | Where-Object { $ids -contains [int]$_.ParentProcessId } | ForEach-Object { [int]$_.ProcessId })",
    "  $new=@($children | Where-Object { $ids -notcontains $_ })",
    "  $ids=@($ids+$new | Select-Object -Unique)",
    "} while ($new.Count -gt 0)",
    "$processes=@(Get-Process -Id $ids -ErrorAction SilentlyContinue)",
    "[pscustomobject]@{",
    "  rootPid=$rootPid",
    "  processCount=$processes.Count",
    "  workingSetBytes=[int64](($processes | Measure-Object WorkingSet64 -Sum).Sum)",
    "  summedPeakWorkingSetBytes=[int64](($processes | Measure-Object PeakWorkingSet64 -Sum).Sum)",
    "  processes=@($processes | Select-Object Id,ProcessName,WorkingSet64,PeakWorkingSet64)",
    "} | ConvertTo-Json -Depth 4 -Compress",
  ].join("\n");
  return JSON.parse(execFileSync(
    "powershell.exe",
    ["-NoProfile", "-Command", script],
    { encoding: "utf8" },
  ));
};

const launchStarted = performance.now();
const electronApp = await electron.launch({
  executablePath,
  timeout: PACKAGED_STARTUP_TIMEOUT_MS,
});
try {
  const window = await electronApp.firstWindow({ timeout: PACKAGED_STARTUP_TIMEOUT_MS });
  await window
    .getByRole("heading", { name: "焼鈍条件の候補検討", level: 1 })
    .waitFor({ timeout: PACKAGED_STARTUP_TIMEOUT_MS });
  const launchToFirstUsableMs = performance.now() - launchStarted;
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
  userData = mode === "portable"
    ? join(appRoot, "user-data")
    : join(process.env.LOCALAPPDATA, "Material Decision Workbench");
  database = join(userData, "workbench.db");
  const lifecycleDatabaseBefore = (await stat(database)).size;
  const processTreeBeforeLifecycle = readProcessTreeMemory(
    electronApp.process().pid,
  );
  const timedJson = async (operation) => {
    const started = performance.now();
    const response = await operation();
    const headersMs = performance.now() - started;
    const body = new Uint8Array(await response.arrayBuffer());
    const bodyReceivedMs = performance.now() - started;
    assert(
      response.ok,
      `Data Lifecycle packaged probe failed: ${response.status} ${new TextDecoder().decode(body)}`,
    );
    const parsed = JSON.parse(new TextDecoder().decode(body));
    const parsedMs = performance.now() - started;
    return {
      parsed,
      status: response.status,
      headersMs,
      bodyReceivedMs,
      parsedMs,
      responseBytes: body.byteLength,
    };
  };
  const lifecycleRows = 1_000;
  const lifecycleContent = JSON.stringify(
    Array.from({ length: lifecycleRows }, (_, index) => ({
      id: `packaged-${String(index).padStart(6, "0")}`,
      x: index % 101,
      target: (index * 7) % 113,
    })),
  );
  const datasetCatalog = await timedJson(
    () => authenticatedFetch("/api/data-library/datasets"),
  );
  const profileRevision = datasetCatalog.parsed[0]?.profile_revision;
  assert(profileRevision?.id && profileRevision?.profile_digest);
  const connectorResult = await timedJson(
    () => authenticatedFetch("/api/data-lifecycle/connectors", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        schema_version: "source-connector/v1",
        name: `Packaged lifecycle ${mode}`,
        connector_type: "object_storage_json_v1",
        source_locator: `s3://packaged-benchmark.local/${mode}.json`,
        selection: {
          schema_version: "object-selection/v1",
          format: "json_array",
          primary_key: "id",
          included_fields: ["id", "x", "target"],
        },
        trigger_policy: "manual_only",
      }),
    }),
  );
  const recipeResult = await timedJson(
    () => authenticatedFetch("/api/data-lifecycle/recipes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        schema_version: "curation-recipe/v1",
        recipe_id: `packaged-lifecycle-${mode}`,
        version: 1,
        name: `Packaged lifecycle ${mode}`,
        steps: [
          { kind: "coerce_number_v1", fields: ["x", "target"] },
          { kind: "required_fields_v1", fields: ["id", "x"] },
          { kind: "target_eligibility_v1", fields: ["target"] },
        ],
      }),
    }),
  );
  const requestBody = JSON.stringify({
    schema_version: "source-fetch-request/v1",
    trigger_kind: "manual",
    object_content: lifecycleContent,
    object_version: `packaged-${mode}-1`,
  });
  const fetchResult = await timedJson(
    () => authenticatedFetch(
      `/api/data-lifecycle/connectors/${connectorResult.parsed.id}/fetch`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: requestBody,
      },
    ),
  );
  assert.equal(fetchResult.parsed.snapshot.row_count, lifecycleRows);
  const curationResult = await timedJson(
    () => authenticatedFetch(
      `/api/data-lifecycle/raw-snapshots/${fetchResult.parsed.snapshot.id}/curation-runs`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          recipe_resource_id: recipeResult.parsed.id,
          profile_revision_id: profileRevision.id,
          profile_digest: profileRevision.profile_digest,
        }),
      },
    ),
  );
  assert.equal(curationResult.parsed.rows.length, lifecycleRows);
  const approvalResult = await timedJson(
    () => authenticatedFetch(
      `/api/data-lifecycle/curation-runs/${curationResult.parsed.id}/approve`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          reason: "packaged lifecycle probe",
          overrides: [],
        }),
      },
    ),
  );
  const trainingResult = await timedJson(
    () => authenticatedFetch(
      `/api/data-lifecycle/canonical-dataset-revisions/${approvalResult.parsed.id}/training-snapshots`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          purpose: "packaged lifecycle probe",
          targets: [{ target_key: "target", field: "target" }],
          split: {
            strategy_id: "sorted-group-round-robin-v1",
            group_field: "id",
            folds: 5,
          },
        }),
      },
    ),
  );
  assert.equal(trainingResult.parsed.row_count, lifecycleRows);
  const detailResult = await timedJson(
    () => authenticatedFetch(
      `/api/data-lifecycle/connectors/${connectorResult.parsed.id}`,
    ),
  );
  assert.equal(detailResult.parsed.training_snapshots.length, 1);
  const lifecycleDatabaseAfter = (await stat(database)).size;
  const processTreeAfterLifecycle = readProcessTreeMemory(
    electronApp.process().pid,
  );
  lifecycleBenchmark = {
    schemaVersion: "packaged-data-lifecycle-benchmark/v1",
    mode,
    rowCount: lifecycleRows,
    columnCount: 3,
    sourceCharacters: lifecycleContent.length,
    sourceUtf8Bytes: Buffer.byteLength(lifecycleContent),
    requestBodyBytes: Buffer.byteLength(requestBody),
    launchToFirstUsableMs,
    databaseBeforeBytes: lifecycleDatabaseBefore,
    databaseAfterBytes: lifecycleDatabaseAfter,
    databaseIncrementBytes: lifecycleDatabaseAfter - lifecycleDatabaseBefore,
    processTreeBeforeLifecycle,
    processTreeAfterLifecycle,
    operations: {
      catalog: datasetCatalog,
      connector: connectorResult,
      recipe: recipeResult,
      fetch: fetchResult,
      curation: curationResult,
      approval: approvalResult,
      trainingSnapshot: trainingResult,
      detail: detailResult,
    },
  };
  for (const operation of Object.values(lifecycleBenchmark.operations)) {
    delete operation.parsed;
  }
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
  const initialProjectsResponse = await authenticatedFetch("/api/projects");
  assert.equal(initialProjectsResponse.status, 200);
  assert.deepEqual(
    (await initialProjectsResponse.json()).map(({ id }) => id),
    ["default"],
  );
  const galleryResponse = await authenticatedFetch("/api/sample-gallery");
  assert.equal(galleryResponse.status, 200);
  assert.deepEqual(
    new Set((await galleryResponse.json()).map(({ project_id }) => project_id)),
    new Set([
      "welding-stage-b-default",
      "battery-degradation-v1-default",
      "mpea-room-tensile-v1-default",
    ]),
  );
  const sampleInstallResponse = await authenticatedFetch("/api/sample-gallery", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_ids: [] }),
  });
  assert.equal(sampleInstallResponse.status, 200);
  assert.equal((await sampleInstallResponse.json()).length, 3);
  const responseCurveSamples = [
    {
      projectId: "battery-degradation-v1-default",
      target: "capacity_percent",
      variable: "process.cycle_index",
    },
  ];
  for (const task of responseCurveSamples) {
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

  // A non-material journey proves that the shared Project/Candidate/Run
  // surfaces do not depend on the annealing or welding examples.
  const taskCatalogResponse = await authenticatedFetch("/api/task-definitions");
  assert.equal(taskCatalogResponse.status, 200);
  const taskCatalog = await taskCatalogResponse.json();
  const flankTask = taskCatalog.find(
    (item) => item.definition.task_definition.id === "flank-wear-v1",
  );
  assert(flankTask);
  const datasetsResponse = await authenticatedFetch(
    "/api/data-library/datasets?include_gallery=true",
  );
  assert.equal(datasetsResponse.status, 200);
  assert(
    (await datasetsResponse.json()).some(
      (item) => item.profile_revision.profile_id === "cutting-flank-wear-v1",
    ),
  );
  const flankProjectResponse = await authenticatedFetch("/api/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: "Domain-neutral acceptance: 工具摩耗",
      task_id: "flank-wear-v1",
      target_values: { VB_max: 200 },
    }),
  });
  assert.equal(flankProjectResponse.status, 201);
  const flankProject = await flankProjectResponse.json();
  assert(flankProject.dataset_view_revision_id);
  assert(flankProject.model_package_ref_id);
  const flankCandidateResponse = await authenticatedFetch(
    `/api/projects/${flankProject.id}/candidates`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(flankTask.starter_candidate),
    },
  );
  assert.equal(flankCandidateResponse.status, 201);
  const flankCandidate = await flankCandidateResponse.json();
  const flankCandidateUrl = (
    `/api/projects/${flankProject.id}/candidates/${flankCandidate.id}`
  );
  const flankPreview = await authenticatedFetch(
    `${flankCandidateUrl}/preview?expected_revision=${flankCandidate.revision}`,
    { method: "POST" },
  );
  assert.equal(flankPreview.status, 200);
  assert.deepEqual(
    new Set(Object.keys((await flankPreview.json()).predictions)),
    new Set(["VB_mean", "VB_max"]),
  );
  const flankScreening = await authenticatedFetch(
    `/api/screening?project_id=${flankProject.id}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        purpose: "design_space_map",
        base_candidate_id: flankCandidate.id,
        base_inputs: flankCandidate.inputs,
        samples: 48,
        seed: 543,
        target: "VB_max",
        proposal: { support_policy: "allow_with_warning" },
        variables: {
          "process.cutting_speed_mpm": {
            mode: "range",
            min: 150,
            max: 250,
          },
        },
      }),
    },
  );
  assert.equal(flankScreening.status, 201);
  assert.equal((await flankScreening.json()).project_id, flankProject.id);
  const flankActivity = await authenticatedFetch(
    `${flankCandidateUrl}/decision-activities/robustness-analysis-v1/runs`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        expected_revision: flankCandidate.revision,
        parameters: {
          schema_version: "robustness-parameters/v1",
          sample_count: 8,
          seed: 543,
          tolerance_profile: {
            fields: {
              "process.cutting_speed_mpm": {
                kind: "absolute",
                amount: 1,
              },
            },
          },
        },
      }),
    },
  );
  assert.equal(flankActivity.status, 201);
  const flankSnapshot = await authenticatedFetch(
    `${flankCandidateUrl}/snapshots`,
    { method: "POST" },
  );
  assert.equal(flankSnapshot.status, 201);
  const flankSnapshotPayload = await flankSnapshot.json();
  const flankActual = await authenticatedFetch(
    `${flankCandidateUrl}/actuals?expected_revision=${flankCandidate.revision}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        property: "VB_max",
        mean: 188,
        std: 4,
        replicates: 3,
        unit: "µm",
        experiment_no: "DOMAIN-NEUTRAL-543",
      }),
    },
  );
  assert.equal(flankActual.status, 201);
  const flankActualPayload = await flankActual.json();
  const flankDecision = await authenticatedFetch(
    `/api/projects/${flankProject.id}/decision`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        candidate_id: flankCandidate.id,
        snapshot_id: flankActualPayload.snapshot_id ?? flankSnapshotPayload.id,
        note: "工具摩耗の実測と予測を確認",
      }),
    },
  );
  assert.equal(flankDecision.status, 200);
  const flankHistory = await authenticatedFetch(
    `/api/projects/${flankProject.id}/history`,
  );
  assert.equal(flankHistory.status, 200);
  const flankHistoryPayload = await flankHistory.json();
  const flankHistoryItem = flankHistoryPayload.candidates.find(
    (item) => item.candidate.id === flankCandidate.id,
  );
  assert(flankHistoryItem);
  assert.equal(flankHistoryItem.actuals.length, 1);
  assert(flankHistoryItem.snapshots.length >= 1);
  assert.equal(flankHistoryItem.decision.note, "工具摩耗の実測と予測を確認");
  const flankActivityRuns = await authenticatedFetch(
    `/api/projects/${flankProject.id}/decision-activity-runs`
      + `?candidate_id=${flankCandidate.id}`,
  );
  assert.equal(flankActivityRuns.status, 200);
  assert.equal((await flankActivityRuns.json()).length, 1);

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
  await window.getByRole("heading", { name: "ワークスペース" }).waitFor();
  await window.getByRole("button", { name: "保存場所を管理" }).click();
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
  const databaseBeforeTamperedRestore = await sha256File(database);
  await electronApp.evaluate(async ({ dialog }, filePath) => {
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [filePath] });
  }, tamperedPath);
  await window.getByRole("button", { name: "保存場所を管理" }).click();
  await window.getByRole("button", { name: "ファイルを選ぶ" }).click();
  await window.getByRole("alert").waitFor({ timeout: PACKAGED_STARTUP_TIMEOUT_MS });
  assert.equal(await sha256File(database), databaseBeforeTamperedRestore);
  assert.equal((await readSmokeProject()).notes, changedMarker);
  await window.getByRole("button", { name: "閉じる" }).click();

  const portableBackupPath = join(artifacts, "packaged-portable-workspace.mdwb");
  const restorePath = mode === "installed" ? portableBackupPath : backupPath;
  assert((await stat(restorePath)).size > 0);
  await electronApp.evaluate(async ({ dialog }, filePath) => {
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [filePath] });
  }, restorePath);
  await window.getByRole("button", { name: "保存場所を管理" }).click();
  await window.getByRole("button", { name: "ファイルを選ぶ" }).click();
  await window.getByRole("button", { name: "この内容へ復元" }).waitFor({
    timeout: PACKAGED_STARTUP_TIMEOUT_MS,
  });
  await window.getByRole("button", { name: "この内容へ復元" }).click();
  // 復元成功の通知は自動で閉じる。再起動後の画面見出しを先に待つと、
  // 遅い端末では通知の表示期間を使い切るため、receiptを先に捕捉する。
  await window.getByText("Workspaceを復元し、APIの起動確認まで完了しました。")
    .waitFor({ timeout: PACKAGED_STARTUP_TIMEOUT_MS });
  await window.getByRole(
    "heading",
    { name: "ワークスペース", exact: true },
  ).waitFor({ timeout: PACKAGED_STARTUP_TIMEOUT_MS });
  const restoredMarker = mode === "installed"
    ? "packaged-smoke-portable-before-backup"
    : backupMarker;
  assert.equal((await readSmokeProject()).notes, restoredMarker);
  const restoredProjects = await (
    await authenticatedFetch("/api/projects")
  ).json();
  const restoredFlankProject = restoredProjects.find(
    (project) => project.name === "Domain-neutral acceptance: 工具摩耗",
  );
  assert(restoredFlankProject);
  const restoredFlankHistory = await (
    await authenticatedFetch(`/api/projects/${restoredFlankProject.id}/history`)
  ).json();
  const restoredFlankItem = restoredFlankHistory.candidates.find(
    (item) => item.candidate.name === flankTask.starter_candidate.name,
  );
  assert(restoredFlankItem);
  assert.equal(restoredFlankItem.actuals.length, 1);
  assert(restoredFlankItem.snapshots.length >= 1);
  assert.equal(
    restoredFlankItem.decision.note,
    "工具摩耗の実測と予測を確認",
  );
  const restoredFlankActivities = await (
    await authenticatedFetch(
      `/api/projects/${restoredFlankProject.id}/decision-activity-runs`
        + `?candidate_id=${restoredFlankItem.candidate.id}`,
    )
  ).json();
  assert.equal(restoredFlankActivities.length, 1);

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

  console.log(JSON.stringify({
    mode,
    executablePath,
    screenshot,
    database,
    log,
    layout,
    lifecycleBenchmark,
  }, null, 2));
} finally {
  await electronApp.close();
}

assert(database && userData && lifecycleBenchmark);
const databaseFiles = await readdir(userData);
assert(!databaseFiles.includes("workbench.db-wal"));
assert(!databaseFiles.includes("workbench.db-shm"));
const movedDatabase = `${database}.move-check`;
await rename(database, movedDatabase);
await rename(movedDatabase, database);

const restartStarted = performance.now();
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
    { name: "ワークスペース", exact: true },
  ).waitFor({ timeout: PACKAGED_STARTUP_TIMEOUT_MS });
  lifecycleBenchmark.restartToFirstUsableMs = performance.now() - restartStarted;
} finally {
  await restartedApp.close();
}
await writeFile(
  join(artifacts, `data-lifecycle-packaged-${mode}.json`),
  `${JSON.stringify(lifecycleBenchmark, null, 2)}\n`,
  "utf8",
);
