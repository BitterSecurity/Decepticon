/**
 * /agent — switch the active orchestrator for this CLI session.
 *
 * Only orchestration agents are surfaced here. Sub-agents (recon,
 * exploit, postexploit, …) are invoked via the orchestrator's task()
 * tool and shouldn't be selected directly. Available orchestrators are
 * discovered dynamically from the langgraph runtime via
 * POST /assistants/search.
 *
 * Selection writes to the per-process assistant override (see
 * commands/assistantOverride.ts) which useAgent reads on every submit()
 * / resume(). The choice persists for the lifetime of this CLI process.
 *
 * Usage:
 *   /agent              List orchestrators + current selection
 *   /agent <name>       Switch to the named orchestrator
 *   /agent clear        Clear the override (resume default behaviour)
 */

import type { Command } from "./types.js";
import {
  getAssistantOverride,
  setAssistantOverride,
} from "./assistantOverride.js";

interface AssistantRow {
  assistant_id: string;
  graph_id: string;
  name: string;
}

const CATALOG_PAGE_SIZE = 100;
const CATALOG_TIMEOUT_MS = 10_000;

function apiBase(): string {
  return process.env.DECEPTICON_API_URL || "http://localhost:2024";
}

/**
 * Discover orchestrators dynamically from the agent runtime.
 * Returns deduplicated graph_ids registered on the server.
 */
async function listOrchestrators(): Promise<string[]> {
  const signal = AbortSignal.timeout(CATALOG_TIMEOUT_MS);
  const graphIds = new Set<string>();
  for (let offset = 0; ; offset += CATALOG_PAGE_SIZE) {
    const res = await fetch(`${apiBase()}/assistants/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit: CATALOG_PAGE_SIZE, offset }),
      signal,
    });
    if (!res.ok) {
      throw new Error(`assistants/search HTTP ${res.status}: ${await res.text()}`);
    }
    const assistants = (await res.json()) as AssistantRow[];
    for (const assistant of assistants) graphIds.add(assistant.graph_id);
    if (assistants.length < CATALOG_PAGE_SIZE) return [...graphIds];
  }
}

const agent: Command = {
  name: "agent",
  description: "Show or switch the active orchestrator for this session",
  argumentHint: "[<name> | clear]",
  async execute(args, ctx) {
    const arg = args.trim();
    if (process.env.DECEPTICON_WEB_TERMINAL === "true" && arg) {
      ctx.addSystemEvent("Use the Plan, Red, or Blue controls above the web terminal to switch modes.");
      return;
    }

    // No arg → list orchestrators + current override
    if (!arg) {
      void (async () => {
        try {
          const orchestrators = await listOrchestrators();
          const current = getAssistantOverride();
          const lines: string[] = [];
          if (current) {
            lines.push(`Active orchestrator override: ${current}`);
          } else {
            lines.push(
              "No override active — using the launcher's default orchestrator selection.",
            );
          }
          lines.push("");
          lines.push("Available orchestrators:");
          if (orchestrators.length === 0) {
            lines.push("  (none — is the langgraph stack up?)");
          } else {
            for (const o of orchestrators) {
              const mark = o === current ? "*" : " ";
              lines.push(`  ${mark} ${o}`);
            }
          }
          lines.push("");
          lines.push("Usage:");
          lines.push("  /agent <name>     Switch (e.g. /agent vulnresearch)");
          lines.push("  /agent clear      Drop the override, resume default behaviour");
          ctx.addSystemEvent(lines.join("\n"));
        } catch (err) {
          ctx.addSystemEvent(
            `Could not list orchestrators: ${err instanceof Error ? err.message : String(err)}`,
          );
        }
      })();
      return;
    }

    // Clear
    if (arg === "clear" || arg === "off" || arg === "none") {
      const prev = getAssistantOverride();
      const initial = process.env.DECEPTICON_ASSISTANT_ID || "decepticon";
      try {
        if (ctx.selectAssistant && !(await ctx.selectAssistant(initial))) {
          return;
        }
        setAssistantOverride("");
        ctx.addSystemEvent(
          prev
            ? `Orchestrator override cleared (was '${prev}'). Default behaviour resumes on next message.`
            : "No override was set.",
        );
      } catch (error) {
        ctx.addSystemEvent(error instanceof Error ? error.message : String(error));
      }
      return;
    }

    // Switch — validate against server-registered orchestrators
    void (async () => {
      try {
        const orchestrators = await listOrchestrators();
        if (!orchestrators.includes(arg)) {
          const available = orchestrators.length > 0
            ? `Available: ${orchestrators.join(", ")}.`
            : "No orchestrators registered — is the langgraph stack up?";
          ctx.addSystemEvent(
            `'${arg}' isn't currently registered with the agent runtime. ${available}`,
          );
          return;
        }
        if (ctx.selectAssistant && !(await ctx.selectAssistant(arg))) {
          return;
        }
        setAssistantOverride(arg);
        ctx.addSystemEvent(
          `Active orchestrator: ${arg}\nNext message routes here. Type /agent clear to revert.`,
        );
      } catch (err) {
        ctx.addSystemEvent(
          `Failed to switch orchestrator: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    })();
  },
};

export default agent;
