import { app, BrowserWindow, dialog, ipcMain } from "electron";
import { ChildProcess, spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { createServer } from "node:net";
import { existsSync } from "node:fs";
import { join, resolve } from "node:path";

const API_HOST = "127.0.0.1";
const HEALTH_TIMEOUT_MS = 20_000;
const HEALTH_RETRY_MS = 250;
const LAUNCH_TOKEN = randomBytes(32).toString("base64url");

let mainWindow: BrowserWindow | undefined;
let sidecar: ChildProcess | undefined;
let apiPort: number | undefined;
let sidecarReady = false;
let isQuitting = false;
let shutdownInProgress: Promise<void> | undefined;

function workspaceRoot(): string {
  return resolve(__dirname, "../../..");
}

function rendererPath(): string {
  return join(workspaceRoot(), "apps", "web", "dist", "index.html");
}

function frontendDevServerUrl(): string {
  return process.env.MATERIAL_WORKBENCH_DEV_SERVER_URL ?? "http://127.0.0.1:5180";
}

function sidecarCommand(port: number): { command: string; args: string[] } {
  return {
    command: "uv",
    args: [
      "run",
      "uvicorn",
      "main:app",
      "--app-dir",
      "backend/src",
      "--host",
      API_HOST,
      "--port",
      String(port),
    ],
  };
}

function sidecarOutput(process: ChildProcess): string {
  return [process.stdout, process.stderr]
    .map((stream) => (stream as (NodeJS.ReadableStream & { read?: () => Buffer | null }) | null)?.read?.()?.toString() ?? "")
    .filter(Boolean)
    .join("\n")
    .trim();
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

async function startSidecar(): Promise<void> {
  const port = await findAvailablePort();
  apiPort = port;
  const { command, args } = sidecarCommand(port);
  const childProcess = spawn(command, args, {
    cwd: workspaceRoot(),
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, WORKBENCH_LAUNCH_TOKEN: LAUNCH_TOKEN },
  });
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
            `Python API が起動に失敗しました (code: ${code ?? "none"}, signal: ${signal ?? "none"})。${sidecarOutput(childProcess) ? `\n${sidecarOutput(childProcess)}` : ""}`,
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
          `Python API が予期せず終了しました (code: ${code ?? "none"}, signal: ${signal ?? "none"})。${sidecarOutput(childProcess) ? `\n${sidecarOutput(childProcess)}` : ""}`,
        ),
      );
    }
  });
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

    const finish = () => resolveShutdown();
    process.once("exit", finish);

    // uv may spawn the Python interpreter as a child on Windows. Kill only the
    // process tree rooted at the PID we created, so unrelated local APIs remain untouched.
    const taskkill = spawn("taskkill", ["/PID", String(process.pid), "/T", "/F"], {
      windowsHide: true,
      stdio: "ignore",
    });
    taskkill.once("error", () => {
      process.kill();
      resolveShutdown();
    });
    taskkill.once("exit", finish);
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

app.whenReady()
  .then(async () => {
    ipcMain.on("workbench:runtime-config", (event) => {
      event.returnValue = apiPort ? {
        apiBaseUrl: `http://${API_HOST}:${apiPort}`,
        launchToken: LAUNCH_TOKEN,
      } : null;
    });
    await startSidecar();
    await createMainWindow();
  })
  .catch((error: unknown) => failAndQuit(error));

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
