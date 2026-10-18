import fs from "node:fs/promises";
import path from "node:path";
import { app, safeStorage } from "electron";

interface StoredSettings {
  encryptedApiKey?: string;
  apiKeyVerifiedAt?: string;
}

export class SettingsStore {
  private filePath(): string {
    return path.join(app.getPath("userData"), "settings.json");
  }

  private async read(): Promise<StoredSettings> {
    try {
      return JSON.parse(await fs.readFile(this.filePath(), "utf8")) as StoredSettings;
    } catch {
      return {};
    }
  }

  async hasApiKey(): Promise<boolean> {
    if (process.env.OPENAI_API_KEY?.trim()) return true;
    return Boolean((await this.read()).encryptedApiKey);
  }

  async hasVerifiedApiKey(): Promise<boolean> {
    if (process.env.OPENAI_API_KEY?.trim()) return false;
    const current = await this.read();
    return Boolean(current.encryptedApiKey && current.apiKeyVerifiedAt);
  }

  async getApiKey(): Promise<string | null> {
    if (process.env.OPENAI_API_KEY?.trim()) return process.env.OPENAI_API_KEY.trim();
    const encrypted = (await this.read()).encryptedApiKey;
    if (!encrypted) return null;
    if (!safeStorage.isEncryptionAvailable()) throw new Error("Windows secure storage is unavailable. Use OPENAI_API_KEY in .env instead.");
    return safeStorage.decryptString(Buffer.from(encrypted, "base64"));
  }

  async saveApiKey(apiKey: string | null): Promise<void> {
    if (process.env.OPENAI_API_KEY?.trim()) throw new Error("The API key is managed by OPENAI_API_KEY in .env.");
    const current = await this.read();
    if (!apiKey) {
      delete current.encryptedApiKey;
      delete current.apiKeyVerifiedAt;
    } else {
      const clean = apiKey.trim();
      if (!/^sk-[A-Za-z0-9_-]{20,}$/.test(clean)) throw new Error("That does not look like an OpenAI API key.");
      if (!safeStorage.isEncryptionAvailable()) throw new Error("Windows secure storage is unavailable. Use OPENAI_API_KEY in .env instead.");
      current.encryptedApiKey = safeStorage.encryptString(clean).toString("base64");
      current.apiKeyVerifiedAt = new Date().toISOString();
    }
    await fs.mkdir(path.dirname(this.filePath()), { recursive: true });
    await fs.writeFile(this.filePath(), JSON.stringify(current, null, 2), { encoding: "utf8", mode: 0o600 });
  }

  async markApiKeyVerified(): Promise<void> {
    if (process.env.OPENAI_API_KEY?.trim()) return;
    const current = await this.read();
    if (!current.encryptedApiKey) return;
    current.apiKeyVerifiedAt = new Date().toISOString();
    await fs.writeFile(this.filePath(), JSON.stringify(current, null, 2), { encoding: "utf8", mode: 0o600 });
  }
}

