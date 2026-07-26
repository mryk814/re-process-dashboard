import { app, BrowserWindow, dialog, ipcMain, session, shell } from "electron";
import { ChildProcess, spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { createServer } from "node:net";
import { createWriteStream, existsSync, mkdirSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const API_HOST = "127.0.0.1";
// A first packaged launch creates and migrates the local workspace database,
// then validates every bundled Dataset and Model Package.  On a typical
// Windows machine that cold path takes about 25 seconds, while subsequent
// launches are much faster.
const HEALTH_TIMEOUT_MS = 90_000;
const HEALTH_RETRY_MS = 250;
const LAUNCH_TOKEN = randomBytes(32).toString("base64url");
const sidecarOutputs = new WeakMap<ChildProcess, string>();
const expectedSidecarExits = new WeakSet<ChildProcess>();

type WorkspaceManifestSummary = {
  bundleId: string;
  createdAt: string;
  appVersion: string;
  projectCount: number;
  candidateCount: number;
  snapshotCount: number;
  activityCount: number;
  chainCount: number;
  sourceLifecycleCount: number;
  resourceCount: number;
  warnings: string[];
};

type WorkspaceOperationResult =
  | { status: "cancelled" }
  | { status: "created"; fileName: string; sizeBytes: number; summary: WorkspaceManifestSummary }
  | { status: "prepared"; fileName: string; summary: WorkspaceManifestSummary }
  | { status: "restored"; summary: WorkspaceManifestSummary };

type MaintenanceResult = Record<string, unknown>;

let mainWindow: BrowserWindow | undefined;
let sidecar: ChildProcess | undefined;
let apiPort: number | undefined;
let sidecarReady = false;
let isQuitting = false;
let shutdownInProgress: Promise<void> | undefined;
let sidecarLogPath: string | undefined;
let workspaceOperation: Promise<unknown> | undefined;
let preparedRestore: { token: string; fileName: string; summary: WorkspaceManifestSummary } | undefined;
let quitAfterWorkspaceOperation = false;
let workspaceNotice: { tone: "success" | "error"; message: string } | undefined;
const hasSingleInstanceLock = app.requestSingleInstanceLock();
if (!hasSingleInstanceLock) app.quit();

function workspaceRoot(): string {
  return resolve(__dirname, "../../..");
}

function portableRoot(): string | undefined {
  if (!app.isPackaged) return undefined;
  const root = dirname(process.execPath);
  return existsSync(join(root, "portable.marker")) ? root : undefined;
}

function configureUserDataPath(): void {
  if (!app.isPackaged) return;
  const root = portableRoot()
    ?? join(process.env.LOCALAPPDATA ?? join(dirname(app.getPath("appData")), "Local"), "Material Decision Workbench");
  const userData = portableRoot() ? join(root, "user-data") : root;
  mkdirSync(userData, { recursive: true });
  app.setPath("userData", userData);
}

function rendererPath(): string {
  return join(workspaceRoot(), "apps", "web", "dist", "index.html");
}

function frontendDevServerUrl(): string {
  return process.env.MATERIAL_WORKBENCH_DEV_SERVER_URL ?? "http://127.0.0.1:5180";
}

function workspaceDatabasePath(): string {
  return resolve(
    process.env.WORKBENCH_DB_PATH
      ?? (app.isPackaged
        ? join(app.getPath("userData"), "workbench.db")
        : join(workspaceRoot(), "data", "workbench.db")),
  );
}

function workspaceDataLibraryPath(): string {
  return resolve(
    process.env.WORKBENCH_DATA_LIBRARY_PATH
      ?? join(dirname(workspaceDatabasePath()), "data-library"),
  );
}

function sidecarEnvironment(port?: number): NodeJS.ProcessEnv {
  const resources = app.isPackaged ? process.resourcesPath : workspaceRoot();
  return {
    ...process.env,
    ...(port === undefined ? {} : {
      WORKBENCH_API_HOST: API_HOST,
      WORKBENCH_API_PORT: String(port),
      WORKBENCH_LAUNCH_TOKEN: LAUNCH_TOKEN,
    }),
    WORKBENCH_DB_PATH: workspaceDatabasePath(),
    WORKBENCH_DATA_LIBRARY_PATH: workspaceDataLibraryPath(),
    ...(app.isPackaged ? {
      WORKBENCH_RESOURCE_ROOT: resources,
      WORKBENCH_SOURCE_PATH: join(resources, "data", "source", "material_workbench_tutorial_v2.xlsx"),
      WORKBENCH_FLANK_WEAR_SOURCE_PATH: join(resources, "data", "source", "cutting_tool_flank_wear_synthetic_dataset.xlsx"),
    } : {}),
    PYTHONUTF8: "1",
    PYTHONIOENCODING: "utf-8",
  };
}

function sidecarCommand(port: number): { command: string; args: string[]; cwd: string; env: NodeJS.ProcessEnv } {
  if (app.isPackaged) {
    const resources = process.resourcesPath;
    return {
      command: join(resources, "sidecar", "material-workbench-sidecar.exe"),
      args: [],
      cwd: resources,
      env: sidecarEnvironment(port),
    };
  }
  return {
    command: "uv",
    args: [
      "run",
      "python",
      "-m",
      "uvicorn",
      "main:app",
      "--app-dir",
      "backend/src",
      "--host",
      API_HOST,
      "--port",
      String(port),
    ],
    cwd: workspaceRoot(),
    env: sidecarEnvironment(port),
  };
}

function maintenanceCommand(args: string[]): {
  command: string;
  args: string[];
  cwd: string;
  env: NodeJS.ProcessEnv;
} {
  if (app.isPackaged) {
    const resources = process.resourcesPath;
    return {
      command: join(resources, "sidecar", "material-workbench-sidecar.exe"),
      args: ["workspace", ...args],
      cwd: resources,
      env: sidecarEnvironment(),
    };
  }
  return {
    command: "uv",
    args: ["run", "python", "backend/src/sidecar.py", "workspace", ...args],
    cwd: workspaceRoot(),
    env: sidecarEnvironment(),
  };
}

function sidecarOutput(process: ChildProcess): string {
  return sidecarOutputs.get(process)?.trim() ?? "";
}

function runMaintenance(args: string[]): Promise<MaintenanceResult> {
  const command = maintenanceCommand([
    ...args,
    "--database",
    workspaceDatabasePath(),
    "--data-library",
    workspaceDataLibraryPath(),
  ]);
  return new Promise((resolveResult, rejectResult) => {
    const process = spawn(command.command, command.args, {
      cwd: command.cwd,
      env: command.env,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    const capture = (current: string, chunk: Buffer) =>
      `${current}${chunk.toString("utf8")}`.slice(-4 * 1024 * 1024);
    process.stdout?.on("data", (chunk: Buffer) => {
      stdout = capture(stdout, chunk);
    });
    process.stderr?.on("data", (chunk: Buffer) => {
      stderr = capture(stderr, chunk);
    });
    process.once("error", (error) => rejectResult(error));
    process.once("exit", (code, signal) => {
      const lines = stdout.trim().split(/\r?\n/).filter(Boolean);
      let payload: MaintenanceResult | undefined;
      try {
        payload = JSON.parse(lines.at(-1) ?? "") as MaintenanceResult;
      } catch {
        // The maintenance CLI contract is one JSON object on stdout.
      }
      if (code === 0 && payload) {
        resolveResult(payload);
        return;
      }
      let detail = stderr.trim() || stdout.trim();
      try {
        const errorPayload = JSON.parse(
          detail.split(/\r?\n/).filter(Boolean).at(-1) ?? "",
        ) as { message?: unknown };
        if (typeof errorPayload.message === "string") detail = errorPayload.message;
      } catch {
        // Keep the captured process output.
      }
      rejectResult(
        new Error(
          detail || `Workspace処理が終了しました (code: ${code ?? "none"}, signal: ${signal ?? "none"})。`,
        ),
      );
    });
  });
}

function recordValue(record: unknown, key: string): unknown {
  return typeof record === "object" && record !== null ? Reflect.get(record, key) : undefined;
}

function diagnosticWarnings(diagnostics: unknown): string[] {
  return Array.isArray(diagnostics)
    ? diagnostics.flatMap((item) => (
      recordValue(item, "status") === "warning" && typeof recordValue(item, "detail") === "string"
        ? [recordValue(item, "detail") as string]
        : []
    ))
    : [];
}

function workspaceManifestSummary(manifest: unknown): WorkspaceManifestSummary {
  const tableCounts = recordValue(manifest, "table_counts");
  const count = (table: string) => {
    const value = recordValue(tableCounts, table);
    return typeof value === "number" ? value : 0;
  };
  const resources = recordValue(manifest, "bundled_resources");
  const diagnostics = recordValue(manifest, "diagnostics");
  const warnings = diagnosticWarnings(diagnostics);
  return {
    bundleId: String(recordValue(manifest, "bundle_id") ?? ""),
    createdAt: String(recordValue(manifest, "created_at") ?? ""),
    appVersion: String(recordValue(manifest, "app_version") ?? ""),
    projectCount: count("projects"),
    candidateCount: count("candidate_revisions"),
    snapshotCount: count("snapshots"),
    activityCount: count("decision_activity_runs"),
    chainCount:
      count("chain_snapshot_records")
      + count("chain_distribution_runs")
      + count("chain_analysis_variant_records")
      + count("chain_execution_state")
      + count("chain_stage_memo"),
    sourceLifecycleCount:
      count("source_connectors")
      + count("source_fetch_attempts")
      + count("raw_source_snapshots")
      + count("curation_recipes")
      + count("source_curation_runs")
      + count("canonical_dataset_approvals")
      + count("approved_training_snapshots"),
    resourceCount: Array.isArray(resources) ? resources.length : 0,
    warnings,
  };
}

function exclusiveWorkspaceOperation<T>(operation: () => Promise<T>): Promise<T> {
  if (workspaceOperation) {
    return Promise.reject(new Error("別のWorkspace処理が進行中です。完了してからお試しください。"));
  }
  const current = operation();
  workspaceOperation = current;
  return current.finally(() => {
    if (workspaceOperation === current) workspaceOperation = undefined;
  });
}

function timestampForFilename(date = new Date()): string {
  return date.toISOString().replace(/[-:]/g, "").replace(/\..+/, "").replace("T", "-");
}

function startupFailureMessage(
  output: string,
  code: number | null,
  signal: NodeJS.Signals | null,
): string {
  const marker = output.match(/WORKBENCH_STARTUP_ERROR\s+(\{[^\r\n]+\})/);
  if (marker) {
    try {
      const diagnosis = JSON.parse(marker[1]) as {
        label?: string;
        detail?: string;
        error_type?: string;
      };
      const label = diagnosis.label || "起動処理";
      const detail = diagnosis.detail || diagnosis.error_type || "原因を特定できませんでした";
      return `${label}の準備に失敗しました。\n${detail}${sidecarLogPath ? `\n\n診断ログ: ${sidecarLogPath}` : ""}`;
    } catch {
      // Fall through to the generic summary while preserving the log.
    }
  }
  return `Python API が起動に失敗しました (code: ${code ?? "none"}, signal: ${signal ?? "none"})。${
    sidecarLogPath ? `\n診断ログ: ${sidecarLogPath}` : ""
  }`;
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolveWait) => setTimeout(resolveWait, milliseconds));
}

async function findAvailablePort(): Promise<number> {
  return new Promise<number>((resolveAvailable, rejectAvailable) => {
    const server = createServer();
    server.once("error", rejectAvailable);
    server.listen(0, API_HOST, () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close();
        rejectAvailable(new Error("空きloopback portを取得できませんでした。"));
        return;
      }
      server.close((error) => (error ? rejectAvailable(error) : resolveAvailable(address.port)));
    });
  });
}

