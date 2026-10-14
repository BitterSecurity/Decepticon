/**
 * Command type definitions for the Decepticon CLI slash command system.
 *
 * Simplified from Claude Code's three-type system (prompt/local/local-jsx)
 * to a single `Command` interface — all commands execute locally.
 */

/** Context passed to command handlers. */
export interface CommandContext {
  /** Add a system event to the activity stream. */
  addSystemEvent: (content: string) => void;
  /** Clear all events from the activity stream. */
  clearEvents: () => void;
  /** Submit a message to the agent. */
  submit: (input: string) => void;
  selectAssistant?: (assistantId: string) => boolean | Promise<boolean>;
  /** Resume a paused run with optional feedback. */
  resume: (value?: string) => void;
  /** Exit the application. */
  exit: () => void;
}

/** Result of executing a command. */
export interface CommandResult {
  /** If true, submit args as a message to the agent after handling. */
  shouldSubmit?: boolean;
}

/** A slash command definition. */
export interface Command {
  /** Internal name used for lookup (e.g. "help"). */
  name: string;
  /** User-facing description shown in autocomplete and /help. */
  description: string;
  /** Alternative names that also trigger this command. */
  aliases?: string[];
  /** Hint for expected arguments (e.g. "<path>"). */
  argumentHint?: string;
  /** Whether the command is hidden from autocomplete. */
  isHidden?: boolean;
  /** Execute the command. */
  execute: (args: string, context: CommandContext) => CommandResult | void | Promise<CommandResult | void>;
}
