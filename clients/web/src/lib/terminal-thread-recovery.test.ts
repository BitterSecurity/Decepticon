import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import vm from "node:vm";
import ts from "typescript";

const require = createRequire(import.meta.url);
const sourceUrl = new URL("../../server/terminal-server.ts", import.meta.url);

async function connect(readStatus: number, createStatus: number): Promise<{
  spawnedThreads: string[];
  messages: Record<string, unknown>[];
  closeCode: number | null;
}> {
  const spawnedThreads: string[] = [];
  class Socket {
    static OPEN = 1;
    readyState = 1;
    messages: Record<string, unknown>[] = [];
    closeCode: number | null = null;
    send(text: string): void { this.messages.push(JSON.parse(text)); }
    close(code: number): void { this.closeCode = code; }
    on(): void {}
  }
  let handler: ((socket: Socket, request: { headers: { origin: string }; url: string }) => Promise<void>) | undefined;
  class Server {
    on(event: string, callback: typeof handler): void {
      if (event === "connection") handler = callback;
    }
    close(): void {}
  }
  const boundaries: Record<string, unknown> = {
    ws: { WebSocket: Socket, WebSocketServer: Server },
    "node-pty": {
      spawn: (_command: string, _args: string[], options: { env: Record<string, string> }) => {
        spawnedThreads.push(options.env.DECEPTICON_THREAD_ID ?? "");
        return { pid: 123, onData(): void {}, onExit(): void {} };
      },
    },
    "node:fs/promises": {
      readFile: async () => { throw new Error("No approval documents in this environment"); },
    },
  };
  const source = readFileSync(sourceUrl, "utf8").replaceAll("import.meta.url", JSON.stringify(sourceUrl.href));
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const serverModule = { exports: {} };
  vm.runInNewContext(compiled, {
    module: serverModule, exports: serverModule.exports,
    require: (name: string) => Object.hasOwn(boundaries, name) ? boundaries[name] : require(name),
    process: { env: {}, on(): void {}, exit(): never { throw new Error("Unexpected process exit"); } },
    console: { log(): void {}, warn(): void {}, error(): void {} },
    URL, Buffer, AbortController, setTimeout, clearTimeout,
    fetch: async (_url: string, options?: { method?: string }) => {
      const status = options?.method === "POST" ? createStatus : options?.method === "PATCH" ? 200 : readStatus;
      return { status, ok: status >= 200 && status < 300, json: async () => ({ thread_id: "replacement" }) };
    },
  });
  assert.ok(handler, "actual server must register its connection handler");
  const socket = new Socket();
  await handler(socket, {
    headers: { origin: "http://localhost:3000" },
    url: "/terminal?engagementSlug=owned-case&engagementId=owned&agentId=soundwave&threadId=missing",
  });
  return { spawnedThreads, messages: socket.messages, closeCode: socket.closeCode };
}

test("a missing thread is replaced before the terminal receives its thread ID", async () => {
  const connection = await connect(404, 200);
  assert.deepEqual(connection, {
    spawnedThreads: ["replacement"],
    messages: [{ type: "threadId", threadId: "replacement" }],
    closeCode: null,
  });
});

test("thread service failure closes the connection without starting a terminal", async () => {
  const connection = await connect(503, 200);
  assert.equal(connection.closeCode, 1011);
  assert.equal(connection.spawnedThreads.length, 0);
  assert.equal(connection.messages[0]?.type, "error");
});

test("replacement creation failure closes the connection without starting a terminal", async () => {
  const connection = await connect(404, 503);
  assert.equal(connection.closeCode, 1011);
  assert.equal(connection.spawnedThreads.length, 0);
  assert.equal(connection.messages[0]?.type, "error");
});
