/**
 * useAgent — LangGraph SDK streaming hook for Ink.
 *
 * Uses stream_mode="values" (full state snapshots) and diffs the messages
 * array to detect new entries — the same approach as Python StreamingEngine.
 *
 * Also subscribes to stream_mode="custom" for sub-agent events emitted by
 * StreamingRunnable via get_stream_writer(). This enables real-time visibility
 * into sub-agent tool calls, bash execution, and AI reasoning.
 *
 * Supports three run lifecycle operations:
 * - interrupt(): Pause at checkpoint (Ctrl+C single press) — state preserved
 * - cancel(): Hard abort (Ctrl+C double press, or from paused) — state lost
 * - resume(): Continue from pause point with optional feedback
 */

import { useState, useCallback, useRef, useEffect } from "react";
import { Client } from "@langchain/langgraph-sdk";
import { saveThread, touchThread, loadThreadByIndex } from "../utils/threadStore.js";
import type { ActiveQuestion, AgentEvent, AskUserOption } from "../types.js";
import {
  type SubagentCustomEvent,
  STREAM_OPTIONS,
  extractText,
  stripResultTags,
} from "@decepticon/streaming";
import { getModelOverride } from "../commands/modelOverride.js";
import { getAssistantOverride } from "../commands/assistantOverride.js";
import { blueMonitorReady } from "../commands/blue.js";
import { assertRedApproved } from "../commands/planMode.js";

interface LangChainMessage {
  type: string; // "human", "ai", "tool"
  name?: string; // tool name (on ToolMessage)
  content: string | Array<{ type: string; text?: string }>;
  tool_calls?: Array<{
    id: string;
    name: string;
    args: Record<string, unknown>;
  }>;
  readonly invalid_tool_calls?: readonly unknown[];
  tool_call_id?: string;
  status?: string; // "success" | "error" on tool messages
  response_metadata?: {
    finish_reason?: string;
    stop_reason?: string;
    token_usage?: {
      completion_tokens?: number;
      prompt_tokens?: number;
      total_tokens?: number;
    };
  };
}

interface UseAgentOptions {
  apiUrl?: string;
  /** Load the previous thread from disk (--resume flag). */
  resumeThread?: boolean;
}

interface PendingTool {
  name: string;
  args: Record<string, unknown>;
}

export interface StreamStats {
  startTime: number;
  totalTokens: number;
  promptTokens: number;
  completionTokens: number;
}

/** Agent run lifecycle state. */
export type RunState = "idle" | "connecting" | "streaming" | "paused";

interface UseAgentReturn {
  submit: (message: string) => void;
  /** Pause the current run at checkpoint (Ctrl+C single). State preserved. */
  interrupt: () => Promise<void>;
  /** Hard cancel the current run (Ctrl+C double, or from paused). State lost. */
  cancel: () => void;
  /** Resume a paused run with optional operator feedback. */
  resume: (value?: string) => void;
  /** Enqueue a message to auto-submit when current run completes. */
  enqueue: (message: string) => void;
  /** Clear the queued message. */
  clearQueuedMessage: () => void;
  events: AgentEvent[];
  /** Current run lifecycle state. */
  runState: RunState;
  /** Derived from runState for backward compatibility. */
  isStreaming: boolean;
  pendingTool: PendingTool | null;
  streamStats: StreamStats | null;
  /** Currently active agent name (e.g. "decepticon", "recon"). */
  activeAgent: string | null;
  /** Persistent assistant id ("soundwave" | "decepticon") — shown when no subagent is streaming. */
  assistantId: string;
  /** Queued message to auto-submit on completion. */
  queuedMessage: string | null;
  /** Pending operator question while a picker is awaiting an answer. */
  activeQuestion: ActiveQuestion | null;
  /** Submit a structured answer to the current ask_user_question prompt. */
  answerQuestion: (value: string | string[]) => void;
  error: string | null;
  clearEvents: () => void;
  addSystemEvent: (content: string) => void;
  selectAssistant: (assistantId: string) => Promise<boolean>;
}

const INITIAL_ASSISTANT_ID =
  process.env.DECEPTICON_ASSISTANT_ID || "decepticon";
let _nextEventId = 0;


async function assertAssistantReady(assistant: string): Promise<void> {
  if (assistant === "blue_cell" && !(await blueMonitorReady())) {
    throw new DOMException("Blue sensor is unavailable. Use /blue up 127.0.0.1:3000 before sending in Blue mode.", "NotAllowedError");
  }
  if (assistant === "decepticon" && process.env.DECEPTICON_ENGAGEMENT) await assertRedApproved();
}

