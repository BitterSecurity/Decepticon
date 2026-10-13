/** Exercise terminal startup against controlled socket, process, network, and clock boundaries. */
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { readFileSync } from "node:fs";
import ts from "typescript";
import { createHash } from "node:crypto";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import vm from "node:vm";

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let complete!: (value: T) => void;
  const promise = new Promise<T>((resolve) => { complete = resolve; });
  return { promise, resolve: complete };
}

class Socket extends EventEmitter {
  static OPEN = 1;
  readyState = Socket.OPEN;
  messages: string[] = [];
  closeCode: number | null = null;
  send(message: string): void {
    assert.equal(this.readyState, Socket.OPEN, "must never send on a closed socket");
    this.messages.push(message);
  }
  close(code = 1000): void {
    this.closeCode = code;
    this.readyState = 3;
    this.emit("close");
  }
}

type Response = { status: number; ok: boolean; json: () => Promise<{ thread_id: string }> };
type Request = { method?: string; signal?: AbortSignal };
function response(status: number, threadId = "created"): Response {
  return { status, ok: status >= 200 && status < 300, json: async () => ({ thread_id: threadId }) };
}

function server(options: {
  fetch?: (url: string, request: Request) => Promise<Response>;
  scope?: Promise<Buffer>;
} = {}) {
  const calls: { url: string; method: string }[] = [];
  const terms: { threadId: string; killed: boolean }[] = [];
  let handler!: (socket: Socket, request: { headers: { origin: string }; url: string }) => Promise<void>;
  let now = 0;
  let timerId = 0;
  const timers = new Map<number, { due: number; callback: () => void }>();
  const sourceUrl = new URL("../../server/terminal-server.ts", import.meta.url);
  const source = readFileSync(sourceUrl, "utf8")
    .replace(/^import .*;\r?$/gm, "")
    .replaceAll("import.meta.url", JSON.stringify(sourceUrl.href));
  vm.runInNewContext(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText, {
    WebSocket: Socket,
    WebSocketServer: class {
      on(event: string, callback: typeof handler): void {
        if (event === "connection") handler = callback;
      }
      close(): void {}
    },
    pty: { spawn(_command: string, _args: string[], config: { env: Record<string, string> }) {
      const term = { threadId: config.env.DECEPTICON_THREAD_ID, killed: false };
      terms.push(term);
      return {
        pid: terms.length, onData(): void {}, onExit(): void {},
        kill(): void { term.killed = true; },
      };
    } },
    resolve, dirname, join, fileURLToPath, createHash,
    readFile: async () => {
      if (options.scope) return options.scope;
      throw new Error("No approval documents");
    },
    fetch: async (url: string, request: Request = {}) => {
      calls.push({ url, method: request.method ?? "GET" });
      if (request.method === "PATCH") return response(200);
      return options.fetch ? options.fetch(url, request) : response(200);
    },
    process: { env: {}, on(): void {}, exit(): never { throw new Error("Unexpected exit"); } },
    console: { log(): void {}, error(): void {} },
    URL, Buffer, AbortController,
    setTimeout(callback: () => void, delay: number): number {
      const id = ++timerId;
      timers.set(id, { due: now + delay, callback });
      return id;
    },
    clearTimeout(id: number): void { timers.delete(id); },
  });
  assert.equal(typeof handler, "function");
  return {
    calls, terms, timers,
    connect(socket: Socket, threadId = "existing"): Promise<void> {
      return handler(socket, {
        headers: { origin: "http://localhost:3000" },
        url: `/terminal?engagementSlug=owned-case&engagementId=owned&agentId=soundwave&threadId=${threadId}`,
      });
    },
    advance(milliseconds: number): void {
      now += milliseconds;
      for (const [id, timer] of timers) {
        if (timer.due <= now) { timers.delete(id); timer.callback(); }
      }
    },
  };
}

for (const state of [2, 3]) {
  test(`socket state ${state} after scope read prevents startup`, async () => {
    const scope = deferred<Buffer>();
    const fixture = server({ scope: scope.promise });
    const socket = new Socket();
    const pending = fixture.connect(socket, "");
    socket.readyState = state;
    socket.emit("close");
    scope.resolve(Buffer.from("{}"));
    await pending;
    assert.deepEqual({ calls: fixture.calls, terms: fixture.terms }, { calls: [], terms: [] });
  });
}

test("disconnect during thread creation prevents persistence and PTY spawn", async () => {
  const entered = deferred<void>();
  const creation = deferred<Response>();
  const fixture = server({ fetch: async () => { entered.resolve(); return creation.promise; } });
  const socket = new Socket();
  const pending = fixture.connect(socket, "");
  await entered.promise;
  socket.close();
  creation.resolve(response(200));
  await pending;
  assert.deepEqual({ methods: fixture.calls.map((call) => call.method), terms: fixture.terms },
    { methods: ["POST"], terms: [] });
});

test("closed older startup cannot overwrite a reconnect session", async () => {
  const entered = deferred<void>();
  const creation = deferred<Response>();
  let creates = 0;
  const fixture = server({ fetch: async () => {
    if (++creates === 1) { entered.resolve(); return creation.promise; }
    return response(200, "reconnected");
  } });
  const oldSocket = new Socket();
  const pending = fixture.connect(oldSocket, "");
  await entered.promise;
  oldSocket.close();
  const currentSocket = new Socket();
  await fixture.connect(currentSocket, "");
  creation.resolve(response(200, "stale"));
  await pending;
  currentSocket.close();
  fixture.advance(60_000);
  assert.deepEqual(fixture.terms, [{ threadId: "reconnected", killed: true }]);
});

test("disconnect after startup retains the PTY until orphan expiry", async () => {
  const fixture = server();
  const socket = new Socket();
  await fixture.connect(socket, "");
  socket.close();
  fixture.advance(59_999);
  assert.equal(fixture.terms[0].killed, false);
  fixture.advance(1);
  assert.equal(fixture.terms[0].killed, true);
});

test("closed scope read cannot displace an already attached socket", async () => {
  const scope = deferred<Buffer>();
  const fixture = server({ scope: scope.promise });
  scope.resolve(Buffer.from("{}"));
  const attached = new Socket();
  await fixture.connect(attached, "");
  const stale = new Socket();
  const pending = fixture.connect(stale, "");
  stale.close();
  await pending;
  assert.equal(attached.readyState, Socket.OPEN);
});
