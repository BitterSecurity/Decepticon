"use client";

/**
 * WebTerminal — xterm.js terminal embedding the Decepticon CLI.
 *
 * Connects to the standalone terminal WebSocket server which spawns
 * the CLI in a PTY. Reports the thread ID back to the parent via callback.
 *
 * Reconnection strategy:
 * - Silent reconnect with exponential backoff (1s → 2s → 4s, cap 4s)
 * - No terminal spam during reconnect — only a single status line
 * - Status bar shows connection state at all times
 * - After successful reconnect, clears the status message
 * - After 15 failed attempts, stops and offers manual retry
 */

import { useEffect, useRef, useCallback, useState } from "react";
import { cn } from "@/lib/utils";

const TERMINAL_WS_URL = process.env.NEXT_PUBLIC_TERMINAL_WS_URL ?? "ws://localhost:3003";
const MAX_RECONNECT_DELAY = 4000;
const INITIAL_RECONNECT_DELAY = 1000;
const MAX_RECONNECT_ATTEMPTS = 15;

type ConnectionState = "connecting" | "connected" | "reconnecting" | "disconnected" | "error";

/**
 * Strip parser-hostile bytes before writing to xterm.
 *
 * The PTY occasionally emits a lone ``\x7f`` (DEL) while the parser is in
 * ground state. xterm.js' VT parser then fires
 * ``Parsing error code=127`` for every offending byte, flooding the dev
 * console with thousands of identical errors after the agent banner.
 * DEL has no defined semantic in modern terminals; lone NUL is similarly
 * spurious. Dropping both removes the noise without losing real output.
 */
function sanitizeTermBytes(s: string): string {
  if (!s) return s;
  return s.replace(/[\x00\x7f]/g, "");
}

interface WebTerminalProps {
  engagementId: string;
  engagementSlug: string;
  targetType: string;
  targetValue: string;
  authorizationConfirmed: boolean;
  agentId?: string;
  threadId?: string;
  className?: string;
  onThreadId?: (threadId: string) => void;
}

