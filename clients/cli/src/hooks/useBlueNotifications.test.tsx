// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useBlueNotifications } from "./useBlueNotifications.js";

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
});
