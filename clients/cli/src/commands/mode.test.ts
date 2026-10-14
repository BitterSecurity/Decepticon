import { createHash } from "node:crypto";
import { mkdtemp, mkdir, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
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
    selectAssistant: (assistant) => { selected.push(assistant); return true; },
    resume: () => {},
    exit: () => {},
  };
  return { ctx, messages, selected };
}

describe("CLI run modes", () => {
  afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); setAssistantOverride(""); });

  it("keeps Plan explicit and accepts a pinned direct Red scope", async () => {
    const root = await mkdtemp(path.join(tmpdir(), "decepticon-mode-command-"));
    await mkdir(path.join(root, "plan"));
    const roe = '{"in_scope":[{"target":"example.com"}]}';
    await writeFile(path.join(root, "plan", "roe.json"), roe);
    await writeFile(path.join(root, ".red-approved"), `roe:${createHash("sha256").update(roe).digest("hex")}`);
    vi.stubEnv("DECEPTICON_ENGAGEMENT", "direct");
    vi.stubEnv("DECEPTICON_WORKSPACE_PATH", root);
    const { ctx, selected } = context();
    await mode.execute("plan", ctx);
    expect(getAssistantOverride()).toBe("soundwave");
    await mode.execute("red", ctx);
    expect(getAssistantOverride()).toBe("decepticon");
    expect(selected).toEqual(["soundwave", "decepticon"]);
  });

  it("selects Blue without its sensor", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    const { ctx, messages, selected } = context();
    await mode.execute("blue", ctx);
    expect(selected).toEqual(["blue_cell"]);
    expect(getAssistantOverride()).toBe("blue_cell");
  });

  it("selects Red without approving absent scope", async () => {
    const root = await mkdtemp(path.join(tmpdir(), "decepticon-unapproved-mode-"));
    vi.stubEnv("DECEPTICON_ENGAGEMENT", "unapproved");
    vi.stubEnv("DECEPTICON_WORKSPACE_PATH", root);
    const { ctx, selected } = context();
    await mode.execute("red", ctx);
    expect(selected).toEqual(["decepticon"]);
    expect(getAssistantOverride()).toBe("decepticon");
  });

  it("does not authorize a validated draft when the active run cannot stop", async () => {
    const root = await mkdtemp(path.join(tmpdir(), "decepticon-mode-stop-"));
    await mkdir(path.join(root, "plan"));
    for (const filename of ["roe.json", "threat-profile.json", "conops.json", "deconfliction.json", "contact.json", "data-handling.json", "abort.json", "cleanup.json"]) {
      await writeFile(path.join(root, "plan", filename), "{}");
    }
    await writeFile(path.join(root, ".planning-draft-ready"), "23bb0ad057e96e5098d6c8ef731ce1afadd9e1957f6adf6b720196ad35409418");
    vi.stubEnv("DECEPTICON_ENGAGEMENT", "stop-failed");
    vi.stubEnv("DECEPTICON_WORKSPACE_PATH", root);
    const { ctx } = context();
    ctx.selectAssistant = async () => false;
    await mode.execute("red approve", ctx);
    await expect(readFile(path.join(root, ".red-approved"))).rejects.toMatchObject({ code: "ENOENT" });
    expect(getAssistantOverride()).toBe("");
  });

  it("keeps web terminal mode routing with the web selector", async () => {
    vi.stubEnv("DECEPTICON_WEB_TERMINAL", "true");
    const { ctx, messages, selected } = context();
    await mode.execute("plan", ctx);
    expect(selected).toEqual([]);
    expect(messages.at(-1)).toContain("controls above the web terminal");
  });
});
