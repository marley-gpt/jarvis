import fs from "node:fs/promises";
import path from "node:path";
import OpenAI from "openai";
import { app } from "electron";
import type { JarvisIntent } from "./intent.js";
import { SettingsStore } from "./settings.js";

interface UsageLedger {
  month: string;
  spentAud: number;
  inputTokens: number;
  outputTokens: number;
}

export interface BrainStatus {
  configured: boolean;
  model: string;
  spentAud: number;
  monthlyLimitAud: number;
}

const MODEL = "gpt-6-luna";
const INPUT_USD_PER_MILLION = 0.10;
const OUTPUT_USD_PER_MILLION = 0.50;
const USD_TO_AUD_BUFFERED = 1.65;

export class BrainController {
  private readonly settings = new SettingsStore();
  private readonly monthlyLimitAud = 45;
  private ledger: UsageLedger = { month: this.monthKey(), spentAud: 0, inputTokens: 0, outputTokens: 0 };
  private configured = false;

  async initialize(): Promise<void> {
    this.configured = await this.settings.hasApiKey();
    await this.loadLedger();
  }

  status(): BrainStatus {
    this.rollMonth();
    return {
      configured: this.configured,
      model: MODEL,
      spentAud: this.ledger.spentAud,
      monthlyLimitAud: this.monthlyLimitAud,
    };
  }

  async configure(apiKey: string | null): Promise<BrainStatus> {
    await this.settings.saveApiKey(apiKey);
    this.configured = await this.settings.hasApiKey();
    return this.status();
  }

  async interpret(command: string, context: string, signal?: AbortSignal): Promise<JarvisIntent> {
    const apiKey = await this.settings.getApiKey();
    if (!apiKey) throw new Error("Connect an OpenAI API key in Settings to use conversational commands.");
    this.rollMonth();
    if (this.ledger.spentAud >= this.monthlyLimitAud) {
      throw new Error(`AI budget paused at A$${this.monthlyLimitAud.toFixed(2)} for this month. Local commands still work.`);
    }

    const client = new OpenAI({ apiKey });
    const response = await client.responses.create({
      model: MODEL,
      reasoning: { effort: "low" },
      max_output_tokens: 400,
      parallel_tool_calls: false,
      instructions: [
        "You are the conversational brain inside a local Windows assistant named JARVIS.",
        "Be concise, honest, and practical. Never claim an action happened unless you call a provided function.",
        "Choose at most one function that best matches the user''s request.",
        "Opening any normal installed app is allowed. Closing an app is proposed by name and the local safety layer decides whether approval is needed.",
        "Never weaken permissions, bypass confirmations, expose secrets, or invent access.",
        "If the request is a question or cannot be fulfilled with a function, answer in plain language.",
        context,
      ].join("\n"),
      input: command,
      tools: [
        this.tool("open_app", "Open an installed Windows application by its normal display name.", { app: this.stringProperty("Application name, such as Chrome, Spotify, Notepad, or Excel") }),
        this.tool("close_app", "Close a running Windows application by name.", { app: this.stringProperty("Application name") }),
        this.tool("open_website", "Open an allowed website in the controlled browser.", { url: this.stringProperty("Website URL") }),
        this.tool("list_files", "List files in an authorised folder.", { path: this.stringProperty("Absolute Windows folder path") }),
        this.tool("create_folder", "Create a folder inside an authorised root.", { path: this.stringProperty("Absolute Windows folder path") }),
        this.tool("organise_files", "Plan and organise files in an authorised folder.", { path: this.stringProperty("Absolute Windows folder path") }),
        this.tool("delete_file", "Request deletion of one file. Local approval is always required.", { path: this.stringProperty("Absolute Windows file path") }),
        this.tool("run_workflow", "Run an installed ecommerce workflow.", { workflowId: this.stringProperty("Workflow ID") }),
        this.tool("system_status", "Report current JARVIS status.", {}),
        this.tool("check_updates", "Check the trusted JARVIS repository for an update.", {}),
      ],
    }, { signal });

    await this.recordUsage(response.usage?.input_tokens ?? 0, response.usage?.output_tokens ?? 0);
    const call = response.output.find((item) => item.type === "function_call");
    if (!call || call.type !== "function_call") {
      const text = response.output_text.trim();
      return { type: "reply", text: text || "I need a little more detail before I can help with that." };
    }

    const args = JSON.parse(call.arguments || "{}") as Record<string, string>;
    switch (call.name) {
      case "open_app": return { type: "open_app", app: args.app };
      case "close_app": return { type: "close_app", app: args.app };
      case "open_website": return { type: "open_website", url: args.url };
      case "list_files": return { type: "list_files", path: args.path };
      case "create_folder": return { type: "create_folder", path: args.path };
      case "organise_files": return { type: "organise_files", path: args.path };
      case "delete_file": return { type: "delete_file", path: args.path };
      case "run_workflow": return { type: "run_workflow", workflowId: args.workflowId };
      case "system_status": return { type: "status" };
      case "check_updates": return { type: "check_updates" };
      default: return { type: "reply", text: "I understood the request, but that action is not connected yet." };
    }
  }

  private tool(name: string, description: string, properties: Record<string, object>) {
    return {
      type: "function" as const,
      name,
      description,
      strict: true,
      parameters: {
        type: "object",
        properties,
        required: Object.keys(properties),
        additionalProperties: false,
      },
    };
  }

  private stringProperty(description: string): object {
    return { type: "string", description };
  }

  private monthKey(): string {
    return new Date().toISOString().slice(0, 7);
  }

  private ledgerPath(): string {
    return path.join(app.getPath("userData"), "ai-usage.json");
  }

  private rollMonth(): void {
    const current = this.monthKey();
    if (this.ledger.month !== current) this.ledger = { month: current, spentAud: 0, inputTokens: 0, outputTokens: 0 };
  }

  private async loadLedger(): Promise<void> {
    try {
      this.ledger = JSON.parse(await fs.readFile(this.ledgerPath(), "utf8")) as UsageLedger;
    } catch {
      this.ledger = { month: this.monthKey(), spentAud: 0, inputTokens: 0, outputTokens: 0 };
    }
    this.rollMonth();
  }

  private async recordUsage(inputTokens: number, outputTokens: number): Promise<void> {
    const usd = inputTokens / 1_000_000 * INPUT_USD_PER_MILLION + outputTokens / 1_000_000 * OUTPUT_USD_PER_MILLION;
    this.ledger.inputTokens += inputTokens;
    this.ledger.outputTokens += outputTokens;
    this.ledger.spentAud += usd * USD_TO_AUD_BUFFERED;
    await fs.mkdir(path.dirname(this.ledgerPath()), { recursive: true });
    await fs.writeFile(this.ledgerPath(), JSON.stringify(this.ledger, null, 2), "utf8");
  }
}

