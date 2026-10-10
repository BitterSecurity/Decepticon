#!/usr/bin/env node
/**
 * Terminal WebSocket Server — spawns Decepticon CLI in a PTY.
 *
 * Session-persistent architecture:
 *   PTY processes are keyed by engagement slug and survive WebSocket
 *   disconnects. When the browser reconnects (tab refresh, network blip,
 *   hotswap), it reattaches to the SAME PTY — no new CLI banner, no lost
 *   state, no [Reconnecting...] spam.
 *
 *   PTYs are only destroyed when:
 *     1. The CLI process itself exits (user typed Ctrl+C, engagement finished)
 *     2. No WebSocket reconnects within ORPHAN_TTL (60s) after disconnect
 *     3. The terminal server shuts down (SIGTERM)
 *
 * Protocol (Server → Client):
 *   - JSON { type: "threadId", threadId: "..." }
 *   - JSON { type: "pong" }
 *   - JSON { type: "reattached", scrollback: "..." } — sent on reattach with recent output
 *   - Raw text — PTY stdout/stderr
 *
 * Protocol (Client → Server):
 *   - JSON { type: "resize", cols, rows }
 *   - JSON { type: "ping" }
 *   - Raw text — stdin for PTY
 */

import { WebSocketServer, WebSocket } from "ws";
import * as pty from "node-pty";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const PORT = parseInt(process.env.TERMINAL_PORT ?? "3003", 10);
const WEB_PORT = process.env.WEB_PORT ?? "3000";
const CLI_PATH = resolve(__dirname, "../../cli/src/index.tsx");
const LANGGRAPH_API_URL = process.env.LANGGRAPH_API_URL ?? "http://localhost:2024";
const ORPHAN_TTL = 60_000; // Kill orphaned PTYs after 60s with no WS
const SCROLLBACK_LIMIT = 50_000; // chars of recent output to buffer for reattach

const ALLOWED_ORIGINS = new Set(
  (process.env.TERMINAL_ALLOWED_ORIGINS ?? `http://localhost:${WEB_PORT},http://127.0.0.1:${WEB_PORT}`)
    .split(",")
    .map((o) => o.trim())
    .filter(Boolean),
);

const wss = new WebSocketServer({ port: PORT });
console.log(`[terminal-server] Listening on ws://localhost:${PORT}`);

// ── Session Pool ─────────────────────────────────────────────────

interface Session {
  key: string;
  term: pty.IPty;
  ws: WebSocket | null;         // currently-attached WS (null = orphaned)
  scrollback: string;           // ring buffer of recent output
  threadId: string;
  orphanTimer: ReturnType<typeof setTimeout> | null;
  dead: boolean;                // PTY exited
  exitCode: number | null;
}

const sessions = new Map<string, Session>();

function sessionKey(slug: string): string {
  return slug;
}

function appendScrollback(session: Session, data: string): void {
  session.scrollback += data;
  if (session.scrollback.length > SCROLLBACK_LIMIT) {
    const trimmed = session.scrollback.slice(-SCROLLBACK_LIMIT);
    // Resume at a line boundary so replay never starts mid escape sequence,
    // which would mis-color or swallow output on the client after term.reset().
    const nl = trimmed.indexOf("\n");
    session.scrollback = nl >= 0 && nl < trimmed.length - 1 ? trimmed.slice(nl + 1) : trimmed;
  }
}

function destroySession(key: string): void {
  const session = sessions.get(key);
  if (!session) return;
  if (session.orphanTimer) clearTimeout(session.orphanTimer);
  if (!session.dead) {
    try { session.term.kill(); } catch { /* already dead */ }
  }
  sessions.delete(key);
  console.log(`[terminal-server] Session ${key} destroyed`);
}

// ── Helpers ──────────────────────────────────────────────────────

function isAllowedOrigin(origin: string | undefined): boolean {
  if (!origin) return false;
  try { return ALLOWED_ORIGINS.has(new URL(origin).origin); } catch { return false; }
}

async function createThread(engagementId: string, agentId: string): Promise<string> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  try {
    const res = await fetch(`${LANGGRAPH_API_URL}/threads`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ metadata: { engagement_id: engagementId, decepticon_assistant: agentId } }),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`Thread create: ${res.status}`);
    return ((await res.json()) as { thread_id: string }).thread_id;
  } finally {
    clearTimeout(timer);
  }
}

