import path from "node:path";
import { app, BrowserWindow, ipcMain, shell } from "electron";
import dotenv from "dotenv";
import { JarvisEngine } from "./engine.js";

dotenv.config();

let window: BrowserWindow | null = null;
const engine = new JarvisEngine();

function createWindow(): void {
  window = new BrowserWindow({
    width: 1500,
    height: 940,
    minWidth: 1120,
    minHeight: 720,
    backgroundColor: "#05090d",
    titleBarStyle: "hiddenInset",
    webPreferences: {
      preload: path.join(app.getAppPath(), "dist-electron", "preload", "index.cjs"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
    },
  });

  window.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://")) void shell.openExternal(url);
    return { action: "deny" };
  });
  window.webContents.on("will-navigate", (event) => event.preventDefault());

  const devUrl = process.env.VITE_DEV_SERVER_URL;
  if (devUrl) void window.loadURL(devUrl);
  else void window.loadFile(path.join(app.getAppPath(), "dist", "index.html"));
}

ipcMain.handle("jarvis:get-snapshot", () => engine.snapshot());
ipcMain.handle("jarvis:submit", (_event, command: unknown) => {
  if (typeof command !== "string" || command.length > 2_000) throw new Error("Invalid command.");
  return engine.submit(command);
});
ipcMain.handle("jarvis:approval", (_event, approvalId: unknown, allow: unknown) => {
  if (typeof approvalId !== "string" || typeof allow !== "boolean") throw new Error("Invalid approval response.");
  return engine.respondToApproval(approvalId, allow);
});
ipcMain.handle("jarvis:emergency-stop", () => engine.emergencyStop());
ipcMain.handle("jarvis:reset-stop", () => engine.resetEmergencyStop());

engine.on("changed", (snapshot) => window?.webContents.send("jarvis:snapshot", snapshot));

app.whenReady().then(() => {
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