export function useAgent({
  apiUrl = process.env.DECEPTICON_API_URL || "http://localhost:2024",
  resumeThread = false,
}: UseAgentOptions = {}): UseAgentReturn {
  const clientRef = useRef(new Client({ apiUrl }));
  // Thread ID priority: env var (from web terminal) > --resume flag > new thread
  const envThreadId = process.env.DECEPTICON_THREAD_ID || null;
  const threadIdRef = useRef<string | null>(envThreadId ?? null);
  const resumeInitialized = useRef(false);
  const eventsRef = useRef<AgentEvent[]>([]);
  const lastCountRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const runIdRef = useRef<string | null>(null);
  const switchingRef = useRef(false);
  const streamReadyRef = useRef<Promise<void> | null>(null);
  const streamReadyResolveRef = useRef<(() => void) | null>(null);
  const queuedMessageRef = useRef<string | null>(null);

  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [runState, setRunState] = useState<RunState>("idle");
  const [pendingTool, setPendingTool] = useState<PendingTool | null>(null);
  const [streamStats, setStreamStats] = useState<StreamStats | null>(null);
  const [activeAgent, setActiveAgent] = useState<string | null>(null);
  const [assistantId, setAssistantId] = useState<string>(INITIAL_ASSISTANT_ID);
  const [error, setError] = useState<string | null>(null);
  const [queuedMessage, setQueuedMessage] = useState<string | null>(null);
  const [activeQuestion, setActiveQuestion] = useState<ActiveQuestion | null>(null);

  // Ref for runState to avoid stale closures in async callbacks
  const runStateRef = useRef<RunState>(runState);
  runStateRef.current = runState;
  // Mirror activeQuestion so handleStreamComplete can check it without stale state.
  const activeQuestionRef = useRef<ActiveQuestion | null>(activeQuestion);
  activeQuestionRef.current = activeQuestion;
  // tool_call_ids the CLI has already shown a picker for. Dedupes the second
  // emission LangGraph fires when the ToolNode re-executes the tool body
  // after Command(resume=...).
  const askedQuestionIds = useRef<Set<string>>(new Set());
  const assistantIdRef = useRef<string>(INITIAL_ASSISTANT_ID);
  const threadAssistantRef = useRef<string>(INITIAL_ASSISTANT_ID);
  const modeSessionsRef = useRef(new Map<string, {
    threadId: string | null;
    events: AgentEvent[];
    lastCount: number;
    runState: RunState;
    runId: string | null;
    question: ActiveQuestion | null;
    askedIds: Set<string>;
    queued: string | null;
  }>());

  // Derived for backward compatibility
  const isStreaming = runState === "streaming" || runState === "connecting";

  // Async initialization: load saved thread for --resume flag
  useEffect(() => {
    if (envThreadId || !resumeThread || resumeInitialized.current) return;
    resumeInitialized.current = true;
    loadThreadByIndex(0).then((saved) => {
      if (saved) {
        threadIdRef.current = saved.threadId;
        if (saved.assistantId) {
          assistantIdRef.current = saved.assistantId;
          threadAssistantRef.current = saved.assistantId;
          setAssistantId(saved.assistantId);
        }
      }
    }).catch(() => {});
  }, []);

  const addEvent = useCallback(
    (partial: Omit<AgentEvent, "id" | "timestamp">) => {
      const newEvent: AgentEvent = {
        ...partial,
        id: `evt-${++_nextEventId}`,
        timestamp: Date.now(),
      };
      eventsRef.current.push(newEvent);
      setEvents([...eventsRef.current]);
    },
    [],
  );

  const addSystemEvent = useCallback(
    (content: string) => {
      addEvent({ type: "system", content });
    },
    [addEvent],
  );

  const resetStreamState = useCallback(() => {
    runStateRef.current = "idle";
    setRunState("idle");
    setPendingTool(null);
    setStreamStats(null);
    setActiveAgent(null);
  }, []);

  // ── Enqueue / clear queue ───────────────────────────────────────

  const enqueue = useCallback(
    (message: string) => {
      queuedMessageRef.current = message;
      setQueuedMessage(message);
      addEvent({ type: "system", content: `Queued: "${message}"` });
    },
    [addEvent],
  );

  const clearQueuedMessage = useCallback(() => {
    queuedMessageRef.current = null;
    setQueuedMessage(null);
  }, []);

  // ── Stream event processing (shared by submit and resume) ──────

  const processStream = useCallback(
    async (
      stream: AsyncIterable<{ event: string; data: unknown }>,
      abortController: AbortController,
    ) => {
      // Capture run_id from the first metadata event for interrupt/cancel
      // LangGraph SDK emits: { event: "metadata", data: { run_id, thread_id } }
      const toolCallArgs = new Map<string, Record<string, unknown>>();
      const toolCallNames = new Map<string, string>();
      let cumTotal = 0;
      let cumPrompt = 0;
      let cumCompletion = 0;
      let completionReceived = false;
      let serverErrorReceived = false;


      const handleCustomEvent = (data: SubagentCustomEvent) => {
        switch (data.type) {
          case "subagent_start":
            setActiveAgent(data.agent);
            addEvent({
              type: "subagent_start",
              content: data.prompt ?? `Starting ${data.agent}`,
              subagent: data.agent,
            });
            break;

          case "subagent_tool_call":
            setPendingTool({
              name: data.tool ?? "",
              args: data.args ?? {},
            });
            break;

          case "subagent_tool_result": {
            setPendingTool(null);
            const status: "success" | "error" =
              data.status === "error" ? "error" : "success";

            if (data.tool === "bash") {
              addEvent({
                type: "bash_result",
                content: data.content ?? "",
                toolName: "bash",
                toolArgs: data.args ?? {},
                status,
                subagent: data.agent,
              });
            } else {
              addEvent({
                type: "tool_result",
                content: data.content ?? "",
                toolName: data.tool ?? "",
                toolArgs: data.args ?? {},
                status,
                subagent: data.agent,
              });
            }
            break;
          }

          case "subagent_message":
            addEvent({
              type: "ai_message",
              content: data.text ?? "",
              subagent: data.agent,
            });
            break;

          case "subagent_end":
            addEvent({
              type: "subagent_end",
              content: data.elapsed
                ? `Completed (${Math.floor(data.elapsed / 1000)}s)`
                : "Completed",
              subagent: data.agent,
              status: data.error ? "error" : "success",
            });
            setActiveAgent(threadAssistantRef.current);
            setPendingTool(null);
            break;

          case "background_complete": {
            // SandboxNotificationMiddleware fires this when a background
            // bash session finishes. The middleware also injects the
            // captured output into the agent's message stream as a
            // <system-reminder>, so the agent doesn't need to call
            // bash_output — this event exists purely so the CLI can
            // render a Claude-Code-style "● Background command ..."
            // line with the output inline, instead of leaving the
            // operator with just a tool-call shadow.
            const exit = data.exit_code;
            const status: "success" | "error" =
              exit === 0 || exit === null || exit === undefined ? "success" : "error";
            addEvent({
              type: "background_complete",
              content: data.content ?? "",
              session: data.session,
              command: data.command,
              exitCode: exit ?? null,
              elapsed: data.elapsed,
              status,
              subagent: data.agent,
            });
            break;
          }

          case "planning_draft_ready": {
            addEvent({
              type: "system",
              content:
                "Planning draft ready for review. Plan remains active; use /mode red after reviewing the documents.",
            });
            break;
          }

          case "engagement_ready": {
            addEvent({
              type: "system",
              content:
                "Planning complete. Review the scope, then use /mode red to start Red explicitly.",
            });
            break;
          }

          case "ask_user_question": {
            // The backend tool body re-runs once on Command(resume=...) so the
            // same custom event arrives twice. Dedupe by tool_call_id.
            const sourceId = data.id ?? "";
            if (sourceId && askedQuestionIds.current.has(sourceId)) {
              break;
            }
            if (sourceId) askedQuestionIds.current.add(sourceId);

            const question = data.question ?? "";
            const header = data.header ?? "";
            const options = (data.options ?? []) as AskUserOption[];
            const multiSelect = !!data.multi_select;
            const allowOther = !!data.allow_other;

            addEvent({
              type: "ask_user_question",
              content: question,
              subagent: data.agent,
              sourceId,
              question,
              header,
              options,
              multiSelect,
              allowOther,
            });
            setPendingTool(null);
            activeQuestionRef.current = {
              sourceId,
              question,
              header,
              options,
              multiSelect,
              allowOther,
            };
            setActiveQuestion(activeQuestionRef.current);
            // The backend interrupt() will pause the stream; flag the run as
            // paused immediately so the REPL hides the normal prompt and
            // shows the picker.
            runStateRef.current = "paused";
            setRunState("paused");
            break;
          }
        }
      };

      let ready = (): void => {};
      streamReadyRef.current = new Promise<void>((resolve) => { ready = resolve; });
      streamReadyResolveRef.current = ready;
      for await (const event of stream) {
        if (abortController.signal.aborted) break;

        // Capture run_id from metadata event for precise interrupt/cancel
        if (event.event === "metadata") {
          const meta = event.data as { run_id?: string };
          if (meta.run_id) {
            runIdRef.current = meta.run_id;
            ready();
          }
          continue;
        }

        // Handle server-side errors (LLM connection failures, etc.)
        if (event.event === "error") {
          const errData = event.data as
            | { message?: string; error?: string }
            | string;
          const errMsg =
            typeof errData === "string"
              ? errData
              : errData?.message ?? errData?.error ?? "Server error";
          setError(errMsg);
          serverErrorReceived = true;
          continue;
        }

        // Handle custom events (sub-agent streaming from StreamingRunnable)
        if (event.event === "custom") {
          const data = event.data as SubagentCustomEvent;
          if (data && typeof data === "object" && "type" in data) {
            handleCustomEvent(data);
          }
          continue;
        }

        // Belt-and-braces: a LangGraph interrupt() bubbles up as an
        // `updates` chunk with `__interrupt__` even if the preceding custom
        // event was lost. Surface the picker from the interrupt payload so
        // the run never silently ends with the operator stranded.
        if (event.event === "updates") {
          const updates = event.data as
            | { __interrupt__?: Array<{ value?: unknown }> }
            | undefined;
          const interrupts = updates?.__interrupt__;
          if (Array.isArray(interrupts)) {
            for (const it of interrupts) {
              const v = it?.value;
              if (
                v &&
                typeof v === "object" &&
                "type" in v &&
                (v as { type: unknown }).type === "ask_user_question"
              ) {
                handleCustomEvent(v as SubagentCustomEvent);
              }
            }
          }
          continue;
        }

        if (event.event !== "values") continue;

        const data = event.data as {
          messages?: LangChainMessage[];
        };
        const messages = data.messages ?? [];
        const newMessages = messages.slice(lastCountRef.current);
        lastCountRef.current = messages.length;

        for (const msg of newMessages) {
          if (msg.type === "human") continue;

          if (msg.type === "ai") {
            // Extract token usage
            const usage = msg.response_metadata?.token_usage;
            if (usage) {
              cumTotal += usage.total_tokens ?? 0;
              cumPrompt += usage.prompt_tokens ?? 0;
              cumCompletion += usage.completion_tokens ?? 0;
              setStreamStats((prev) =>
                prev
                  ? { ...prev, totalTokens: cumTotal, promptTokens: cumPrompt, completionTokens: cumCompletion }
                  : prev,
              );
            }

            // Emit AI text content (even when tool_calls are present)
            const text = stripResultTags(extractText(msg.content));
            if (text) {
              addEvent({ type: "ai_message", content: text });
            }

            const invalidToolCount = msg.invalid_tool_calls?.length ?? 0;
            if (invalidToolCount > 0) {
              const runReference = runIdRef.current ? ` Run: ${runIdRef.current}.` : "";
              addEvent({
                type: "system",
                content: `The model returned invalid tool calls. Inspect the model response format.${runReference}`,
              });
            }

            if (msg.tool_calls?.length) {
              for (const tc of msg.tool_calls) {
                toolCallArgs.set(tc.id, tc.args);
                toolCallNames.set(tc.id, tc.name);
                if (tc.name === "task") {
                  // Emit delegate event for sub-agent dispatch
                  addEvent({
                    type: "delegate",
                    content: (tc.args.description as string) ?? "",
                    subagent: (tc.args.subagent_type as string) ?? "",
                  });
                } else {
                  setPendingTool({ name: tc.name, args: tc.args });
                }
              }
            } else {
              setPendingTool(null);
              completionReceived = true;
              if (!text && invalidToolCount === 0) {
                const finishReason = msg.response_metadata?.finish_reason
                  ?? msg.response_metadata?.stop_reason;
                let diagnosis = "The model returned no displayable text or tool calls; the cause is unknown.";
                if (finishReason === "length" || finishReason === "max_tokens") {
                  diagnosis = "The provider reported an output token limit with no displayable text or tool calls.";
                } else if (finishReason === "content_filter") {
                  diagnosis = "The provider reported a content filter with no displayable text or tool calls.";
                }
                const runReference = runIdRef.current ? ` Run: ${runIdRef.current}.` : "";
                addEvent({
                  type: "system",
                  content: `${diagnosis}${runReference} Inspect the server and model logs for this run.`,
                });
              }
            }

          } else if (msg.type === "tool") {
            const content =
              typeof msg.content === "string"
                ? msg.content
                : extractText(msg.content);
            const tcId = msg.tool_call_id ?? "";
            const args = toolCallArgs.get(tcId) ?? {};
            const toolName = msg.name ?? toolCallNames.get(tcId) ?? "";
            const status: "success" | "error" =
              msg.status === "error" ? "error" : "success";

            setPendingTool(null);

            // Suppress task() tool results — already shown via sub-agent custom events
            if (toolName === "task") continue;

            if (toolName === "bash") {
              addEvent({
                type: "bash_result",
                content,
                toolName: "bash",
                toolArgs: args,
                status,
              });
            } else {
              addEvent({
                type: "tool_result",
                content,
                toolName,
                toolArgs: args,
                status,
              });
            }
          }
        }
      }

      if (abortController.signal.aborted) return;

      // Detect unexpected disconnection: stream ended but no completion event.
      //
      // ``ask_user_question`` ends the stream by raising LangGraph's
      // ``interrupt()`` \u2014 the run is PAUSED, not lost. ``activeQuestionRef``
      // is the same signal ``handleStreamComplete`` already uses to keep
      // runState=paused and render the picker. Without this guard every
      // interview turn surfaced a bogus "Connection to server lost" notice
      // even though the run was sitting in a clean interrupt waiting for the
      // operator's pick.
      const interrupted = activeQuestionRef.current !== null;
      if (!completionReceived && !serverErrorReceived && !abortController.signal.aborted && !interrupted) {
        addSystemEvent(
          "\u26a0\ufe0f Connection to server lost. The run continues server-side. "
          + "Use /resume to reconnect.",
        );
        setRunState("idle");
      }
    },
    [addEvent, addSystemEvent, setRunState],
  );

  // ── Handle stream completion (shared by submit and resume) ─────

  const handleStreamComplete = useCallback(
    (abortController: AbortController) => {
      if (abortController.signal.aborted) return;
      streamReadyResolveRef.current?.();
      streamReadyResolveRef.current = null;

      // The stream ended because the backend tool called langgraph.interrupt
      // and is waiting for the operator's pick. Keep runState=paused so the
      // REPL renders the picker, but null abortRef so a follow-up submit/
      // cancel is not silently blocked by a completed AbortController.
      if (activeQuestionRef.current) {
        abortRef.current = null;
        return;
      }

      abortRef.current = null;
      runIdRef.current = null;
      resetStreamState();

      // Auto-submit queued message
      const pending = queuedMessageRef.current;
      if (pending && !switchingRef.current) {
        queuedMessageRef.current = null;
        setQueuedMessage(null);
        const assistant = assistantIdRef.current;
        setTimeout(() => {
          if (assistantIdRef.current === assistant && !switchingRef.current) submitRef.current(pending);
          else {
            const saved = modeSessionsRef.current.get(assistant);
            if (saved) saved.queued = pending;
            else { queuedMessageRef.current = pending; setQueuedMessage(pending); }
          }
        }, 0);
      }
    },
    [resetStreamState],
  );

  const pauseRun = useCallback(async (): Promise<RunState> => {
    if (abortRef.current && streamReadyRef.current) {
      let timeout: ReturnType<typeof setTimeout> | undefined;
      try {
        await Promise.race([
          streamReadyRef.current,
          new Promise<never>((_resolve, reject) => {
            timeout = setTimeout(() => reject(new DOMException("Timed out waiting for the run ID; retry the mode switch.", "TimeoutError")), 10_000);
          }),
        ]);
      } finally {
        clearTimeout(timeout);
      }
    }
    const threadId = threadIdRef.current;
    const runId = runIdRef.current;
    const active = abortRef.current !== null;
    if (active && threadId && runId) {
      const controller = new AbortController();
      let timeout: ReturnType<typeof setTimeout> | undefined;
      try {
        await Promise.race([
          clientRef.current.runs.cancel(threadId, runId, true, "interrupt", { signal: controller.signal }),
          new Promise<never>((_resolve, reject) => {
            timeout = setTimeout(() => {
              const error = new DOMException("TimeoutError: server pause did not acknowledge within 10 seconds.", "TimeoutError");
              controller.abort(error);
              reject(error);
            }, 10_000);
          }),
        ]);
      } finally {
        clearTimeout(timeout);
      }
    }
    abortRef.current?.abort();
    abortRef.current = null;
    streamReadyRef.current = null;
    return active && runId ? "paused" : active ? "idle" : runStateRef.current;
  }, []);

  // ── Interrupt (pause at checkpoint) ────────────────────────────

  const interrupt = useCallback(async (): Promise<void> => {
    if (switchingRef.current) return;
    switchingRef.current = true;
    try {
      const pausedState = await pauseRun();
      resetStreamState();
      runStateRef.current = pausedState;
      setRunState(pausedState);
      addSystemEvent("Paused. Type /resume to continue, or send a new message.");
    } catch (error) {
      const message = `Could not pause: ${error instanceof Error ? error.message : String(error)}`;
      setError(message);
      addSystemEvent(message);
    } finally {
      switchingRef.current = false;
    }
  }, [addSystemEvent, pauseRun, resetStreamState]);

  // ── Cancel (hard abort, no resume) ─────────────────────────────

  const cancel = useCallback(() => {
    if (switchingRef.current) return;
    // Abort local stream
    abortRef.current?.abort();
    abortRef.current = null;

    // Hard cancel on server — destroys run state
    const threadId = threadIdRef.current;
    const runId = runIdRef.current;
    if (threadId && runId) {
      clientRef.current.runs
        .cancel(threadId, runId, false, "rollback")
        .catch(() => {
          addEvent({ type: "system", content: "Warning: server cancel failed." });
        });
    }

    runIdRef.current = null;
    // Clear queued message and any pending picker on hard cancel
    queuedMessageRef.current = null;
    setQueuedMessage(null);
    setActiveQuestion(null);
    resetStreamState();
    addEvent({ type: "system", content: "Cancelled." });
  }, [addEvent, resetStreamState]);

  // ── Clear ──────────────────────────────────────────────────────

  const clearEvents = useCallback(() => {
    if (switchingRef.current) return;
    eventsRef.current = [];
    setEvents([]);
    threadIdRef.current = null;
    lastCountRef.current = 0;
    runIdRef.current = null;
    queuedMessageRef.current = null;
    setQueuedMessage(null);
    setActiveQuestion(null);
    askedQuestionIds.current.clear();
    setRunState("idle");
  }, []);

  const selectAssistant = useCallback(async (next: string): Promise<boolean> => {
    if (switchingRef.current) {
      addSystemEvent("A mode switch is already in progress. Retry after it finishes.");
      return false;
    }
    const current = assistantIdRef.current;
    if (next === current) return true;
    switchingRef.current = true;
    try {
      const pausedState = await pauseRun();
      modeSessionsRef.current.set(current, {
        threadId: threadIdRef.current,
        events: [...eventsRef.current],
        lastCount: lastCountRef.current,
        runState: pausedState,
        runId: runIdRef.current,
        question: activeQuestionRef.current,
        askedIds: new Set(askedQuestionIds.current),
        queued: queuedMessageRef.current,
      });
      const saved = modeSessionsRef.current.get(next);
      threadIdRef.current = saved?.threadId ?? null;
      lastCountRef.current = saved?.lastCount ?? 0;
      eventsRef.current = [...(saved?.events ?? [])];
      setEvents(eventsRef.current);
      runIdRef.current = saved?.runId ?? null;
      activeQuestionRef.current = saved?.question ?? null;
      setActiveQuestion(activeQuestionRef.current);
      askedQuestionIds.current = new Set(saved?.askedIds);
      queuedMessageRef.current = saved?.queued ?? null;
      setQueuedMessage(queuedMessageRef.current);
      resetStreamState();
      runStateRef.current = saved?.runState ?? "idle";
      setRunState(runStateRef.current);
      setError(null);
      assistantIdRef.current = next;
      threadAssistantRef.current = next;
      setAssistantId(next);
      return true;
    } catch (error) {
      const message = `Could not switch mode: ${error instanceof Error ? error.message : String(error)}`;
      setError(message);
      addSystemEvent(message);
      return false;
    } finally {
      switchingRef.current = false;
    }
  }, [addSystemEvent, resetStreamState, pauseRun]);

  // ── Submit (only when idle or paused) ──────────────────────────

  const submit = useCallback(
    (message: string): void => {
      // If streaming/connecting, callers should use enqueue() instead
      if (switchingRef.current) {
        addSystemEvent("Mode switch in progress. Send this message again after it finishes.");
        return;
      }
      if (abortRef.current) return;

      const selectedAssistant = getAssistantOverride() || assistantIdRef.current;
      if (threadIdRef.current && selectedAssistant !== threadAssistantRef.current) {
        threadIdRef.current = null;
        lastCountRef.current = 0;
        askedQuestionIds.current.clear();
      }
      threadAssistantRef.current = selectedAssistant;
      setAssistantId(selectedAssistant);

      // If paused, cancel the paused run gracefully. Use "interrupt"
      // strategy to preserve thread state so the follow-up message
      // can see prior tool results and context (#617).
      if (runStateRef.current === "paused") {
        const threadId = threadIdRef.current;
        const runId = runIdRef.current;
        if (threadId && runId) {
          clientRef.current.runs
            .cancel(threadId, runId, false, "interrupt")
            .catch(() => {});
        }
        runIdRef.current = null;
        // Clear any leftover picker so a fresh submit does not render on top
        // of a stale ask_user_question.
        setActiveQuestion(null);
      }

      setRunState("connecting");
      addEvent({ type: "user", content: message });

      const abortController = new AbortController();
      abortRef.current = abortController;

      streamReadyRef.current = null;
      const runStream = async () => {
        const client = clientRef.current;
        setError(null);

        try {
          await assertAssistantReady(selectedAssistant);
        } catch (error) {
          if (abortController.signal.aborted) return;
          const denied = error instanceof Error ? error.message : "Mode prerequisite failed.";
          setError(denied);
          addEvent({ type: "system", content: denied });
          handleStreamComplete(abortController);
          return;
        }

        // Create thread if needed (retry for server startup race condition)
        if (!threadIdRef.current) {
          const maxRetries = 5;
          for (let attempt = 1; attempt <= maxRetries; attempt++) {
            if (abortController.signal.aborted) return;
            try {
              const thread = await client.threads.create();
              if (abortController.signal.aborted) return;
              threadIdRef.current = thread.thread_id;
              await saveThread(thread.thread_id, selectedAssistant, message);
              break;
            } catch (err) {
              if (abortController.signal.aborted) return;
              if (attempt === maxRetries) {
                const msg =
                  err instanceof Error ? err.message : "Failed to create thread";
                const connectionError = `Connection failed: ${msg}`;
                setError(connectionError);
                addEvent({ type: "system", content: connectionError });
                // Clear queued message to prevent infinite retry loop
                queuedMessageRef.current = null;
                setQueuedMessage(null);
                handleStreamComplete(abortController);
                return;
              }
              // Server may still be loading graphs — wait and retry
              await new Promise((r) => setTimeout(r, 2000));
            }
          }
        }

        if (abortController.signal.aborted) return;

        setRunState("streaming");
        setPendingTool(null);
        setActiveAgent(selectedAssistant);
        setStreamStats({ startTime: Date.now(), totalTokens: 0, promptTokens: 0, completionTokens: 0 });

        // Engagement metadata is hydrated from configurable; model selection
        // uses a declared graph input channel so it survives checkpointing.
        const input: Record<string, unknown> = {
          messages: [{ role: "user", content: message }],
          model_override: getModelOverride(),
        };

        const configurable: Record<string, unknown> = {};
        const slug = process.env.DECEPTICON_ENGAGEMENT;
        if (slug) {
          configurable.engagement_name = slug;
          configurable.workspace_path =
            process.env.DECEPTICON_WORKSPACE_PATH ?? "/workspace";
        }
        const targetValue = process.env.DECEPTICON_TARGET;
        if (targetValue) {
          configurable.target_value = targetValue;
          configurable.target_type = process.env.DECEPTICON_TARGET_TYPE ?? "";
          configurable.authorization_confirmed =
            process.env.DECEPTICON_AUTHORIZATION_CONFIRMED === "true";
        }
        const hasConfigurable = Object.keys(configurable).length > 0;
        const streamConfig = hasConfigurable ? { configurable } : undefined;

        try {
          const stream = client.runs.stream(
            threadIdRef.current!,
            selectedAssistant,
            {
              input,
              ...(streamConfig ? { config: streamConfig } : {}),
              ...STREAM_OPTIONS,
              onDisconnect: "continue",
              signal: abortController.signal,
            },
          );

          await processStream(stream, abortController);
        } catch (err) {
          // Ignore abort errors — triggered by interrupt() or cancel()
          if (abortController.signal.aborted) return;
          const msg =
            err instanceof Error ? err.message : "Unknown streaming error";
          setError(msg);
        }

        handleStreamComplete(abortController);
      };

      runStream().catch((err) => {
        if (abortController.signal.aborted) return;
        setError(err instanceof Error ? err.message : "Unknown error");
        abortRef.current = null;
        runIdRef.current = null;
        resetStreamState();
      });
    },
    [addEvent, processStream, handleStreamComplete, resetStreamState],
  );

  // Ref to submit for use in deferred auto-submit (avoids stale closure)
  const submitRef = useRef(submit);
  submitRef.current = submit;

  const prepareResume = useCallback(async (controller: AbortController): Promise<boolean> => {
    try {
      await assertAssistantReady(threadAssistantRef.current);
      return !controller.signal.aborted;
    } catch (error) {
      if (controller.signal.aborted) return false;
      const denied = error instanceof Error ? error.message : "Mode prerequisite failed.";
      setError(denied);
      addSystemEvent(denied);
      abortRef.current = null;
      return false;
    }
  }, [addSystemEvent]);

  // ── Answer a structured ask_user_question prompt ───────────────

  const answerQuestion = useCallback(
    (value: string | string[]): void => {
      if (switchingRef.current || abortRef.current) return;
      streamReadyRef.current = null;
      const current = activeQuestionRef.current;
      if (!current) return;

      if (!threadIdRef.current) {
        addEvent({ type: "system", content: "No active thread — cannot resume." });
        setRunState("idle");
        return;
      }

      const abortController = new AbortController();
      abortRef.current = abortController;

      const runResume = async () => {
        const client = clientRef.current;
        setError(null);
        if (!(await prepareResume(abortController))) return;

        try {
          const state = await client.threads.getState(threadIdRef.current!);
          const msgs = (state.values as { messages?: unknown[] })?.messages;
          if (!abortController.signal.aborted && msgs) lastCountRef.current = msgs.length;
        } catch { /* proceed with current count */ }

        if (abortController.signal.aborted) return;

        const display = Array.isArray(value) ? value.join(", ") : value;
        addEvent({
          type: "ask_user_answer",
          content: display,
          subagent: threadAssistantRef.current,
          sourceId: current.sourceId,
        });
        activeQuestionRef.current = null;
        setActiveQuestion(null);

        setRunState("streaming");
        setPendingTool(null);
        setStreamStats({
          startTime: Date.now(),
          totalTokens: 0,
          promptTokens: 0,
          completionTokens: 0,
        });

        try {
          const stream = client.runs.stream(
            threadIdRef.current!,
            threadAssistantRef.current,
            {
              command: { resume: value, update: { model_override: getModelOverride() } },
              ...STREAM_OPTIONS,
              onDisconnect: "continue",
              signal: abortController.signal,
            },
          );
          await processStream(stream, abortController);
        } catch (err) {
          if (abortController.signal.aborted) return;
          setError(err instanceof Error ? err.message : "Answer submit failed");
        }
        handleStreamComplete(abortController);
      };

      runResume().catch((err) => {
        if (abortController.signal.aborted) return;
        setError(err instanceof Error ? err.message : "Answer submit error");
        abortRef.current = null;
        runIdRef.current = null;
        resetStreamState();
      });
    },
    [addEvent, processStream, handleStreamComplete, resetStreamState, prepareResume],
  );

  // ── Resume (pause point OR previous session) ───────────────────

  const resume = useCallback(
    (value?: string): void => {
      if (switchingRef.current || abortRef.current) return;
      streamReadyRef.current = null;
      const isPaused = runStateRef.current === "paused";
      const hasThread = !!threadIdRef.current;

      // Case 1: Paused — resume from checkpoint with Command({ resume })
      if (isPaused && hasThread) {
        const abortController = new AbortController();
        abortRef.current = abortController;

        const runResume = async () => {
          const client = clientRef.current;
          setError(null);
          if (!(await prepareResume(abortController))) return;
          if (value) addEvent({ type: "user", content: value });
          addEvent({ type: "system", content: "Resuming from checkpoint..." });

          try {
            const state = await client.threads.getState(threadIdRef.current!);
            const msgs = (state.values as { messages?: unknown[] })?.messages;
            if (!abortController.signal.aborted && msgs) lastCountRef.current = msgs.length;
          } catch { /* proceed with current count */ }

          if (abortController.signal.aborted) return;

          setRunState("streaming");
          setPendingTool(null);
          setActiveAgent(threadAssistantRef.current);
          setStreamStats({ startTime: Date.now(), totalTokens: 0, promptTokens: 0, completionTokens: 0 });

          try {
            const stream = client.runs.stream(
              threadIdRef.current!,
              threadAssistantRef.current,
              {
                command: { resume: value ?? true, update: { model_override: getModelOverride() } },
                ...STREAM_OPTIONS,
                onDisconnect: "continue",
                signal: abortController.signal,
              },
            );
            await processStream(stream, abortController);
          } catch (err) {
            if (abortController.signal.aborted) return;
            setError(err instanceof Error ? err.message : "Resume failed");
          }
          handleStreamComplete(abortController);
        };

        runResume().catch((err) => {
          if (abortController.signal.aborted) return;
          setError(err instanceof Error ? err.message : "Resume error");
          abortRef.current = null;
          runIdRef.current = null;
          resetStreamState();
        });
        return;
      }

      // Case 2: Load a specific thread by ID (from session picker or --resume)
      if (value && runStateRef.current === "idle") {
        // Clear current events before restoring a different session
        eventsRef.current = [];
        setEvents([]);

        threadIdRef.current = value;
        touchThread(value).catch(() => {});
        addEvent({ type: "system", content: "Restoring session..." });

        // Fetch thread state and restore conversation history
        const client = clientRef.current;
        client.threads.getState(value).then((state) => {
          const msgs = (state.values as { messages?: LangChainMessage[] })?.messages ?? [];
          lastCountRef.current = msgs.length;

          for (const msg of msgs) {
            if (msg.type === "human") {
              const text = extractText(msg.content);
              if (text) addEvent({ type: "user", content: text });
            } else if (msg.type === "ai") {
              const text = stripResultTags(extractText(msg.content));
              if (text) addEvent({ type: "ai_message", content: text });
            }
          }

          addEvent({ type: "system", content: "Session restored. Send a message to continue." });
        }).catch(() => {
          addEvent({ type: "system", content: "Could not restore this session. Check server availability and the session ID." });
          lastCountRef.current = 0;
        });
        return;
      }

      // Case 3: Nothing to resume
      addEvent({ type: "system", content: "Nothing to resume." });
    },
    [addEvent, processStream, handleStreamComplete, resetStreamState, prepareResume],
  );

  return {
    submit,
    interrupt,
    cancel,
    resume,
    enqueue,
    clearQueuedMessage,
    events,
    runState,
    isStreaming,
    pendingTool,
    streamStats,
    activeAgent,
    assistantId,
    queuedMessage,
    activeQuestion,
    answerQuestion,
    error,
    clearEvents,
    addSystemEvent,
    selectAssistant,
  };
}
