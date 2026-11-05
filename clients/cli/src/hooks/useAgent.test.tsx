// @vitest-environment jsdom
import { transferableAbortController } from "node:util";
import { createServer } from "node:http";
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
  Client: vi.fn(function Client() { return mockState.client; }),
}));

vi.mock("../utils/threadStore.js", () => ({
  saveThread: vi.fn(async () => {}),
  touchThread: vi.fn(async () => {}),
  loadThreadByIndex: vi.fn(async () => null),
  listThreads: vi.fn(async () => []),
  validateThread: vi.fn(async (threadId: string) => ({ threadId, assistantId: "soundwave", engagementName: "eng-abc", workspacePath: "/tmp/ws", legacyMetadata: false })),
  staleEngagementCheckpoint: vi.fn(() => null),
}));

vi.mock("../commands/rolePurpose.js", () => ({
  rolePurpose: vi.fn(async (role: string) => role === "soundwave" ? "planning" : role === "autobot" ? "observation" : "execution"),
}));

vi.mock("../commands/planMode.js", () => ({
  assertRedApproved: vi.fn(async () => {}),
}));

// ── Module-level binding (reset per test via dynamic import) ─────────────────
// useAgent reads INITIAL_ASSISTANT_ID = process.env.DECEPTICON_ASSISTANT_ID at
// module load time. Static import would capture the value before vi.stubEnv()
// runs in beforeEach. Dynamic import after stubbing gets the right default.
let useAgent: (typeof import("./useAgent.js"))["useAgent"];

// ── Common event fixtures ────────────────────────────────────────────────────
const noopValuesEvent: StreamEvent = { event: "values", data: { messages: [] } };

const completedValuesEvent: StreamEvent = { event: "values", data: { messages: [{ type: "ai", content: "Completed" }] } };

const engagementReadyEvent: StreamEvent = {
  event: "custom",
  data: { type: "engagement_ready" },
};

