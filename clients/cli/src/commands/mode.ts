import { existsSync } from "node:fs";
import type { Command } from "./types.js";
import { getAssistantOverride, setAssistantOverride } from "./assistantOverride.js";
import { blueConfigPath, blueMonitorReady, blueUp, engagementLabel } from "./blue.js";

const ASSISTANTS = {
  plan: "soundwave",
  red: "decepticon",
  blue: "autobot",
} as const;

type RunMode = keyof typeof ASSISTANTS;

function currentMode(): RunMode {
  const assistant = getAssistantOverride() || process.env.DECEPTICON_ASSISTANT_ID;
  if (assistant === "soundwave") return "plan";
  if (assistant === "autobot") return "blue";
  return "red";
}

const mode: Command = {
  name: "mode",
  description: "Switch Plan, Red, or Blue conversation",
  argumentHint: "[plan | red | blue]",
  complete: (args) => ["plan", "red", "blue"].filter((value) => value.startsWith(args)),
  async execute(args, ctx) {
    const selected = args.trim().toLowerCase();
    if (!selected) {
      if (ctx.openPicker) {
        ctx.openPicker({
          title: `Mode (current: ${currentMode()})`,
          options: [
            { label: "Plan", value: "plan", description: "Draft scope with Soundwave" },
            { label: "Red", value: "red", description: "Decepticon; enforces the engagement scope" },
            { label: "Blue", value: "blue", description: "Blue Cell; connect a local sensor for telemetry" },
          ],
          onSelect: async (value) => { await mode.execute(value, ctx); },
        });
        return;
      }
      ctx.addSystemEvent(`Current mode: ${currentMode()}. Use /mode plan, /mode red, or /mode blue. Plan drafts do not start Red; Blue requires a connected local sensor.`);
      return;
    }
    if (selected !== "plan" && selected !== "red" && selected !== "blue") {
      ctx.addSystemEvent("Usage: /mode plan | red | blue");
      return;
    }
    const next = selected;
    const rest = args.trim().replace(/^blue\b/, "").trim();
    try {
      if (next === "blue" && !(await blueMonitorReady())) {
        // Fail-closed: Blue requires a running AutoBot sensor. Entering Blue
        // auto-boots the stack from the engagement blue.json (or an explicit
        // target) — if that fails, the operator stays in the current mode.
        const label = engagementLabel();
        const hasConfig = Boolean(label) && existsSync(blueConfigPath(label));
        const target = rest ? rest.split(/\s+/)[0] : "";
        if (!hasConfig && !target) {
          ctx.addSystemEvent(
            "Blue requires a running AutoBot sensor. Start one with " +
            "/mode blue 127.0.0.1:3000, or configure the engagement first: " +
            "/blue engagement create --label <label> --target 127.0.0.1:3000",
          );
          return;
        }
        await blueUp(`up${target ? ` ${target}` : ""}${rest.includes("--logs") ? rest.slice(rest.indexOf("--logs")) : ""}`, ctx);
      }
      const assistant = ASSISTANTS[next];
      if (ctx.selectAssistant && !(await ctx.selectAssistant(assistant))) {
        return;
      }
      setAssistantOverride(assistant);
      if (next === "red") ctx.addSystemEvent("Red enforces the engagement scope in plan/roe.json. Create the engagement with targets, or import a scope with /scope import <file> --confirm.");
      if (next === "blue") ctx.addSystemEvent("Blue conversation selected — the AutoBot sensor stack is running. Send target traffic to http://127.0.0.1:18080; use /blue status to inspect coverage.");
      ctx.addSystemEvent(`Mode: ${next}. The next message goes to ${assistant}.`);
    } catch (error) {
      ctx.addSystemEvent(`Blue unavailable: ${error instanceof Error ? error.message : String(error)} — staying in ${currentMode()} mode.`);
    }
  },
};

export default mode;
