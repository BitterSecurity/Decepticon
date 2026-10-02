/**
 * REPL — Main screen with dual prompt/transcript mode.
 *
 * - Prompt mode (default): compact view with collapsed sub-agent sessions
 * - Transcript mode (ctrl+o): full expanded view of all events
 */

import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Box, Text, Static, useApp } from "ink";
import { useAgent } from "../hooks/useAgent.js";
import { useBlueNotifications } from "../hooks/useBlueNotifications.js";
import { useOpplan } from "../hooks/useOpplan.js";
import { useSubAgentSessions } from "../hooks/useSubAgentSessions.js";
import { useGlobalKeybindings } from "../hooks/useGlobalKeybindings.js";
import { useAppState } from "../state/AppState.js";
import { Banner } from "../components/Banner.js";
import { EventItem } from "../components/EventItem.js";
import { ActivityIndicator } from "../components/ActivityIndicator.js";
import { OpplanStatus } from "../components/OpplanStatus.js";
import { Prompt } from "../components/Prompt.js";
import { QuestionPicker } from "../components/QuestionPicker.js";
import { AgentSessionGroup } from "../components/agents/AgentSessionGroup.js";
import { CoordinatorPanel } from "../components/agents/CoordinatorPanel.js";
import { ScreenProvider } from "../components/shell/ScreenContext.js";
import { ExpandOutputProvider } from "../components/shell/ExpandOutputContext.js";
import { SubAgentProvider } from "../components/shell/SubAgentContext.js";
import { parseSlashCommand, findCommand } from "../commands/registry.js";
import { groupConsecutiveTools } from "../utils/groupEvents.js";
import { formatDuration } from "../utils/format.js";
import { GLYPH_DOT, GLYPH_HOOK, GLYPH_SEP, AGENT_COLORS } from "../utils/theme.js";
import { ToolGroupSummary } from "../components/messages/ToolGroupSummary.js";
import type { CommandContext, CommandResult } from "../commands/types.js";
import type { AgentEvent, ScreenMode, SubAgentSession } from "../types.js";
import { ErrorMessage } from "../components/messages/ErrorMessage.js";
import { SessionPicker } from "../components/SessionPicker.js";
import { listThreads } from "../utils/threadStore.js";
import type { ThreadEntry } from "../utils/threadStore.js";
import type { ToolGroup } from "../utils/groupEvents.js";

export type Screen = ScreenMode;

interface REPLProps {
  initialMessage?: string;
  resumeThread?: boolean;
}

