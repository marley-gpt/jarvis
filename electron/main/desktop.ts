import fs from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";
import { APP_ALLOWLIST, resolveAllowedPath } from "./security.js";

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

const groups: Record<string, string[]> = {
  Images: [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"],
  Documents: [".pdf", ".doc", ".docx", ".txt", ".md"],
  Spreadsheets: [".csv", ".xls", ".xlsx"],
  Archives: [".zip", ".rar", ".7z", ".tar", ".gz"],
};

function categoryFor(file: string): string {
  const extension = path.extname(file).toLowerCase();
  return Object.entries(groups).find(([, extensions]) => extensions.includes(extension))?.[0] ?? "Other";
}

export class DesktopController {
  openApp(appName: string): string {
    const app = APP_ALLOWLIST[appName.toLowerCase()];
    if (!app) throw new Error(`Application is not allowlisted. Allowed: ${Object.keys(APP_ALLOWLIST).join(", ")}.`);
    const child = spawn(app.command, app.args, { detached: true, stdio: "ignore", windowsHide: false });
    child.unref();
    return `Opened ${app.label}.`;
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
}
