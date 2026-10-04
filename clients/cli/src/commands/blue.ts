import { spawn } from "node:child_process";
import { isIP } from "node:net";
import { isAbsolute } from "node:path";
import type { Command, CommandContext } from "./types.js";
import { setAssistantOverride } from "./assistantOverride.js";

interface SensorMetrics {
  latest_seq: number;
  events_total: number;
  events_ingested_total?: number;
  counters_started_at?: string;
  body_files: number;
  body_bytes: number;
  body_evicted_total: number;
  events_evicted_total: number;
  rejected_total: number;
  collector_available?: boolean;
  collector_long_lines_skipped_total?: number;
  collector_dropped_records_total?: number;
  collector_routing_dropped_records_total?: number;
  collector_retries_failed_total?: number;
  collector_paused_inputs?: number;
  target_log_events?: number;
  target_log_last_received_at?: string | null;
  proxy_events?: number;
  proxy_last_received_at?: string | null;
  collector_start_time_seconds?: number | null;
  collector_sources?: Record<string, {
    records_total: number;
    files_opened_total: number;
    long_lines_skipped_total: number;
    paused: number;
  }>;
}

interface SensorSource {
  source: string;
  sensor_file: string;
  events_retained: number;
  latest_seq: number;
  last_received_at: string;
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

export function parseBlueUpArgs(input: string): { upstream: string; logDir?: string } {
  const match = /^up\s+(\S+)(?:\s+--logs(?:\s+|=)(.+))?$/.exec(input.trim());
  if (!match) throw new Error("Usage: /blue up 127.0.0.1:3000 [--logs /absolute/host/logs]");
  const upstream = parseLocalUpstream(match[1]);
  if (!match[2]) return { upstream };
  let logDir = match[2].trim();
  if ((logDir.startsWith('"') && logDir.endsWith('"')) ||
      (logDir.startsWith("'") && logDir.endsWith("'"))) {
    logDir = logDir.slice(1, -1);
  }
  if (!isAbsolute(logDir) || logDir.includes("\0")) {
    throw new Error("--logs needs an absolute directory path on the Docker host");
  }
  return { upstream, logDir };
}

function composeArgs(logsOverlay = false): string[] {
  const file = process.env.BLUE_SENSOR_COMPOSE_FILE ?? "/app/blue-sensor/compose.runtime.yaml";
  const logsFile = process.env.BLUE_SENSOR_LOGS_COMPOSE_FILE ?? "/app/blue-sensor/compose.logs.yaml";
  const project = process.env.DECEPTICON_COMPOSE_PROJECT ?? "decepticon";
  const envFile = process.env.DECEPTICON_COMPOSE_ENV_FILE;
  return ["compose", "-f", file, ...(logsOverlay ? ["-f", logsFile] : []),
    "-p", `${project}-blue`, ...(envFile ? ["--env-file", envFile] : [])];
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

function runCompose(args: string[], environment: NodeJS.ProcessEnv, logsOverlay = false): Promise<string> {
  return runDocker([...composeArgs(logsOverlay), ...args], environment);
}

interface DockerMount {
  Type?: string;
  Source?: string;
  Destination?: string;
}

async function enrolledLogSource(): Promise<string> {
  const id = (await runCompose(["ps", "-q", "blue-collector"], process.env)).trim();
  if (!id) return "collector not running";
  const mounts = JSON.parse(await runDocker(
    ["inspect", "--format", "{{json .Mounts}}", id], process.env,
  )) as DockerMount[];
  const mount = mounts.find((item) => item.Destination === "/target-logs");
  return mount?.Type === "bind" ? mount.Source ?? "unknown" : "none (proxy only)";
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

function receivedDuringCollectorRun(receivedAt: string | null | undefined, startedAt: number | null | undefined): boolean {
  if (!receivedAt || !startedAt) return false;
  const received = Date.parse(receivedAt);
  return Number.isFinite(received) && received >= startedAt * 1000;
}

export function assessBlueCoverage(logSource: string, metrics: SensorMetrics): string[] {
  if (!metrics.collector_available) return ["Collector is unavailable; source delivery cannot be verified."];
  const proxy = metrics.collector_sources?.proxy;
  const target = metrics.collector_sources?.target_logs;
  const since = metrics.collector_start_time_seconds;
  const lines = [
    proxy && proxy.records_total > 0 && receivedDuringCollectorRun(metrics.proxy_last_received_at, since)
      ? "Proxy: an HTTP record reached the receiver during this collector run."
      : "Proxy: no HTTP record confirmed during this collector run; send a request through http://127.0.0.1:18080.",
  ];
  if (logSource === "none (proxy only)") {
    lines.push("Target logs: no host log directory enrolled.");
  } else if (logSource === "collector not running") {
    lines.push("Target logs: collector is not running.");
  } else if (!target || target.files_opened_total === 0) {
    lines.push(`Target logs: ${logSource} is mounted, but no matching top-level file has been opened.`);
  } else if (
    target.records_total === 0 ||
    !receivedDuringCollectorRun(metrics.target_log_last_received_at, since)
  ) {
    lines.push("Target logs: a file was opened, but no record reached the receiver during this collector run.");
  } else {
    lines.push("Target logs: a record reached the receiver during this collector run. Produce a new target log to confirm live emission.");
  }
  return lines;
}

const blue: Command = {
  name: "blue",
  description: "Run the always-on local Blue Cell sensor and incident monitor",
  argumentHint: "<up|status|verify|sources|events|incidents|metrics|analyze|stop> [local port] [--logs /host/path]",
  async execute(args, context: CommandContext) {
    const [action = "status", value = ""] = args.trim().split(/\s+/);
    try {
      if (action === "up") {
        const { upstream, logDir } = parseBlueUpArgs(args);
        const gateway = await hostGatewayIp();
        const project = process.env.DECEPTICON_COMPOSE_PROJECT ?? "decepticon";
        context.addSystemEvent(`Starting Blue sensor for ${upstream}…`);
        await runCompose(["up", "-d", "--build", "--wait", "--wait-timeout", "600"], {
          ...process.env,
          BLUE_TARGET_UPSTREAM: upstream,
          ...(logDir ? { BLUE_TARGET_LOG_DIR: logDir } : {}),
          BLUE_HOST_GATEWAY_IP: gateway,
          DECEPTICON_CORE_NETWORK: `${project}_decepticon-net`,
        }, Boolean(logDir));
        context.addSystemEvent(`Blue Cell is monitoring ${upstream}${logDir ? ` and logs in ${logDir}` : ""}. Send traffic to http://127.0.0.1:18080; use /blue status to inspect coverage.`);
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
        let coverage: string;
        try {
          const metrics = await queryMonitor<{ watch_enabled: boolean; watch_backlog: number; watch_failures_total: number; watch_retrying_windows: number }>("/metrics");
          watch = `\nAI watch: ${metrics.watch_enabled ? "running" : "disabled"}; backlog=${metrics.watch_backlog}; retrying=${metrics.watch_retrying_windows}; failed_windows=${metrics.watch_failures_total}`;
        } catch {
          watch = "\nAI watch: unavailable";
        }
        try {
          const [source, metrics] = await Promise.all([enrolledLogSource(), query<SensorMetrics>("/metrics")]);
          const targetCollector = metrics.collector_sources?.target_logs;
          coverage = `\nCoverage: logs=${source}; collector=${metrics.collector_available ? "ready" : "unavailable"}; ` +
            `log_files_opened=${targetCollector?.files_opened_total ?? "unknown"}; ` +
            `log_records_collected=${targetCollector?.records_total ?? "unknown"}; ` +
            `target_log_events=${metrics.target_log_events ?? 0}; ` +
            `last_target_log=${metrics.target_log_last_received_at ?? "never"}; ` +
            `proxy_events=${metrics.proxy_events ?? 0}; ` +
            `last_proxy_event=${metrics.proxy_last_received_at ?? "never"}; ` +
            `events_ingested=${metrics.events_ingested_total ?? "unknown"}; ` +
            `events_evicted=${metrics.events_evicted_total}; ` +
            `long_lines_skipped=${metrics.collector_long_lines_skipped_total ?? 0}; ` +
            `output_dropped=${metrics.collector_dropped_records_total ?? 0}; ` +
            `routing_dropped=${metrics.collector_routing_dropped_records_total ?? 0}; ` +
            `retry_failures=${metrics.collector_retries_failed_total ?? 0}; ` +
            `rejected=${metrics.rejected_total}; ` +
            `paused_inputs=${metrics.collector_paused_inputs ?? 0}`;
        } catch {
          coverage = "\nCoverage: sensor or source unavailable";
        }
        context.addSystemEvent((output.trim() || "Blue sensor is not running. Use /blue up 127.0.0.1:3000.") + watch + coverage);
        return;
      }
      if (action === "sources") {
        const parsed = value ? Number(value) : 20;
        if (!Number.isInteger(parsed) || parsed < 1 || parsed > 100) {
          throw new Error("Source count must be between 1 and 100");
        }
        const page = await query<{ sources: SensorSource[]; has_more: boolean }>(`/sources?limit=${parsed}`);
        context.addSystemEvent(page.sources.length
          ? page.sources.map((source) => `${source.source} ${source.sensor_file} events=${source.events_retained} last=${source.last_received_at} seq=${source.latest_seq}`).join("\n") + (page.has_more ? "\nMore sources exist; increase the limit." : "")
          : "No target or proxy file has delivered an event yet. Produce a real target event, then check again.");
        return;
      }
      if (action === "verify") {
        const [source, metrics] = await Promise.all([enrolledLogSource(), query<SensorMetrics>("/metrics")]);
        context.addSystemEvent(assessBlueCoverage(source, metrics).join("\n"));
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
      throw new Error("Usage: /blue up 127.0.0.1:3000 [--logs /absolute/host/logs] | status | verify | sources [1-100] | events [1-100] | incidents [1-100] | metrics | analyze | stop");
    } catch (error) {
      context.addSystemEvent(`Blue sensor: ${error instanceof Error ? error.message : String(error)}`);
    }
  },
};

export default blue;