async function waitForHealthySidecar(process: ChildProcess, port: number): Promise<void> {
  const deadline = Date.now() + HEALTH_TIMEOUT_MS;
  let lastFailure = "";
  const healthUrl = `http://${API_HOST}:${port}/health`;

  while (Date.now() < deadline) {
    if (process.exitCode !== null) {
      const output = sidecarOutput(process);
      throw new Error(
        `Python API が起動直後に終了しました (exit code ${process.exitCode})。${output ? `\n${output}` : ""}`,
      );
    }

    try {
      const response = await fetch(healthUrl, { headers: { "X-Workbench-Launch-Token": LAUNCH_TOKEN } });
      if (response.ok && process.exitCode === null) {
        return;
      }
      lastFailure = `health endpoint returned HTTP ${response.status}`;
    } catch (error) {
      lastFailure = error instanceof Error ? error.message : String(error);
    }

    await wait(HEALTH_RETRY_MS);
  }

  throw new Error(
    `Python API の起動を ${HEALTH_TIMEOUT_MS / 1000} 秒待ちましたが、${healthUrl} が応答しません。${lastFailure ? `\n最後の確認: ${lastFailure}` : ""}`,
  );
}

async function startSidecarOnPort(port: number): Promise<void> {
  apiPort = port;
  const { command, args, cwd, env } = sidecarCommand(port);
  const logDirectory = join(app.getPath("userData"), "logs");
  mkdirSync(logDirectory, { recursive: true });
  const logPath = join(logDirectory, `sidecar-${timestampForFilename()}.log`);
  sidecarLogPath = logPath;
  const logStream = createWriteStream(logPath, { flags: "w" });
  const childProcess = spawn(command, args, {
    cwd,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
    env,
  });
  const captureOutput = (chunk: Buffer) => {
    const next = `${sidecarOutputs.get(childProcess) ?? ""}${chunk.toString("utf8")}`;
    sidecarOutputs.set(childProcess, next.slice(-16_384));
  };
  childProcess.stdout?.on("data", captureOutput);
  childProcess.stderr?.on("data", captureOutput);
  childProcess.stdout?.pipe(logStream, { end: false });
  childProcess.stderr?.pipe(logStream, { end: false });
  childProcess.once("exit", () => logStream.end());
  sidecar = childProcess;

  const startupFailure = new Promise<never>((_, reject) => {
    childProcess.once("error", (error) => {
      reject(
        new Error(
          `Python API を起動できませんでした。\`${command}\` が実行可能か確認してください。\n${error.message}`,
        ),
      );
    });
    childProcess.once("exit", (code, signal) => {
      if (!sidecarReady) {
        reject(
          new Error(
            startupFailureMessage(sidecarOutput(childProcess), code, signal),
          ),
        );
      }
    });
  });

  await Promise.race([waitForHealthySidecar(childProcess, port), startupFailure]);
  sidecarReady = true;

  childProcess.once("exit", (code, signal) => {
    sidecarReady = false;
    if (sidecar === childProcess) sidecar = undefined;
    if (!isQuitting && !expectedSidecarExits.has(childProcess)) {
      void failAndQuit(
        new Error(
          `Python API が予期せず終了しました (code: ${code ?? "none"}, signal: ${signal ?? "none"})。${
            sidecarLogPath ? `\n診断ログ: ${sidecarLogPath}` : ""
          }`,
        ),
      );
    }
  });
}

