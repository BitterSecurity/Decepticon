import { spawn } from "node:child_process";
import { isIP } from "node:net";
import type { Command, CommandContext } from "./types.js";
import { setAssistantOverride } from "./assistantOverride.js";

interface SensorMetrics {
  latest_seq: number;
  events_total: number;
  body_files: number;
  body_bytes: number;
  body_evicted_total: number;
  events_evicted_total: number;
  rejected_total: number;
}

interface SensorEvent {
  seq: number;
  occurred_at?: string;
  event_type?: string;
  method?: string;
  path?: string;
  status?: number;
  request_body_status?: string;
  message?: string;
}

interface BlueIncident {
  id: string;
  event_seq: number;
  rule_id: string;
  severity: string;
  analysis_state: string;
}

export function parseLocalUpstream(input: string): string {
  const candidate = input.includes("://") ? input : `http://${input}`;
  const url = new URL(candidate);
  if (
    !["http:", "https:"].includes(url.protocol) ||
    !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname) ||
    !url.port ||
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  ) {
    throw new Error("Use a local HTTP service such as 127.0.0.1:3000");
  }
  return url.origin;
}

function composeArgs(): string[] {
  const file = process.env.BLUE_SENSOR_COMPOSE_FILE ?? "/app/blue-sensor/compose.runtime.yaml";
  const project = process.env.DECEPTICON_COMPOSE_PROJECT ?? "decepticon";
  const envFile = process.env.DECEPTICON_COMPOSE_ENV_FILE;
  return ["compose", "-f", file, "-p", `${project}-blue`, ...(envFile ? ["--env-file", envFile] : [])];
}

function sensorUrl(): string {
  return process.env.BLUE_SENSOR_URL ?? "http://127.0.0.1:18081";
}

function monitorUrl(): string {
  return process.env.BLUE_MONITOR_URL ?? "http://127.0.0.1:18085";
}

