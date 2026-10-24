import assert from "node:assert/strict";
import { test } from "node:test";
import { createServer } from "node:http";
import { createServer as createTcpServer } from "node:net";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { stripVTControlCharacters } from "node:util";
import { WebSocket } from "ws";

test("web terminal boots the real CLI and renders its connected composer", { timeout: 120_000 }, async () => {
  const root = await mkdtemp(join(tmpdir(), "terminal-cli-boot-"));
  await mkdir(join(root, "workspace", "loader-test", "plan"), { recursive: true });
  await writeFile(join(root, "workspace", "loader-test", "plan", "roe.json"), JSON.stringify({ machine_enforcement: { in_scope: ["example.com"] } }));
  const mutations: string[] = [];
  const api = createServer((request, response) => {
    response.setHeader("Content-Type", "application/json");
    if (request.method !== "GET") mutations.push(`${request.method} ${request.url}`);
    if (request.url === "/threads" && request.method === "POST") { let body = ""; request.on("data", chunk => { body += chunk; }); request.on("end", () => { assert.equal(JSON.parse(body).metadata.workspace_path, "/workspace"); }); response.end(JSON.stringify({ thread_id: "loader-thread" })); return; }
    if (request.url === "/threads/loader-thread") {
      response.end(JSON.stringify({ thread_id: "loader-thread", updated_at: "2026-10-24", metadata: { assistantId: "decepticon", engagement_name: "loader-test", workspace_path: "/workspace" } })); return;
    }
    if (request.url === "/threads/loader-thread/state") { response.end(JSON.stringify({ values: { messages: [] }, tasks: [], next: [] })); return; }
    if (request.url === "/ok") { response.end('{}'); return; }
    response.writeHead(404).end('{}');
  });
  api.listen(0, "127.0.0.1");
  await once(api, "listening");
  const apiAddress = api.address();
  if (!apiAddress || typeof apiAddress === "string") throw new Error("Missing API listener");
  const apiUrl = `http://127.0.0.1:${apiAddress.port}`;
  const reservation = createTcpServer();
  reservation.listen(0, "127.0.0.1");
  await once(reservation, "listening");
  const address = reservation.address();
  if (!address || typeof address === "string") throw new Error("Missing terminal port");
  await new Promise<void>(resolve => reservation.close(() => resolve()));
  const child = spawn(process.execPath, ["--import", "tsx", fileURLToPath(new URL("../../server/terminal-server.ts", import.meta.url))], {
    cwd: fileURLToPath(new URL("../../", import.meta.url)),
    env: { ...process.env, CI: "false", CONTINUOUS_INTEGRATION: "false", TERMINAL_PORT: String(address.port), WEB_PORT: "3000", LANGGRAPH_API_URL: apiUrl, BLUE_MONITOR_URL: apiUrl, WORKSPACE_PATH: join(root, "workspace"), DECEPTICON_RUNTIME_ENGAGEMENT: "loader-test", DECEPTICON_HOME: root, DECEPTICON_WEB_STATE_DIR: join(root, "web-state"), DECEPTICON_INITIAL_MESSAGE: "", DECEPTICON_LANGUAGE: "en" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let serverOutput = "";
  child.stdout.on("data", data => { serverOutput += data; });
  child.stderr.on("data", data => { serverOutput += data; });
  let socket: WebSocket | undefined;
  try {
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(serverOutput || "Terminal startup timed out")), 20_000);
      child.stdout.on("data", () => { if (serverOutput.includes("Listening on")) { clearTimeout(timer); resolve(); } });
      child.once("exit", () => { clearTimeout(timer); reject(new Error(serverOutput)); });
    });
    socket = new WebSocket(`ws://127.0.0.1:${address.port}/terminal?engagementSlug=loader-test&agentId=decepticon`, { origin: "http://localhost:3000" });
    const text = await new Promise<string>((resolve, reject) => {
      let output = "";
      const timer = setTimeout(() => reject(new Error(stripVTControlCharacters(output).slice(-1200) || serverOutput)), 90_000);
      socket!.on("message", data => {
        output += data.toString();
        const plain = stripVTControlCharacters(output);
        if (plain.includes("connected") && plain.includes("Enter send")) { clearTimeout(timer); resolve(plain); }
      });
      socket!.once("error", error => { clearTimeout(timer); reject(error); });
      socket!.once("close", () => { clearTimeout(timer); reject(new Error(stripVTControlCharacters(output).slice(-1200))); });
    });
    assert.match(text, /loader-test/);
    assert.doesNotMatch(text, /SyntaxError|Unexpected token|EACCES/);
    const composer = join(root, "web-state", "loader-test", "decepticon", "composer");
    socket.send("unsent web draft");
    let storedDraft = false;
    const deadline = Date.now() + 10_000;
    while (Date.now() < deadline && !storedDraft) {
      try {
        for (const name of (await readdir(composer)).filter(name => name.endsWith(".json"))) {
          const memory = JSON.parse(await readFile(join(composer, name), "utf8"));
          if (memory.draft === "unsent web draft") storedDraft = true;
        }
      } catch (error) {
        if (!(error instanceof Error && "code" in error && error.code === "ENOENT")) throw error;
      }
      if (!storedDraft) await new Promise(resolve => setTimeout(resolve, 50));
    }
    assert.ok(storedDraft, "actual PTY input must persist under the private web state root");
    assert.deepEqual(mutations, ["POST /threads"], "opening a terminal may create its thread but must not execute an agent run");
  } finally {
    socket?.terminate();
    const exited = once(child, "exit");
    child.kill("SIGTERM");
    await Promise.race([exited, new Promise(resolve => setTimeout(resolve, 2000))]);
    if (child.exitCode === null) child.kill("SIGKILL");
    api.closeAllConnections();
    await new Promise<void>(resolve => api.close(() => resolve()));
    await rm(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
});
