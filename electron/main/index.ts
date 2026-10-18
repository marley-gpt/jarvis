import path from "node:path";
import { app, BrowserWindow, ipcMain, shell } from "electron";
import dotenv from "dotenv";
import { JarvisEngine } from "./engine.js";
import { assertAllowedUrl } from "./security.js";

dotenv.config();

let window: BrowserWindow | null = null;
let engine: JarvisEngine | null = null;

function getEngine(): JarvisEngine {
  if (!engine) throw new Error("JARVIS is still starting.");
  return engine;
}

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
    try {
      assertAllowedUrl(url);
      void shell.openExternal(url);
    } catch {
      // Ignore renderer attempts to open an unapproved origin.
    }
    return { action: "deny" };
  });
  window.webContents.on("will-navigate", (event) => event.preventDefault());
  window.webContents.session.setPermissionRequestHandler((_contents, permission, callback) => {
    callback(permission === "media");
  });

  const devUrl = process.env.VITE_DEV_SERVER_URL;
  if (devUrl) void window.loadURL(devUrl);
  else void window.loadFile(path.join(app.getAppPath(), "dist", "index.html"));
}

ipcMain.handle("jarvis:get-snapshot", () => getEngine().snapshot());
ipcMain.handle("jarvis:submit", (_event, command: unknown) => {
  if (typeof command !== "string" || command.length > 2_000) throw new Error("Invalid command.");
  return getEngine().submit(command);
});
ipcMain.handle("jarvis:cancel-task", (_event, taskId: unknown) => {
  if (typeof taskId !== "string" || taskId.length > 100) throw new Error("Invalid task.");
  return getEngine().cancelTask(taskId);
});
ipcMain.handle("jarvis:approval", (_event, approvalId: unknown, allow: unknown) => {
  if (typeof approvalId !== "string" || typeof allow !== "boolean") throw new Error("Invalid approval response.");
  return getEngine().respondToApproval(approvalId, allow);
});
ipcMain.handle("jarvis:emergency-stop", () => getEngine().emergencyStop());
ipcMain.handle("jarvis:reset-stop", () => getEngine().resetEmergencyStop());
ipcMain.handle("jarvis:configure-brain", (_event, apiKey: unknown) => {
  if (apiKey !== null && (typeof apiKey !== "string" || apiKey.length > 500)) throw new Error("Invalid API key.");
  return getEngine().configureBrain(apiKey);
});
ipcMain.handle("jarvis:test-brain", () => getEngine().testBrain());
ipcMain.handle("jarvis:transcribe-audio", (_event, audio: unknown, mimeType: unknown, durationMs: unknown) => {
  if (!(audio instanceof ArrayBuffer) || audio.byteLength === 0 || audio.byteLength > 10 * 1024 * 1024) {
    throw new Error("Invalid microphone recording.");
  }
  if (typeof mimeType !== "string" || mimeType.length > 100 || !/^audio\/(webm|ogg|mp4|wav)/i.test(mimeType)) {
    throw new Error("Unsupported microphone audio format.");
  }
  if (typeof durationMs !== "number" || !Number.isFinite(durationMs) || durationMs < 250 || durationMs > 31_000) {
    throw new Error("Voice recordings must be between 0.25 and 30 seconds.");
  }
  return getEngine().transcribeAudio(new Uint8Array(audio), mimeType, durationMs);
});
ipcMain.handle("jarvis:check-updates", () => getEngine().checkForUpdates());

app.whenReady().then(async () => {
  engine = new JarvisEngine();
  engine.on("changed", (snapshot) => window?.webContents.send("jarvis:snapshot", snapshot));
  await engine.initialize();
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

