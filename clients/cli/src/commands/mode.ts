import { approveRedMode } from "./planMode.js";
import type { Command } from "./types.js";
import { getAssistantOverride, setAssistantOverride } from "./assistantOverride.js";

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
  argumentHint: "[plan | red [approve] | blue]",
  async execute(args, ctx) {
    if (process.env.DECEPTICON_WEB_TERMINAL === "true") {
      ctx.addSystemEvent("Use the Plan, Red, or Blue controls above the web terminal to switch modes.");
      return;
    }
    const input = args.trim().toLowerCase();
    const selected = input === "red approve" ? "red" : input;
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
      const assistant = ASSISTANTS[next];
      if (ctx.selectAssistant && !(await ctx.selectAssistant(assistant))) {
        return;
      }
      setAssistantOverride(assistant);
      if (input === "red approve") await approveRedMode();
      if (next === "red") ctx.addSystemEvent("Red operations require approved scope. Review the validated Plan, then use /mode red approve to authorize it.");
      if (next === "blue") ctx.addSystemEvent("Blue conversation selected. Connect a local sensor with /blue up 127.0.0.1:3000 before investigating telemetry.");
      ctx.addSystemEvent(`Mode: ${next}. The next message goes to ${assistant}.`);
    } catch (error) {
      ctx.addSystemEvent(error instanceof Error ? error.message : String(error));
    }
  },
};

export default mode;
