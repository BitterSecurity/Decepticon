// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { formatBlueNotification, useBlueNotifications } from "./useBlueNotifications.js";

describe("Blue Cell chat notifications", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("inserts detection and later agent assessment into the active chat", async () => {
    vi.useFakeTimers();
    vi.stubEnv("BLUE_MONITOR_URL", "http://blue-monitor:8085");
    const events: string[] = [];
    const fetchMock = vi.fn(async (url: string) => {
      const after = new URL(url).searchParams.get("after");
      const notifications = after === "0"
        ? [{ seq: 1, kind: "detected", payload: {
          id: "incident-1", rule_id: "path_traversal", severity: "high",
          evidence: { method: "GET", uri: "/?file=../etc/passwd", status: 404 },
        } }]
        : after === "1"
          ? [{ seq: 2, kind: "assessment", payload: {
            id: "incident-1", analysis: "Traversal attempt observed; impact not proven.",
          } }]
          : [];
      return { ok: true, json: async () => ({ notifications, has_more: false }) };
    });
    vi.stubGlobal("fetch", fetchMock);

    const { unmount } = renderHook(() => useBlueNotifications((message) => events.push(message)));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(events).toHaveLength(1);
    expect(events[0]).toContain("path_traversal");

    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(events).toHaveLength(2);
    expect(events[1]).toContain("Traversal attempt observed");

    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(events).toHaveLength(2);
    unmount();
  });

  it("shows autonomous alerts and review gaps with their evidence window", () => {
    const alert = formatBlueNotification({
      seq: 3,
      kind: "watch_alert",
      payload: {
        id: "watch-incident",
        severity: "medium",
        analysis: "Repeated unusual requests need review.",
        evidence: { event_seqs: [7, 9] },
      },
    });
    expect(alert).toContain("상주 감시 알림");
    expect(alert).toContain("근거 이벤트 7, 9");
    const gap = formatBlueNotification({
      seq: 4,
      kind: "watch_error",
      payload: { id: "watch-gap", start_seq: 10, end_seq: 12, error: "agent unavailable" },
    });
    expect(gap).toContain("10–12");
    expect(gap).toContain("agent unavailable");
  });

  it("shows collector loss as an observation gap", () => {
    const message = formatBlueNotification({
      seq: 5,
      kind: "coverage_gap",
      payload: {
        id: "coverage-1",
        reason: "collector_data_loss",
        counters: { collector_long_lines_skipped_total: 2 },
      },
    });
    expect(message).toContain("수집 공백");
    expect(message).toContain("collector_long_lines_skipped_total=2");
    const restart = formatBlueNotification({
      seq: 6,
      kind: "coverage_gap",
      payload: { id: "coverage-2", reason: "collector_restarted" },
    });
    expect(restart).toContain("재시작");
    expect(restart).toContain("연속성");
  });
});
