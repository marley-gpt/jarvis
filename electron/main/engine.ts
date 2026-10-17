import { EventEmitter } from "node:events";
import fs from "node:fs/promises";
import path from "node:path";
import { app } from "electron";
import { BrowserController } from "./browser.js";
import { DesktopController } from "./desktop.js";
import { allowedOrigins, allowedRoots } from "./security.js";
import type { ActionEvent, ActivityEvent, Approval, JarvisTask, Risk, Snapshot, Workflow } from "./types.js";

const now = () => new Date().toISOString();
const id = (prefix: string) => `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

export class JarvisEngine extends EventEmitter {
  private readonly browser = new BrowserController();
  private readonly desktop = new DesktopController();
  private readonly tasks: JarvisTask[] = [];
  private readonly approvals: Approval[] = [];
  private readonly activity: ActivityEvent[] = [];
  private readonly controllers = new Map<string, AbortController>();
  private readonly approvalHandlers = new Map<string, () => Promise<void>>();
  private stopped = false;
  private spentAud = 0;

  constructor() {
    super();
    this.log("info", "brain", "JARVIS core initialised. Safety interlocks are active.");
  }

  snapshot(): Snapshot {
    const active = this.tasks.some((task) => task.status === "running");
    const waiting = this.approvals.length > 0;
    return {
      system: {
        status: this.stopped ? "stopped" : waiting ? "awaiting_approval" : active ? "active" : "idle",
        emergencyStop: this.stopped,
        voiceAvailable: process.env.VITE_ENABLE_VOICE === "true",
        allowedRoots,
        allowedOrigins,
        budgetAud: 50,
        spentAud: this.spentAud,
      },
      tasks: structuredClone(this.tasks.slice(0, 30)),
      approvals: structuredClone(this.approvals),
      activity: structuredClone(this.activity.slice(0, 150)),
    };
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
      const command = task.command;
      let match: RegExpMatchArray | null;

      if ((match = command.match(/^open\s+(notepad|calculator|explorer|paint|terminal)$/i))) {
        await this.action(task, `Open ${match[1]}`, "change", "skills", async () => this.desktop.openApp(match![1]));
        return this.complete(task, `Opened ${match[1]}.`);
      }

      if ((match = command.match(/^open\s+(.+)$/i))) {
        await this.action(task, "Navigate browser", "change", "browser", () => this.browser.navigate(match![1], signal));
        return this.complete(task, `Browser navigation completed.`);
      }

      if ((match = command.match(/^list files(?: in)?\s+(.+)$/i))) {
        const entries = await this.action(task, "Read folder contents", "read", "files", () => this.desktop.listFiles(match![1]));
        const preview = entries.slice(0, 12).map((entry) => `${entry.kind === "folder" ? "Folder" : "File"}: ${entry.name}`).join("\n");
        return this.complete(task, entries.length ? `${entries.length} item(s) found.\n${preview}` : "The folder is empty.");
      }

      if ((match = command.match(/^create folder\s+(.+)$/i))) {
        const result = await this.action(task, "Create folder", "change", "files", () => this.desktop.createFolder(match![1]));
        return this.complete(task, result);
      }

      if ((match = command.match(/^organise files(?: in)?\s+(.+)$/i))) {
        const plan = await this.action(task, "Build organisation plan", "read", "files", () => this.desktop.planOrganisation(match![1]));
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

      if ((match = command.match(/^delete\s+(.+)$/i))) {
        const target = match[1];
        return this.requestApproval(task, {
          kind: "delete",
          summary: `Delete ${path.basename(target)}`,
          detail: `Permanent file deletion requested for: ${target}`,
        }, async () => {
          const result = await this.action(task, "Delete file", "critical", "files", () => this.desktop.deleteFile(target));
          this.complete(task, result);
        });
      }

      if ((match = command.match(/^run workflow\s+([\w-]+)$/i))) {
        return await this.runWorkflow(task, match[1], signal);
      }

      if (/^(status|what are you doing)$/i.test(command)) {
        return this.complete(task, "JARVIS is online. Permission controls and emergency stop are active.");
      }

      throw new Error("Command not recognised. Try opening an app or URL, listing files, creating a folder, organising files, deleting a file, or running a workflow.");
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
        if (step.type === "spend" && (step.amountAud ?? 0) > 50 - this.spentAud) {
          throw new Error("Blocked: the proposed spend would exceed the A$50 monthly ceiling.");
        }
        return this.requestApproval(task, {
          kind: step.type,
          summary: step.label,
          detail: step.type === "spend" ? `Proposed spend: A$${step.amountAud ?? 0}` : "External publishing action requested.",
        }, async () => {
          if (step.type === "spend") this.spentAud += step.amountAud ?? 0;
          this.complete(task, `${workflow.label}: approved checkpoint recorded. Configure a site-specific adapter before external mutation.`);
        });
      }
    }
    this.complete(task, `${workflow.label} completed in review mode. No publishing or spend occurred.`);
  }

  private async action<T>(
    task: JarvisTask,
    label: string,
    risk: Risk,
    source: ActivityEvent["source"],
    work: () => Promise<T> | T,
  ): Promise<T> {
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

  private requestApproval(
    task: JarvisTask,
    input: Pick<Approval, "kind" | "summary" | "detail">,
    handler: () => Promise<void>,
  ): void {
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