async function query<T>(path: string): Promise<T> {
  const response = await fetch(`${sensorUrl()}${path}`, {
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw new Error(`Blue receiver HTTP ${response.status}`);
  return (await response.json()) as T;
}

async function queryMonitor<T>(path: string): Promise<T> {
  const response = await fetch(`${monitorUrl()}${path}`, {
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw new Error(`Blue monitor HTTP ${response.status}`);
  return (await response.json()) as T;
}

function runDocker(args: string[], environment: NodeJS.ProcessEnv): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn("docker", args, {
      env: environment,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout?.on("data", (chunk: Buffer) => { stdout += chunk.toString(); });
    child.stderr?.on("data", (chunk: Buffer) => { stderr += chunk.toString(); });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolve(stdout || stderr);
      else reject(new Error(`docker exited ${code}: ${stderr.slice(-1200)}`));
    });
  });
}

function runCompose(args: string[], environment: NodeJS.ProcessEnv): Promise<string> {
  return runDocker([...composeArgs(), ...args], environment);
}

async function hostGatewayIp(): Promise<string> {
  const address = (process.env.BLUE_HOST_GATEWAY_IP ?? await runDocker(
    ["network", "inspect", "bridge", "--format", "{{(index .IPAM.Config 0).Gateway}}"],
    process.env,
  )).trim();
  if (isIP(address) !== 4) {
    throw new Error("Cannot determine the Docker host gateway IPv4 address");
  }
  return address;
}

function formatEvent(event: SensorEvent): string {
  if (event.event_type === "http_access") {
    return `${event.seq} ${event.occurred_at ?? ""} ${event.method ?? ""} ${event.path ?? ""} ${event.status ?? ""} body=${event.request_body_status ?? "none"}`;
  }
  return `${event.seq} ${event.occurred_at ?? ""} ${event.event_type ?? "log"} ${event.message ?? ""}`;
}

const blue: Command = {
  name: "blue",
  description: "Run the always-on local Blue Cell sensor and incident monitor",
  argumentHint: "<up|status|events|incidents|metrics|analyze|stop> [local port]",
  async execute(args, context: CommandContext) {
    const [action = "status", value = ""] = args.trim().split(/\s+/);
    try {
      if (action === "up") {
        if (!value) throw new Error("Usage: /blue up 127.0.0.1:3000");
        const upstream = parseLocalUpstream(value);
        const gateway = await hostGatewayIp();
        const project = process.env.DECEPTICON_COMPOSE_PROJECT ?? "decepticon";
        context.addSystemEvent(`Starting Blue sensor for ${upstream}…`);
        await runCompose(["up", "-d", "--build"], {
          ...process.env,
          BLUE_TARGET_UPSTREAM: upstream,
          BLUE_HOST_GATEWAY_IP: gateway,
          DECEPTICON_CORE_NETWORK: `${project}_decepticon-net`,
        });
        context.addSystemEvent(`Blue Cell is monitoring ${upstream}. Send traffic to http://127.0.0.1:18080; use /blue incidents for automatic detections.`);
        return;
      }
      if (action === "stop" || action === "down") {
        await runCompose(["down"], process.env);
        context.addSystemEvent("Blue sensor stopped. Stored events and bodies remain in Docker volumes.");
        return;
      }
      if (action === "status") {
        const output = await runCompose(["ps"], process.env);
        let watch = "";
        try {
          const metrics = await queryMonitor<{ watch_enabled: boolean; watch_backlog: number; watch_failures_total: number; watch_retrying_windows: number }>("/metrics");
          watch = `\nAI watch: ${metrics.watch_enabled ? "running" : "disabled"}; backlog=${metrics.watch_backlog}; retrying=${metrics.watch_retrying_windows}; failed_windows=${metrics.watch_failures_total}`;
        } catch {
          watch = "\nAI watch: unavailable";
        }
        context.addSystemEvent((output.trim() || "Blue sensor is not running. Use /blue up 127.0.0.1:3000.") + watch);
        return;
      }
      if (action === "metrics") {
        const metrics = await query<SensorMetrics>("/metrics");
        const monitor = await queryMonitor<Record<string, number>>("/metrics");
        context.addSystemEvent(JSON.stringify({ sensor: metrics, monitor }, null, 2));
        return;
      }
      if (action === "incidents") {
        const parsed = value ? Number(value) : 20;
        if (!Number.isInteger(parsed) || parsed < 1 || parsed > 100) {
          throw new Error("Incident count must be between 1 and 100");
        }
        const page = await queryMonitor<{ incidents: BlueIncident[] }>(`/incidents?limit=${parsed}`);
        context.addSystemEvent(page.incidents.length
          ? page.incidents.map((incident) => `${incident.id} ${incident.severity} ${incident.rule_id} event=${incident.event_seq} analysis=${incident.analysis_state}`).join("\n")
          : "No Blue incidents yet. The monitor is watching the target continuously.");
        return;
      }
      if (action === "events") {
        const parsed = value ? Number(value) : 20;
        if (!Number.isInteger(parsed) || parsed < 1 || parsed > 100) {
          throw new Error("Event count must be between 1 and 100");
        }
        const metrics = await query<SensorMetrics>("/metrics");
        const after = Math.max(0, metrics.latest_seq - parsed);
        const page = await query<{ events: SensorEvent[] }>(`/events?after=${after}&limit=${parsed}`);
        context.addSystemEvent(page.events.length ? page.events.map(formatEvent).join("\n") : "No Blue events yet.");
        return;
      }
      if (action === "analyze") {
        await queryMonitor<Record<string, number>>("/metrics");
        setAssistantOverride("blue_cell");
        context.addSystemEvent("Blue Cell is investigating live target events. The automatic monitor continues independently; use /agent clear to return.");
        context.submit("Inspect the latest local target telemetry with blue_sensor_scan, investigate noteworthy events with blue_sensor_body when useful, and report evidence, uncertainty, and immediate defensive actions.");
        return;
      }
      throw new Error("Usage: /blue up 127.0.0.1:3000 | status | events [1-100] | incidents [1-100] | metrics | analyze | stop");
    } catch (error) {
      context.addSystemEvent(`Blue sensor: ${error instanceof Error ? error.message : String(error)}`);
    }
  },
};

export default blue;
