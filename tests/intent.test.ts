import { describe, expect, it } from "vitest";
import { parseLocalIntent, simplifyCommand } from "../electron/main/intent.js";

describe("natural language cleanup", () => {
  it("removes wake words and polite filler", () => {
    expect(simplifyCommand("Hey JARVIS, could you close Spotify for me?")).toBe("close Spotify");
  });
});

describe("local intent parser", () => {
  it("opens arbitrary Start-menu apps", () => {
    expect(parseLocalIntent("please open Spotify")).toEqual({ type: "open_app", app: "Spotify" });
  });

  it("closes arbitrary apps", () => {
    expect(parseLocalIntent("can you close Google Chrome for me")).toEqual({ type: "close_app", app: "Google Chrome" });
  });

  it("keeps websites separate from apps", () => {
    expect(parseLocalIntent("open github.com")).toEqual({ type: "open_website", url: "github.com" });
  });

  it("handles update requests", () => {
    expect(parseLocalIntent("check for updates")).toEqual({ type: "check_updates" });
  });
});

