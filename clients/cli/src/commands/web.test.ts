import { EventEmitter } from "node:events";
import { spawn, type ChildProcess } from "node:child_process";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CommandContext } from "./types.js";
import web, { formatComposeFailure } from "./web.js";

vi.mock("node:child_process", () => ({ spawn: vi.fn() }));

afterEach(() => {
  vi.unstubAllEnvs();
  vi.clearAllMocks();
});

describe("formatComposeFailure", () => {
  it("keeps the tail of docker compose stderr so the real error is visible", () => {
    const stderr = `${"pulling layer\n".repeat(80)}Error response from daemon: ports are not available: bind: address already in use`;

    const message = formatComposeFailure("start", 1, stderr);

    expect(message).toContain("Failed to start web (exit 1)");
    expect(message).toContain("address already in use");
  });
});

describe("/web startup", () => {
  it("starts only web with the host install path and leaves core containers alone", async () => {
    vi.stubEnv("DECEPTICON_COMPOSE_PROJECT", "decepticon");
    vi.stubEnv("DECEPTICON_COMPOSE_FILE", "/decepticon-home/docker-compose.yml");
    vi.stubEnv("DECEPTICON_COMPOSE_ENV_FILE", "/decepticon-home/.env");
    vi.stubEnv("DECEPTICON_HOST_HOME", "/home/operator/.decepticon");
    const proc = Object.assign(new EventEmitter(), {
      stdout: new EventEmitter(),
      stderr: new EventEmitter(),
    }) as ChildProcess;
    vi.mocked(spawn).mockImplementation(() => {
      queueMicrotask(() => proc.emit("close", 0));
      return proc;
    });
    const events: string[] = [];
    const context: CommandContext = {
      addSystemEvent: (event) => { events.push(event); },
      clearEvents: () => {},
      submit: () => {},
      resume: () => {},
      exit: () => {},
    };

    await web.execute("up", context);

    expect(spawn).toHaveBeenCalledOnce();
    const [command, args, options] = vi.mocked(spawn).mock.calls[0]!;
    expect(command).toBe("docker");
    expect(args).toContain("--no-deps");
    expect(options).toMatchObject({
      env: expect.objectContaining({ DECEPTICON_HOME: "/home/operator/.decepticon" }),
    });
    expect(events.at(-1)).toContain("Web dashboard up");
  });

  it("refuses to launch Compose without a host install path", async () => {
    vi.stubEnv("DECEPTICON_HOST_HOME", "");
    const events: string[] = [];
    const context: CommandContext = {
      addSystemEvent: (event) => { events.push(event); },
      clearEvents: () => {},
      submit: () => {},
      resume: () => {},
      exit: () => {},
    };

    await web.execute("up", context);

    expect(spawn).not.toHaveBeenCalled();
    expect(events.at(-1)).toContain("host install path is unavailable");
  });
});