async function startSidecar(): Promise<void> {
  let lastError: unknown;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      await startSidecarOnPort(await findAvailablePort());
      return;
    } catch (error) {
      lastError = error;
      const message = error instanceof Error ? error.message : String(error);
      if (!/address already in use|WinError 10048|EADDRINUSE/i.test(message) || attempt === 2) throw error;
    }
  }
  throw lastError;
}

function stopSidecar(): Promise<void> {
  if (shutdownInProgress) {
    return shutdownInProgress;
  }

  const stopping = new Promise<void>((resolveShutdown, rejectShutdown) => {
    const process = sidecar;
    sidecar = undefined;
    sidecarReady = false;

    if (!process?.pid || process.exitCode !== null) {
      resolveShutdown();
      return;
    }
    expectedSidecarExits.add(process);

    let finished = false;
    let timeout: NodeJS.Timeout | undefined;
    const finish = () => {
      if (finished) return;
      finished = true;
      if (timeout) clearTimeout(timeout);
      resolveShutdown();
    };
    process.once("exit", finish);

    // uv may spawn the Python interpreter as a child on Windows. Kill only the
    // process tree rooted at the PID we created, so unrelated local APIs remain untouched.
    const taskkill = spawn("taskkill", ["/PID", String(process.pid), "/T", "/F"], {
      windowsHide: true,
      stdio: "ignore",
    });
    taskkill.once("error", () => {
      if (process.exitCode === null) process.kill();
    });
    taskkill.once("exit", (code) => {
      if (code !== 0 && process.exitCode === null) process.kill();
    });
    timeout = setTimeout(() => {
      if (process.exitCode === null) process.kill();
      timeout = setTimeout(() => {
        if (process.exitCode === null) {
          finished = true;
          rejectShutdown(
            new Error(
              "Python APIを停止できなかったため、Workspaceの切替を中止しました。",
            ),
          );
        }
      }, 1_000);
    }, 5_000);
  });
  shutdownInProgress = stopping.finally(() => {
    if (shutdownInProgress) shutdownInProgress = undefined;
  });
  return shutdownInProgress;
}

