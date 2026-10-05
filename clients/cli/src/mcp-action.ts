import blue from "./commands/blue.js";
import web from "./commands/web.js";
import type { Command, CommandContext } from "./commands/types.js";

export async function runMcpAction(args: string[]): Promise<string> {
  const [surface, action, ...rest] = args;
  let command: Command;
  let commandArgs: string;
  if (surface === "blue") {
    if (!action || !["up", "status", "verify", "stop"].includes(action)) {
      throw new Error("Blue action must be up, status, verify, or stop");
    }
    if (action === "up") {
      if (rest.length !== 1 && !(rest.length === 3 && rest[1] === "--logs")) {
        throw new Error("Blue up requires a local upstream and optional --logs directory");
      }
    } else if (rest.length) {
      throw new Error(`Blue ${action} takes no arguments`);
    }
    command = blue;
    commandArgs = [action, ...rest].join(" ");
  } else if (surface === "web") {
    if (!action || !["up", "down", "url"].includes(action) || rest.length) {
      throw new Error("Web action must be up, down, or url");
    }
    command = web;
    commandArgs = action;
  } else {
    throw new Error("MCP action must be blue or web");
  }

  const events: string[] = [];
  const unsupported = () => { throw new Error("Interactive CLI operation is unavailable in MCP action mode"); };
  const context: CommandContext = {
    addSystemEvent: (event) => events.push(event),
    clearEvents: unsupported,
    submit: unsupported,
    resume: unsupported,
    exit: unsupported,
  };
  await command.execute(commandArgs, context);
  const failure = events.find((event) => event.startsWith("Blue sensor:") || event.startsWith("❌"));
  if (failure) throw new Error(failure);
  return events.join("\n");
}

if (process.argv[1]?.endsWith("mcp-action.js")) {
  runMcpAction(process.argv.slice(2)).then(
    (output) => { process.stdout.write(`${output}\n`); },
    (error: unknown) => {
      process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
      process.exitCode = 1;
    },
  );
}
