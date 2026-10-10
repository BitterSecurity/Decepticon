"use client";

/**
 * useRunObserver — real-time observation of LangGraph runs via @decepticon/streaming.
 *
 * Uses the shared streaming library (same as CLI) to process LangGraph events.
 * No custom parsing — SubagentCustomEvent is the canonical event type.
 *
 * Architecture:
 *   Terminal Server → (creates thread) → WebTerminal → (onThreadId) → Live Page
 *                                                                        ↓
 *   CLI ──submit──→ LangGraph Server ←──joinStream──── useRunObserver(threadId)
 *                                                           ↓
 *                                              @decepticon/streaming (shared)
 *                                                    ↓              ↓
 *                                              CLI (Ink UI)    Web (Canvas Graph)
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { Client } from "@langchain/langgraph-sdk";
import { type SubagentCustomEvent, STREAM_OPTIONS } from "@decepticon/streaming";

import { langGraphApiUrl } from "@/lib/browser-routes";

const POLL_INTERVAL = 2000;

interface UseRunObserverOptions {
  threadId: string | null;
}

interface UseRunObserverReturn {
  /** Sub-agent custom events from the stream (for graph visualization). */
  events: SubagentCustomEvent[];
  /** Whether a run is currently active. */
  isRunning: boolean;
  /** Active run ID if any. */
  activeRunId: string | null;
}

export function useRunObserver({ threadId }: UseRunObserverOptions): UseRunObserverReturn {
  const [events, setEvents] = useState<SubagentCustomEvent[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);

  const apiUrl = typeof window !== "undefined"
    ? langGraphApiUrl(window.location, process.env.NEXT_PUBLIC_LANGGRAPH_API_URL ?? "http://localhost:2024")
    : (process.env.LANGGRAPH_API_URL ?? "http://localhost:2024");

  const clientRef = useRef(new Client({ apiUrl }));
  const observingRunRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const eventsRef = useRef<SubagentCustomEvent[]>([]);

  // Poll for active runs and join their stream
  useEffect(() => {
    if (!threadId) return;

    let active = true;
    const client = clientRef.current;

    // New thread = new session: start from an empty event log. Within a thread
    // events accumulate across runs so the feed/graph mirror the CLI's full
    // session history instead of resetting on every operator message.
    eventsRef.current = [];
    setEvents([]);
    observingRunRef.current = null;

    const poll = async () => {
      if (!active) return;
      try {
        const runs = await client.runs.list(threadId, { limit: 1 }) as Array<{
          run_id: string;
          status: string;
        }>;

        const runningRun = runs.find(
          (r) => r.status === "pending" || r.status === "running",
        );

        if (runningRun && runningRun.run_id !== observingRunRef.current) {
          setIsRunning(true);
          setActiveRunId(runningRun.run_id);
          observingRunRef.current = runningRun.run_id;
          joinRunStream(threadId, runningRun.run_id);
        } else if (!runningRun && observingRunRef.current) {
          observingRunRef.current = null;
          setIsRunning(false);
          setActiveRunId(null);
        }
      } catch (err) {
        console.error("[useRunObserver] Poll error:", err);
      }
    };

    const interval = setInterval(poll, POLL_INTERVAL);
    poll();

    return () => {
      active = false;
      clearInterval(interval);
      abortRef.current?.abort();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId]);

  const joinRunStream = useCallback(async (tid: string, runId: string) => {
    abortRef.current?.abort();
    const abort = new AbortController();
    abortRef.current = abort;

    const client = clientRef.current;
    const seenToolCalls = new Set<string>();
    const streamModes = STREAM_OPTIONS.streamMode;

    try {
      const stream = client.runs.joinStream(tid, runId, {
        signal: abort.signal,
        streamMode: streamModes,
      });

      for await (const event of stream) {
        if (abort.signal.aborted) break;
        if (!event.event || !event.data) continue;

        if (event.event === "custom") {
          const customEvent = event.data as SubagentCustomEvent;
          if (customEvent?.type) {
            eventsRef.current.push(customEvent);
            setEvents([...eventsRef.current]);
          }
        } else if (event.event === "values") {
          // Extract orchestrator (decepticon) tool calls from message history
          const data = event.data as { messages?: Array<{
            type: string;
            name?: string;
            tool_calls?: Array<{ name: string; args?: Record<string, unknown> }>;
          }> };
          if (data?.messages) {
            const newEvents: SubagentCustomEvent[] = [];
            const lastMsg = data.messages[data.messages.length - 1];

            if (lastMsg?.type === "ai" && lastMsg.tool_calls?.length) {
              for (let i = 0; i < lastMsg.tool_calls.length; i++) {
                const tc = lastMsg.tool_calls[i];
                if (tc.name === "task") continue; // Sub-agent delegation — handled by custom events
                // Key by position too, so parallel calls to the same tool in one
                // message are not collapsed into a single event.
                const key = `decepticon-${tc.name}-${i}-${data.messages.length}`;
                if (!seenToolCalls.has(key)) {
                  seenToolCalls.add(key);
                  newEvents.push({
                    type: "subagent_tool_call",
                    agent: "decepticon",
                    tool: tc.name,
                    args: tc.args,
                  });
                }
              }
            } else if (lastMsg?.type === "tool" && lastMsg.name) {
              if (lastMsg.name !== "task") {
                const key = `decepticon-${lastMsg.name}-result-${data.messages.length}`;
                if (!seenToolCalls.has(key)) {
                  seenToolCalls.add(key);
                  newEvents.push({
                    type: "subagent_tool_result",
                    agent: "decepticon",
                    tool: lastMsg.name,
                  });
                }
              }
            }

            if (newEvents.length > 0) {
              eventsRef.current.push(...newEvents);
              setEvents([...eventsRef.current]);
            }
          }
        }
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return;
      console.error("[useRunObserver] Stream error:", err);
    } finally {
      if (observingRunRef.current === runId) {
        observingRunRef.current = null;
        setIsRunning(false);
        setActiveRunId(null);
      }
    }
  }, []);

  return { events, isRunning, activeRunId };
}
