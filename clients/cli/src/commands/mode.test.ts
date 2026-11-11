import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import mode from "./mode.js";
import { getAssistantOverride, setAssistantOverride } from "./assistantOverride.js";
import type { CommandContext } from "./types.js";

function context() {
  const messages: string[] = [];
  const selected: string[] = [];
  const ctx: CommandContext = {
    addSystemEvent: (message) => messages.push(message),
    clearEvents: () => {},
    submit: () => {},
    selectAssistant: async (assistant) => { selected.push(assistant); return true; },
    resume: () => {},
    exit: () => {},
  };
  return { ctx, messages, selected };
}

describe("CLI run modes", () => {
  beforeEach(() => { vi.unstubAllEnvs(); });
  afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); setAssistantOverride(""); });

  it("keeps Plan explicit and switches to Red with an enforced scope", async () => {
    const root = await mkdtemp(path.join(tmpdir(), "decepticon-mode-command-"));
    await mkdir(path.join(root, "plan"));
    await writeFile(path.join(root, "plan", "roe.json"), JSON.stringify({
      engagement_name: "direct",
      machine_enforcement: { mode: "enforce", in_scope: ["example.com"], out_of_scope: [] },
    }));
    vi.stubEnv("DECEPTICON_ENGAGEMENT", "direct");
    vi.stubEnv("DECEPTICON_WORKSPACE_PATH", root);
    const { ctx, selected } = context();
    await mode.execute("plan", ctx);
    expect(getAssistantOverride()).toBe("soundwave");
    expect(selected).toEqual(["soundwave"]);
    await mode.execute("red", ctx);
    expect(getAssistantOverride()).toBe("decepticon");
    expect(selected).toEqual(["soundwave", "decepticon"]);
  });

  it("selects Red directly without a prior Plan conversation", async () => {
    const { ctx, messages, selected } = context();
    await mode.execute("red", ctx);
    expect(getAssistantOverride()).toBe("decepticon");
    expect(selected).toEqual(["decepticon"]);
    expect(messages.at(-2)).toContain("enforces the engagement scope");
  });

  it("refuses Blue when no sensor runs and no target is configured", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    const { ctx, messages } = context();
    await mode.execute("blue", ctx);
    expect(messages.join("\n")).toContain("Blue requires a running AutoBot sensor");
    expect(getAssistantOverride()).not.toBe("autobot");
  });

  it("selects Blue when the sensor stack is already running", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true }));
    const { ctx, selected } = context();
    await mode.execute("blue", ctx);
    expect(selected).toEqual(["autobot"]);
    expect(getAssistantOverride()).toBe("autobot");
  });

  it("attempts the sensor boot on Blue entry and stays out on failure", async () => {
    vi.stubEnv("DECEPTICON_ENGAGEMENT", "tui-live");
    const configRoot = mkdtempSync(path.join(tmpdir(), "mode-blue-config-"));
    vi.stubEnv("DECEPTICON_BLUE_WORKSPACE", configRoot);
    writeFileSync(
      path.join(configRoot, "blue.json"),
      JSON.stringify({ version: 1, targets: [{ id: "local-target", upstream: "http://127.0.0.1:3000", enabled: true }] }),
    );
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    const { ctx, selected, messages } = context();
    await mode.execute("blue", ctx);
    // The boot attempt runs (blueUp → the sensor compose); in the test env
    // it cannot complete, so the operator stays in the current mode with
    // the failure surfaced — fail-closed, never a silent dead assistant.
    expect(messages.join("\n")).toContain("Blue unavailable");
    expect(selected).toEqual([]);
    rmSync(configRoot, { recursive: true, force: true });
  });

  it("selects Red even without a scope file", async () => {
    const root = await mkdtemp(path.join(tmpdir(), "decepticon-unapproved-mode-"));
    vi.stubEnv("DECEPTICON_ENGAGEMENT", "unapproved");
    vi.stubEnv("DECEPTICON_WORKSPACE_PATH", root);
    const { ctx, selected } = context();
    await mode.execute("red", ctx);
    expect(selected).toEqual(["decepticon"]);
    expect(getAssistantOverride()).toBe("decepticon");
  });
});