import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

function expandWindowsEnv(value: string): string {
  return value.replace(/%([^%]+)%/g, (_, key: string) => process.env[key] ?? "");
}

function configuredRoots(): string[] {
  const fallback = path.join(os.homedir(), "Documents", "JarvisWorkspace");
  const raw = process.env.JARVIS_ALLOWED_ROOTS ?? fallback;
  return raw.split(";").map((entry) => path.resolve(expandWindowsEnv(entry.trim()))).filter(Boolean);
}

function configuredOrigins(): string[] {
  const defaults = "https://github.com,https://www.google.com,https://business.facebook.com,https://admin.shopify.com";
  return (process.env.JARVIS_ALLOWED_ORIGINS ?? defaults)
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean)
    .map((entry) => new URL(entry).origin);
}

export const allowedRoots = configuredRoots();
export const allowedOrigins = configuredOrigins();

async function canonicalise(target: string): Promise<string> {
  const resolved = path.resolve(expandWindowsEnv(target.replace(/^["']|["']$/g, "")));
  try {
    return await fs.realpath(resolved);
  } catch {
    let parent = path.dirname(resolved);
    while (parent !== path.dirname(parent)) {
      try {
        const realParent = await fs.realpath(parent);
        return path.join(realParent, path.relative(parent, resolved));
      } catch {
        parent = path.dirname(parent);
      }
    }
    return resolved;
  }
}

export function isPathWithinRoot(candidate: string, root: string): boolean {
  const item = path.resolve(candidate).toLocaleLowerCase();
  const base = path.resolve(root).toLocaleLowerCase();
  return item === base || item.startsWith(base + path.sep);
}

export async function resolveAllowedPath(target: string): Promise<string> {
  if (!target.trim()) throw new Error("A file or folder path is required.");
  const candidate = await canonicalise(target);
  const canonicalRoots = await Promise.all(allowedRoots.map(canonicalise));
  if (!canonicalRoots.some((root) => isPathWithinRoot(candidate, root))) {
    throw new Error("Access denied: target is outside the configured JARVIS roots.");
  }
  return candidate;
}

export function isUrlAllowed(rawUrl: string, origins: string[] = allowedOrigins): boolean {
  let target: URL;
  try {
    target = new URL(/^https?:\/\//i.test(rawUrl) ? rawUrl : `https://${rawUrl}`);
  } catch {
    return false;
  }
  if (!["http:", "https:"].includes(target.protocol)) return false;
  return origins.some((rawOrigin) => {
    const allowed = new URL(rawOrigin);
    return target.protocol === allowed.protocol &&
      (target.hostname === allowed.hostname || target.hostname.endsWith(`.${allowed.hostname}`));
  });
}

export function assertAllowedUrl(rawUrl: string): URL {
  const input = /^https?:\/\//i.test(rawUrl) ? rawUrl : `https://${rawUrl}`;
  const target = new URL(input);
  if (!isUrlAllowed(target.href)) throw new Error(`Navigation blocked: ${target.origin} is not in JARVIS_ALLOWED_ORIGINS.`);
  return target;
}

export const APP_ALLOWLIST: Record<string, { command: string; args: string[]; label: string }> = {
  notepad: { command: "notepad.exe", args: [], label: "Notepad" },
  calculator: { command: "calc.exe", args: [], label: "Calculator" },
  explorer: { command: "explorer.exe", args: [], label: "File Explorer" },
  paint: { command: "mspaint.exe", args: [], label: "Paint" },
  terminal: { command: "wt.exe", args: [], label: "Windows Terminal" },
};
