/**
 * Command registry — central registration and lookup for slash commands.
 *
 * Follows Claude Code's pattern: all commands registered in a single array,
 * lookup by name or alias.
 */

import { localCommands } from "./local.js";
import type { Command } from "./types.js";
import help from "./help.js";
import clear from "./clear.js";
import file from "./file.js";
import quit from "./quit.js";
import { terminalCommands } from "./terminal.js";
import resume from "./resume.js";
import model from "./model.js";
import plugins from "./plugins.js";
import agent from "./agent.js";
import blue from "./blue.js";
import mode from "./mode.js";
import { scope } from "./scope.js";
import { convenienceCommands } from "./convenience.js";

/** All registered commands. Add new commands here. */
const COMMANDS: Command[] = [help, clear, file, quit, resume, model, plugins, mode, scope, agent, blue, ...localCommands, ...convenienceCommands, ...terminalCommands];

/** Get all registered commands. */
export function getCommands(): Command[] {
  return COMMANDS;
}

/** Find a command by name or alias. Returns undefined if not found. */
export function findCommand(input: string): Command | undefined {
  const name = input.toLowerCase();
  return COMMANDS.find(
    (cmd) => cmd.name === name || cmd.aliases?.includes(name),
  );
}

/**
 * Parse a slash command string into command name and arguments.
 * Returns null if the input is not a slash command.
 *
 * Examples:
 *   "/help"        → { name: "help", args: "" }
 *   "/clear"       → { name: "clear", args: "" }
 *   "/unknown foo" → { name: "unknown", args: "foo" }
 */
/**
 * Nearest registered command for a mistyped name, or null when nothing is
 * close enough. Mirrors the launcher's alias-aware suggestion pattern
 * (clients/launcher/cmd/root.go) so both CLIs behave the same way.
 */
export function suggestCommand(input: string): string | null {
  const name = input.replace(/^\//, "").toLowerCase();
  if (name.length === 0 || name.length > 32) return null;
  const distance = (left: string, right: string): number => {
    const a = [...left], b = [...right];
    const row = Array.from({ length: b.length + 1 }, (_, i) => i);
    for (let i = 1; i <= a.length; i++) {
      let carry = row[0]!;
      row[0] = i;
      for (let j = 1; j <= b.length; j++) {
        const temp = row[j]!;
        row[j] = Math.min(row[j]! + 1, row[j - 1]! + 1, carry + (a[i - 1] === b[j - 1] ? 0 : 1));
        carry = temp;
      }
    }
    return row[b.length]!;
  };
  let best: string | null = null;
  let bestDistance = 3; // suggestion threshold (<= 2)
  for (const command of COMMANDS) {
    for (const candidate of [command.name, ...(command.aliases ?? [])]) {
      const d = distance(name, candidate.toLowerCase());
      if (d < bestDistance) {
        bestDistance = d;
        best = command.name;
      }
    }
  }
  return best;
}

export function parseSlashCommand(
  input: string,
): { name: string; args: string } | null {
  const trimmed = input.trim();
  if (!trimmed.startsWith("/")) return null;

  const spaceIdx = trimmed.indexOf(" ");
  if (spaceIdx === -1) {
    return { name: trimmed.slice(1), args: "" };
  }
  return {
    name: trimmed.slice(1, spaceIdx),
    args: trimmed.slice(spaceIdx + 1).trim(),
  };
}
