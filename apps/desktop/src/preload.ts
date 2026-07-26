import { contextBridge, ipcRenderer } from "electron";

type RuntimeConfig = {
  apiBaseUrl: string;
  launchToken: string;
};

const config = ipcRenderer.sendSync("workbench:runtime-config") as RuntimeConfig | null;
if (!config) throw new Error("Desktop runtime configuration is unavailable.");

contextBridge.exposeInMainWorld("workbenchDesktop", Object.freeze({
  ...config,
  exportWorkspace: () => ipcRenderer.invoke("workbench:workspace-export"),
  prepareWorkspaceRestore: () => ipcRenderer.invoke("workbench:workspace-prepare-restore"),
  confirmWorkspaceRestore: () => ipcRenderer.invoke("workbench:workspace-confirm-restore"),
  cancelWorkspaceRestore: () => ipcRenderer.invoke("workbench:workspace-cancel-restore"),
  takeWorkspaceNotice: () => ipcRenderer.invoke("workbench:workspace-take-notice"),
}));
