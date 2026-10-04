import { describe, expect, it } from "vitest";
import { assessBlueCoverage, parseBlueUpArgs, parseLocalUpstream } from "./blue.js";

describe("Blue local target boundary", () => {
  it.each([
    ["127.0.0.1:3000", "http://127.0.0.1:3000"],
    ["http://localhost:8080", "http://localhost:8080"],
    ["https://[::1]:8443", "https://[::1]:8443"],
  ])("accepts a local listener at %s", (input, expected) => {
    expect(parseLocalUpstream(input)).toBe(expected);
  });

  it.each([
    "https://example.com:443",
    "http://192.168.1.5:8080",
    "http://127.0.0.1:3000/path",
    "http://user:pass@localhost:3000",
    "file://localhost:3000",
    "localhost",
  ])("rejects a non-local listener or ambiguous target at %s", (input) => {
    expect(() => parseLocalUpstream(input)).toThrow();
  });
});

describe("Blue local log enrollment", () => {
  it("accepts an existing host directory argument including spaces", () => {
    expect(parseBlueUpArgs('up 127.0.0.1:3000 --logs "/srv/my app/logs"')).toEqual({
      upstream: "http://127.0.0.1:3000",
      logDir: "/srv/my app/logs",
    });
  });

  it("keeps proxy-only mode explicit", () => {
    expect(parseBlueUpArgs("up localhost:3000")).toEqual({ upstream: "http://localhost:3000" });
  });

  it.each([
    "up 127.0.0.1:3000 --logs relative/logs",
    "up 127.0.0.1:3000 --logs",
    "up https://public.example:3000 --logs /tmp/logs",
  ])("rejects invalid enrollment at %s", (input) => {
    expect(() => parseBlueUpArgs(input)).toThrow();
  });
});

describe("Blue source verification", () => {
  const metrics = {
    latest_seq: 2,
    events_total: 2,
    body_files: 0,
    body_bytes: 0,
    body_evicted_total: 0,
    events_evicted_total: 0,
    rejected_total: 0,
    collector_available: true,
    collector_start_time_seconds: 1000,
    proxy_last_received_at: new Date(1001 * 1000).toISOString(),
    target_log_last_received_at: new Date(1002 * 1000).toISOString(),
    collector_sources: {
      proxy: { records_total: 1, files_opened_total: 1, long_lines_skipped_total: 0, paused: 0 },
      target_logs: { records_total: 1, files_opened_total: 1, long_lines_skipped_total: 0, paused: 0 },
    },
  };

  it("reports records delivered during the current collector run", () => {
    const lines = assessBlueCoverage("/srv/logs", metrics);
    expect(lines[0]).toContain("HTTP record reached");
    expect(lines[1]).toContain("record reached the receiver");
  });

  it("does not treat an old retained record as current delivery", () => {
    const lines = assessBlueCoverage("/srv/logs", {
      ...metrics,
      target_log_last_received_at: new Date(999 * 1000).toISOString(),
    });
    expect(lines[1]).toContain("no record reached");
  });

  it("distinguishes a missing matching log file from a silent file", () => {
    const missing = assessBlueCoverage("/srv/logs", {
      ...metrics,
      collector_sources: {
        ...metrics.collector_sources,
        target_logs: { ...metrics.collector_sources.target_logs, files_opened_total: 0 },
      },
    });
    expect(missing[1]).toContain("no matching top-level file");
    const silent = assessBlueCoverage("/srv/logs", {
      ...metrics,
      collector_sources: {
        ...metrics.collector_sources,
        target_logs: { ...metrics.collector_sources.target_logs, records_total: 0 },
      },
    });
    expect(silent[1]).toContain("no record reached");
  });
});