export function REPL({ initialMessage, resumeThread }: REPLProps) {
  const { exit } = useApp();
  const agent = useAgent({ resumeThread });
  useBlueNotifications(agent.addSystemEvent);
  const opplan = useOpplan(agent.events);
  const sessions = useSubAgentSessions(agent.events);
  const screen = useAppState((s) => s.screen);
  const [showSessionPicker, setShowSessionPicker] = useState(false);
  const [pickerSessions, setPickerSessions] = useState<ThreadEntry[]>([]);
  // Increment to force <Static> re-mount on session switch (resets its internal render history)
  const [sessionKey, setSessionKey] = useState(0);

  // Auto-submit initial message when DECEPTICON_INITIAL_MESSAGE env is set
  const autoStarted = useRef(false);
  useEffect(() => {
    if (!initialMessage || autoStarted.current) return;
    autoStarted.current = true;
    const timer = setTimeout(() => agent.submit(initialMessage), 200);

    return () => clearTimeout(timer);
  }, []);

  // ── Global keybindings ──────────────────────────────────────────
  useGlobalKeybindings({
    onInterrupt: agent.interrupt,
    onCancel: agent.cancel,
    onExit: exit,
    onClearQueue: agent.clearQueuedMessage,
    addSystemEvent: agent.addSystemEvent,
    runState: agent.runState,
    hasQueuedMessage: agent.queuedMessage != null,
    modalActive: showSessionPicker || agent.activeQuestion != null,
  });

  // ── Command handling ────────────────────────────────────────────
  const commandContext = useMemo<CommandContext>(
    () => ({
      addSystemEvent: agent.addSystemEvent,
      clearEvents: agent.clearEvents,
      submit: agent.submit,
      resume: agent.resume,
      exit,
    }),
    [agent.addSystemEvent, agent.clearEvents, agent.submit, agent.resume, exit],
  );

  const handleSubmit = useCallback(
    (input: string) => {
      const trimmed = input.trim();
      if (!trimmed) return;

      // Slash commands always execute immediately (even during streaming)
      const parsed = parseSlashCommand(trimmed);
      if (parsed) {
        // /resume with no args → open interactive session picker
        if ((parsed.name === "resume" || parsed.name === "r") && !parsed.args) {
          // If paused, resume from checkpoint directly
          if (agent.runState === "paused") {
            agent.resume();
            return;
          }
          // Otherwise show session picker (async)
          listThreads().then((savedSessions) => {
            if (savedSessions.length === 0) {
              agent.addSystemEvent("No previous sessions found.");
              return;
            }
            setPickerSessions(savedSessions);
            setShowSessionPicker(true);
          }).catch(() => {
            agent.addSystemEvent("Failed to load sessions.");
          });
          return;
        }

        const cmd = findCommand(parsed.name);
        if (cmd) {
          const result = cmd.execute(parsed.args, commandContext);
          if (result && typeof (result as Promise<unknown>).then === "function") {
            (result as Promise<CommandResult | void>).then((r) => {
              if (r?.shouldSubmit) agent.submit(parsed.args);
            });
          } else if ((result as CommandResult | void)?.shouldSubmit) {
            agent.submit(parsed.args);
          }
          return;
        }
        // Unknown command — show error
        agent.addSystemEvent(`Unknown command: /${parsed.name}. Type /help for available commands.`);
        return;
      }

      // If streaming/connecting → queue; if idle/paused → submit
      if (agent.runState === "streaming" || agent.runState === "connecting") {
        agent.enqueue(trimmed);
      } else {
        agent.submit(trimmed);
      }
    },
    [agent, commandContext],
  );

  // ── Derive prompt-mode items ────────────────────────────────────
  // Separate events into "main" (not part of any session) and session groups
  const { mainEvents, completedSessions } = useMemo(() => {
    const sessionEventIds = new Set(sessions.flatMap((s) => s.eventIds));
    return {
      mainEvents: agent.events.filter(
        (e) =>
          !sessionEventIds.has(e.id) &&
          e.type !== "subagent_start" &&
          e.type !== "subagent_end",
      ),
      completedSessions: sessions.filter((s) => s.status !== "running"),
    };
  }, [agent.events, sessions]);

  // Build static items: banner + grouped main events + completed sessions
  const staticItems = useMemo(() => {
    type Item =
      | { id: "__banner__"; kind: "banner"; ts: number }
      | { id: string; kind: "event"; event: AgentEvent; ts: number }
      | { id: string; kind: "group"; group: ToolGroup; ts: number }
      | { id: string; kind: "session"; sessionIdx: number; ts: number };

    const items: Item[] = [{ id: "__banner__", kind: "banner", ts: 0 }];

    // Apply tool grouping to main events (consecutive read/search → summary)
    const grouped = groupConsecutiveTools(mainEvents);
    for (const g of grouped) {
      if (g.kind === "group") {
        items.push({ id: g.group.id, kind: "group", group: g.group, ts: g.group.timestamp });
      } else {
        items.push({ id: g.event.id, kind: "event", event: g.event, ts: g.event.timestamp });
      }
    }

    for (let i = 0; i < completedSessions.length; i++) {
      const s = completedSessions[i]!;
      items.push({
        id: `session-${s.id}`,
        kind: "session",
        sessionIdx: i,
        ts: s.startTime,
      });
    }

    // Sort by timestamp (banner stays first with ts=0)
    items.sort((a, b) => a.ts - b.ts);
    return items;
  }, [mainEvents, completedSessions]);

  // ID of the most recent bash_result — gets expanded in prompt mode
  const lastBashEventId = useMemo(() => {
    for (let i = mainEvents.length - 1; i >= 0; i--) {
      if (mainEvents[i]!.type === "bash_result") return mainEvents[i]!.id;
    }
    return null;
  }, [mainEvents]);

  // ── TRANSCRIPT MODE ─────────────────────────────────────────────
  if (screen === "transcript") {
    return (
      <ScreenProvider value="transcript">
        <Box flexDirection="row">
          <Box flexDirection="column" flexGrow={1}>
            <TranscriptView
              events={agent.events}
              sessions={sessions}
            />
          </Box>
        </Box>
      </ScreenProvider>
    );
  }

  // ── PROMPT MODE ─────────────────────────────────────────────────
  return (
    <ScreenProvider value="prompt">
      <Box flexDirection="row">
        <Box flexDirection="column" flexGrow={1}>
          {/* Static region: banner + completed events + completed sessions */}
          <Static key={sessionKey} items={staticItems}>
            {(item) => (
              <Box key={item.id}>
                {item.kind === "banner" ? (
                  <Banner />
                ) : item.kind === "session" ? (
                  <AgentSessionGroup
                    session={completedSessions[item.sessionIdx]!}
                    events={agent.events}
                    screen="prompt"
                    isLast={true}
                  />
                ) : item.kind === "group" ? (
                  <ToolGroupSummary group={item.group} />
                ) : (
                  <ExpandOutputProvider value={item.event.id === lastBashEventId}>
                    <EventItem event={item.event} />
                  </ExpandOutputProvider>
                )}
              </Box>
            )}
          </Static>

          {/* Dynamic region: coordinator panel (running + recently completed agents) */}
          {sessions.length > 0 && (
            <Box marginTop={1}>
              <CoordinatorPanel
                sessions={sessions}
                events={agent.events}
              />
            </Box>
          )}

          <ActivityIndicator
            runState={agent.runState}
            streamStats={agent.streamStats}
          />

          {/* Persistent OPPLAN display */}
          {opplan && opplan.objectives.length > 0 && (
            <OpplanStatus opplan={opplan} />
          )}

          {agent.error && <ErrorMessage content={agent.error} />}

          {/* No global "(ctrl+o to expand)" hint here: the Prompt footer
              already shows "ctrl+o: expand" persistently while idle, and
              truncated/collapsed items (BashResult, ToolGroupSummary, …)
              render their own hint inline. A blanket hint below the Static
              region duplicated whichever hint the last item already showed,
              producing two identical "(ctrl+o to expand)" lines. */}

          {showSessionPicker ? (
            <SessionPicker
              sessions={pickerSessions}
              onSelect={(session: ThreadEntry) => {
                setShowSessionPicker(false);
                // Clear terminal + force <Static> re-mount so previous session
                // output is fully removed (Static is append-only internally).
                process.stdout.write("\x1B[2J\x1B[H");
                setSessionKey((k) => k + 1);
                agent.resume(session.threadId);
              }}
              onCancel={() => setShowSessionPicker(false)}
            />
          ) : agent.activeQuestion ? (
            <QuestionPicker
              question={agent.activeQuestion}
              onSubmit={agent.answerQuestion}
              onCancel={agent.cancel}
            />
          ) : (
            <Prompt
              runState={agent.runState}
              onSubmit={handleSubmit}
              activeAgent={agent.activeAgent}
              assistantId={agent.assistantId}
              queuedMessage={agent.queuedMessage}
              onEditQueue={agent.enqueue}
            />
          )}
        </Box>

      </Box>
    </ScreenProvider>
  );
}