async function createMainWindow(): Promise<void> {
  const window = new BrowserWindow({
    width: 1600,
    height: 1000,
    minWidth: 1180,
    minHeight: 760,
    backgroundColor: "#F5F7FA",
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      nodeIntegrationInSubFrames: false,
      sandbox: true,
      preload: join(__dirname, "preload.js"),
      // Keep web security enabled. The loopback API explicitly permits Origin: null for this file renderer.
      webSecurity: true,
    },
  });
  mainWindow = window;

  window.once("ready-to-show", () => window.show());
  window.on("closed", () => {
    if (mainWindow === window) {
      mainWindow = undefined;
    }
  });

  if (app.isPackaged) {
    const productionRenderer = rendererPath();
    if (!existsSync(productionRenderer)) {
      throw new Error(
        `ビルド済みフロントエンドが見つかりません: ${productionRenderer}\n先に apps/web の build を実行してください。`,
      );
    }
    await window.loadFile(productionRenderer);
    return;
  }

  await window.loadURL(frontendDevServerUrl());
}

async function failAndQuit(error: unknown): Promise<void> {
  if (isQuitting) {
    return;
  }
  isQuitting = true;
  const message = error instanceof Error ? error.message : String(error);
  dialog.showErrorBox("Material Decision Workbench を起動できません", message);
  await stopSidecar();
  app.quit();
}

