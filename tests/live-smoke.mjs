import { tmpdir } from "node:os";
import { join } from "node:path";
import { writeFile } from "node:fs/promises";
import { _electron as electron } from "playwright";

const root = process.cwd();
const userData = join(tmpdir(), "jarvis-live-smoke-" + Date.now());
const electronApp = await electron.launch({
  args: ["--user-data-dir=" + userData, "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", root],
  cwd: root,
  env: { ...process.env, VITE_DEV_SERVER_URL: "", OPENAI_API_KEY: "sk-not-a-real-jarvis-smoke-test-key-0000000000" },
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
  await page.waitForFunction(
    () => {
      const text = document.body.innerText;
      return text.includes("Asked Calculator to close.") || text.includes("Force-closed Calculator");
    },
    undefined,
    { timeout: 15_000 },
  );

  await page.getByRole("button", { name: /Connections/ }).click();
  const connectionCount = await page.locator(".connection-card").count();
  const actionStepCount = await page.locator(".action-step").count();

  await page.getByRole("button", { name: /settings/i }).click();
  await page.getByLabel("OpenAI API key").fill("sk-not-a-real-jarvis-smoke-test-key-0000000000");
  await page.getByRole("button", { name: /connect & test/i }).click();
  await page.waitForFunction(() => document.body.innerText.includes("OpenAI rejected this key (401)"), undefined, { timeout: 25_000 });
  const apiError = await page.locator(".settings-error").innerText();
  await page.getByRole("button", { name: "Close settings" }).click();

  const voiceCheck = await page.evaluate(async () => {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const type = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "audio/webm";
    const recorder = new MediaRecorder(stream, { mimeType: type });
    const chunks = [];
    recorder.ondataavailable = (event) => event.data.size && chunks.push(event.data);
    await new Promise((resolve, reject) => {
      recorder.onerror = () => reject(new Error("Recorder failed"));
      recorder.onstop = resolve;
      recorder.start(100);
      setTimeout(() => recorder.stop(), 600);
    });
    stream.getTracks().forEach((track) => track.stop());
    const blob = new Blob(chunks, { type });
    try {
      await window.jarvis.transcribeAudio(await blob.arrayBuffer(), type, 600);
      return { bytes: blob.size, ipcError: "" };
    } catch (error) {
      return { bytes: blob.size, ipcError: error instanceof Error ? error.message : String(error) };
    }
  });
  if (voiceCheck.bytes < 100 || !voiceCheck.ipcError.includes("OpenAI rejected this key (401)")) {
    throw new Error("Voice capture/IPC smoke check failed: " + JSON.stringify(voiceCheck));
  }

  const cancelCheck = await page.evaluate(async () => {
    const { taskId } = await window.jarvis.submit("Explain the current system in detail");
    await window.jarvis.cancelTask(taskId);
    const snapshot = await window.jarvis.getSnapshot();
    return snapshot.tasks.find((task) => task.id === taskId)?.status;
  });
  if (cancelCheck !== "cancelled") throw new Error("Per-task cancellation smoke check failed.");

  const snapshot = await page.evaluate(() => window.jarvis.getSnapshot());
  const results = snapshot.tasks.filter((task) => ["open calculator", "close calculator"].includes(task.command)).map((task) => ({ command: task.command, status: task.status, result: task.result }));
  const report = JSON.stringify({ title: await page.title(), results, apiError, connectionCount, actionStepCount, voiceCheck, cancelCheck }, null, 2);
  if (process.env.JARVIS_SMOKE_SCREENSHOT) await page.screenshot({ path: process.env.JARVIS_SMOKE_SCREENSHOT, fullPage: true });
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

