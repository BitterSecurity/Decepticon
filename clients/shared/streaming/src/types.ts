/**
 * Canonical event types from StreamingRunnable's get_stream_writer().
 *
 * This is the single source of truth — both Web and CLI import from here.
 * Matches the backend contract at decepticon/core/subagent_streaming.py.
 */

/** String literal union of all sub-agent event types. */
export type SubagentEventType =
  | "subagent_start"
  | "subagent_end"
  | "subagent_tool_call"
  | "subagent_tool_result"
  | "subagent_message"
  | "ask_user_question"
  | "engagement_ready"
  | "planning_draft_ready"
  | "background_complete";

/** One choice presented in an ask_user_question picker. */
export interface AskUserOption {
  label: string;
  description: string;
}

/** Custom event payload from StreamingRunnable's get_stream_writer(). */
export interface SubagentCustomEvent {
  type: SubagentEventType;
  agent: string;
  tool?: string;
  args?: Record<string, unknown>;
  content?: string;
  text?: string;
  prompt?: string;
  elapsed?: number;
  status?: string;
  cancelled?: boolean;
  error?: boolean;
  // ask_user_question fields. `id` is the LangChain tool_call_id and is used
  // by consumers to deduplicate the second emission that fires when LangGraph
  // re-executes the tool body after Command(resume=...).
  id?: string;
  question?: string;
  header?: string;
  options?: AskUserOption[];
  multi_select?: boolean;
  allow_other?: boolean;
  // background_complete fields (auto-delivered when a bash background
  // session finishes). ``content`` carries the captured output (already
  // truncated to a head+tail preview by the middleware when large).
  session?: string;
  command?: string;
  exit_code?: number | null;
}

/** Minimal event shape accepted by shared utility functions. */
export interface StreamEvent {
  id: string;
  type: string;
  content?: string;
  subagent?: string;
  timestamp: number;
  /**
   * Terminal status for `subagent_end` events. The CLI normalizes the
   * backend's `error` boolean into `"error" | "success"` before events reach
   * the shared utilities; consumers that forward the raw backend event leave
   * this unset and set `error` instead (see {@link SubagentCustomEvent}).
   * `deriveSubAgentSessions` honors either signal.
   */
  status?: string;
  /** Raw backend error flag on `subagent_end` (the SubagentCustomEvent contract). */
  error?: boolean;
  /**
   * Per-invocation id emitted by the backend's StreamingRunnable. Lets
   * `deriveSubAgentSessions` distinguish two concurrent dispatches of the
   * SAME agent (e.g. orchestrator calling `task("recon", ...)` twice in
   * parallel) — before this field existed the grouping was keyed on
   * agent name and the second start silently collapsed the first
   * session's stream into the second.
   */
  sessionId?: string;
}