function workspaceSummaryText(summary: WorkspaceManifestSummary): string {
  return [
    `作成日時: ${summary.createdAt || "不明"}`,
    `作成アプリ版: ${summary.appVersion || "不明"}`,
    `プロジェクト ${summary.projectCount}件 / 候補編集版 ${summary.candidateCount}件`,
    `予測snapshot ${summary.snapshotCount}件 / 検討アクティビティ ${summary.activityCount}件`,
    `Chain証拠 ${summary.chainCount}件 / Source lifecycle ${summary.sourceLifecycleCount}件`,
    `同梱Data Asset・Model Package ${summary.resourceCount}件`,
    ...(summary.warnings.length ? ["", `注意: ${summary.warnings.join(" / ")}`] : []),
  ].join("\n");
}

async function cancelPreparedRestore(): Promise<void> {
  const pending = preparedRestore;
  if (!pending) return;
  await runMaintenance(["cancel", "--restore-token", pending.token]);
  if (preparedRestore?.token === pending.token) preparedRestore = undefined;
}

async function prepareRestoreFromNativeDialog(): Promise<WorkspaceOperationResult> {
  const options: Electron.OpenDialogOptions = {
    title: "Workspaceバックアップを選択",
    properties: ["openFile"],
    filters: [
      { name: "Material Decision Workspace", extensions: ["mdwb"] },
      { name: "すべてのファイル", extensions: ["*"] },
    ],
  };
  const selection = mainWindow
    ? await dialog.showOpenDialog(mainWindow, options)
    : await dialog.showOpenDialog(options);
  const source = selection.filePaths[0];
  if (selection.canceled || !source) return { status: "cancelled" };
  await cancelPreparedRestore();
  const result = await runMaintenance(["prepare", "--source", source]);
  const token = recordValue(result, "restore_token");
  const manifest = recordValue(result, "manifest");
  if (typeof token !== "string" || !manifest) {
    throw new Error("Workspaceバックアップの検証結果が不正です。");
  }
  const summary = workspaceManifestSummary(manifest);
  summary.warnings = [
    ...new Set([
      ...summary.warnings,
      ...diagnosticWarnings(recordValue(result, "diagnostics")),
    ]),
  ];
  preparedRestore = { token, fileName: basename(source), summary };
  return {
    status: "prepared",
    fileName: basename(source),
    summary,
  };
}