// ── Transcript View ───────────────────────────────────────────────

// ── Transcript helpers (Static-only rendering) ──────────────────

function TranscriptSessionHeader({ session }: { session: SubAgentSession }) {
  const color = AGENT_COLORS[session.agent] ?? "white";
  const headerDesc = session.description.split("\n")[0] ?? session.description;

  return (
    <Box flexDirection="column" marginTop={1}>
      <Text>
        <Text color="gray">{`${GLYPH_DOT} `}</Text>
        <Text bold color={color}>
          {session.agent.charAt(0).toUpperCase() + session.agent.slice(1)}
        </Text>
        <Text dimColor italic>{`(${headerDesc})`}</Text>
      </Text>
      <Text dimColor>{`  ${GLYPH_HOOK}  Prompt:`}</Text>
      {session.description.split("\n").map((line: string, i: number) => (
        <Text key={`p${i}`} dimColor wrap="wrap">
          {`       ${line}`}
        </Text>
      ))}
    </Box>
  );
}

function TranscriptSessionFooter({ session }: { session: SubAgentSession }) {
  const elapsed = formatDuration((session.endTime ?? Date.now()) - session.startTime);
  const toolText = `${session.toolCount} tool use${session.toolCount !== 1 ? "s" : ""}`;
  const dotColor = session.status === "error" ? "red" : "green";

  return (
    <Box flexDirection="column">
      <Text>
        <Text color={dotColor}>{`${GLYPH_DOT} `}</Text>
        <Text dimColor>{`Done (${toolText}${GLYPH_SEP}${elapsed})`}</Text>
      </Text>
    </Box>
  );
}

