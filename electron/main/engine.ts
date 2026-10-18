import { EventEmitter } from "node:events";
import fs from "node:fs/promises";
import path from "node:path";
import { app } from "electron";
import { BrainController } from "./brain.js";
import { BrowserController } from "./browser.js";
import { DesktopController } from "./desktop.js";
import { parseLocalIntent, type JarvisIntent } from "./intent.js";
import { allowedOrigins, allowedRoots } from "./security.js";
import { UpdateController } from "./updater.js";
import type { ActionEvent, ActivityEvent, Approval, JarvisTask, Risk, Snapshot, Workflow } from "./types.js";

const now = () => new Date().toISOString();
const id = (prefix: string) => `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

export class JarvisEngine extends EventEmitter {
  private readonly browser = new BrowserController();
  private readonly desktop = new DesktopController();
  private readonly brain = new BrainController();
  private readonly updater = new UpdateController();
  private readonly tasks: JarvisTask[] = [];
  private readonly approvals: Approval[] = [];
  private readonly activity: ActivityEvent[] = [];
  private readonly controllers = new Map<string, AbortController>();
  private readonly approvalHandlers = new Map<string, () => Promise<void>>();
  private stopped = false;
  private workflowSpentAud = 0;

  constructor() {
    super();
    this.updater.on("changed", () => this.changed());
    this.log("info", "brain", "JARVIS core initialised. Safety interlocks are active.");
  }

  async initialize(): Promise<void> {
    await this.brain.initialize();
    await this.updater.initialize();
    this.log("success", "system", this.brain.status().configured
      ? "Conversational brain connected."
      : "Basic controls ready. Connect OpenAI in Settings for conversational commands.");
    this.changed();
  }

  snapshot(): Snapshot {
    const active = this.tasks.some((task) => task.status === "running");
    const waiting = this.approvals.length > 0;
    const brain = this.brain.status();
    return {
      system: {
        status: this.stopped ? "stopped" : waiting ? "awaiting_approval" : active ? "active" : "idle",
        emergencyStop: this.stopped,
        voiceAvailable: process.env.VITE_ENABLE_VOICE === "true",
        allowedRoots,
        allowedOrigins,
        budgetAud: 50,
        spentAud: this.workflowSpentAud + brain.spentAud,
        brainConfigured: brain.configured,
        brainVerified: brain.verified,
        brainModel: brain.model,
        brainSpentAud: brain.spentAud,
        brainMonthlyLimitAud: brain.monthlyLimitAud,
        update: this.updater.snapshot(),
      },
      tasks: structuredClone(this.tasks.slice(0, 30)),
      approvals: structuredClone(this.approvals),
      activity: structuredClone(this.activity.slice(0, 150)),
    };
  }

  async configureBrain(apiKey: string | null): Promise<Snapshot> {
    try {
      const status = await this.brain.configure(apiKey);
      this.log("success", "brain", status.configured ? "OpenAI brain tested and connected securely." : "OpenAI brain disconnected.");
      this.changed();
      return this.snapshot();
    } catch (error) {
      this.log("error", "brain", error instanceof Error ? error.message : String(error));
      this.changed();
      throw error;
    }
  }

  async testBrain(): Promise<Snapshot> {
    try {
      await this.brain.testConnection();
      this.log("success", "brain", "OpenAI connection test passed.");
      this.changed();
      return this.snapshot();
    } catch (error) {
      this.log("error", "brain", error instanceof Error ? error.message : String(error));
      this.changed();
      throw error;
    }
  }

  async checkForUpdates(): Promise<void> {
    await this.updater.check();
    this.log("info", "system", this.updater.snapshot().message);
    this.changed();
  }

  async submit(command: string): Promise<{ taskId: string }> {
    const clean = command.trim();
    if (!clean) throw new Error("Enter a command.");
    if (this.stopped) throw new Error("Emergency stop is active. Reset it before running commands.");

    const task: JarvisTask = {
      id: id("task"),
      command: clean,
      title: clean.length > 72 ? `${clean.slice(0, 69)}…` : clean,
      status: "queued",
      progress: 0,
      createdAt: now(),
      updatedAt: now(),
      actions: [],
    };
    this.tasks.unshift(task);
    const controller = new AbortController();
    this.controllers.set(task.id, controller);
    this.changed();
    void this.execute(task, controller.signal);
    return { taskId: task.id };
  }

  async respondToApproval(approvalId: string, allow: boolean): Promise<void> {
    const index = this.approvals.findIndex((approval) => approval.id === approvalId);
    if (index < 0) throw new Error("Approval request is no longer available.");
    const [approval] = this.approvals.splice(index, 1);
    const task = this.getTask(approval.taskId);
    const handler = this.approvalHandlers.get(approvalId);
    this.approvalHandlers.delete(approvalId);

    if (Date.now() > new Date(approval.expiresAt).getTime()) {
      task.status = "cancelled";
      task.error = "Approval expired. Submit the command again to create a fresh request.";
      task.updatedAt = now();
      this.log("warning", "brain", `Expired: ${approval.summary}`);
      this.changed();
      return;
    }
    if (!allow) {
      task.status = "cancelled";
      task.error = "Action declined by operator.";
      task.updatedAt = now();
      this.log("warning", "brain", `Declined: ${approval.summary}`);
      this.changed();
      return;
    }
    if (this.stopped) throw new Error("Emergency stop is active.");

    task.status = "running";
    task.updatedAt = now();
    this.log("info", "brain", `Approved: ${approval.summary}`);
    this.changed();
    try {
      if (!handler) throw new Error("Approval handler is no longer available.");
      await handler();
    } catch (error) {
      task.status = "failed";
      task.error = error instanceof Error ? error.message : String(error);
      task.progress = 100;
      task.updatedAt = now();
      this.log("error", "brain", task.error);
      this.changed();
    }
  }

  async emergencyStop(): Promise<void> {
    this.stopped = true;
    for (const controller of this.controllers.values()) controller.abort();
    for (const task of this.tasks.filter((item) => ["queued", "running", "waiting_approval"].includes(item.status))) {
      task.status = "cancelled";
      task.error = "Emergency stop activated.";
      task.updatedAt = now();
    }
    this.approvals.splice(0);
    this.approvalHandlers.clear();
    await this.browser.stop();
    this.log("error", "brain", "EMERGENCY STOP — active tasks aborted and browser control closed.");
    this.changed();
  }

  resetEmergencyStop(): void {
    this.stopped = false;
    this.log("warning", "brain", "Emergency stop reset by operator.");
    this.changed();
  }

  private async execute(task: JarvisTask, signal: AbortSignal): Promise<void> {
    task.status = "running";
    task.progress = 5;
    task.updatedAt = now();
    this.log("info", "brain", `Received: ${task.command}`);
    this.changed();

    try {
      let intent = parseLocalIntent(task.command);
      if (!intent) {
        if (!this.brain.status().configured) {
          throw new Error("I do not understand that yet. Connect the OpenAI brain in Settings, or say “help” for local commands.");
        }
        intent = await this.action(task, "Understand natural-language request", "read", "brain", () =>
          this.brain.interpret(task.command, this.brainContext(), signal));
      }
      await this.executeIntent(task, intent, signal);
    } catch (error) {
      if (signal.aborted || this.stopped) return;
      task.status = "failed";
      task.error = error instanceof Error ? error.message : String(error);
      task.progress = 100;
      task.updatedAt = now();
      this.log("error", "brain", task.error);
      this.changed();
    } finally {
      if (!["waiting_approval", "running"].includes(task.status)) this.controllers.delete(task.id);
    }
  }

  private async executeIntent(task: JarvisTask, intent: JarvisIntent, signal: AbortSignal): Promise<void> {
    switch (intent.type) {
      case "open_app": {
        const result = await this.action(task, `Open ${intent.app}`, "change", "skills", () => this.desktop.openApp(intent.app));
        return this.complete(task, result);
      }
      case "close_app": {
        if (intent.app.toLowerCase() === "browser") {
          await this.action(task, "Close controlled browser", "change", "browser", () => this.browser.stop());
          return this.complete(task, "Closed the controlled browser.");
        }
        const plan = await this.action(task, `Find running ${intent.app}`, "read", "skills", () => this.desktop.planCloseApp(intent.app));
        if (plan.requiresApproval) {
          return this.requestApproval(task, {
            kind: "close_app",
            summary: `Close ${plan.label}`,
            detail: `JARVIS will ask these process groups to close: ${plan.processImages.join(", ")}. Unsaved work may prompt inside the app.`,
          }, async () => {
            const result = await this.action(task, `Close ${plan.label}`, "critical", "skills", () => this.desktop.closeApp(plan));
            this.complete(task, result);
          });
        }
        const result = await this.action(task, `Close ${plan.label}`, "change", "skills", () => this.desktop.closeApp(plan));
        return this.complete(task, result);
      }
      case "open_website":
        await this.action(task, "Navigate controlled browser", "change", "browser", () => this.browser.navigate(intent.url, signal));
        return this.complete(task, "Browser navigation completed.");
      case "list_files": {
        const entries = await this.action(task, "Read folder contents", "read", "files", () => this.desktop.listFiles(intent.path));
        const preview = entries.slice(0, 12).map((entry) => `${entry.kind === "folder" ? "Folder" : "File"}: ${entry.name}`).join("\n");
        return this.complete(task, entries.length ? `${entries.length} item(s) found.\n${preview}` : "The folder is empty.");
      }
      case "create_folder": {
        const result = await this.action(task, "Create folder", "change", "files", () => this.desktop.createFolder(intent.path));
        return this.complete(task, result);
      }
      case "organise_files": {
        const plan = await this.action(task, "Build organisation plan", "read", "files", () => this.desktop.planOrganisation(intent.path));
        if (!plan.length) return this.complete(task, "No files need organising.");
        return this.requestApproval(task, {
          kind: "significant_change",
          summary: `Organise ${plan.length} file(s)`,
          detail: plan.slice(0, 20).map((item) => `${path.basename(item.from)} → ${item.category}`).join("\n"),
        }, async () => {
          const result = await this.action(task, "Move files into category folders", "critical", "files", () => this.desktop.applyOrganisation(plan));
          this.complete(task, result);
        });
      }
      case "delete_file":
        return this.requestApproval(task, {
          kind: "delete",
          summary: `Delete ${path.basename(intent.path)}`,
          detail: `Permanent file deletion requested for: ${intent.path}`,
        }, async () => {
          const result = await this.action(task, "Delete file", "critical", "files", () => this.desktop.deleteFile(intent.path));
          this.complete(task, result);
        });
      case "run_workflow":
        return this.runWorkflow(task, intent.workflowId, signal);
      case "status": {
        const brain = this.brain.status();
        return this.complete(task, `JARVIS is online. AI brain: ${brain.configured ? "connected" : "not connected"}. ${this.updater.snapshot().message}`);
      }
      case "help":
        return this.complete(task, [
          "Speak normally. Examples:",
          "• Open Chrome",
          "• Close Spotify",
          "• Show files in %USERPROFILE%\\Documents\\JarvisWorkspace",
          "• Organise files in %USERPROFILE%\\Documents\\JarvisWorkspace\\Inbox",
          "• Open github.com",
          "• Check for updates",
        ].join("\n"));
      case "check_updates":
        await this.action(task, "Check for JARVIS updates", "read", "system", () => this.updater.check());
        return this.complete(task, this.updater.snapshot().message);
      case "install_update": {
        const update = this.updater.snapshot();
        if (!update.available) return this.complete(task, "No update is waiting to be installed.");
        if (!update.canInstall) throw new Error(update.message);
        return this.requestApproval(task, {
          kind: "update",
          summary: `Install JARVIS ${update.latestVersion ?? "update"}`,
          detail: "JARVIS will close, pull the trusted GitHub branch, install dependencies, rebuild, and restart. A local log is retained if anything fails.",
        }, async () => {
          this.complete(task, "Update approved. JARVIS will restart when installation finishes.");
          await this.updater.installAndRestart();
        });
      }
      case "reply":
        return this.complete(task, intent.text);
    }
  }

  private async runWorkflow(task: JarvisTask, workflowId: string, signal: AbortSignal): Promise<void> {
    const file = path.join(app.getAppPath(), "config", "workflows.json");
    const parsed = JSON.parse(await fs.readFile(file, "utf8")) as { workflows: Workflow[] };
    const workflow = parsed.workflows.find((item) => item.id === workflowId);
    if (!workflow) throw new Error(`Unknown workflow: ${workflowId}.`);

    this.log("info", workflowId.includes("meta") ? "meta" : "skills", `Starting workflow: ${workflow.label}`);
    for (let index = 0; index < workflow.steps.length; index += 1) {
      if (signal.aborted) throw new Error("Task was cancelled.");
      const step = workflow.steps[index];
      task.progress = Math.round((index / workflow.steps.length) * 90) + 5;
      task.updatedAt = now();
      this.changed();

      if (step.type === "navigate") {
        if (!step.url) throw new Error(`Workflow step "${step.label}" is missing a URL.`);
        await this.action(task, step.label, "change", workflowId.includes("meta") ? "meta" : "browser", () => this.browser.navigate(step.url!, signal));
      } else if (step.type === "checkpoint") {
        await this.action(task, step.label, "read", "brain", async () => "Human checkpoint reached.");
      } else {
        const brainSpend = this.brain.status().spentAud;
        if (step.type === "spend" && (step.amountAud ?? 0) > 50 - this.workflowSpentAud - brainSpend) {
          throw new Error("Blocked: the proposed spend would exceed the A$50 monthly ceiling.");
        }
        return this.requestApproval(task, {
          kind: step.type,
          summary: step.label,
          detail: step.type === "spend" ? `Proposed spend: A$${step.amountAud ?? 0}` : "External publishing action requested.",
        }, async () => {
          if (step.type === "spend") this.workflowSpentAud += step.amountAud ?? 0;
          this.complete(task, `${workflow.label}: approved checkpoint recorded. Configure a site-specific adapter before external mutation.`);
        });
      }
    }
    this.complete(task, `${workflow.label} completed in review mode. No publishing or spend occurred.`);
  }

  private brainContext(): string {
    return [
      `Authorised file roots: ${allowedRoots.join("; ")}`,
      `Authorised web origins: ${allowedOrigins.join(", ")}`,
      "Installed workflows: product-listing-draft, meta-ads-review.",
      "Opening apps is broad. File access and browser navigation remain restricted by the local engine.",
    ].join("\n");
  }

  private async action<T>(task: JarvisTask, label: string, risk: Risk, source: ActivityEvent["source"], work: () => Promise<T> | T): Promise<T> {
    const event: ActionEvent = { id: id("action"), taskId: task.id, label, status: "running", risk, startedAt: now() };
    task.actions.unshift(event);
    task.updatedAt = now();
    this.log("info", source, label);
    this.changed();
    try {
      const result = await work();
      event.status = "completed";
      event.finishedAt = now();
      this.log("success", source, `${label} completed.`);
      this.changed();
      return result;
    } catch (error) {
      event.status = "failed";
      event.finishedAt = now();
      event.error = error instanceof Error ? error.message : String(error);
      this.changed();
      throw error;
    }
  }

  private requestApproval(task: JarvisTask, input: Pick<Approval, "kind" | "summary" | "detail">, handler: () => Promise<void>): void {
    const approval: Approval = {
      id: id("approval"),
      taskId: task.id,
      ...input,
      createdAt: now(),
      expiresAt: new Date(Date.now() + 10 * 60_000).toISOString(),
    };
    this.approvals.push(approval);
    this.approvalHandlers.set(approval.id, handler);
    task.status = "waiting_approval";
    task.progress = Math.max(task.progress, 55);
    task.updatedAt = now();
    this.log("warning", "brain", `Approval required: ${approval.summary}`);
    this.changed();
  }

  private complete(task: JarvisTask, result: string): void {
    task.status = "completed";
    task.progress = 100;
    task.result = result;
    task.updatedAt = now();
    this.controllers.delete(task.id);
    this.log("success", "brain", result.split("\n")[0]);
    this.changed();
  }

  private getTask(taskId: string): JarvisTask {
    const task = this.tasks.find((item) => item.id === taskId);
    if (!task) throw new Error("Task no longer exists.");
    return task;
  }

  private log(level: ActivityEvent["level"], source: ActivityEvent["source"], message: string): void {
    this.activity.unshift({ id: id("log"), time: now(), level, source, message });
    if (this.activity.length > 250) this.activity.length = 250;
  }

  private changed(): void {
    this.emit("changed", this.snapshot());
  }
}

