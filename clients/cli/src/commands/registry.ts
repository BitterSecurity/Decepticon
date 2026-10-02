/**
 * Command registry — central registration and lookup for slash commands.
 *
 * Follows Claude Code's pattern: all commands registered in a single array,
 * lookup by name or alias.
 */

import type { Command } from "./types.js";
import help from "./help.js";
import clear from "./clear.js";
import file from "./file.js";
import quit from "./quit.js";
import resume from "./resume.js";
import model from "./model.js";
import plugins from "./plugins.js";
import agent from "./agent.js";
import web from "./web.js";
import blue from "./blue.js";

/** All registered commands. Add new commands here. */
const COMMANDS: Command[] = [help, clear, file, quit, resume, model, plugins, agent, web, blue];

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
