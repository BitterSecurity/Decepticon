import type { Command } from "./types.js";
import { getAssistantOverride, setAssistantOverride } from "./assistantOverride.js";
import { approveRedMode } from "./planMode.js";
import { blueMonitorReady } from "./blue.js";

const ASSISTANTS = {
  plan: "soundwave",
  red: "decepticon",
  blue: "blue_cell",
} as const;

type RunMode = keyof typeof ASSISTANTS;

function currentMode(): RunMode {
  const assistant = getAssistantOverride() || process.env.DECEPTICON_ASSISTANT_ID;
  if (assistant === "soundwave") return "plan";
  if (assistant === "blue_cell") return "blue";
  return "red";
}

const mode: Command = {
  name: "mode",
  description: "Switch Plan, Red, or Blue conversation",
  argumentHint: "[plan | red | blue]",
  async execute(args, ctx) {
    if (process.env.DECEPTICON_WEB_TERMINAL === "true") {
      ctx.addSystemEvent("Use the Plan, Red, or Blue controls above the web terminal to switch modes.");
      return;
    }
    const selected = args.trim().toLowerCase();
    if (!selected) {
      ctx.addSystemEvent(`Current mode: ${currentMode()}. Use /mode plan, /mode red, or /mode blue. Plan drafts do not start Red; Blue requires a connected local sensor.`);
      return;
    }
    if (!(selected in ASSISTANTS)) {
      ctx.addSystemEvent("Usage: /mode plan | red | blue");
      return;
    }
    const next = selected as RunMode;
    try {
      if (next === "red") await approveRedMode();
      if (next === "blue" && !(await blueMonitorReady())) {
        ctx.addSystemEvent("Blue needs a connected local sensor. Use /blue up 127.0.0.1:3000, then /mode blue.");
        return;
      }
      const assistant = ASSISTANTS[next];
      if (ctx.selectAssistant && !ctx.selectAssistant(assistant)) {
        ctx.addSystemEvent("Wait for the current run to finish before switching modes.");
        return;
      }
      setAssistantOverride(assistant);
      ctx.addSystemEvent(`Mode: ${next}. The next message goes to ${assistant}.`);
    } catch (error) {
      ctx.addSystemEvent(error instanceof Error ? error.message : String(error));
    }
  },
};

export default mode;
