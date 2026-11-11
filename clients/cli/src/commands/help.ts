import type { Command } from "./types.js";
import { getCommands } from "./registry.js";
import { KEYBOARD_HELP } from "../components/keyboardHelp.js";

/** Display grouping for /help. Commands not listed fall under "Other". */
const CATEGORIES: ReadonlyArray<readonly [string, readonly string[]]> = [
  ["Session", ["clear", "resume", "sessions", "queue", "export", "file"]],
  ["Model & agents", ["model", "agent"]],
  ["Mode & scope", ["mode", "scope"]],
  ["Blue monitoring", ["blue", "alerts", "status"]],
  ["Diagnostics", ["doctor"]],
  ["Stack & plugins", ["plugins", "mcp"]],
  ["Meta", ["help", "quit"]],
];

/** Worked examples for commands whose usage is not obvious from the hint. */
const EXAMPLES: ReadonlyArray<readonly [string, string]> = [
  ["/mode", "/mode red approve"],
  ["/scope", "/scope import <file> --confirm"],
  ["/resume", "/resume"],
  ["/export", "/export markdown ./findings.md"],
  ["/blue", "/blue up 127.0.0.1:3000"],
];

const help: Command = {
  name: "help",
  description: "Show available commands and shortcuts",
  aliases: ["?"],
  execute(_args, ctx) {
    const commands = getCommands();
    const visible = commands.filter((c) => !c.isHidden);
    const maxLen = Math.max(...visible.map((c) => c.name.length + (c.argumentHint?.length ?? 0) + 1));

    const renderCommand = (command: (typeof visible)[number]): string => {
      const usage = command.argumentHint ? `/${command.name} ${command.argumentHint}` : `/${command.name}`;
      const aliases = command.aliases?.length
        ? ` (also ${command.aliases.map((name) => `/${name}`).join(", ")})`
        : "";
      return `  ${usage.padEnd(maxLen + 4)}${command.description}${aliases}`;
    };

    const grouped = new Map<string, Command[]>();
    const uncategorized: typeof visible = [];
    for (const command of visible) {
      const category = CATEGORIES.find(([, names]) => names.includes(command.name))?.[0];
      if (category) {
        const bucket = grouped.get(category) ?? [] as Command[];
        bucket.push(command);
        grouped.set(category, bucket);
      } else {
        uncategorized.push(command);
      }
    }

    const lines: string[] = ["Examples:"];
    for (const [name, example] of EXAMPLES) {
      lines.push(`  ${name.padEnd(10)} ${example}`);
    }
    for (const [category, bucket] of grouped) {
      lines.push("", `Commands — ${category}:`, ...bucket.map(renderCommand));
    }
    if (uncategorized.length > 0) {
      lines.push("", "Commands — Other:", ...uncategorized.map(renderCommand));
    }
    lines.push("", "Shortcuts:", ...KEYBOARD_HELP.slice(1));

    ctx.addSystemEvent(lines.join("\n"));
  },
};

export default help;