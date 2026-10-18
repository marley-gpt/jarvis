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
  verified: boolean;
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
  private verified = false;

  async initialize(): Promise<void> {
    this.configured = await this.settings.hasApiKey();
    await this.loadLedger();
  }

  status(): BrainStatus {
    this.rollMonth();
    return {
      configured: this.configured,
      verified: this.verified,
      model: MODEL,
      spentAud: this.ledger.spentAud,
      monthlyLimitAud: this.monthlyLimitAud,
    };
  }

  async configure(apiKey: string | null): Promise<BrainStatus> {
    if (!apiKey) {
      await this.settings.saveApiKey(null);
      this.configured = false;
      this.verified = false;
      return this.status();
    }

    const clean = apiKey.trim();
    if (!clean) throw new Error("Paste your complete OpenAI API key first.");
    await this.verifyKey(clean);
    await this.settings.saveApiKey(clean);
    this.configured = true;
    this.verified = true;
    return this.status();
  }

  async testConnection(): Promise<BrainStatus> {
    const apiKey = await this.settings.getApiKey();
    if (!apiKey) throw new Error("No OpenAI API key is saved. Paste one below and choose Connect & test.");
    await this.verifyKey(apiKey);
    this.configured = true;
    this.verified = true;
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
    let response;
    try {
      response = await client.responses.create({
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
    } catch (error) {
      throw new Error(this.explainApiError(error));
    }

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

  private async verifyKey(apiKey: string): Promise<void> {
    const client = new OpenAI({ apiKey, maxRetries: 0, timeout: 20_000 });
    try {
      const response = await client.responses.create({
        model: MODEL,
        reasoning: { effort: "none" },
        max_output_tokens: 16,
        input: "Reply with OK.",
      });
      await this.recordUsage(response.usage?.input_tokens ?? 0, response.usage?.output_tokens ?? 0);
    } catch (error) {
      this.verified = false;
      throw new Error(this.explainApiError(error));
    }
  }

  private explainApiError(error: unknown): string {
    if (error instanceof OpenAI.AuthenticationError || (error instanceof OpenAI.APIError && error.status === 401)) {
      return "OpenAI rejected this key (401). Create a fresh API key at platform.openai.com/api-keys, then paste the complete key with no spaces.";
    }
    if (error instanceof OpenAI.PermissionDeniedError || (error instanceof OpenAI.APIError && error.status === 403)) {
      return "The key is valid, but its project or region does not allow this request (403). Check the key's project permissions and API access.";
    }
    if (error instanceof OpenAI.NotFoundError || (error instanceof OpenAI.APIError && error.status === 404)) {
      return `The key cannot access ${MODEL} (404). Create the key in a project that has access to this model.`;
    }
    if (error instanceof OpenAI.RateLimitError || (error instanceof OpenAI.APIError && error.status === 429)) {
      const code = error instanceof OpenAI.APIError ? String(error.code ?? "") : "";
      if (code === "credit_balance_exhausted") {
        return "The key is valid, but the API credit balance is empty. Add API credits in OpenAI billing, then test again.";
      }
      if (code.includes("spend_limit") || code.includes("usage_limit")) {
        return "The key is valid, but its OpenAI project or organisation spending limit has been reached. Raise the API limit, then test again.";
      }
      return "OpenAI is rate-limiting this project (429). Wait a moment, then test again.";
    }
    if (error instanceof OpenAI.APIConnectionError) {
      return "JARVIS could not reach the OpenAI API. Check the internet connection, firewall, VPN, or proxy, then test again.";
    }
    if (error instanceof OpenAI.APIError) {
      const suffix = error.status ? ` (HTTP ${error.status})` : "";
      return `OpenAI could not verify the connection${suffix}. ${error.message}`;
    }
    return error instanceof Error ? error.message : "OpenAI could not verify the connection.";
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

