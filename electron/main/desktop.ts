import fs from "node:fs/promises";
import path from "node:path";
import { execFile, spawn } from "node:child_process";
import { promisify } from "node:util";
import { shell } from "electron";
import { APP_ALLOWLIST, resolveAllowedPath } from "./security.js";

const execFileAsync = promisify(execFile);

export interface FileEntry {
  name: string;
  path: string;
  kind: "file" | "folder";
  size: number;
  modifiedAt: string;
}

export interface MovePlan {
  from: string;
  to: string;
  category: string;
}

export interface ClosePlan {
  label: string;
  processImages: string[];
  requiresApproval: boolean;
}

interface InstalledShortcut {
  label: string;
  shortcutPath: string;
}

const groups: Record<string, string[]> = {
  Images: [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"],
  Documents: [".pdf", ".doc", ".docx", ".txt", ".md"],
  Spreadsheets: [".csv", ".xls", ".xlsx"],
  Archives: [".zip", ".rar", ".7z", ".tar", ".gz"],
};

const protectedProcessFragments = [
  "system", "idle", "registry", "smss", "csrss", "wininit", "services", "lsass", "winlogon",
  "dwm", "fontdrvhost", "securityhealth", "msmpeng", "credential", "keepass", "bitwarden",
  "1password", "codex", "chatgpt",
];

function categoryFor(file: string): string {
  const extension = path.extname(file).toLowerCase();
  return Object.entries(groups).find(([, extensions]) => extensions.includes(extension))?.[0] ?? "Other";
}

function normaliseApp(value: string): string {
  return value.toLowerCase().replace(/\.exe$/i, "").replace(/[^a-z0-9]+/g, "");
}

function parseTaskList(csv: string): Array<{ image: string; pid: number }> {
  return csv.split(/\r?\n/).flatMap((line) => {
    const match = line.match(/^"([^"]+)","(\d+)"/);
    return match ? [{ image: match[1], pid: Number(match[2]) }] : [];
  });
}

export class DesktopController {
  private shortcutCache: InstalledShortcut[] | null = null;

  async openApp(appName: string): Promise<string> {
    const key = appName.toLowerCase().trim();
    const builtIn = APP_ALLOWLIST[key];
    if (builtIn) {
      const child = spawn(builtIn.command, builtIn.args, { detached: true, stdio: "ignore", windowsHide: false });
      child.unref();
      return `Opened ${builtIn.label}.`;
    }

    const installed = await this.findInstalledApp(appName);
    if (!installed) {
      throw new Error(`I could not find "${appName}" in the Windows Start menu. Try its full app name.`);
    }
    const error = await shell.openPath(installed.shortcutPath);
    if (error) throw new Error(`Windows could not open ${installed.label}: ${error}`);
    return `Opened ${installed.label}.`;
  }

  async planCloseApp(appName: string): Promise<ClosePlan> {
    const key = appName.toLowerCase().trim();
    const builtIn = APP_ALLOWLIST[key];
    if (builtIn && !builtIn.closable) {
      throw new Error(`Closing ${builtIn.label} is protected because it can stop the Windows desktop or active command sessions.`);
    }

    const { stdout } = await execFileAsync("tasklist.exe", ["/FO", "CSV", "/NH"], { windowsHide: true });
    const tasks = parseTaskList(stdout);
    const requested = normaliseApp(builtIn?.label ?? appName);
    const preferred = builtIn?.processes.map(normaliseApp) ?? [];
    const matches = tasks.filter((task) => {
      const image = normaliseApp(task.image);
      if (task.pid === process.pid || image === "electron") return false;
      return preferred.includes(image) || image === requested || (requested.length >= 4 && image.includes(requested));
    });
    if (!matches.length) throw new Error(`I could not find a running app matching "${appName}".`);

    const images = [...new Set(matches.map((item) => item.image))];
    const protectedImage = images.find((image) => protectedProcessFragments.some((part) => normaliseApp(image).includes(part)));
    if (protectedImage) throw new Error(`Closing ${protectedImage} is blocked because it is a protected system, security, credential, or JARVIS process.`);
    return {
      label: builtIn?.label ?? appName,
      processImages: images,
      requiresApproval: !builtIn,
    };
  }