export function WebTerminal({
  engagementId,
  engagementSlug,
  targetType,
  targetValue,
  authorizationConfirmed,
  agentId = "soundwave",
  threadId,
  className,
  onThreadId,
}: WebTerminalProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [connState, setConnState] = useState<ConnectionState>("connecting");

  const engagementIdRef = useRef(engagementId);
  engagementIdRef.current = engagementId;
  const engagementSlugRef = useRef(engagementSlug);
  engagementSlugRef.current = engagementSlug;
  const agentIdRef = useRef(agentId);
  agentIdRef.current = agentId;
  const threadIdRef = useRef(threadId);
  threadIdRef.current = threadId;
  const targetTypeRef = useRef(targetType);
  targetTypeRef.current = targetType;
  const targetValueRef = useRef(targetValue);
  targetValueRef.current = targetValue;
  const authorizationConfirmedRef = useRef(authorizationConfirmed);
  authorizationConfirmedRef.current = authorizationConfirmed;
  const onThreadIdRef = useRef(onThreadId);
  onThreadIdRef.current = onThreadId;

  const termRef = useRef<import("xterm").Terminal | null>(null);
  const fitRef = useRef<import("@xterm/addon-fit").FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptRef = useRef(0);
  const disposedRef = useRef(false);
  const resizeObserverRef = useRef<ResizeObserver | null>(null);
  const resizeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Track whether we've shown the reconnecting message (to avoid spam)
  const reconnectMsgShownRef = useRef(false);
  // True between sending a ping and receiving the next inbound frame; the
  // pong-timeout only force-closes the socket while this is still set.
  const awaitingPongRef = useRef(false);
  // Track the onData listener for manual retry so we can dispose it
  const retryListenerRef = useRef<{ dispose: () => void } | null>(null);
  // Track the main onData listener so we can dispose it on reconnect
  const onDataDisposableRef = useRef<{ dispose: () => void } | null>(null);
  // ── xterm write flow control ──
  // xterm throws "write data discarded, use flow control to avoid losing data"
  // when data is pushed into term.write() faster than its VT parser drains it —
  // e.g. a large scrollback replay on reattach or a burst of CLI output. Queue
  // inbound chunks and release the next only after xterm's write callback fires,
  // so its internal buffer holds at most one chunk and never overflows.
  const writeQueueRef = useRef<string[]>([]);
  const writingRef = useRef(false);

  // Feed queued output into xterm one chunk at a time, waiting for the parse
  // callback before releasing the next. This is xterm's recommended flow-control
  // pattern; our own JS array absorbs the backlog instead of xterm's buffer.
  const enqueueWrite = useCallback((data: string) => {
    if (!data) return;
    writeQueueRef.current.push(data);
    if (writingRef.current) return;
    writingRef.current = true;
    const pump = () => {
      const term = termRef.current;
      const chunk = writeQueueRef.current.shift();
      if (!term || chunk === undefined) {
        writingRef.current = false;
        return;
      }
      term.write(chunk, pump);
    };
    pump();
  }, []);

  const cleanup = useCallback(() => {
    disposedRef.current = true;
    if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
    if (resizeTimerRef.current) clearTimeout(resizeTimerRef.current);
    retryListenerRef.current?.dispose();
    retryListenerRef.current = null;
    onDataDisposableRef.current?.dispose();
    onDataDisposableRef.current = null;
    writeQueueRef.current = [];
    writingRef.current = false;
    resizeObserverRef.current?.disconnect();
    wsRef.current?.close();
    termRef.current?.dispose();
    wsRef.current = null;
    termRef.current = null;
    fitRef.current = null;
  }, []);

  const connectWs = useCallback(() => {
    if (disposedRef.current) return;
    const term = termRef.current;
    if (!term) return;

    // Dispose any pending manual-retry listener
    retryListenerRef.current?.dispose();
    retryListenerRef.current = null;

    // Close old WS
    if (wsRef.current && wsRef.current.readyState !== WebSocket.CLOSED) {
      wsRef.current.close();
    }

    const eid = engagementIdRef.current;
    const slug = engagementSlugRef.current;
    const aid = agentIdRef.current;
    const tid = threadIdRef.current;
    const targetType = targetTypeRef.current;
    const targetValue = targetValueRef.current;
    const authorizationConfirmed = authorizationConfirmedRef.current;

    let wsUrl =
      `${TERMINAL_WS_URL}?engagementId=${encodeURIComponent(eid)}` +
      `&engagementSlug=${encodeURIComponent(slug)}` +
      `&agentId=${encodeURIComponent(aid)}` +
      `&targetType=${encodeURIComponent(targetType)}` +
      `&targetValue=${encodeURIComponent(targetValue)}` +
      `&authorizationConfirmed=${authorizationConfirmed}`;
    if (tid) wsUrl += `&threadId=${encodeURIComponent(tid)}`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnState("connected");
      reconnectAttemptRef.current = 0;
      reconnectMsgShownRef.current = false;
      // Silent reconnect — no terminal output. If the server reattaches us
      // to an existing session, the scrollback replay handles visual continuity.
      if (term.cols > 0 && term.rows > 0) {
        ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
      }
    };

    ws.onmessage = (event) => {
      awaitingPongRef.current = false;
      const data = typeof event.data === "string" ? event.data : "";
      if (data.startsWith("{")) {
        try {
          const msg = JSON.parse(data);
          // Filter ALL control messages — never write JSON frames to the terminal
          if (msg.type === "threadId" && msg.threadId) {
            onThreadIdRef.current?.(msg.threadId);
            return;
          }
          if (msg.type === "pong" || msg.type === "error" || msg.type === "ping") {
            return; // Control message — consume silently
          }
          if (msg.type === "reattached") {
            // Server reattached us to an existing PTY — full reset then
            // scrollback replay arrives as raw text right after this message.
            // Drop any stale queued output so the replay starts from a clean slate.
            writeQueueRef.current = [];
            term.reset();
            return;
          }
        } catch {
          // Not valid JSON — pass through as terminal output
        }
      }
      enqueueWrite(sanitizeTermBytes(data));
    };

    ws.onclose = (ev) => {
      if (disposedRef.current) return;

      if (ev.code === 4001) {
        // Server handed this session to another connection (e.g. a second tab).
        // Go quiet without the "session ended" banner and without reconnecting,
        // which would only ping-pong the session back and forth.
        setConnState("disconnected");
        return;
      }

      if (ev.code === 1000) {
        // Clean close — process exited normally
        setConnState("disconnected");
        term.writeln("\r\n\x1b[90m[Session ended. Press Enter to start a new session.]\x1b[0m");
        const disposable = term.onData(() => {
          disposable.dispose();
          reconnectAttemptRef.current = 0;
          reconnectMsgShownRef.current = false;
          connectWs();
        });
        retryListenerRef.current = disposable;
        return;
      }

      // Abnormal close — reconnect silently
      scheduleReconnect();
    };

    ws.onerror = () => {
      if (disposedRef.current) return;
      // onerror always fires before onclose — just update the indicator
      setConnState("reconnecting");
    };

    // Forward input — dispose previous listener to prevent duplicates on reconnect
    onDataDisposableRef.current?.dispose();
    onDataDisposableRef.current = term.onData((data: string) => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(data);
      }
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const scheduleReconnect = useCallback(() => {
    if (disposedRef.current) return;

    const attempt = reconnectAttemptRef.current;

    if (attempt >= MAX_RECONNECT_ATTEMPTS) {
      setConnState("error");
      const term = termRef.current;
      if (term) {
        term.writeln("\r\n\x1b[31m[Connection failed. Press Enter to retry.]\x1b[0m");
        reconnectMsgShownRef.current = false;
        const disposable = term.onData(() => {
          disposable.dispose();
          reconnectAttemptRef.current = 0;
          connectWs();
        });
        retryListenerRef.current = disposable;
      }
      return;
    }

    // Show ONE reconnecting message on the first attempt only
    // No terminal output during reconnect — status bar shows state.
    // The server's session persistence means the PTY is still alive;
    // on reattach the scrollback replays seamlessly.
    reconnectMsgShownRef.current = true;

    setConnState("reconnecting");
    const delay = Math.min(INITIAL_RECONNECT_DELAY * Math.pow(2, attempt), MAX_RECONNECT_DELAY);
    reconnectAttemptRef.current = attempt + 1;

    reconnectTimerRef.current = setTimeout(() => {
      if (!disposedRef.current) connectWs();
    }, delay);
  }, [connectWs]);

  // Initialize terminal + first connection
  const init = useCallback(async () => {
    const container = containerRef.current;
    if (!container || disposedRef.current) return;

    try {
      const [{ Terminal }, { FitAddon }] = await Promise.all([
        import("xterm"),
        import("@xterm/addon-fit"),
      ]);
      await import("xterm/css/xterm.css");

      if (termRef.current) {
        // Already initialized — just reconnect WS
        connectWs();
        return;
      }

      const term = new Terminal({
        cursorBlink: true,
        cursorStyle: "bar",
        fontSize: 13,
        fontFamily: "'JetBrains Mono', 'IBM Plex Mono', 'Fira Code', monospace",
        theme: {
          background: "#0a0e14",
          foreground: "#d4d4d4",
          cursor: "#faa32c",
          selectionBackground: "#264f78",
          black: "#1e1e1e",
          red: "#f44747",
          green: "#6a9955",
          yellow: "#d7ba7d",
          blue: "#569cd6",
          magenta: "#c586c0",
          cyan: "#4ec9b0",
          white: "#d4d4d4",
          brightBlack: "#808080",
          brightRed: "#f44747",
          brightGreen: "#6a9955",
          brightYellow: "#d7ba7d",
          brightBlue: "#569cd6",
          brightMagenta: "#c586c0",
          brightCyan: "#4ec9b0",
          brightWhite: "#ffffff",
        },
        allowTransparency: true,
        scrollback: 10000,
      });

      const fit = new FitAddon();
      term.loadAddon(fit);
      term.open(container);
      if (container.clientWidth > 0 && container.clientHeight > 0) fit.fit();

      termRef.current = term;
      fitRef.current = fit;

      // Resize observer
      const resizeObserver = new ResizeObserver(() => {
        if (resizeTimerRef.current) clearTimeout(resizeTimerRef.current);
        resizeTimerRef.current = setTimeout(() => {
          const el = containerRef.current;
          if (!el || el.clientWidth === 0 || el.clientHeight === 0) return;
          try {
            fitRef.current?.fit();
            const ws = wsRef.current;
            const t = termRef.current;
            if (ws?.readyState === WebSocket.OPEN && t && t.cols > 0 && t.rows > 0) {
              ws.send(JSON.stringify({ type: "resize", cols: t.cols, rows: t.rows }));
            }
          } catch {
            // ignore
          }
        }, 150);
      });
      resizeObserver.observe(container);
      resizeObserverRef.current = resizeObserver;

      // Connect
      connectWs();
    } catch (err) {
      setConnState("error");
      console.error("[WebTerminal] Init failed:", err);
    }
  }, [connectWs]);

  useEffect(() => {
    disposedRef.current = false;
    init();
    return cleanup;
  }, [init, cleanup]);

  // ── Heartbeat: detect silently-dead sockets ──────────────────────
  // Ping every 15s. A live socket answers with a pong (or is already
  // streaming PTY output); both clear awaitingPongRef via onmessage. Only when
  // nothing came back within PONG_TIMEOUT is the socket half-open — close it
  // and let reconnect take over. Pongs are filtered in onmessage.
  useEffect(() => {
    const PING_INTERVAL = 15000;
    const PONG_TIMEOUT = 5000;
    let pongTimer: ReturnType<typeof setTimeout>;

    const pingTimer = setInterval(() => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      try {
        awaitingPongRef.current = true;
        ws.send(JSON.stringify({ type: "ping" }));
        clearTimeout(pongTimer);
        pongTimer = setTimeout(() => {
          if (awaitingPongRef.current && wsRef.current === ws && ws.readyState === WebSocket.OPEN) {
            ws.close();
          }
        }, PONG_TIMEOUT);
      } catch {
        ws.close();
      }
    }, PING_INTERVAL);

    return () => {
      clearInterval(pingTimer);
      clearTimeout(pongTimer);
    };
  }, [connState]);

  // ── Visibility: reconnect when tab becomes visible ───────────────
  // Browsers throttle/kill WS in background tabs. Reconnect on focus.
  useEffect(() => {
    const handler = () => {
      if (document.visibilityState === "visible") {
        const ws = wsRef.current;
        if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
          reconnectAttemptRef.current = 0;
          reconnectMsgShownRef.current = false;
          connectWs();
        }
      }
    };
    document.addEventListener("visibilitychange", handler);
    return () => document.removeEventListener("visibilitychange", handler);
  }, [connectWs]);

  // ── Network: reconnect when browser comes back online ────────────
  useEffect(() => {
    const handler = () => {
      const ws = wsRef.current;
      if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
        reconnectAttemptRef.current = 0;
        reconnectMsgShownRef.current = false;
        connectWs();
      }
    };
    window.addEventListener("online", handler);
    return () => window.removeEventListener("online", handler);
  }, [connectWs]);

  const statusColor: Record<ConnectionState, string> = {
    connecting: "bg-amber-400",
    connected: "bg-emerald-400",
    reconnecting: "bg-amber-400 animate-pulse",
    disconnected: "bg-zinc-500",
    error: "bg-red-400",
  };

  const statusLabel: Record<ConnectionState, string> = {
    connecting: "Connecting...",
    connected: "Connected",
    reconnecting: "Reconnecting...",
    disconnected: "Disconnected",
    error: "Connection Error",
  };

  return (
    <div className={cn("relative flex flex-col", className)}>
      {/* Status bar */}
      <div className="flex items-center gap-2 border-b border-white/[0.06] bg-[#0a0e14] px-3 py-1.5">
        <div className={cn("h-2 w-2 rounded-full", statusColor[connState])} />
        <span className="text-[11px] text-zinc-500">{statusLabel[connState]}</span>
        <span className="flex-1" />
        <span className="text-[10px] font-mono text-zinc-600">{engagementSlug}</span>
      </div>
      {/* Terminal container */}
      <div
        ref={containerRef}
        className="flex-1"
        style={{
          backgroundColor: "#0a0e14",
          padding: "8px",
          minHeight: 0,
        }}
      />
    </div>
  );
}