// ── Suite ────────────────────────────────────────────────────────────────────
describe("useAgent — engagement handoff lifecycle", () => {
  beforeEach(async () => {
    vi.resetModules();
    vi.clearAllMocks();
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

  it("renders cumulative SDK partial messages before values and finalizes only their matching ID", async () => {
    const client = mockState.client;
    if (!client) throw new Error("Missing fixture client");
    const stream = createControllableStream();
    client.runs.stream.mockReturnValueOnce(stream);
    const { result } = renderHook(() => useAgent());
    act(() => result.current.submit("stream a response"));
    await act(async () => { await vi.runAllTimersAsync(); });
    await act(async () => { await stream.emit({ event: "messages/partial", data: [{ type: "ai", id: "live", content: "Hello" }] }); });
    expect(result.current.streamingText).toBe("Hello");
    await act(async () => { await stream.emit({ event: "messages/partial", data: [{ type: "ai", id: "live", content: "Hello world" }] }); });
    expect(result.current.streamingText).toBe("Hello world");
    await act(async () => { await stream.emit({ event: "values", data: { messages: [{ type: "ai", id: "previous", content: "Earlier" }] } }); });
    expect(result.current.streamingText).toBe("Hello world");
    await act(async () => { await stream.emit({ event: "values", data: { messages: [{ type: "ai", id: "previous", content: "Earlier" }, { type: "ai", id: "live", content: "Hello world" }] } }); });
    expect(result.current.streamingText).toBe("");
    expect(result.current.events.filter((event) => event.content === "Hello world")).toHaveLength(1);
    await act(async () => { await stream.emit({ event: "messages/complete", data: [{ type: "ai", id: "live", content: "Hello world" }] }); });
    expect(result.current.streamingText).toBe("");
    await act(async () => { stream.end(); await vi.runAllTimersAsync(); });
  });

  it("accepts single SDK tuples and ignores malformed or child-namespace chunks", async () => {
    const client = mockState.client;
    if (!client) throw new Error("Missing fixture client");
    const stream = createControllableStream();
    client.runs.stream.mockReturnValueOnce(stream);
    const { result } = renderHook(() => useAgent());
    act(() => result.current.submit("tuple response"));
    await act(async () => { await vi.runAllTimersAsync(); });
    await act(async () => { await stream.emit({ event: "messages", data: [{ type: "AIMessageChunk", id: "tuple", content: "Hi " }, {}] }); });
    await act(async () => { await stream.emit({ event: "messages", data: [{ type: "AIMessageChunk", id: "tuple", content: "there" }, {}] }); });
    expect(result.current.streamingText).toBe("Hi there");
    await act(async () => { await stream.emit({ event: "messages/partial|child", data: [{ type: "ai", id: "child", content: "Child output" }] }); });
    await act(async () => { await stream.emit({ event: "messages", data: [null, {}] }); });
    expect(result.current.streamingText).toBe("Hi there");
    expect(result.current.error).toBeNull();
    await act(async () => { await stream.emit({ event: "values", data: { messages: [{ type: "ai", id: "tuple", content: "Hi there" }] } }); });
    await act(async () => { stream.end(); await vi.runAllTimersAsync(); });
    expect(result.current.streamingText).toBe("");
  });

  it("clears anonymous partial output when its authoritative values arrive and clears paused drafts", async () => {
    const client = mockState.client;
    if (!client) throw new Error("Missing fixture client");
    const stream = createControllableStream();
    client.runs.stream.mockReturnValueOnce(stream);
    const { result } = renderHook(() => useAgent());
    act(() => result.current.submit("anonymous response"));
    await act(async () => { await vi.runAllTimersAsync(); });
    await act(async () => { await stream.emit({ event: "messages/partial", data: [{ type: "ai", content: "Draft" }] }); });
    expect(result.current.streamingText).toBe("Draft");
    await act(async () => { await stream.emit(completedValuesEvent); });
    expect(result.current.streamingText).toBe("");
    await act(async () => { await stream.emit({ event: "messages/partial", data: [{ type: "ai", id: "question", content: "Choose" }] }); });
    await act(async () => { await stream.emit({ event: "updates", data: { __interrupt__: [{ value: { type: "ask_user_question", tool_call_id: "q-live", question: "Choose", options: [{ label: "One" }] } }] } }); });
    await act(async () => { stream.end(); await vi.runAllTimersAsync(); });
    expect(result.current.streamingText).toBe("");
  });

  it("preserves active streams and queued prompts when a fresh conversation is requested", async () => {
    // Given an active run with a waiting message
    const client = mockState.client;
    if (!client) throw new Error("Missing fixture client");
    const stream = createControllableStream();
    client.runs.stream.mockReturnValueOnce(stream);
    const { result } = renderHook(() => useAgent());
    act(() => result.current.submit("active request"));
    await act(async () => { await vi.runAllTimersAsync(); });
    act(() => result.current.enqueue("waiting request"));
    const thread = result.current.threadId;
    // When /new or /clear asks to clear the conversation
    act(() => result.current.clearEvents());
    // Then the active thread and queue remain attached
    expect(result.current.threadId).toBe(thread);
    expect(result.current.runState).toBe("streaming");
    expect(result.current.queuedMessages).toEqual(["waiting request"]);
    expect(client.runs.cancel).not.toHaveBeenCalled();
    expect(result.current.events).toEqual(expect.arrayContaining([expect.objectContaining({ content: expect.stringContaining("Finish or cancel") })]));
    act(() => result.current.clearQueuedMessage());
    await act(async () => { stream.end(); await vi.runAllTimersAsync(); });
  });

  it.each(["resume", "answerQuestion"] as const)("carries runtime controls on submit and %s", async method => {
    vi.stubEnv("DECEPTICON_LLM__FALLBACK_MODELS", '["openai/fallback"]');
    vi.stubEnv("DECEPTICON_LLM__EFFORT", "low");
    vi.stubEnv("DECEPTICON_LLM__AUTOCOMPACT", "4096");
    const stream = mockState.client!.runs.stream as Mock;
    stream.mockReturnValueOnce(createMockStream([{ event: "custom", data: { type: "ask_user_question", question: "Continue?" } }]));
    const { result } = renderHook(() => useAgent());
    act(() => result.current.submit("start"));
    await act(async () => { await vi.runAllTimersAsync(); });
    act(() => result.current[method]("yes"));
    await act(async () => { await vi.runAllTimersAsync(); });
    const expected = { fallback_models: ["openai/fallback"], effort: "low", autocompact: 4096 };
    expect(stream.mock.calls[0]?.[2].input.run_controls).toEqual(expected);
    expect(stream.mock.calls[1]?.[2].command.update.run_controls).toEqual(expected);
  });

  it("displays observed model routing, provider usage and budget events", async () => {
    const stream = mockState.client!.runs.stream as Mock;
    stream.mockReturnValueOnce(createMockStream([
      { event: "custom", data: { type: "runtime_status", model: "openai/primary", effort: "low", context_tokens: 100, autocompact: 4096 } },
      { event: "custom", data: { type: "model_fallback_engaged", model: "openai/fallback", attempt: 1 } },
      { event: "custom", data: { type: "compact.end" } },
      { event: "custom", data: { type: "budget_usage", spent_usd: 0.5, cap_usd: 1, used_pct: 50 } },
      { event: "values", data: { messages: [{ type: "ai", content: "verified", response_metadata: { model_name: "served-model", token_usage: { prompt_tokens: 120, total_tokens: 125, completion_tokens: 5 } } }] } },
    ]));
    const { result } = renderHook(() => useAgent());
    act(() => result.current.submit("start"));
    await act(async () => { await vi.runAllTimersAsync(); });
    expect(result.current.runtimeStatus).toMatchObject({ route: "openai/fallback", servedModel: "served-model", contextTokens: 120, estimated: false, compaction: "checked", fallbackAttempt: 1 });
    expect(result.current.budgetUsage).toEqual({ spent: 0.5, cap: 1, pct: 50 });
  });

  it("disconnects the client stream without cancelling server work when the CLI unmounts", async () => {
    // Given an attached stream
    const client = mockState.client;
    if (!client) throw new Error("Missing fixture client");
    const stream = createControllableStream();
    client.runs.stream.mockReturnValueOnce(stream);
    const { result, unmount } = renderHook(() => useAgent());
    act(() => result.current.submit("keep server work running"));
    await act(async () => { await vi.runAllTimersAsync(); });
    // When the CLI exits and its hook is unmounted
    unmount();
    // Then the SDK signal aborts locally without a server cancellation
    expect(client.runs.stream).toHaveBeenCalledWith(expect.any(String), "soundwave", expect.objectContaining({
      onDisconnect: "continue", signal: expect.objectContaining({ aborted: true }),
    }));
    expect(client.runs.cancel).not.toHaveBeenCalled();
    stream.end();
  });

  it("starts a new server thread after clearing an idle conversation", async () => {
    // Given a completed conversation
    const client = mockState.client;
    if (!client) throw new Error("Missing fixture client");
    const { result } = renderHook(() => useAgent());
    act(() => result.current.submit("first conversation"));
    await act(async () => { await vi.runAllTimersAsync(); });
    // When the idle conversation is cleared and another message sent
    act(() => result.current.clearEvents());
    expect(result.current.threadId).toBeNull();
    expect(result.current.events).toEqual([]);
    act(() => result.current.submit("second conversation"));
    await act(async () => { await vi.runAllTimersAsync(); });
    // Then thread creation happens again without deleting the saved thread
    expect(client.threads.create).toHaveBeenCalledTimes(2);
  });

  it("starts in Red when no assistant override is configured", async () => {
    // Given a fresh CLI process with no selected assistant.
    vi.stubEnv("DECEPTICON_ASSISTANT_ID", "");
    vi.resetModules();
    const { useAgent: useDefaultAgent } = await import("./useAgent.js");
    // When the hook starts, then Red is selected without a Plan conversation.
    const { result } = renderHook(() => useDefaultAgent());
    expect(result.current.assistantId).toBe("decepticon");
  });

  it("keeps Red and Blue conversations on separate threads (events do not cross modes)", async () => {
    // Blue is a continuous-observation conversation distinct from Red; switching
    // modes must swap to a separate per-mode transcript so Blue events never
    // appear in the Red conversation and vice versa.
    const client = mockState.client;
    if (!client) throw new Error("Missing fixture client");
    const { result } = renderHook(() => useAgent());

    await act(async () => { await result.current.selectAssistant("decepticon"); });
    act(() => result.current.addSystemEvent("RED-ONLY-EVENT"));
    expect(result.current.events.some((e) => e.content === "RED-ONLY-EVENT")).toBe(true);

    // Switch to Blue: the Red-only event must not be visible.
    await act(async () => { await result.current.selectAssistant("autobot"); });
    expect(result.current.assistantId).toBe("autobot");
    expect(result.current.events.some((e) => e.content === "RED-ONLY-EVENT")).toBe(false);
    act(() => result.current.addSystemEvent("BLUE-ONLY-EVENT"));
    expect(result.current.events.some((e) => e.content === "BLUE-ONLY-EVENT")).toBe(true);

    // Back to Red: the Red transcript is restored and the Blue event is absent.
    await act(async () => { await result.current.selectAssistant("decepticon"); });
    expect(result.current.events.some((e) => e.content === "RED-ONLY-EVENT")).toBe(true);
    expect(result.current.events.some((e) => e.content === "BLUE-ONLY-EVENT")).toBe(false);
  });

  it.each([["soundwave", "resume"], ["soundwave", "answerQuestion"], ["plugin-executor", "resume"], ["plugin-executor", "answerQuestion"], ["autobot", "resume"], ["autobot", "answerQuestion"]] as const)("isolates workflow namespaces for %s on submit and %s", async (assistant, method) => {
    // Given trusted launch settings in both namespaces.
    vi.stubEnv("DECEPTICON_WORKFLOW_ID", "red-custom");
    vi.stubEnv("DECEPTICON_WORKFLOW_VERSION", "exec-v2");
    vi.stubEnv("DECEPTICON_PLANNING_WORKFLOW_ID", "plan-custom");
    vi.stubEnv("DECEPTICON_PLANNING_WORKFLOW_VERSION", "plan-v3");
    const blue = await import("../commands/blue.js");
    vi.spyOn(blue, "blueMonitorReady").mockResolvedValue(true);
    const { setAssistantOverride } = await import("../commands/assistantOverride.js");
    const { result } = renderHook(() => useAgent());
    await act(async () => { await result.current.selectAssistant(assistant); });
    setAssistantOverride(assistant);
    const stream = mockState.client!.runs.stream as Mock;
    stream.mockReturnValueOnce(createMockStream([{ event: "custom", data: { type: "ask_user_question", question: "Continue?" } }]));
    // When the run submits and answers a question.
    act(() => result.current.submit("start"));
    await act(async () => { await vi.runAllTimersAsync(); });
    act(() => result.current[method]("yes"));
    await act(async () => { await vi.runAllTimersAsync(); });
    // Then every request has only the assistant purpose's workflow namespace.
    expect(stream.mock.calls).toHaveLength(2);
    const { rolePurpose } = await import("../commands/rolePurpose.js");
    expect(rolePurpose).toHaveBeenCalledTimes(2);
    for (const call of stream.mock.calls) {
      const config = call[2].config.configurable;
      expect(config.workflow_id).toBe(assistant === "plugin-executor" ? "red-custom" : undefined);
      expect(config.workflow_version).toBe(assistant === "plugin-executor" ? "exec-v2" : undefined);
      expect(config.planning_workflow_id).toBe(assistant === "soundwave" ? "plan-custom" : undefined);
      expect(config.planning_workflow_version).toBe(assistant === "soundwave" ? "plan-v3" : undefined);
    }
  });

  it.each(["submit", "resume", "answerQuestion"] as const)("leaves a pinned planning selector unset on %s after launch settings are removed", async (method) => {
    // Given a Plan thread launched with a nondefault immutable workflow pin.
    vi.stubEnv("DECEPTICON_PLANNING_WORKFLOW_ID", "plan-custom");
    vi.stubEnv("DECEPTICON_PLANNING_WORKFLOW_VERSION", "plan-v3");
    const { result } = renderHook(() => useAgent());
    const stream = vi.mocked(mockState.client?.runs.stream);
    if (!stream) throw new Error("Missing test client");
    stream.mockReturnValueOnce(createMockStream([{ event: "custom", data: { type: "ask_user_question", question: "Continue?" } }]));
    act(() => result.current.submit("start"));
    await act(async () => { await vi.runAllTimersAsync(); });
    vi.stubEnv("DECEPTICON_PLANNING_WORKFLOW_ID", "");
    vi.stubEnv("DECEPTICON_PLANNING_WORKFLOW_VERSION", "");
    // When the operator continues without explicit workflow settings.
    act(() => result.current[method]("continue"));
    await act(async () => { await vi.runAllTimersAsync(); });
    // Then the existing thread is reused and no selector overrides its checkpoint.
    expect(stream.mock.calls).toHaveLength(2);
    expect(stream.mock.calls[1]?.[0]).toBe(stream.mock.calls[0]?.[0]);
    expect(stream.mock.calls[1]?.[2].config?.configurable).not.toHaveProperty("planning_workflow_id");
    expect(stream.mock.calls[1]?.[2].config?.configurable).not.toHaveProperty("planning_workflow_version");
  });

  it.each([["plugin-executor", "DECEPTICON_WORKFLOW"], ["soundwave", "DECEPTICON_PLANNING_WORKFLOW"]] as const)("rejects a version without its workflow ID for %s before dispatch", async (assistant, namespace) => {
    // Given a trusted launch version with no corresponding ID.
    vi.stubEnv(`${namespace}_ID`, "");
    vi.stubEnv(`${namespace}_VERSION`, "v2");
    const { setAssistantOverride } = await import("../commands/assistantOverride.js");
    setAssistantOverride(assistant);
    const { result } = renderHook(() => useAgent());
    // When submitted, then the malformed selector fails locally.
    act(() => result.current.submit("start"));
    await act(async () => { await vi.runAllTimersAsync(); });
    expect(mockState.client!.runs.stream).not.toHaveBeenCalled();
    expect(result.current.error).toContain(`requires ${namespace}_ID`);
  });

  it("requires scope approval for a plugin execution assistant", async () => {
    // Given a plugin assistant and an unapproved workspace.
    const { assertRedApproved } = await import("../commands/planMode.js");
    vi.mocked(assertRedApproved).mockRejectedValueOnce(new Error("scope required"));
    const { setAssistantOverride } = await import("../commands/assistantOverride.js");
    setAssistantOverride("plugin-executor");
    const { result } = renderHook(() => useAgent());
    // When execution is submitted.
    act(() => result.current.submit("start"));
    await act(async () => { await vi.runAllTimersAsync(); });
    // Then the run is blocked before streaming.
    expect(mockState.client!.runs.stream).not.toHaveBeenCalled();
    expect(result.current.error).toContain("scope required");
  });

  it("keeps assistant and thread when a paused transition commit fails", async () => {
    // Given an existing Plan thread.
    const { result } = renderHook(() => useAgent());
    act(() => result.current.submit("start"));
    await act(async () => { await vi.runAllTimersAsync(); });
    const thread = (mockState.client!.runs.stream as Mock).mock.calls[0][0];
    // When scope commit fails after pausing.
    await act(async () => { expect(await result.current.selectAssistant("decepticon", async () => { throw new Error("scope changed"); })).toBe(false); });
    // Then the previous assistant and thread remain selected.
    expect(result.current.assistantId).toBe("soundwave");
    act(() => result.current.submit("continue"));
    await act(async () => { await vi.runAllTimersAsync(); });
    expect((mockState.client!.runs.stream as Mock).mock.calls.at(-1)?.[0]).toBe(thread);
  });

  it("restores the validated latest session transcript before allowing input", async () => {
    // Given explicit continue and a persisted assistant-specific conversation.
    vi.stubEnv("DECEPTICON_RESUME_LATEST", "true");
    const store = await import("../utils/threadStore.js");
    vi.mocked(store.loadThreadByIndex).mockResolvedValueOnce({ threadId: "saved", assistantId: "soundwave", lastUsed: "today", title: "saved", legacyMetadata: false });
    const client = mockState.client;
    if (!client) throw new TypeError("Missing fixture");
    client.threads.getState.mockResolvedValueOnce({ values: { messages: [{ type: "human", content: "previous request" }, { type: "human", content: "internal runtime directive", additional_kwargs: { decepticon_internal: true } }, { type: "ai", content: "previous answer" }] }, tasks: [{ interrupts: [{ value: { type: "ask_user_question", id: "restore-q", question: "Continue?" } }] }] });
    // When the hook initializes.
    const { result } = renderHook(() => useAgent());
    await act(async () => { await vi.runAllTimersAsync(); });
    // Then the original transcript and validated thread are available for export and continued input.
    expect(result.current.threadId).toBe("saved");
    expect(result.current.activeQuestion?.sourceId).toBe("restore-q");
    expect(result.current.runState).toBe("paused");
    expect(result.current.events.map(event => event.content)).toEqual(["previous request", "previous answer"]);
  });

  it("delivers multiple queued messages in FIFO order after editing and removal", async () => {
    // Given a running stream and three queued messages.
    const stream = createControllableStream();
    const client = mockState.client;
    if (!client) throw new TypeError("Missing fixture");
    let completedRuns = 1;
    client.runs.stream.mockReturnValueOnce(stream).mockImplementation(() => createMockStream([{ event: "values", data: { messages: Array.from({ length: ++completedRuns }, (_, index) => ({ type: "ai", content: `Completed ${index}` })) } }]));
    const { result } = renderHook(() => useAgent());
    act(() => result.current.submit("initial"));
    await act(async () => { await vi.runAllTimersAsync(); });
    act(() => { result.current.enqueue("first"); result.current.enqueue("remove"); result.current.enqueue("third"); result.current.removeQueuedMessage(1); result.current.editQueuedMessage(1, "edited"); });
    expect(result.current.queuedMessages).toEqual(["first", "edited"]);
    // When the active run completes.
    await act(async () => { await stream.emit(completedValuesEvent); stream.end(); await vi.runAllTimersAsync(); });
    // Then each remaining item is delivered once in order.
    expect(client.runs.stream.mock.calls.map(call => call[2].input.messages[0].content)).toEqual(["initial", "first", "edited"]);
    expect(result.current.queuedMessages).toEqual([]);
  });

  it("sends the selected model through graph input on set, repeat, change and clear", async () => {
    const { setModelOverride } = await import("../commands/modelOverride.js");
    const { result } = renderHook(() => useAgent());
    const selections = ["auth/gpt-6-luna", "auth/gpt-6-luna", "auth/gpt-6.1-sol", ""];
    for (const selected of selections) {
      setModelOverride(selected);
      act(() => result.current.submit("hello"));
      await act(async () => { await vi.runAllTimersAsync(); });
    }
    const requests = (mockState.client!.runs.stream as Mock).mock.calls.map((call) => call[2]);
    expect(requests.map((request) => request.input.model_override)).toEqual(selections);
    expect(requests.every((request) => request.context === undefined)).toBe(true);
    expect(requests.every((request) => request.config?.configurable.model_override === undefined)).toBe(true);
  });

  it.each(["resume", "answerQuestion"] as const)(
    "%s updates the checkpoint with the current model, including clear", async (method) => {
      const { setModelOverride } = await import("../commands/modelOverride.js");
      const stream = mockState.client!.runs.stream as Mock;
      const { result } = renderHook(() => useAgent());
      for (const selected of ["auth/gpt-6.1-sol", ""]) {
        const pausedStream = createControllableStream();
        stream.mockReturnValueOnce(pausedStream);
        act(() => result.current.submit("hello"));
        await act(async () => { await vi.runAllTimersAsync(); });
        await act(async () => {
          await pausedStream.emit({
            event: "custom", data: { type: "ask_user_question", question: "Continue?" },
          });
        });
        await act(async () => { pausedStream.end(); await vi.runAllTimersAsync(); });
        expect(result.current.runState).toBe("paused");
        setModelOverride(selected);
        act(() => result.current[method]("yes"));
        await act(async () => { await vi.runAllTimersAsync(); });
        expect(stream.mock.calls.at(-1)?.[2].command).toEqual({
          resume: "yes", update: { model_override: selected },
        });
      }
    },
  );

  it.each(["resume", "answerQuestion"] as const)(
    "%s carries engagement context in config.configurable, same as the initial submit (#973)", async (method) => {
      const stream = mockState.client!.runs.stream as Mock;
      const { result } = renderHook(() => useAgent());

      const pausedStream = createControllableStream();
      stream.mockReturnValueOnce(pausedStream);
      act(() => result.current.submit("hello"));
      await act(async () => { await vi.runAllTimersAsync(); });
      await act(async () => {
        await pausedStream.emit({
          event: "custom", data: { type: "ask_user_question", question: "Continue?" },
        });
      });
      await act(async () => { pausedStream.end(); await vi.runAllTimersAsync(); });
      expect(result.current.runState).toBe("paused");

      act(() => result.current[method]("yes"));
      await act(async () => { await vi.runAllTimersAsync(); });

      const resumeCall = stream.mock.calls.at(-1)?.[2] as Record<string, unknown>;
      const configurable = (resumeCall.config as Record<string, unknown> | undefined)
        ?.configurable as Record<string, unknown> | undefined;
      expect(configurable).toMatchObject({
        engagement_name: "eng-abc",
        workspace_path: "/tmp/ws",
        target_type: "web_url",
        target_value: "https://target.example",
        authorization_confirmed: true,
      });
    },
  );

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

  it.each(["", "BENIGN_QA_OK"])("reports invalid tool calls when visible content is %j", async (content) => {
    const client = createMockClient();
    mockState.client = client;
    client.runs.stream.mockReturnValueOnce(createMockStream([
      { event: "metadata", data: { run_id: "run-invalid-tool" } },
      { event: "values", data: { messages: [{
        type: "ai", content,
        invalid_tool_calls: [{ name: "format_note", args: "not-json", error: "Invalid JSON" }],
      }] } },
    ]));
    const { result } = renderHook(() => useAgent());

    act(() => result.current.submit("hello"));
    await act(async () => { await vi.runAllTimersAsync(); });

    const notices = result.current.events.filter((event) => event.type === "system");
    expect(notices).toHaveLength(1);
    expect(notices[0]?.content).toContain("invalid tool call");
    expect(notices[0]?.content).toContain("run-invalid-tool");
  });

  it("reports a failed history lookup without claiming the session was loaded", async () => {
    const client = mockState.client;
    if (!client) throw new TypeError("Expected an initialized test client");
    client.threads.getState.mockRejectedValueOnce(new TypeError("Session not found"));
    const { result } = renderHook(() => useAgent());

    act(() => result.current.resume("missing-thread"));
    await act(async () => { await vi.runAllTimersAsync(); });

    const notices = result.current.events.filter((event) => event.type === "system");
    expect(notices.at(-1)?.content).toBe(
      "Could not restore this session: Session not found",
    );
  });

  it("keeps Plan active when a legacy engagement_ready event arrives", async () => {
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

    expect(result.current.assistantId).toBe("soundwave");
    expect(result.current.events.at(-1)?.content).toContain("/mode red");

    // Clean up — end stream so the hook reaches an idle state
    await act(async () => {
      stream.end();
      await vi.runAllTimersAsync();
    });
  });

  it("keeps a queued Plan message in Plan after the legacy completion event", async () => {
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
      result.current.submit("start engagement");
    });
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    act(() => {
      result.current.enqueue("queued follow-up");
    });
    expect(result.current.queuedMessage).toBe("queued follow-up");

    await act(async () => {
      await firstStream.emit(engagementReadyEvent);
    });
    await act(async () => {
      await firstStream.emit(completedValuesEvent);
      firstStream.end();
      await vi.runAllTimersAsync(); // fire setTimeout(0) auto-submit
    });

    const streamCalls = (mc.runs.stream as Mock).mock.calls;
    const createCalls = (mc.threads.create as Mock).mock.calls;

    expect(createCalls.length).toBe(1);
    expect(streamCalls.length).toBe(2);
    expect(streamCalls[1][0]).toBe("thread-soundwave");
    expect(streamCalls[1][1]).toBe("soundwave");

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
      await firstStream.emit(completedValuesEvent);
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

  it("keeps separate Plan and Red threads and restores the Plan transcript", async () => {
    const mc = mockState.client!;
    (mc.threads.create as Mock)
      .mockResolvedValueOnce({ thread_id: "interview-thread" })
      .mockResolvedValueOnce({ thread_id: "red-thread" });
    (mc.runs.stream as Mock)
      .mockReturnValueOnce(createMockStream([noopValuesEvent]))
      .mockReturnValueOnce(createMockStream([noopValuesEvent]))
      .mockReturnValueOnce(createMockStream([noopValuesEvent]));
    const { setAssistantOverride } = await import("../commands/assistantOverride.js");
    const { result } = renderHook(() => useAgent());

    act(() => result.current.submit("prepare plan"));
    await act(async () => { await vi.runAllTimersAsync(); });
    await act(async () => expect(await result.current.selectAssistant("decepticon")).toBe(true));
    setAssistantOverride("decepticon");
    act(() => result.current.submit("begin approved Red work"));
    await act(async () => { await vi.runAllTimersAsync(); });

    const calls = (mc.runs.stream as Mock).mock.calls;
    expect(mc.threads.create).toHaveBeenCalledTimes(2);
    expect(calls[0][0]).not.toBe(calls[1][0]);
    expect(calls[1][1]).toBe("decepticon");
    expect(result.current.assistantId).toBe("decepticon");
    expect(result.current.events.some((event) => event.content === "prepare plan")).toBe(false);

    await act(async () => expect(await result.current.selectAssistant("soundwave")).toBe(true));
    setAssistantOverride("soundwave");
    expect(result.current.events.some((event) => event.content === "prepare plan")).toBe(true);
    expect(result.current.events.some((event) => event.content === "begin approved Red work")).toBe(false);
    act(() => result.current.submit("revise plan"));
    await act(async () => { await vi.runAllTimersAsync(); });
    expect(mc.threads.create).toHaveBeenCalledTimes(2);
    expect((mc.runs.stream as Mock).mock.calls[2][0]).toBe("interview-thread");
  });

  it("does not stream a Red message after scope approval becomes stale", async () => {
    const { assertRedApproved } = await import("../commands/planMode.js");
    vi.mocked(assertRedApproved).mockRejectedValueOnce(new Error("Red scope changed"));
    const { result } = renderHook(() => useAgent());

    await act(async () => expect(await result.current.selectAssistant("decepticon")).toBe(true));
    act(() => result.current.submit("continue Red work"));
    await act(async () => { await vi.runAllTimersAsync(); });

    expect(mockState.client!.runs.stream).not.toHaveBeenCalled();
    expect(result.current.error).toContain("Red scope changed");
    expect(result.current.runState).toBe("idle");
  });

  it("waits for server interruption and restores the paused mode and queue", async () => {
    const stream = createControllableStream();
    (mockState.client!.runs.stream as Mock).mockReturnValueOnce(stream);
    let acknowledge = () => {};
    (mockState.client!.runs.cancel as Mock).mockImplementationOnce(() => new Promise<void>((resolve) => { acknowledge = resolve; }));
    const { result } = renderHook(() => useAgent());
    act(() => result.current.submit("plan"));
    await act(async () => { await vi.runAllTimersAsync(); await stream.emit({ event: "metadata", data: { run_id: "plan-run" } }); });
    act(() => result.current.enqueue("queued plan"));
    let switching: Promise<boolean> | boolean = false;
    act(() => { switching = result.current.selectAssistant("autobot"); });
    await act(async () => { await Promise.resolve(); });
    expect(result.current.assistantId).toBe("soundwave");
    await act(async () => { acknowledge(); await switching; });
    expect(result.current.assistantId).toBe("autobot");
    expect(mockState.client!.runs.cancel).toHaveBeenCalledWith(expect.any(String), "plan-run", true, "interrupt", expect.objectContaining({ signal: expect.any(AbortSignal) }));
    await act(async () => { await stream.emit({ event: "values", data: { messages: [{ type: "ai", content: "late plan" }] } }); stream.end(); });
    expect(result.current.events.some((event) => event.content === "late plan")).toBe(false);
    await act(async () => { await result.current.selectAssistant("soundwave"); });
    expect(result.current.runState).toBe("paused");
    expect(result.current.queuedMessage).toBe("queued plan");
  });

  it.each(["autobot", "soundwave"])("keeps the active mode and never commits %s when server interruption fails", async (next) => {
    const stream = createControllableStream();
    (mockState.client!.runs.stream as Mock).mockReturnValueOnce(stream);
    (mockState.client!.runs.cancel as Mock).mockRejectedValueOnce(new Error("pause unavailable"));
    const { result } = renderHook(() => useAgent());
    act(() => result.current.submit("plan"));
    await act(async () => { await vi.runAllTimersAsync(); await stream.emit({ event: "metadata", data: { run_id: "plan-run" } }); });
    const commit = vi.fn(async () => {});
    await act(async () => { expect(await result.current.selectAssistant(next, commit)).toBe(false); });
    expect(commit).not.toHaveBeenCalled();
    expect(result.current.error).toContain("pause unavailable");
    expect(result.current.assistantId).toBe("soundwave");
    expect(result.current.runState).toBe("streaming");
    await act(async () => { stream.end(); });
  });

  it("keeps the original paused session recoverable when a mode lookup fails", async () => {
    const stream = createControllableStream();
    const client = mockState.client;
    if (!client) throw new TypeError("Missing client fixture");
    client.runs.stream.mockReturnValueOnce(stream);
    const { listThreads } = await import("../utils/threadStore.js");
    const { result } = renderHook(() => useAgent({ resumeThread: true }));
    await act(async () => { await vi.runAllTimersAsync(); });
    act(() => result.current.submit("original"));
    await act(async () => { await vi.runAllTimersAsync(); await stream.emit({ event: "metadata", data: { run_id: "active-run" } }); });
    vi.mocked(listThreads).mockRejectedValueOnce(new Error("lookup offline"));
    await act(async () => { expect(await result.current.selectAssistant("decepticon")).toBe(false); });
    expect(result.current.assistantId).toBe("soundwave");
    expect(result.current.runState).toBe("paused");
    act(() => result.current.resume());
    await act(async () => { await vi.runAllTimersAsync(); });
    expect(client.runs.stream).toHaveBeenCalledTimes(2);
    await act(async () => { stream.end(); });
  });

  it("refuses restoring another session while undelivered messages remain", async () => {
    const stream = createControllableStream();
    const client = mockState.client;
    if (!client) throw new TypeError("Missing client fixture");
    client.runs.stream.mockReturnValueOnce(stream);
    const { result } = renderHook(() => useAgent());
    act(() => result.current.submit("original"));
    await act(async () => { await vi.runAllTimersAsync(); });
    act(() => result.current.enqueue("belongs to original"));
    await act(async () => { await stream.emit({ event: "error", data: { message: "offline" } }); stream.end(); await vi.runAllTimersAsync(); });
    const original = result.current.threadId;
    const { validateThread } = await import("../utils/threadStore.js");
    vi.mocked(validateThread).mockClear();
    act(() => result.current.resume("other-thread"));
    await act(async () => { await vi.runAllTimersAsync(); });
    expect(validateThread).not.toHaveBeenCalled();
    expect(result.current.threadId).toBe(original);
    expect(result.current.queuedMessages).toEqual(["belongs to original"]);
    expect(result.current.events.at(-1)?.content).toContain("/queue");
  });

  it("isolates a late thread creation after switching while connecting", async () => {
    let finishCreate = (_thread: { thread_id: string }) => {};
    (mockState.client!.threads.create as Mock).mockImplementationOnce(() => new Promise((resolve) => { finishCreate = resolve; }));
    const { result } = renderHook(() => useAgent());
    await act(async () => { result.current.submit("plan"); });
    await act(async () => { await result.current.selectAssistant("decepticon"); });
    await act(async () => { finishCreate({ thread_id: "old-thread" }); await vi.runAllTimersAsync(); });
    act(() => result.current.submit("red"));
    await act(async () => { await vi.runAllTimersAsync(); });
    expect(mockState.client!.threads.create).toHaveBeenCalledTimes(2);
    expect((mockState.client!.runs.stream as Mock).mock.calls[0][0]).not.toBe("old-thread");
  });

  it("waits for metadata before interrupting a dispatched run and rejects a competing switch", async () => {
    const stream = createControllableStream();
    (mockState.client!.runs.stream as Mock).mockReturnValueOnce(stream);
    const { result } = renderHook(() => useAgent());
    act(() => result.current.submit("plan"));
    await act(async () => { await vi.runAllTimersAsync(); });
    let switching: Promise<boolean> | boolean = false;
    act(() => { switching = result.current.selectAssistant("autobot"); });
    await act(async () => { expect(await result.current.selectAssistant("decepticon")).toBe(false); });
    expect(mockState.client!.runs.cancel).not.toHaveBeenCalled();
    await act(async () => { await stream.emit({ event: "metadata", data: { run_id: "late-run" } }); await switching; });
    expect(mockState.client!.runs.cancel).toHaveBeenCalledWith(expect.any(String), "late-run", true, "interrupt", expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(result.current.assistantId).toBe("autobot");
    await act(async () => stream.end());
  });

  it("shares one ten-second deadline between metadata and server cancellation", async () => {
    const stream = createControllableStream();
    (mockState.client!.runs.stream as Mock).mockReturnValueOnce(stream);
    let cancellationSignal: AbortSignal | undefined;
    (mockState.client!.runs.cancel as Mock).mockImplementation((_thread: string, _run: string, _wait: boolean, _action: string, options: { signal: AbortSignal }) => {
      cancellationSignal = options.signal;
      return new Promise<void>(() => {});
    });
    const { result } = renderHook(() => useAgent());
    await act(async () => { result.current.submit("plan"); });
    let outcome: boolean | undefined;
    act(() => { void result.current.selectAssistant("autobot").then((selected) => { outcome = selected; }); });
    try {
      await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
      await act(async () => { await stream.emit({ event: "metadata", data: { run_id: "delayed-run" } }); });
      expect(cancellationSignal?.aborted).toBe(false);
      await act(async () => { await vi.advanceTimersByTimeAsync(3999); });
      expect(outcome).toBeUndefined();
      await act(async () => { await vi.advanceTimersByTimeAsync(1); });
      expect(outcome).toBe(false);
      expect(cancellationSignal?.aborted).toBe(true);
      expect(result.current.assistantId).toBe("soundwave");
      expect(result.current.runState).toBe("streaming");
      expect(result.current.error).not.toBeNull();
    } finally {
      await act(async () => stream.end());
    }
  });

  it("restores a pending question after visiting another mode", async () => {
    (mockState.client!.runs.stream as Mock).mockReturnValueOnce(createMockStream([
      { event: "metadata", data: { run_id: "question-run" } },
      { event: "custom", data: { type: "ask_user_question", question: "Scope?", tool_call_id: "scope-question" } },
    ]));
    const { result } = renderHook(() => useAgent());
    act(() => result.current.submit("plan"));
    await act(async () => { await vi.runAllTimersAsync(); });
    const question = result.current.activeQuestion;
    expect(question).not.toBeNull();
    await act(async () => { await result.current.selectAssistant("autobot"); });
    expect(result.current.activeQuestion).toBeNull();
    await act(async () => { await result.current.selectAssistant("soundwave"); });
    expect(result.current.activeQuestion).toEqual(question);
    expect(result.current.runState).toBe("paused");
    expect(mockState.client!.runs.cancel).not.toHaveBeenCalled();
  });

  it("bounds SDK retry backoff when the cancellation endpoint keeps failing", async () => {
    vi.useRealTimers();
    const { Client } = await vi.importActual<typeof import("@langchain/langgraph-sdk")>("@langchain/langgraph-sdk");
    let requests = 0;
    const server = createServer((_request, response) => { requests++; response.writeHead(503).end("pause unavailable"); });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    if (!address || typeof address === "string") throw new TypeError("Expected loopback listener");
    const client = new Client({ apiUrl: `http://127.0.0.1:${address.port}` });
    let cancellationSignal: AbortSignal | undefined;
    (mockState.client!.runs.cancel as Mock).mockImplementation((thread: string, run: string, wait: boolean, action: "interrupt", options: { signal: AbortSignal }) => {
      cancellationSignal = options.signal;
      // Node fetch requires its own AbortSignal rather than jsdom's signal.
      const controller = transferableAbortController();
      const abort = () => controller.abort(options.signal.reason);
      options.signal.addEventListener("abort", abort, { once: true });
      return client.runs.cancel(thread, run, wait, action, { signal: controller.signal })
        .finally(() => options.signal.removeEventListener("abort", abort));
    });
    const stream = createControllableStream();
    (mockState.client!.runs.stream as Mock).mockReturnValueOnce(stream);
    const { result } = renderHook(() => useAgent());
    try {
      await act(async () => { result.current.submit("plan"); });
      await act(async () => { await stream.emit({ event: "metadata", data: { run_id: "retry-run" } }); });
      let settled = false;
      let deadline: ReturnType<typeof setTimeout> | undefined;
      await act(async () => {
        const switching = result.current.selectAssistant("autobot").then(() => { settled = true; });
        await Promise.race([switching, new Promise<void>((resolve) => { deadline = setTimeout(resolve, 11_000); })]);
        clearTimeout(deadline);
      });
      expect(requests, result.current.error ?? "no hook error").toBeGreaterThan(0);
      expect(settled).toBe(true);
      expect(cancellationSignal?.aborted).toBe(true);
      expect(result.current.assistantId).toBe("soundwave");
      expect(result.current.runState).toBe("streaming");
      expect(result.current.error).not.toBeNull();
    } finally {
      await act(async () => stream.end());
      server.closeAllConnections();
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
  }, 20_000);

  it.each(["resume", "answerQuestion"] as const)("%s rechecks scope and preserves the Red question when approval became stale", async (method) => {
    const { assertRedApproved } = await import("../commands/planMode.js");
    (mockState.client!.runs.stream as Mock).mockReturnValueOnce(createMockStream([
      { event: "metadata", data: { run_id: "red-question-run" } },
      { event: "custom", data: { type: "ask_user_question", question: "Continue?", id: "red-question" } },
    ]));
    const { result } = renderHook(() => useAgent());
    await act(async () => { await result.current.selectAssistant("decepticon"); });
    act(() => result.current.submit("approved Red"));
    await act(async () => { await vi.runAllTimersAsync(); });
    const question = result.current.activeQuestion;
    await act(async () => { await result.current.selectAssistant("soundwave"); });
    vi.mocked(assertRedApproved).mockRejectedValueOnce(new Error("Red scope changed"));
    await act(async () => { await result.current.selectAssistant("decepticon"); });
    act(() => result.current[method]("yes"));
    await act(async () => { await vi.runAllTimersAsync(); });
    expect(mockState.client!.runs.stream).toHaveBeenCalledTimes(1);
    expect(result.current.error).toContain("Red scope changed");
    expect(result.current.activeQuestion).toEqual(question);
    expect(result.current.runState).toBe("paused");
  });

  it.each(["resume", "answerQuestion"] as const)("%s preserves the Blue question when the sensor disconnects", async (method) => {
    const blue = await import("../commands/blue.js");
    const readiness = vi.spyOn(blue, "blueMonitorReady").mockResolvedValueOnce(true);
    (mockState.client!.runs.stream as Mock).mockReturnValueOnce(createMockStream([
      { event: "metadata", data: { run_id: "blue-question-run" } },
      { event: "custom", data: { type: "ask_user_question", question: "Inspect?", id: "blue-question" } },
    ]));
    const { result } = renderHook(() => useAgent());
    await act(async () => { await result.current.selectAssistant("autobot"); });
    act(() => result.current.submit("inspect telemetry"));
    await act(async () => { await vi.runAllTimersAsync(); });
    const question = result.current.activeQuestion;
    readiness.mockResolvedValueOnce(false);
    act(() => result.current[method]("yes"));
    await act(async () => { await vi.runAllTimersAsync(); });
    expect(mockState.client!.runs.stream).toHaveBeenCalledTimes(1);
    expect(result.current.error).not.toBeNull();
    expect(result.current.activeQuestion).toEqual(question);
    expect(result.current.runState).toBe("paused");
  });

  it("switches during resume preparation without cancelling the old paused run", async () => {
    (mockState.client!.runs.stream as Mock).mockReturnValueOnce(createMockStream([
      { event: "metadata", data: { run_id: "paused-run" } },
      { event: "custom", data: { type: "ask_user_question", question: "Continue?", id: "paused-question" } },
    ]));
    const { result } = renderHook(() => useAgent());
    act(() => result.current.submit("plan"));
    await act(async () => { await vi.runAllTimersAsync(); });
    let finishState = (_state: { values: object }) => {};
    (mockState.client!.threads.getState as Mock).mockImplementationOnce(() => new Promise((resolve) => { finishState = resolve; }));
    await act(async () => { result.current.resume("yes"); });
    await act(async () => { await result.current.selectAssistant("autobot"); });
    expect(mockState.client!.runs.cancel).not.toHaveBeenCalled();
    await act(async () => { finishState({ values: {} }); });
    expect(result.current.assistantId).toBe("autobot");
    expect(mockState.client!.runs.stream).toHaveBeenCalledTimes(1);
    await act(async () => { await result.current.selectAssistant("soundwave"); });
    expect(result.current.runState).toBe("paused");
    expect(result.current.activeQuestion?.sourceId).toBe("paused-question");
  });

  it("retains all waiting messages without auto-retrying them after thread creation fails", async () => {
    const client = mockState.client;
    if (!client) throw new TypeError("Missing client fixture");
    client.threads.create.mockRejectedValue(new Error("server unavailable"));
    const { result } = renderHook(() => useAgent());
    act(() => result.current.submit("initial"));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    act(() => { result.current.enqueue("queued one"); result.current.enqueue("queued two"); });
    await act(async () => { await vi.runAllTimersAsync(); });
    expect(result.current.queuedMessages).toEqual(["queued one", "queued two"]);
    expect(client.threads.create).toHaveBeenCalledTimes(5);
    expect(client.runs.stream).not.toHaveBeenCalled();
    client.threads.create.mockResolvedValue({ thread_id: "recovered" });
    let completedRuns = 0;
    client.runs.stream.mockImplementation(() => createMockStream([{
      event: "values", data: { messages: Array.from({ length: ++completedRuns }, () => ({ type: "ai", content: "Recovered" })) },
    }]));
    act(() => result.current.submit("retry initial"));
    await act(async () => { await vi.runAllTimersAsync(); });
    expect(client.runs.stream.mock.calls.map(call => call[2].input.messages[0].content)).toEqual(["retry initial", "queued one", "queued two"]);
    expect(result.current.queuedMessages).toEqual([]);
  });

  it("retains waiting messages when the server reports a streaming error", async () => {
    const client = mockState.client;
    if (!client) throw new TypeError("Missing client fixture");
    const stream = createControllableStream();
    client.runs.stream.mockReturnValueOnce(stream);
    const { result } = renderHook(() => useAgent());
    act(() => result.current.submit("initial"));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    act(() => result.current.enqueue("waiting"));
    await act(async () => { await stream.emit({ event: "error", data: { message: "provider unavailable" } }); stream.end(); await vi.runAllTimersAsync(); });
    expect(result.current.queuedMessages).toEqual(["waiting"]);
    expect(client.runs.stream).toHaveBeenCalledTimes(1);
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

  it("routes the next prompt to the restored session assistant instead of the previous mode override", async () => {
    const { setAssistantOverride } = await import("../commands/assistantOverride.js");
    setAssistantOverride("decepticon");
    const { result } = renderHook(() => useAgent());
    act(() => result.current.resume("plan-thread"));
    await act(async () => { await vi.runAllTimersAsync(); });
    act(() => result.current.submit("continue restored work"));
    await act(async () => { await vi.runAllTimersAsync(); });
    expect(mockState.client?.runs.stream).toHaveBeenCalledWith("plan-thread", "soundwave", expect.anything());
  });

  it("keeps a restored web namespace with its thread across modes and resets it for a new session", async () => {
    const { validateThread } = await import("../utils/threadStore.js");
    vi.mocked(validateThread).mockResolvedValueOnce({ threadId: "old-web", assistantId: "soundwave", engagementName: "eng-abc", workspacePath: "/workspace/eng-abc", legacyMetadata: false });
    vi.stubEnv("DECEPTICON_RUNTIME_WORKSPACE_PATH", "/workspace");
    const mc = mockState.client!;
    mc.runs.stream.mockImplementation(() => createMockStream([noopValuesEvent]));
    const { result } = renderHook(() => useAgent());
    act(() => result.current.resume("old-web"));
    await act(async () => { await vi.runAllTimersAsync(); });
    act(() => result.current.submit("continue old session"));
    await act(async () => { await vi.runAllTimersAsync(); });
    await act(async () => { await result.current.selectAssistant("decepticon"); });
    act(() => result.current.submit("new Red session"));
    await act(async () => { await vi.runAllTimersAsync(); });
    await act(async () => { await result.current.selectAssistant("soundwave"); });
    act(() => result.current.submit("return to old session"));
    await act(async () => { await vi.runAllTimersAsync(); });
    act(() => result.current.clearEvents());
    act(() => result.current.submit("new Plan session"));
    await act(async () => { await vi.runAllTimersAsync(); });
    expect(mc.runs.stream.mock.calls.map(call => (call[2].config as { configurable: { workspace_path: string } }).configurable.workspace_path)).toEqual(["/workspace/eng-abc", "/workspace", "/workspace/eng-abc", "/workspace"]);
    expect(mc.runs.stream.mock.calls[0][0]).toBe("old-web");
    expect(mc.runs.stream.mock.calls[2][0]).toBe("old-web");
  });

  it("keeps queuedMessage intact after a legacy engagement_ready event", async () => {
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
      await firstStream.emit(completedValuesEvent);
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
  it("restores a chosen saved conversation from pause without sending the thread ID to the model", async () => {
    const client = mockState.client!;
    client.runs.stream.mockReturnValueOnce(createMockStream([
      { event: "metadata", data: { run_id: "old-run" } },
      { event: "custom", data: { type: "ask_user_question", question: "Continue?", id: "pending" } },
    ]));
    const { result } = renderHook(() => useAgent());
    act(() => result.current.submit("original"));
    await act(async () => { await vi.runAllTimersAsync(); });
    expect(result.current.runState).toBe("paused");
    (client.threads.getState as Mock).mockResolvedValueOnce({ values: { messages: [{ type: "ai", content: "Saved answer" }] }, next: [] });
    act(() => result.current.resume("chosen-thread", true));
    await act(async () => { await vi.runAllTimersAsync(); });
    expect(client.runs.stream).toHaveBeenCalledTimes(1);
    expect(result.current.threadId).toBe("chosen-thread");
    expect(result.current.runState).toBe("idle");
    expect(result.current.events.some(event => event.content === "Saved answer")).toBe(true);
    act(() => result.current.submit("next message"));
    await act(async () => { await vi.runAllTimersAsync(); });
    expect(client.runs.stream).toHaveBeenCalledTimes(2);
    expect((client.runs.stream as Mock).mock.calls[1]?.[0]).toBe("chosen-thread");
    expect((client.runs.stream as Mock).mock.calls[1]?.[2]).not.toHaveProperty("command");
    expect(client.runs.cancel).not.toHaveBeenCalled();
  });
});