  async closeApp(plan: ClosePlan): Promise<string> {
    for (const image of plan.processImages) {
      await execFileAsync("taskkill.exe", ["/IM", image, "/T"], { windowsHide: true });
    }
    return `Asked ${plan.label} to close.`;
  }

  async listFiles(target: string): Promise<FileEntry[]> {
    const safePath = await resolveAllowedPath(target);
    const entries = await fs.readdir(safePath, { withFileTypes: true });
    const result: FileEntry[] = [];
    for (const entry of entries.slice(0, 250)) {
      const entryPath = path.join(safePath, entry.name);
      const stat = await fs.stat(entryPath);
      result.push({
        name: entry.name,
        path: entryPath,
        kind: entry.isDirectory() ? "folder" : "file",
        size: stat.size,
        modifiedAt: stat.mtime.toISOString(),
      });
    }
    return result.sort((a, b) => a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name));
  }

  async createFolder(target: string): Promise<string> {
    const safePath = await resolveAllowedPath(target);
    await fs.mkdir(safePath, { recursive: false });
    return `Created folder ${safePath}.`;
  }

  async planOrganisation(target: string): Promise<MovePlan[]> {
    const safePath = await resolveAllowedPath(target);
    const entries = await fs.readdir(safePath, { withFileTypes: true });
    return entries
      .filter((entry) => entry.isFile())
      .map((entry) => {
        const category = categoryFor(entry.name);
        return { from: path.join(safePath, entry.name), to: path.join(safePath, category, entry.name), category };
      });
  }

  async applyOrganisation(plan: MovePlan[]): Promise<string> {
    for (const item of plan) {
      const from = await resolveAllowedPath(item.from);
      const to = await resolveAllowedPath(item.to);
      await fs.mkdir(path.dirname(to), { recursive: true });
      await fs.rename(from, to);
    }
    return `Organised ${plan.length} file${plan.length === 1 ? "" : "s"}.`;
  }

  async deleteFile(target: string): Promise<string> {
    const safePath = await resolveAllowedPath(target);
    const stat = await fs.stat(safePath);
    if (stat.isDirectory()) throw new Error("Folder deletion is disabled. Delete individual files after review.");
    await fs.unlink(safePath);
    return `Deleted file ${safePath}.`;
  }

  private async findInstalledApp(appName: string): Promise<InstalledShortcut | null> {
    const shortcuts = await this.getStartMenuShortcuts();
    const query = normaliseApp(appName);
    const ranked = shortcuts
      .map((item) => {
        const candidate = normaliseApp(item.label);
        const score = candidate === query ? 0 : candidate.startsWith(query) ? 1 : candidate.includes(query) ? 2 : 99;
        return { item, score };
      })
      .filter((entry) => entry.score < 99)
      .sort((a, b) => a.score - b.score || a.item.label.length - b.item.label.length);
    return ranked[0]?.item ?? null;
  }

  private async getStartMenuShortcuts(): Promise<InstalledShortcut[]> {
    if (this.shortcutCache) return this.shortcutCache;
    const roots = [
      process.env.APPDATA ? path.join(process.env.APPDATA, "Microsoft", "Windows", "Start Menu", "Programs") : "",
      process.env.ProgramData ? path.join(process.env.ProgramData, "Microsoft", "Windows", "Start Menu", "Programs") : "",
    ].filter(Boolean);
    const result: InstalledShortcut[] = [];
    const walk = async (folder: string): Promise<void> => {
      let entries;
      try {
        entries = await fs.readdir(folder, { withFileTypes: true });
      } catch {
        return;
      }
      for (const entry of entries) {
        const itemPath = path.join(folder, entry.name);
        if (entry.isDirectory()) await walk(itemPath);
        else if (entry.isFile() && path.extname(entry.name).toLowerCase() === ".lnk") {
          result.push({ label: path.basename(entry.name, ".lnk"), shortcutPath: itemPath });
        }
      }
    };
    for (const root of roots) await walk(root);
    this.shortcutCache = result;
    return result;
  }
}

