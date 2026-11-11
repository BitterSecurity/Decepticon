import { afterEach, expect, it, vi } from "vitest";
import { findCommand, getCommands } from "./registry.js";
import type { CommandContext } from "./types.js";
import { completions, completedInput } from "../components/composerCompletion.js";
import { setAssistantOverride } from "./assistantOverride.js";

afterEach(() => { vi.unstubAllEnvs(); setAssistantOverride(""); });

function context(): CommandContext {
  return { addSystemEvent: vi.fn(), clearEvents: vi.fn(), submit: vi.fn(), resume: vi.fn(), exit: vi.fn(), selectAssistant: vi.fn(() => true) };
}

it.each(["new", "exit", "?", "about", "cwd", "continue", "r"])("completes and dispatches the /%s alias when typed", async (name) => {
  // Given a registered alias in the interactive command registry
  const command = findCommand(name);
  if (!command) throw new Error(`Missing command: ${name}`);
  // When its exact spelling is completed
  const input = `/${name}`;
  const choices = completions(input, getCommands());
  // Then Enter keeps the alias and dispatch resolves the same command
  expect(choices.some((choice) => choice.value === input)).toBe(true);
  expect(completedInput(input, choices, 0)).toBe(input);
  expect(findCommand(command.name)).toBe(command);
});

it("uses the guarded mode transition when /plan is selected", async () => {
  // Given a normal CLI with the existing mode transition callback
  const ctx = context();
  // When the convenience command runs
  await findCommand("plan")?.execute("", ctx);
  // Then it selects planning without sending a message or approving scope
  expect(ctx.selectAssistant).toHaveBeenCalledWith("soundwave");
  expect(ctx.submit).not.toHaveBeenCalled();
});

it.each(["new", "plan"])("rejects unsupported arguments instead of dropping the /%s prompt", async (name) => {
  // Given a prompt supplied after a no-argument convenience command
  const ctx = context();
  // When the command executes
  await findCommand(name)?.execute("keep this prompt", ctx);
  // Then no destructive action or mode switch happens
  expect(ctx.clearEvents).not.toHaveBeenCalled();
  expect(ctx.selectAssistant).not.toHaveBeenCalled();
  expect(ctx.addSystemEvent).toHaveBeenCalledWith(expect.stringContaining("Usage:"));
});

it("shows both workspace and process paths when the CLI is containerized", async () => {
  // Given a target workspace different from the CLI process directory
  vi.stubEnv("DECEPTICON_WORKSPACE_PATH", "/workspace/example");
  const ctx = context();
  // When the operator checks the directory
  await findCommand("pwd")?.execute("", ctx);
  // Then the paths are labeled independently
  expect(ctx.addSystemEvent).toHaveBeenCalledWith(`Target workspace: /workspace/example\nCLI process directory: ${process.cwd()}`);
});

it("keeps hidden commands and aliases out of autocomplete", () => {
  // Given a hidden command with an alias
  const commands = [{ name: "secret", aliases: ["hidden"], description: "hidden", isHidden: true, execute() {} }];
  // When the slash menu opens
  const choices = completions("/", commands);
  // Then neither spelling is suggested
  expect(choices).toEqual([]);
});
