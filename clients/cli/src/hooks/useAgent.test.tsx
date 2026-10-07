// @vitest-environment jsdom
import { renderHook, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from "vitest";
import {
  createMockStream,
  createMockClient,
  createControllableStream,
  type MockClient,
  type StreamEvent,
} from "./__fixtures__/mockStream.js";

// ── Hoisted mock state ──────────────────────────────────────────────────────
// vi.hoisted() runs before module imports so the factory closure is safe to
// reference even after vitest hoists the vi.mock() calls.
const { mockState } = vi.hoisted(() => ({
  mockState: { client: null as MockClient | null },
}));

vi.mock("@langchain/langgraph-sdk", () => ({
  Client: vi.fn(() => mockState.client),
}));

vi.mock("../utils/threadStore.js", () => ({
  saveThread: vi.fn(async () => {}),
  touchThread: vi.fn(async () => {}),
  loadThreadByIndex: vi.fn(async () => null),
}));

vi.mock("../commands/modelOverride.js", () => ({
  getModelOverride: () => undefined,
}));

// ── Module-level binding (reset per test via dynamic import) ─────────────────
// useAgent reads INITIAL_ASSISTANT_ID = process.env.DECEPTICON_ASSISTANT_ID at
// module load time. Static import would capture the value before vi.stubEnv()
// runs in beforeEach. Dynamic import after stubbing gets the right default.
let useAgent: (typeof import("./useAgent.js"))["useAgent"];

// ── Common event fixtures ────────────────────────────────────────────────────
const noopValuesEvent: StreamEvent = { event: "values", data: { messages: [] } };

const engagementReadyEvent: StreamEvent = {
  event: "custom",
  data: { type: "engagement_ready" },
};

// ── Suite ────────────────────────────────────────────────────────────────────
describe("useAgent — engagement handoff lifecycle", () => {
  beforeEach(async () => {
    vi.resetModules();
    vi.useFakeTimers();
    vi.stubEnv("DECEPTICON_API_URL", "http://localhost:2024");
    vi.stubEnv("DECEPTICON_ASSISTANT_ID", "soundwave");
    vi.stubEnv("DECEPTICON_ENGAGEMENT", "eng-abc");
    vi.stubEnv("DECEPTICON_WORKSPACE_PATH", "/tmp/ws");
    vi.stubEnv("DECEPTICON_TARGET_TYPE", "web_url");
    vi.stubEnv("DECEPTICON_TARGET", "https://target.example");
    vi.stubEnv("DECEPTICON_AUTHORIZATION_CONFIRMED", "true");
    delete process.env.DECEPTICON_THREAD_ID;
    mockState.client = createMockClient();
    ({ useAgent } = await import("./useAgent.js"));
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it.each([
    [{ finish_reason: "length" }, "output token limit"],
    [{ stop_reason: "max_tokens" }, "output token limit"],
    [{ finish_reason: "content_filter" }, "content filter"],
    [{ finish_reason: "stop" }, "cause is unknown"],
    [{}, "cause is unknown"],
  ])("reports empty-response metadata %j without recommending a blind retry", async (metadata, diagnosis) => {
    (mockState.client!.runs.stream as Mock).mockReturnValueOnce(createMockStream([
      { event: "metadata", data: { run_id: "run-empty-response" } },
      { event: "values", data: { messages: [{
        type: "ai", content: "", response_metadata: metadata,
      }] } },
    ]));
    const { result } = renderHook(() => useAgent());

    act(() => result.current.submit("hello"));
    await act(async () => { await vi.runAllTimersAsync(); });

    const notices = result.current.events.filter((event) => event.type === "system");
    expect(notices).toHaveLength(1);
    expect(notices[0].content).toContain(diagnosis);
    expect(notices[0].content).toContain("run-empty-response");
    expect(notices[0].content).not.toContain("/resume");
  });

  it("reports a server error without a duplicate or lost-connection notice", async () => {
    (mockState.client!.runs.stream as Mock).mockReturnValueOnce(createMockStream([
      { event: "error", data: { message: "Model request exceeds context size" } },
    ]));
    const { result } = renderHook(() => useAgent());

    act(() => result.current.submit("hello"));
    await act(async () => { await vi.runAllTimersAsync(); });

    const notices = result.current.events.filter((event) => event.type === "system");
    expect(notices).toEqual([]);
    expect(result.current.error).toBe("Model request exceeds context size");
    expect(result.current.runState).toBe("idle");
  });

  // ── 1. engagement_ready flips assistantId mid-stream ─────────────────────
  it("flips assistantId to 'decepticon' when engagement_ready fires mid-stream", async () => {
    const stream = createControllableStream();
    (mockState.client!.runs.stream as Mock).mockReturnValueOnce(stream);

    const { result } = renderHook(() => useAgent());

    // First runs.stream call should use the INITIAL_ASSISTANT_ID = "soundwave"
    act(() => {
      result.current.submit("hello soundwave");
    });
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect((mockState.client!.runs.stream as Mock).mock.calls[0][1]).toBe("soundwave");

    // Emit engagement_ready while the stream is still open
    await act(async () => {
      await stream.emit(engagementReadyEvent);
    });

    // assistantId state should have flipped; stream has NOT ended yet
    expect(result.current.assistantId).toBe("decepticon");

    // Clean up — end stream so the hook reaches an idle state
    await act(async () => {
      stream.end();
      await vi.runAllTimersAsync();
    });
  });

  // ── 2. Handoff auto-submits enqueued message on fresh decepticon thread ───
  it("auto-submits enqueued message on a fresh decepticon thread after handoff", async () => {
    const firstStream = createControllableStream();
    const secondStream = createMockStream([noopValuesEvent]);
    const mc = mockState.client!;
    (mc.runs.stream as Mock)
      .mockReturnValueOnce(firstStream)
      .mockReturnValueOnce(secondStream);
    (mc.threads.create as Mock)
      .mockResolvedValueOnce({ thread_id: "thread-soundwave" })
      .mockResolvedValueOnce({ thread_id: "thread-decepticon" });

    const { result } = renderHook(() => useAgent());

    // Start soundwave run
    act(() => {
      result.current.submit("start engagement");
    });
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    // Queue a follow-up while the soundwave stream is still active
    act(() => {
      result.current.enqueue("queued follow-up");
    });
    expect(result.current.queuedMessage).toBe("queued follow-up");

    // Emit handoff signal, then end stream to trigger handleStreamComplete
    await act(async () => {
      await firstStream.emit(engagementReadyEvent);
    });
    await act(async () => {
      firstStream.end();
      await vi.runAllTimersAsync(); // fire setTimeout(0) auto-submit
    });

    const streamCalls = (mc.runs.stream as Mock).mock.calls;
    const createCalls = (mc.threads.create as Mock).mock.calls;

    // New thread created for the decepticon run
    expect(createCalls.length).toBe(2);

    // Second stream call targets decepticon assistant on the fresh thread
    expect(streamCalls.length).toBe(2);
    expect(streamCalls[1][0]).toBe("thread-decepticon"); // new thread, not original
    expect(streamCalls[1][1]).toBe("decepticon");

    // Second stream call's input carries the queued message
    const secondInput = streamCalls[1][2].input as { messages: Array<{ content: string }> };
    expect(secondInput.messages[0].content).toBe("queued follow-up");
  });

  // ── 3. Engagement fields travel only via config.configurable ─────────────
  it("injects engagement context via config.configurable only — never as top-level run input", async () => {
    const firstStream = createControllableStream();
    const secondStream = createMockStream([noopValuesEvent]);
    const mc = mockState.client!;
    (mc.runs.stream as Mock)
      .mockReturnValueOnce(firstStream)
      .mockReturnValueOnce(secondStream);
    (mc.threads.create as Mock)
      .mockResolvedValueOnce({ thread_id: "thread-soundwave" })
      .mockResolvedValueOnce({ thread_id: "thread-decepticon" });

    const { result } = renderHook(() => useAgent());

    act(() => {
      result.current.submit("start");
    });
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    act(() => {
      result.current.enqueue("decepticon prompt");
    });

    await act(async () => {
      await firstStream.emit(engagementReadyEvent);
    });
    await act(async () => {
      firstStream.end();
      await vi.runAllTimersAsync();
    });

    const streamCalls = (mc.runs.stream as Mock).mock.calls;
    expect(streamCalls.length).toBe(2);

    for (let i = 0; i < 2; i++) {
      const opts = streamCalls[i][2] as Record<string, unknown>;

      // Engagement fields must never appear anywhere inside `input` — checked
      // recursively via serialization to catch nested placements.
      const inputJson = JSON.stringify(opts.input);
      expect(inputJson).not.toContain("engagement_name");
      expect(inputJson).not.toContain("workspace_path");
      expect(inputJson).not.toContain("target_value");
      expect(inputJson).not.toContain("authorization_confirmed");

      // Engagement fields must be present in config.configurable for every
      // run (the env vars are set for the full test, so both submits route
      // context this way per the post-#182 architecture).
      const configurable = (opts.config as Record<string, unknown> | undefined)
        ?.configurable as Record<string, unknown> | undefined;
      expect(configurable).toMatchObject({
        engagement_name: "eng-abc",
        workspace_path: "/tmp/ws",
        target_type: "web_url",
        target_value: "https://target.example",
        authorization_confirmed: true,
      });
    }
  });

  // ── 4. No handoff — thread state preserved across two submits ────────────
  it("preserves thread across two submits when no engagement_ready fires", async () => {
    const firstStream = createMockStream([noopValuesEvent]);
    const secondStream = createMockStream([noopValuesEvent]);
    const mc = mockState.client!;
    (mc.runs.stream as Mock)
      .mockReturnValueOnce(firstStream)
      .mockReturnValueOnce(secondStream);

    const { result } = renderHook(() => useAgent());

    // First submit — stream has no handoff event
    act(() => {
      result.current.submit("first");
    });
    await act(async () => {
      await vi.runAllTimersAsync();
    });
    // Flush again — processStream → handleStreamComplete → setState chain
    await act(async () => {
      await vi.runAllTimersAsync();
    });
    expect(result.current.runState).toBe("idle");

    // Second submit — same thread should be reused
    act(() => {
      result.current.submit("second");
    });
    await act(async () => {
      await vi.runAllTimersAsync();
    });
    await act(async () => {
      await vi.runAllTimersAsync();
    });
    expect(result.current.runState).toBe("idle");

    const streamCalls = (mc.runs.stream as Mock).mock.calls;
    const createCalls = (mc.threads.create as Mock).mock.calls;

    // Only one thread was created across both runs
    expect(createCalls.length).toBe(1);

    // Both stream calls used the same threadId
    expect(streamCalls.length).toBe(2);
    expect(streamCalls[0][0]).toBe(streamCalls[1][0]);

    // assistantId never flipped
    expect(result.current.assistantId).toBe("soundwave");
  });

  it("keeps Interview on the same thread after a planning draft", async () => {
    const mc = mockState.client!;
    (mc.runs.stream as Mock)
      .mockReturnValueOnce(createMockStream([
        { event: "custom", data: { type: "planning_draft_ready" } },
      ]))
      .mockReturnValueOnce(createMockStream([noopValuesEvent]));
    const { result } = renderHook(() => useAgent());

    act(() => result.current.submit("prepare plan"));
    await act(async () => { await vi.runAllTimersAsync(); });
    act(() => result.current.submit("revise plan"));
    await act(async () => { await vi.runAllTimersAsync(); });

    const calls = (mc.runs.stream as Mock).mock.calls;
    expect(calls).toHaveLength(2);
    expect(calls[0][0]).toBe(calls[1][0]);
    expect(calls[1][1]).toBe("soundwave");
    expect(result.current.assistantId).toBe("soundwave");
    expect(result.current.events).toEqual(expect.arrayContaining([
      expect.objectContaining({
        type: "system",
        content: expect.stringContaining("Planning draft ready for review"),
      }),
    ]));
  });

  it("opens a new thread when the operator selects Red", async () => {
    const mc = mockState.client!;
    (mc.threads.create as Mock)
      .mockResolvedValueOnce({ thread_id: "interview-thread" })
      .mockResolvedValueOnce({ thread_id: "red-thread" });
    (mc.runs.stream as Mock)
      .mockReturnValueOnce(createMockStream([noopValuesEvent]))
      .mockReturnValueOnce(createMockStream([noopValuesEvent]));
    const { setAssistantOverride } = await import("../commands/assistantOverride.js");
    const { result } = renderHook(() => useAgent());

    act(() => result.current.submit("prepare plan"));
    await act(async () => { await vi.runAllTimersAsync(); });
    setAssistantOverride("decepticon");
    act(() => result.current.submit("begin approved Red work"));
    await act(async () => { await vi.runAllTimersAsync(); });

    const calls = (mc.runs.stream as Mock).mock.calls;
    expect(mc.threads.create).toHaveBeenCalledTimes(2);
    expect(calls[0][0]).not.toBe(calls[1][0]);
    expect(calls[1][1]).toBe("decepticon");
    expect(result.current.assistantId).toBe("decepticon");
  });

  it("recovers after thread creation retries are exhausted", async () => {
    const mc = mockState.client!;
    const createThread = mc.threads.create as Mock;
    for (let attempt = 0; attempt < 5; attempt++) {
      createThread.mockRejectedValueOnce(new Error("server unavailable"));
    }
    createThread.mockResolvedValueOnce({ thread_id: "thread-recovered" });
    (mc.runs.stream as Mock).mockReturnValueOnce(createMockStream([noopValuesEvent]));

    const { result } = renderHook(() => useAgent());

    act(() => {
      result.current.submit("first attempt");
    });
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(createThread).toHaveBeenCalledTimes(5);
    expect(result.current.runState).toBe("idle");
    expect(result.current.error).toBe("Connection failed: server unavailable");
    expect(result.current.events).toEqual(expect.arrayContaining([
      expect.objectContaining({
        type: "system",
        content: "Connection failed: server unavailable",
      }),
    ]));

    act(() => {
      result.current.submit("retry");
    });
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(createThread).toHaveBeenCalledTimes(6);
    expect(mc.runs.stream).toHaveBeenCalledTimes(1);
    expect(result.current.runState).toBe("idle");
  });

  // ── 5. engagement_ready alone does not clear queuedMessage ───────────────
  it("keeps queuedMessage intact after engagement_ready — only handleStreamComplete handoff branch clears it", async () => {
    const firstStream = createControllableStream();
    const secondStream = createMockStream([noopValuesEvent]);
    const mc = mockState.client!;
    (mc.runs.stream as Mock)
      .mockReturnValueOnce(firstStream)
      .mockReturnValueOnce(secondStream);
    (mc.threads.create as Mock)
      .mockResolvedValueOnce({ thread_id: "t-soundwave" })
      .mockResolvedValueOnce({ thread_id: "t-decepticon" });

    const { result } = renderHook(() => useAgent());

    act(() => {
      result.current.submit("start");
    });
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    // Queue a message while the stream is active
    act(() => {
      result.current.enqueue("queued-msg");
    });
    expect(result.current.queuedMessage).toBe("queued-msg");

    // Emit engagement_ready — keep the stream open
    await act(async () => {
      await firstStream.emit(engagementReadyEvent);
    });

    // Queue must still be intact — engagement_ready alone must not clear it
    expect(result.current.queuedMessage).toBe("queued-msg");

    // Now end the stream — handleStreamComplete handoff branch auto-submits
    await act(async () => {
      firstStream.end();
      await vi.runAllTimersAsync();
    });
    // Additional flush for the queued auto-submit to complete
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    // After the handoff auto-submit fires, queuedMessage is cleared
    expect(result.current.queuedMessage).toBeNull();
  });
});
