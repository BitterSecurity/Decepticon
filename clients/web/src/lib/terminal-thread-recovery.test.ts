/** Exercise stale-thread startup recovery at the real handler boundary. */
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

type Response = { status: number; ok: boolean; json: () => Promise<unknown> };
type Request = { method?: string; signal?: AbortSignal; body?: string };
function response(status: number, threadId = "created"): Response {
  return { status, ok: status >= 200 && status < 300, json: async () => ({ thread_id: threadId }) };
}

function server(options: {
  fetch?: (url: string, request: Request) => Promise<Response>;
  scope?: Promise<Buffer>;
} = {}) {
  const calls: { url: string; method: string; body?: string }[] = [];
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
      calls.push({ url, method: request.method ?? "GET", body: request.body });
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

test("HTTP 200 keeps an existing thread without creating a replacement", async () => {
  const fixture = server();
  const socket = new Socket();
  await fixture.connect(socket);
  assert.deepEqual({ threads: fixture.terms.map((term) => term.threadId), methods: fixture.calls.map((call) => call.method) },
    { threads: ["existing"], methods: ["GET", "PATCH"] });
});

test("HTTP 404 creates and persists the replacement before starting the terminal", async () => {
  const fixture = server({ fetch: async (_url, request) => response(request.method === "POST" ? 200 : 404, "replacement") });
  const socket = new Socket();
  await fixture.connect(socket);
  assert.deepEqual({ threads: fixture.terms.map((term) => term.threadId), messages: socket.messages.map((message) => JSON.parse(message)),
    methods: fixture.calls.map((call) => call.method), persisted: fixture.calls.find((call) => call.method === "PATCH")?.body },
    { threads: ["replacement"], messages: [{ type: "threadId", threadId: "replacement" }],
      methods: ["GET", "POST", "PATCH"], persisted: JSON.stringify({ threadId: "replacement" }) });
});

for (const status of [401, 403, 500, 503]) {
  test(`validation HTTP ${status} closes without replacing, persisting, or spawning`, async () => {
    const fixture = server({ fetch: async () => response(status) });
    const socket = new Socket();
    await fixture.connect(socket);
    assert.deepEqual({ code: socket.closeCode, terms: fixture.terms, methods: fixture.calls.map((call) => call.method) },
      { code: 1011, terms: [], methods: ["GET"] });
  });
}

for (const existing of ["existing", ""]) {
  test(`creation HTTP 503 stops startup ${existing ? "after a missing stored thread" : "without a stored thread"}`, async () => {
    const fixture = server({ fetch: async (_url, request) => response(request.method === "POST" ? 503 : 404) });
    const socket = new Socket();
    await fixture.connect(socket, existing);
    assert.deepEqual({ code: socket.closeCode, terms: fixture.terms, methods: fixture.calls.map((call) => call.method) },
      { code: 1011, terms: [], methods: existing ? ["GET", "POST"] : ["POST"] });
  });
}

test("validation network failure closes without persisting a thread", async () => {
  const fixture = server({ fetch: async () => { throw new Error("Network unavailable"); } });
  const socket = new Socket();
  await fixture.connect(socket);
  assert.deepEqual({ code: socket.closeCode, terms: fixture.terms, methods: fixture.calls.map((call) => call.method) },
    { code: 1011, terms: [], methods: ["GET"] });
});

for (const threadId of ["", "   ", undefined, 42]) {
  test(`creation with unusable thread ID ${JSON.stringify(threadId)} fails before persistence`, async () => {
    const fixture = server({ fetch: async () => ({ ...response(200), json: async () => ({ thread_id: threadId }) }) });
    const socket = new Socket();
    await fixture.connect(socket, "");
    assert.deepEqual({ code: socket.closeCode, terms: fixture.terms, methods: fixture.calls.map((call) => call.method) },
      { code: 1011, terms: [], methods: ["POST"] });
  });
}

test("creation with invalid JSON stops before persistence and PTY spawn", async () => {
  const fixture = server({ fetch: async () => ({ ...response(200), json: async () => { throw new SyntaxError("Invalid JSON"); } }) });
  const socket = new Socket();
  await fixture.connect(socket, "");
  assert.deepEqual({ code: socket.closeCode, terms: fixture.terms, methods: fixture.calls.map((call) => call.method) },
    { code: 1011, terms: [], methods: ["POST"] });
});

for (const method of ["GET", "POST"]) {
  test(`${method} timeout aborts at ten seconds without persisting or spawning`, async () => {
    const entered = deferred<void>();
    let signal: AbortSignal | undefined;
    const fixture = server({ fetch: async (_url, request) => {
      signal = request.signal;
      entered.resolve();
      return new Promise<Response>((_resolve, reject) => {
        signal?.addEventListener("abort", () => reject(new Error("Request aborted")), { once: true });
      });
    } });
    const socket = new Socket();
    const pending = fixture.connect(socket, method === "GET" ? "existing" : "");
    await Promise.race([entered.promise, pending]);
    assert.ok(signal, "startup must issue a bounded backend request");
    fixture.advance(9999);
    assert.equal(signal.aborted, false);
    fixture.advance(1);
    await pending;
    assert.deepEqual({ aborted: signal.aborted, code: socket.closeCode, terms: fixture.terms,
      timers: fixture.timers.size, methods: fixture.calls.map((call) => call.method) },
      { aborted: true, code: 1011, terms: [], timers: 0, methods: [method] });
  });
}

for (const status of [200, 404]) {
  for (const state of [2, 3]) {
    test(`socket state ${state} during pending validation HTTP ${status} abandons startup`, async () => {
      const entered = deferred<void>();
      const validation = deferred<Response>();
      const fixture = server({ fetch: async () => { entered.resolve(); return validation.promise; } });
      const socket = new Socket();
      const pending = fixture.connect(socket);
      await Promise.race([entered.promise, pending]);
      assert.equal(fixture.calls[0]?.method, "GET");
      socket.readyState = state;
      socket.emit("close");
      validation.resolve(response(status));
      await pending;
      assert.deepEqual({ terms: fixture.terms, methods: fixture.calls.map((call) => call.method), timers: fixture.timers.size },
        { terms: [], methods: ["GET"], timers: 0 });
    });
  }
}

test("a reconnect reuses its live session without validating the stale supplied ID", async () => {
  const fixture = server();
  const first = new Socket();
  await fixture.connect(first, "");
  first.close();
  const reconnect = new Socket();
  await fixture.connect(reconnect, "stale");
  fixture.advance(60_000);
  assert.deepEqual({ terms: fixture.terms, methods: fixture.calls.map((call) => call.method),
    messages: reconnect.messages.map((message) => JSON.parse(message)) },
    { terms: [{ threadId: "created", killed: false }], methods: ["POST", "PATCH"],
      messages: [{ type: "threadId", threadId: "created" }] });
});

test("late validation from a disconnected socket cannot replace a successful reconnect", async () => {
  const entered = deferred<void>();
  const validation = deferred<Response>();
  const fixture = server({ fetch: async (_url, request) => {
    if (!request.method) { entered.resolve(); return validation.promise; }
    return response(200, "reconnected");
  } });
  const stale = new Socket();
  const pending = fixture.connect(stale);
  await Promise.race([entered.promise, pending]);
  assert.equal(fixture.calls[0]?.method, "GET");
  stale.close();
  const current = new Socket();
  await fixture.connect(current, "");
  validation.resolve(response(404));
  await pending;
  current.close();
  fixture.advance(60_000);
  assert.deepEqual(fixture.terms, [{ threadId: "reconnected", killed: true }]);
});
