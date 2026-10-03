import { spawn } from "node:child_process";
import type { Command, CommandContext } from "./types.js";

/**
 * `/web` slash command — start, stop, or print the URL of the web dashboard.
 *
 * v1.1.8 dynamic-spawn model: the `web` compose service is gated behind
 * `profiles: [web]` so `decepticon start` no longer brings the dashboard
 * up by default. This command runs `docker compose --profile web up -d
 * web` against the host docker daemon (the CLI container has docker.sock
 * + the compose project bind-mounted, see docker-compose.yml `cli:`).
 *
 * Behaviour:
 *   /web            — `up -d web` (idempotent)
 *   /web up         — same
 *   /web down       — `stop web` (container preserved for fast re-up)
 *   /web stop       — same
 *   /web url        — print the dashboard URL (no docker call)
 */
const web: Command = {
  name: "web",
  description: "Start, stop, or print the URL of the web dashboard",
  aliases: ["dashboard"],
  argumentHint: "[up|down|stop|url]",
  execute: async (args, context) => {
    const arg = args.trim().toLowerCase();
    if (arg === "url") {
      printURL(context);
      return;
    }
    if (arg === "down" || arg === "stop") {
      await stopWeb(context);
      return;
    }
    await startWeb(context);
  },
};

export default web;

function url(): string {
  return `http://localhost:${process.env["WEB_PORT"] ?? "3000"}`;
}

function printURL(context: CommandContext): void {
  context.addSystemEvent(`Web dashboard URL: ${url()}`);
}

function composeArgs(): string[] {
  const project = process.env["DECEPTICON_COMPOSE_PROJECT"] ?? "decepticon";
  const composeFile =
    process.env["DECEPTICON_COMPOSE_FILE"] ?? "/decepticon-home/docker-compose.yml";
  const envFile =
    process.env["DECEPTICON_COMPOSE_ENV_FILE"] ?? "/decepticon-home/.env";
  return ["compose", "-p", project, "-f", composeFile, "--env-file", envFile];
}

function startWeb(context: CommandContext): Promise<void> {
  const hostHome = process.env["DECEPTICON_HOST_HOME"];
  if (!hostHome) {
    context.addSystemEvent("❌ Cannot start web: host install path is unavailable. Update the Decepticon CLI stack and retry.");
    return Promise.resolve();
  }
  return runDockerCompose(
    context,
    [...composeArgs(), "--profile", "web", "up", "-d", "--no-build", "--no-deps", "web"],
    {
      env: { ...process.env, DECEPTICON_HOME: hostHome },
      pending: "Starting web dashboard…",
      success: () => `✅ Web dashboard up: ${url()}`,
      failure: (code, stderr) => formatComposeFailure("start", code, stderr),
    },
  );
}

function stopWeb(context: CommandContext): Promise<void> {
  return runDockerCompose(
    context,
    [...composeArgs(), "stop", "web"],
    {
      pending: "Stopping web dashboard…",
      success: () => "✅ Web dashboard stopped (container kept for fast re-up)",
      failure: (code, stderr) => formatComposeFailure("stop", code, stderr),
    },
  );
}

interface RunOptions {
  env?: NodeJS.ProcessEnv;
  pending: string;
  success: (stdout: string) => string;
  failure: (code: number | null, stderr: string) => string;
}

function runDockerCompose(
  context: CommandContext,
  args: string[],
  opts: RunOptions,
): Promise<void> {
  return new Promise((resolve) => {
    context.addSystemEvent(opts.pending);
    const proc = spawn("docker", args, {
      stdio: ["ignore", "pipe", "pipe"],
      env: opts.env ?? process.env,
    });
    let stdout = "";
    let stderr = "";
    proc.stdout?.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    proc.stderr?.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    proc.on("error", (err) => {
      // ENOENT typically means the CLI image was built without docker
      // CLI (pre-v1.1.8). Surface it directly so the operator does not
      // hunt through stack traces.
      context.addSystemEvent(
        `❌ Could not exec docker: ${err.message}. ` +
          "Is the docker CLI installed in this image? (cli.Dockerfile v1.1.8+)",
      );
      resolve();
    });
    proc.on("close", (code) => {
      if (code === 0) {
        context.addSystemEvent(opts.success(stdout));
      } else {
        context.addSystemEvent(opts.failure(code, stderr));
      }
      resolve();
    });
  });
}

export function formatComposeFailure(action: "start" | "stop", code: number | null, stderr: string): string {
  const output = stderr.trim();
  const tail = output.length > 1200 ? `…\n${output.slice(-1200)}` : output;
  return `❌ Failed to ${action} web (exit ${code}): ${tail}`;
}
