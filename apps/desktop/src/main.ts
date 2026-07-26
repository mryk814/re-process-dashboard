import { app, BrowserWindow, dialog, ipcMain, session } from "electron";
import { ChildProcess, spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { createServer } from "node:net";
import { createWriteStream, existsSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

const API_HOST = "127.0.0.1";
const HEALTH_TIMEOUT_MS = 20_000;
const HEALTH_RETRY_MS = 250;
const LAUNCH_TOKEN = randomBytes(32).toString("base64url");
const sidecarOutputs = new WeakMap<ChildProcess, string>();

let mainWindow: BrowserWindow | undefined;
let sidecar: ChildProcess | undefined;
let apiPort: number | undefined;
let sidecarReady = false;
let isQuitting = false;
let shutdownInProgress: Promise<void> | undefined;
let sidecarLogPath: string | undefined;
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

function sidecarCommand(port: number): { command: string; args: string[]; cwd: string; env: NodeJS.ProcessEnv } {
  if (app.isPackaged) {
    const resources = process.resourcesPath;
    return {
      command: join(resources, "sidecar", "material-workbench-sidecar.exe"),
      args: [],
      cwd: resources,
      env: {
        ...process.env,
        WORKBENCH_API_HOST: API_HOST,
        WORKBENCH_API_PORT: String(port),
        WORKBENCH_LAUNCH_TOKEN: LAUNCH_TOKEN,
        WORKBENCH_DB_PATH: join(app.getPath("userData"), "workbench.db"),
        WORKBENCH_RESOURCE_ROOT: resources,
        WORKBENCH_SOURCE_PATH: join(resources, "data", "source", "material_workbench_tutorial_v2.xlsx"),
        WORKBENCH_FLANK_WEAR_SOURCE_PATH: join(resources, "data", "source", "cutting_tool_flank_wear_synthetic_dataset.xlsx"),
        PYTHONUTF8: "1",
        PYTHONIOENCODING: "utf-8",
      },
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
    env: {
      ...process.env,
      WORKBENCH_LAUNCH_TOKEN: LAUNCH_TOKEN,
      PYTHONUTF8: "1",
      PYTHONIOENCODING: "utf-8",
    },
  };
}

function sidecarOutput(process: ChildProcess): string {
  return sidecarOutputs.get(process)?.trim() ?? "";
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
    if (!isQuitting) {
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

  shutdownInProgress = new Promise<void>((resolveShutdown) => {
    const process = sidecar;
    sidecar = undefined;
    sidecarReady = false;

    if (!process?.pid || process.exitCode !== null) {
      resolveShutdown();
      return;
    }

    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
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
    setTimeout(() => {
      if (process.exitCode === null) process.kill();
      setTimeout(finish, 1_000);
    }, 5_000);
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
    await startSidecar();
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
  if (isQuitting || !sidecar) {
    return;
  }

  event.preventDefault();
  isQuitting = true;
  void stopSidecar().finally(() => app.quit());
});