/** Live-ticking footer for running sessions — the ONLY dynamic element. */
function RunningSessionFooter({ session }: { session: SubAgentSession }) {
  const [now, setNow] = React.useState(Date.now());
  React.useEffect(() => {
    const interval = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(interval);
  }, []);

  const elapsed = formatDuration(now - session.startTime);
  const toolText = `${session.toolCount} tool use${session.toolCount !== 1 ? "s" : ""}`;
  const color = AGENT_COLORS[session.agent] ?? "white";

  return (
    <Text dimColor italic>
      {"  "}{GLYPH_HOOK}{"  "}
      <Text color={color}>
        {session.agent.charAt(0).toUpperCase() + session.agent.slice(1)}
      </Text>
      {` running (${toolText}${GLYPH_SEP}${elapsed})`}
    </Text>
  );
}

// ── Transcript View ───────────────────────────────────────────────

function TranscriptView({
  events,
  sessions,
}: {
  events: AgentEvent[];
  sessions: ReturnType<typeof useSubAgentSessions>;
}) {
  // Lookup maps
  const sessionEventIds = useMemo(
    () => new Set(sessions.flatMap((s) => s.eventIds)),
    [sessions],
  );
  const sessionByStartId = useMemo(() => {
    const map = new Map<string, (typeof sessions)[number]>();
    for (const s of sessions) map.set(s.startEventId, s);
    return map;
  }, [sessions]);
  const sessionByEndId = useMemo(() => {
    const map = new Map<string, (typeof sessions)[number]>();
    for (const s of sessions) {
      if (s.endEventId) map.set(s.endEventId, s);
    }
    return map;
  }, [sessions]);

  const runningSessions = useMemo(
    () => sessions.filter((s) => s.status === "running"),
    [sessions],
  );

  // ALL events → Static items (append-only, no scroll disruption)
  const staticItems = useMemo(() => {
    type Item =
      | { id: string; kind: "header" }
      | { id: string; kind: "event"; event: AgentEvent }
      | { id: string; kind: "session-header"; session: SubAgentSession }
      | { id: string; kind: "session-event"; event: AgentEvent }
      | { id: string; kind: "session-footer"; session: SubAgentSession };

    const items: Item[] = [{ id: "__transcript-header__", kind: "header" }];

    for (const event of events) {
      // Session start → header
      const startedSession = sessionByStartId.get(event.id);
      if (startedSession) {
        items.push({ id: `sh-${startedSession.id}`, kind: "session-header", session: startedSession });
        continue;
      }

      // Session end → footer
      const endedSession = sessionByEndId.get(event.id);
      if (endedSession) {
        items.push({ id: `sf-${endedSession.id}`, kind: "session-footer", session: endedSession });
        continue;
      }

      // Inner session event (skip raw subagent_start/end)
      if (sessionEventIds.has(event.id)) {
        if (event.type === "subagent_start" || event.type === "subagent_end") continue;
        items.push({ id: event.id, kind: "session-event", event });
        continue;
      }

      // Regular event
      items.push({ id: event.id, kind: "event", event });
    }
    return items;
  }, [events, sessions, sessionEventIds, sessionByStartId, sessionByEndId]);

  return (
    <Box flexDirection="column">
      <Static items={staticItems}>
        {(item) => (
          <Box key={item.id}>
            {item.kind === "header" ? (
              <Box marginBottom={1}>
                <Text bold inverse>{" TRANSCRIPT "}</Text>
                <Text dimColor>{" ctrl+o or Esc to close"}</Text>
              </Box>
            ) : item.kind === "session-header" ? (
              <TranscriptSessionHeader session={item.session} />
            ) : item.kind === "session-footer" ? (
              <TranscriptSessionFooter session={item.session} />
            ) : item.kind === "session-event" ? (
              <Box marginLeft={3}>
                <SubAgentProvider value={true}>
                  <EventItem event={item.event} />
                </SubAgentProvider>
              </Box>
            ) : (
              <EventItem event={item.event} />
            )}
          </Box>
        )}
      </Static>

      {/* Minimal dynamic region: only live-ticking footers for running sessions */}
      {runningSessions.map((s) => (
        <RunningSessionFooter key={s.id} session={s} />
      ))}
    </Box>
  );
}