async function commitPreparedRestore(): Promise<WorkspaceOperationResult> {
  const pending = preparedRestore;
  if (!pending) throw new Error("検証済みのWorkspaceバックアップがありません。");
  const restartPort = apiPort ?? await findAvailablePort();
  let committed = false;
  await stopSidecar();
  try {
    await runMaintenance(["commit", "--restore-token", pending.token]);
    committed = true;
    await startSidecarOnPort(restartPort);
    await runMaintenance(["finalize", "--restore-token", pending.token]);
    preparedRestore = undefined;
    workspaceNotice = {
      tone: "success",
      message: "Workspaceを復元し、APIの起動確認まで完了しました。",
    };
    mainWindow?.webContents.reload();
    return { status: "restored", summary: pending.summary };
  } catch (error) {
    await stopSidecar();
    if (committed) {
      try {
        await runMaintenance(["rollback", "--restore-token", pending.token]);
        committed = false;
      } catch (rollbackError) {
        const detail = rollbackError instanceof Error ? rollbackError.message : String(rollbackError);
        relaunchForWorkspaceRecovery(
          "Workspaceの自動切戻しを完了できません",
          `復元後の起動確認と元Workspaceへの切戻しに失敗しました。アプリを終了し、診断ログを確認してください。\n${detail}`,
        );
      }
    } else {
      await cancelPreparedRestore();
    }
    preparedRestore = undefined;
    try {
      await startSidecarOnPort(restartPort);
    } catch (restartError) {
      const detail = restartError instanceof Error ? restartError.message : String(restartError);
      relaunchForWorkspaceRecovery(
        "WorkspaceのAPIを再起動できません",
        `現在のWorkspaceは維持しました。復旧モードで再起動します。\n${detail}`,
      );
    }
    workspaceNotice = {
      tone: "error",
      message: "Workspaceを復元できなかったため、元の内容へ戻しました。",
    };
    mainWindow?.webContents.reload();
    const detail = error instanceof Error ? error.message : String(error);
    throw new Error(`Workspaceを復元できませんでした。現在のWorkspaceは維持されています。\n${detail}`);
  }
}