async function threadExists(threadId: string): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  try {
    const res = await fetch(
      `${LANGGRAPH_API_URL}/threads/${encodeURIComponent(threadId)}`,
      { signal: controller.signal },
    );
    if (res.status === 404) return false;
    if (!res.ok) throw new Error(`Thread read: ${res.status}`);
    return true;
  } finally {
    clearTimeout(timer);
  }
}

function sendJson(ws: WebSocket, payload: Record<string, unknown>): void {
  if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(payload));
}

function persistThreadId(engagementId: string, threadId: string): void {
  const webUrl = `http://localhost:${process.env.PORT ?? 3000}`;
  fetch(`${webUrl}/api/engagements/${engagementId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ threadId }),
  }).catch(() => {});
}

// ── Connection Handler ───────────────────────────────────────────

wss.on("connection", async (ws: WebSocket, req) => {
  if (!isAllowedOrigin(req.headers.origin)) {
    ws.close(1008, "Origin not allowed");
    return;
  }

  const url = new URL(req.url ?? "/", `http://localhost:${PORT}`);
  const engagementId = url.searchParams.get("engagementId") ?? "";
  const engagementSlug = url.searchParams.get("engagementSlug") ?? "";
  const agentId = url.searchParams.get("agentId") ?? "soundwave";
  const targetType = url.searchParams.get("targetType") ?? "";
  const targetValue = url.searchParams.get("targetValue") ?? "";
  const authorizationConfirmed = url.searchParams.get("authorizationConfirmed") === "true";
  if (!engagementSlug) {
    ws.close(1008, "Missing engagementSlug");
    return;
  }

  const key = sessionKey(engagementSlug);
  let session = sessions.get(key);

  // ── Reattach to existing session ──
  if (session && !session.dead) {
    console.log(`[terminal-server] Reattaching WS to existing session: ${key} (pid=${session.term.pid})`);

    // Cancel orphan timer
    if (session.orphanTimer) {
      clearTimeout(session.orphanTimer);
      session.orphanTimer = null;
    }

    // Detach old WS if any. 4001 (app range) distinguishes "another connection
    // took over" from the genuine PTY-exit 1000 the client treats as session end.
    if (session.ws && session.ws !== ws && session.ws.readyState === WebSocket.OPEN) {
      session.ws.close(4001, "Replaced by new connection");
    }
    session.ws = ws;

    // Send threadId
    if (session.threadId) sendJson(ws, { type: "threadId", threadId: session.threadId });

    // Send scrollback so the client sees recent output without a full re-render
    if (session.scrollback) {
      sendJson(ws, { type: "reattached" });
      ws.send(session.scrollback);
    }

    wireWsToSession(ws, session);
    return;
  }

  // ── Clean up dead session ──
  if (session?.dead) {
    destroySession(key);
    session = undefined;
  }

  // ── Create new session ──
  let threadId = url.searchParams.get("threadId") ?? "";
  if (threadId) {
    try {
      if (!(await threadExists(threadId))) threadId = "";
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      sendJson(ws, { type: "error", message: `Thread validation failed: ${message}` });
      ws.close(1011, "Thread validation failed");
      return;
    }
  }
  if (!threadId) {
    try {
      threadId = await createThread(engagementId, agentId);
      console.log(`[terminal-server] Created thread: ${threadId}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error(`[terminal-server] Thread creation failed: ${msg}`);
      sendJson(ws, { type: "error", message: `Thread creation failed: ${msg}` });
      ws.close(1011, "Thread creation failed");
      return;
    }
  }
  if (threadId) {
    sendJson(ws, { type: "threadId", threadId });
    if (engagementId) persistThreadId(engagementId, threadId);
  }

  const env: Record<string, string> = {
    ...process.env as Record<string, string>,
    TERM: "xterm-256color",
    FORCE_COLOR: "1",
    DECEPTICON_ASSISTANT_ID: agentId,
    DECEPTICON_ENGAGEMENT: engagementSlug,
    DECEPTICON_WORKSPACE_PATH: engagementSlug ? `/workspace/${engagementSlug}` : "/workspace",
    DECEPTICON_API_URL: LANGGRAPH_API_URL,
    DECEPTICON_TARGET_TYPE: targetType,
    DECEPTICON_TARGET: targetValue,
    DECEPTICON_AUTHORIZATION_CONFIRMED: authorizationConfirmed ? "true" : "false",
  };
  if (threadId) env.DECEPTICON_THREAD_ID = threadId;

  let term: pty.IPty;
  try {
    term = pty.spawn("node", ["--import", "tsx/esm", CLI_PATH], {
      name: "xterm-256color",
      cols: 120,
      rows: 30,
      cwd: resolve(__dirname, "../.."),
      env,
    });
    console.log(`[terminal-server] PTY spawned: ${key} pid=${term.pid}`);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`[terminal-server] PTY spawn failed: ${msg}`);
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(`\r\n\x1b[31m[Error: ${msg}]\x1b[0m\r\n`);
    }
    ws.close(1011, "PTY spawn failed");
    return;
  }

  const newSession: Session = {
    key,
    term,
    ws,
    scrollback: "",
    threadId,
    orphanTimer: null,
    dead: false,
    exitCode: null,
  };
  sessions.set(key, newSession);

  // PTY → buffer + current WS
  term.onData((data: string) => {
    appendScrollback(newSession, data);
    if (newSession.ws?.readyState === WebSocket.OPEN) {
      newSession.ws.send(data);
    }
  });

  // PTY exit
  term.onExit(({ exitCode, signal }) => {
    console.log(`[terminal-server] PTY exited: ${key} pid=${term.pid} code=${exitCode} signal=${signal}`);
    newSession.dead = true;
    newSession.exitCode = exitCode;
    if (newSession.ws?.readyState === WebSocket.OPEN) {
      if (exitCode === 0) {
        newSession.ws.send(`\r\n\x1b[32m[Session completed]\x1b[0m\r\n`);
      } else {
        newSession.ws.send(`\r\n\x1b[33m[Process exited: code ${exitCode}${signal ? `, signal ${signal}` : ""}]\x1b[0m\r\n`);
      }
      newSession.ws.close(1000, "PTY exited");
    }
    // Don't destroy immediately — let reattach see the exit message. Guard on
    // session identity so a reconnect that respawns a fresh PTY under the same
    // key within the window isn't killed by this stale timer.
    setTimeout(() => {
      if (sessions.get(key) === newSession) destroySession(key);
    }, 5000);
  });

  wireWsToSession(ws, newSession);
});

// ── Wire a WebSocket to a Session ────────────────────────────────

function wireWsToSession(ws: WebSocket, session: Session): void {
  ws.on("message", (raw: Buffer | string) => {
    const msg = raw.toString();
    try {
      const parsed = JSON.parse(msg);
      if (parsed.type === "ping") {
        sendJson(ws, { type: "pong" });
        return;
      }
      if (parsed.type === "resize" && parsed.cols && parsed.rows) {
        try {
          session.term.resize(
            Math.max(1, Math.min(500, parsed.cols)),
            Math.max(1, Math.min(200, parsed.rows)),
          );
        } catch { /* PTY may have exited */ }
        return;
      }
    } catch {
      // Not JSON — raw stdin
    }
    if (!session.dead) {
      try { session.term.write(msg); } catch { /* PTY exited */ }
    }
  });

  ws.on("close", () => {
    console.log(`[terminal-server] WS disconnected from session ${session.key}`);
    if (session.ws === ws) {
      session.ws = null;
      // Don't kill PTY — start orphan timer instead
      if (!session.dead) {
        session.orphanTimer = setTimeout(() => {
          if (!session.ws && !session.dead) {
            console.log(`[terminal-server] Orphan TTL expired for ${session.key} — destroying`);
            destroySession(session.key);
          }
        }, ORPHAN_TTL);
      }
    }
  });

  ws.on("error", (err) => {
    console.error(`[terminal-server] WS error on ${session.key}: ${err.message}`);
    // Don't kill PTY — let the close handler start orphan timer
  });
}

// ── Server lifecycle ─────────────────────────────────────────────

wss.on("error", (err) => {
  console.error(`[terminal-server] Server error: ${err.message}`);
});

function shutdown() {
  console.log(`[terminal-server] Shutting down, destroying ${sessions.size} sessions...`);
  for (const key of [...sessions.keys()]) destroySession(key);
  wss.close();
  process.exit(0);
}

process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);

process.on("uncaughtException", (err) => {
  console.error(`[terminal-server] Uncaught: ${err.message}`);
  console.error(err.stack);
});

process.on("unhandledRejection", (reason) => {
  console.error(`[terminal-server] Unhandled rejection:`, reason);
});
