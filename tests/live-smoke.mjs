import { tmpdir } from "node:os";
import { join } from "node:path";
import { writeFile } from "node:fs/promises";
import { _electron as electron } from "playwright";

const root = process.cwd();
const userData = join(tmpdir(), "jarvis-live-smoke-" + Date.now());
const electronApp = await electron.launch({
  args: ["--user-data-dir=" + userData, root],
  cwd: root,
  env: { ...process.env, VITE_DEV_SERVER_URL: "" },
});

let page;
try {
  page = await electronApp.firstWindow();
  await page.waitForLoadState("domcontentloaded");
  const input = page.getByLabel("ASK JARVIS");
  const execute = page.getByRole("button", { name: /execute/i });

  await input.fill("open calculator");
  await execute.click();
  await page.waitForFunction(() => document.body.innerText.includes("Opened Calculator."), undefined, { timeout: 15_000 });

  await input.fill("close calculator");
  await execute.click();
  await page.waitForFunction(() => document.body.innerText.includes("Asked Calculator to close."), undefined, { timeout: 15_000 });

  const snapshot = await page.evaluate(() => window.jarvis.getSnapshot());
  const results = snapshot.tasks.slice(0, 2).map((task) => ({ command: task.command, status: task.status, result: task.result }));
  const report = JSON.stringify({ title: await page.title(), results }, null, 2);
  if (process.env.JARVIS_SMOKE_RESULT) await writeFile(process.env.JARVIS_SMOKE_RESULT, report, "utf8");
  console.log(report);
} catch (error) {
  const report = JSON.stringify({
    error: error instanceof Error ? error.stack ?? error.message : String(error),
    title: page ? await page.title().catch(() => "") : "",
    url: page?.url() ?? "",
    body: page ? (await page.locator("body").innerText().catch(() => "")).slice(0, 2_000) : "",
  }, null, 2);
  if (process.env.JARVIS_SMOKE_RESULT) await writeFile(process.env.JARVIS_SMOKE_RESULT, report, "utf8");
  console.error(report);
  process.exitCode = 1;
} finally {
  await electronApp.close();
}

