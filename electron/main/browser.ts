import path from "node:path";
import type { BrowserContext, Page } from "playwright";
import { chromium } from "playwright";
import { app } from "electron";
import { assertAllowedUrl } from "./security.js";

export class BrowserController {
  private context: BrowserContext | null = null;
  private page: Page | null = null;

  private async ensurePage(): Promise<Page> {
    if (this.page && !this.page.isClosed()) return this.page;
    const profile = path.join(app.getPath("userData"), "playwright-profile");
    this.context = await chromium.launchPersistentContext(profile, {
      headless: false,
      viewport: { width: 1440, height: 900 },
      acceptDownloads: false,
    });
    this.page = this.context.pages()[0] ?? await this.context.newPage();
    return this.page;
  }

  async navigate(rawUrl: string, signal?: AbortSignal): Promise<string> {
    if (signal?.aborted) throw new Error("Task was cancelled.");
    const target = assertAllowedUrl(rawUrl);
    const page = await this.ensurePage();
    const cancel = () => void page.evaluate(() => window.stop()).catch(() => undefined);
    signal?.addEventListener("abort", cancel, { once: true });
    try {
      await page.goto(target.href, { waitUntil: "domcontentloaded", timeout: 30_000 });
      return `Opened ${target.href}`;
    } finally {
      signal?.removeEventListener("abort", cancel);
    }
  }

  async stop(): Promise<void> {
    const context = this.context;
    this.context = null;
    this.page = null;
    await context?.close().catch(() => undefined);
  }
}