function relaunchForWorkspaceRecovery(title: string, detail: string): never {
  dialog.showErrorBox(
    title,
    `${detail}${sidecarLogPath ? `\n\n診断ログ: ${sidecarLogPath}` : ""}`,
  );
  app.relaunch();
  app.exit(1);
  throw new Error(detail);
}

function assertTrustedWorkspaceSender(event: Electron.IpcMainInvokeEvent): void {
  const frame = event.senderFrame;
  const mainFrame = mainWindow?.webContents.mainFrame;
  if (
    !mainWindow
    || event.sender !== mainWindow.webContents
    || !frame
    || frame !== mainFrame
  ) {
    throw new Error("Workspace操作を許可できない画面です。");
  }
  const actual = new URL(frame.url);
  const trusted = app.isPackaged
    ? actual.protocol === "file:"
      && resolve(fileURLToPath(actual)).toLocaleLowerCase("en-US")
        === resolve(rendererPath()).toLocaleLowerCase("en-US")
    : actual.origin === new URL(frontendDevServerUrl()).origin;
  if (!trusted) throw new Error("Workspace操作を許可できないURLです。");
}

function registerWorkspaceIpc(): void {
  ipcMain.handle("workbench:workspace-export", (event) => {
    assertTrustedWorkspaceSender(event);
    return exclusiveWorkspaceOperation(async (): Promise<WorkspaceOperationResult> => {
      const options: Electron.SaveDialogOptions = {
        title: "Workspaceバックアップを保存",
        defaultPath: `material-workbench-${timestampForFilename()}.mdwb`,
        filters: [{ name: "Material Decision Workspace", extensions: ["mdwb"] }],
      };
      const selection = mainWindow
        ? await dialog.showSaveDialog(mainWindow, options)
        : await dialog.showSaveDialog(options);
      if (selection.canceled || !selection.filePath) return { status: "cancelled" };
      const result = await runMaintenance([
        "export",
        "--destination",
        selection.filePath,
        "--app-version",
        app.getVersion(),
      ]);
      const manifest = recordValue(result, "manifest");
      const size = recordValue(result, "size_bytes");
      if (!manifest || typeof size !== "number") {
        throw new Error("Workspaceバックアップの作成結果が不正です。");
      }
      return {
        status: "created",
        fileName: basename(selection.filePath),
        sizeBytes: size,
        summary: workspaceManifestSummary(manifest),
      };
    });
  });
  ipcMain.handle("workbench:workspace-prepare-restore", (event) => {
    assertTrustedWorkspaceSender(event);
    return exclusiveWorkspaceOperation(prepareRestoreFromNativeDialog);
  });
  ipcMain.handle("workbench:workspace-confirm-restore", (event) => {
    assertTrustedWorkspaceSender(event);
    return exclusiveWorkspaceOperation(commitPreparedRestore);
  });
  ipcMain.handle("workbench:workspace-cancel-restore", (event) => {
    assertTrustedWorkspaceSender(event);
    return exclusiveWorkspaceOperation(async () => {
      await cancelPreparedRestore();
      return { status: "cancelled" } satisfies WorkspaceOperationResult;
    });
  });
  ipcMain.handle("workbench:workspace-take-notice", (event) => {
    assertTrustedWorkspaceSender(event);
    const notice = workspaceNotice ?? null;
    workspaceNotice = undefined;
    return notice;
  });
}

