import path from "node:path";
import { describe, expect, it } from "vitest";
import { APP_ALLOWLIST, isPathWithinRoot, isUrlAllowed } from "../electron/main/security.js";

describe("filesystem boundary", () => {
  const root = path.join(process.cwd(), "safe-root");

  it("allows the root and descendants", () => {
    expect(isPathWithinRoot(root, root)).toBe(true);
    expect(isPathWithinRoot(path.join(root, "campaigns", "draft.txt"), root)).toBe(true);
  });

  it("blocks sibling and prefix-confusion paths", () => {
    expect(isPathWithinRoot(path.join(process.cwd(), "elsewhere"), root)).toBe(false);
    expect(isPathWithinRoot(root + "-lookalike", root)).toBe(false);
  });
});

describe("browser boundary", () => {
  const origins = ["https://github.com", "https://business.facebook.com"];

  it("allows exact origins and subdomains over the configured protocol", () => {
    expect(isUrlAllowed("https://github.com/openai", origins)).toBe(true);
    expect(isUrlAllowed("https://sub.github.com/path", origins)).toBe(true);
  });

  it("blocks lookalike domains, wrong protocols, and script URLs", () => {
    expect(isUrlAllowed("https://github.com.evil.example", origins)).toBe(false);
    expect(isUrlAllowed("http://github.com", origins)).toBe(false);
    expect(isUrlAllowed("javascript:alert(1)", origins)).toBe(false);
  });
});

describe("application boundary", () => {
  it("contains only explicit application keys", () => {
    expect(Object.keys(APP_ALLOWLIST).sort()).toEqual(["calculator", "explorer", "notepad", "paint", "terminal"]);
    expect(APP_ALLOWLIST["powershell"]).toBeUndefined();
  });
});
