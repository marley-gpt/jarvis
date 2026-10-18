import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("jarvis", {
  getSnapshot: () => ipcRenderer.invoke("jarvis:get-snapshot"),
  submit: (command: string) => ipcRenderer.invoke("jarvis:submit", command),
  respondToApproval: (approvalId: string, allow: boolean) => ipcRenderer.invoke("jarvis:approval", approvalId, allow),
  emergencyStop: () => ipcRenderer.invoke("jarvis:emergency-stop"),
  resetEmergencyStop: () => ipcRenderer.invoke("jarvis:reset-stop"),
  configureBrain: (apiKey: string | null) => ipcRenderer.invoke("jarvis:configure-brain", apiKey),
  checkForUpdates: () => ipcRenderer.invoke("jarvis:check-updates"),
  onSnapshot: (listener: (snapshot: unknown) => void) => {
    const wrapped = (_event: Electron.IpcRendererEvent, snapshot: unknown) => listener(snapshot);
    ipcRenderer.on("jarvis:snapshot", wrapped);
    return () => ipcRenderer.removeListener("jarvis:snapshot", wrapped);
  },
});

