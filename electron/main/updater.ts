import { EventEmitter } from "node:events";
import fs from "node:fs/promises";
import path from "node:path";
import { execFile, spawn } from "node:child_process";
import { promisify } from "node:util";
import { app } from "electron";

const execFileAsync = promisify(execFile);
const REPO = "marley-gpt/jarvis";
const BRANCH = "jarvis";

export interface UpdateState {
  checking: boolean;
  available: boolean;
  canInstall: boolean;
  currentVersion: string;
  latestVersion?: string;
  message: string;
  lastCheckedAt?: string;
}

function compareVersions(left: string, right: string): number {
  const a = left.split(".").map(Number);
  const b = right.split(".").map(Number);
  for (let index = 0; index < Math.max(a.length, b.length); index += 1) {
    const delta = (a[index] ?? 0) - (b[index] ?? 0);
    if (delta) return delta;
  }
  return 0;
}

export class UpdateController extends EventEmitter {
  private timer: NodeJS.Timeout | null = null;
  private gitPath: string | null = null;
  private state: UpdateState = {
    checking: false,
    available: false,
    canInstall: false,
    currentVersion: app.getVersion(),
    message: "Updates check automatically every 30 minutes.",
  };

  snapshot(): UpdateState {
    return { ...this.state };
  }

  async initialize(): Promise<void> {
    this.gitPath = await this.findGit();
    this.state.canInstall = Boolean(this.gitPath && await this.isGitCheckout());
    this.timer = setInterval(() => void this.check().catch(() => undefined), 30 * 60_000);
    setTimeout(() => void this.check().catch(() => undefined), 2_500);
  }

  async check(): Promise<UpdateState> {
    if (this.state.checking) return this.snapshot();
    this.state.checking = true;
    this.state.message = "Checking the trusted JARVIS repository…";
    this.changed();
    try {
      if (this.gitPath && await this.isGitCheckout()) {
        await execFileAsync(this.gitPath, ["-C", app.getAppPath(), "fetch", "--quiet", "origin", BRANCH], { windowsHide: true, timeout: 60_000 });
        const [{ stdout: local }, { stdout: remote }, { stdout: packageText }] = await Promise.all([
          execFileAsync(this.gitPath, ["-C", app.getAppPath(), "rev-parse", "HEAD"], { windowsHide: true }),
          execFileAsync(this.gitPath, ["-C", app.getAppPath(), "rev-parse", `origin/${BRANCH}`], { windowsHide: true }),
          execFileAsync(this.gitPath, ["-C", app.getAppPath(), "show", `origin/${BRANCH}:package.json`], { windowsHide: true }),
        ]);
        const latestVersion = (JSON.parse(packageText) as { version?: string }).version ?? "unknown";
        this.state.available = local.trim() !== remote.trim();
        this.state.canInstall = true;
        this.state.latestVersion = latestVersion;
      } else {
        const response = await fetch(`https://raw.githubusercontent.com/${REPO}/${BRANCH}/package.json`, {
          headers: { "User-Agent": "JARVIS-Local-Control", "Cache-Control": "no-cache" },
        });
        if (!response.ok) throw new Error(`GitHub returned ${response.status}`);
        const remote = await response.json() as { version?: string };
        this.state.latestVersion = remote.version ?? "unknown";
        this.state.available = Boolean(remote.version && compareVersions(remote.version, this.state.currentVersion) > 0);
        this.state.canInstall = false;
      }
      this.state.message = this.state.available
        ? this.state.canInstall
          ? `JARVIS ${this.state.latestVersion ?? "update"} is ready to install.`
          : "An update is available. Automatic installation will activate after JARVIS is moved to its managed Git checkout."
        : "JARVIS is up to date.";
    } catch (error) {
      this.state.message = `Update check failed: ${error instanceof Error ? error.message : String(error)}`;
    } finally {
      this.state.checking = false;
      this.state.lastCheckedAt = new Date().toISOString();
      this.changed();
    }
    return this.snapshot();
  }

  async installAndRestart(): Promise<void> {
    if (!this.gitPath || !await this.isGitCheckout()) throw new Error("Automatic install needs the managed Git checkout.");
    if (!this.state.available) throw new Error("No update is currently available.");
    const { stdout: dirty } = await execFileAsync(this.gitPath, ["-C", app.getAppPath(), "status", "--porcelain", "--untracked-files=no"], { windowsHide: true });
    if (dirty.trim()) throw new Error("Update paused because tracked JARVIS files have local changes.");
    const { stdout: npmLocation } = await execFileAsync("where.exe", ["npm.cmd"], { windowsHide: true });
    const npmPath = npmLocation.split(/\r?\n/).find(Boolean);
    if (!npmPath) throw new Error("npm.cmd was not found.");

    const helper = path.join(app.getPath("userData"), "apply-update.ps1");
    const log = path.join(app.getPath("userData"), "update.log");
    const source = [
      "param([string]$Project,[string]$Git,[string]$Npm,[string]$Log)",
      "$ErrorActionPreference = ''Stop''",
      "Start-Sleep -Seconds 3",
      "try {",
      "  & $Git -C $Project pull --ff-only origin jarvis *>> $Log",
      "  if ($LASTEXITCODE -ne 0) { throw ''git pull failed'' }",
      "  & $Npm install --legacy-peer-deps --no-audit --no-fund *>> $Log",
      "  if ($LASTEXITCODE -ne 0) { throw ''npm install failed'' }",
      "  & $Npm run build *>> $Log",
      "  if ($LASTEXITCODE -ne 0) { throw ''build failed'' }",
      "  $Electron = Join-Path $Project ''node_modules\\electron\\dist\\electron.exe''",
      "  Start-Process -FilePath $Electron -ArgumentList @(''.'') -WorkingDirectory $Project -WindowStyle Hidden",
      "} catch { $_ | Out-String | Add-Content -LiteralPath $Log }",
    ].join("\r\n");
    await fs.writeFile(helper, source, "utf8");
    const child = spawn("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", helper, "-Project", app.getAppPath(), "-Git", this.gitPath, "-Npm", npmPath, "-Log", log], {
      detached: true,
      stdio: "ignore",
      windowsHide: true,
    });
    child.unref();
    setTimeout(() => app.quit(), 500);
  }

  private async isGitCheckout(): Promise<boolean> {
    try {
      await fs.access(path.join(app.getAppPath(), ".git"));
      return true;
    } catch {
      return false;
    }
  }

  private async findGit(): Promise<string | null> {
    const candidates = [
      process.env.JARVIS_GIT_PATH,
      "C:\\Program Files\\Git\\cmd\\git.exe",
      "C:\\Program Files (x86)\\Git\\cmd\\git.exe",
      "git.exe",
    ].filter((value): value is string => Boolean(value));
    for (const candidate of candidates) {
      try {
        await execFileAsync(candidate, ["--version"], { windowsHide: true, timeout: 5_000 });
        return candidate;
      } catch {
        // Try the next known location.
      }
    }
    return null;
  }

  private changed(): void {
    this.emit("changed", this.snapshot());
  }
}


