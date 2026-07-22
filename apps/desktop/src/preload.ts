import { contextBridge, ipcRenderer } from "electron";

type RuntimeConfig = {
  apiBaseUrl: string;
  launchToken: string;
};

const config = ipcRenderer.sendSync("workbench:runtime-config") as RuntimeConfig | null;
if (!config) throw new Error("Desktop runtime configuration is unavailable.");

contextBridge.exposeInMainWorld("workbenchDesktop", Object.freeze({ ...config }));