async function recoverStartupFromBackup(initialError: unknown): Promise<boolean> {
  let failure = initialError;
  for (;;) {
    const detail = failure instanceof Error ? failure.message : String(failure);
    const choice = await dialog.showMessageBox({
      type: "error",
      title: "Material Decision Workbench を起動できません",
      message: "ワークスペースを開けませんでした。",
      detail,
      buttons: ["バックアップから復元", "診断ログを開く", "終了"],
      defaultId: 0,
      cancelId: 2,
      noLink: true,
    });
    if (choice.response === 1) {
      if (sidecarLogPath) await shell.openPath(sidecarLogPath);
      continue;
    }
    if (choice.response !== 0) return false;
    try {
      const prepared = await prepareRestoreFromNativeDialog();
      if (prepared.status !== "prepared") continue;
      const confirmation = await dialog.showMessageBox({
        type: "warning",
        title: "Workspaceを復元",
        message: `${prepared.fileName} の内容へ切り替えます。`,
        detail: `${workspaceSummaryText(prepared.summary)}\n\n検証と起動確認に失敗した場合、現在のWorkspaceは維持されます。`,
        buttons: ["復元する", "選び直す", "終了"],
        defaultId: 0,
        cancelId: 2,
        noLink: true,
      });
      if (confirmation.response === 2) {
        await cancelPreparedRestore();
        return false;
      }
      if (confirmation.response === 1) {
        await cancelPreparedRestore();
        continue;
      }
      await commitPreparedRestore();
      return true;
    } catch (error) {
      failure = error;
    }
  }
}

if (hasSingleInstanceLock) {
  try {
    configureUserDataPath();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    dialog.showErrorBox("保存先を準備できません", message);
    app.exit(1);
  }
}

function configureSidecarRequestHeaders(): void {
  if (!apiPort) throw new Error("sidecar port is unavailable");
  session.defaultSession.webRequest.onBeforeSendHeaders(
    { urls: [`http://${API_HOST}:${apiPort}/*`] },
    (details, callback) => {
      details.requestHeaders["X-Workbench-Launch-Token"] = LAUNCH_TOKEN;
      callback({ requestHeaders: details.requestHeaders });
    },
  );
}

app.whenReady()
  .then(async () => {
    if (!hasSingleInstanceLock) return;
    ipcMain.on("workbench:runtime-config", (event) => {
      event.returnValue = apiPort ? {
        apiBaseUrl: `http://${API_HOST}:${apiPort}`,
        launchToken: LAUNCH_TOKEN,
      } : null;
    });
    registerWorkspaceIpc();
    await runMaintenance(["recover"]);
    try {
      await startSidecar();
    } catch (error) {
      if (!await recoverStartupFromBackup(error)) {
        isQuitting = true;
        await stopSidecar();
        app.quit();
        return;
      }
    }
    configureSidecarRequestHeaders();
    await createMainWindow();
  })
  .catch((error: unknown) => failAndQuit(error));

app.on("second-instance", () => {
  if (!mainWindow) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
});

app.on("activate", () => {
  if (!mainWindow && sidecarReady && !isQuitting) {
    void createMainWindow().catch((error: unknown) => failAndQuit(error));
  }
});

app.on("window-all-closed", () => {
  app.quit();
});

app.on("before-quit", (event) => {
  if (workspaceOperation) {
    event.preventDefault();
    if (!quitAfterWorkspaceOperation) {
      quitAfterWorkspaceOperation = true;
      const options: Electron.MessageBoxOptions = {
        type: "info",
        title: "Workspace処理を完了しています",
        message: "安全に終了できる状態になるまでお待ちください。",
        detail: "バックアップまたは復元の途中ではアプリを終了しません。",
        buttons: ["OK"],
        noLink: true,
      };
      void (mainWindow
        ? dialog.showMessageBox(mainWindow, options)
        : dialog.showMessageBox(options));
      void workspaceOperation.then(
        () => { if (quitAfterWorkspaceOperation) app.quit(); },
        () => { if (quitAfterWorkspaceOperation) app.quit(); },
      );
    }
    return;
  }
  if (isQuitting || !sidecar) {
    return;
  }

  event.preventDefault();
  isQuitting = true;
  void stopSidecar().finally(() => app.quit());
});
