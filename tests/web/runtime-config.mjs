// Run after npm run build --workspace=web (no database or Docker required):
// node --import tsx tests/web/runtime-config.mjs
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createServer } from "node:http";
import { fileURLToPath } from "node:url";
import { Client } from "@langchain/langgraph-sdk";
import WebSocket, { WebSocketServer } from "ws";
import { langGraphApiUrl, terminalWebSocketUrl } from "../../clients/web/src/lib/browser-config.ts";

const root = process.env.WEB_RUNTIME_TEST_ROOT ?? fileURLToPath(new URL("../../", import.meta.url));
const apiRequests = [];
const terminalRequests = [];
const standIn = createServer((req, res) => {
  apiRequests.push(new URL(req.url, "http://localhost"));
  res.writeHead(200, { "Content-Type": "application/json" });
  res.end("[]");
});
const terminals = new WebSocketServer({ server: standIn });
terminals.on("connection", (socket, req) => {
  terminalRequests.push(new URL(req.url, "http://localhost"));
  socket.send("runtime terminal connected");
});
standIn.listen(0, "127.0.0.1");
await once(standIn, "listening");
const location = { protocol: "http:", host: `127.0.0.1:${standIn.address().port}` };

try {
  for (const name of ["blue", "green", "default"]) {
    const env = {
      ...process.env,
      PORT: "0",
      HOSTNAME: "127.0.0.1",
      DATABASE_URL: "postgresql://PRIVATE_DATABASE_SENTINEL@db/test",
      LANGGRAPH_API_URL: "http://PRIVATE_INTERNAL_SENTINEL:2024",
    };
    if (name === "default") {
      delete env.NEXT_PUBLIC_LANGGRAPH_API_URL;
      delete env.NEXT_PUBLIC_TERMINAL_WS_URL;
    } else {
      env.NEXT_PUBLIC_LANGGRAPH_API_URL = `/runtime-${name}-api`;
      env.NEXT_PUBLIC_TERMINAL_WS_URL = `/runtime-${name}-terminal?deployment=${name}`;
    }
    // next start supports an ephemeral port; use the same production build each time.
    const server = spawn(process.execPath, ["node_modules/next/dist/bin/next", "start", "clients/web", "--port", "0", "--hostname", "127.0.0.1"], {
      cwd: root, env, stdio: ["ignore", "pipe", "pipe"],
    });
    let logs = "";
    server.stdout.on("data", (chunk) => { logs += chunk; });
    server.stderr.on("data", (chunk) => { logs += chunk; });
    try {
      for (let attempt = 0; attempt < 100 && !logs.includes("Ready"); attempt++) {
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      assert.match(logs, /Ready/, logs);
      const origin = logs.match(/http:\/\/127\.0\.0\.1:\d+/)[0];
      const response = await fetch(`${origin}/settings`);
      const html = await response.text();
      assert.equal(response.status, 200);
      assert.match(response.headers.get("cache-control"), /no-store/);
      assert.ok(!html.includes("PRIVATE_DATABASE_SENTINEL"));
      assert.ok(!html.includes("PRIVATE_INTERNAL_SENTINEL"));
      const flight = [...html.matchAll(/self\.__next_f\.push\((\[1,.*?\])\)<\/script>/g)]
        .map((match) => JSON.parse(match[1])[1]).join("");
      const config = JSON.parse(flight.match(/"config":(\{[^}]+\})/)[1]);
      assert.deepEqual(config, {
        langGraphApiUrl: name === "default" ? "http://localhost:2024" : `/runtime-${name}-api`,
        terminalWsUrl: name === "default" ? "ws://localhost:3003" : `/runtime-${name}-terminal?deployment=${name}`,
      });
      assert.ok(response.headers.get("content-security-policy")
        .includes("connect-src 'self' ws://localhost:* http://localhost:*"));
      if (name !== "default") {
        const client = new Client({ apiUrl: langGraphApiUrl(location, config.langGraphApiUrl) });
        assert.deepEqual(await client.runs.list("runtime-thread", { limit: 1 }), []);
        assert.equal(apiRequests.at(-1).pathname, `/runtime-${name}-api/threads/runtime-thread/runs`);
        assert.equal(apiRequests.at(-1).searchParams.get("limit"), "1");
        const url = terminalWebSocketUrl(location, config.terminalWsUrl);
        url.searchParams.set("engagementId", "runtime & engagement");
        const socket = new WebSocket(url);
        const [message] = await once(socket, "message");
        assert.equal(message.toString(), "runtime terminal connected");
        assert.equal(terminalRequests.at(-1).pathname, `/runtime-${name}-terminal`);
        assert.equal(terminalRequests.at(-1).searchParams.get("deployment"), name);
        assert.equal(terminalRequests.at(-1).searchParams.get("engagementId"), "runtime & engagement");
        socket.close();
        await once(socket, "close");
      }
      console.log(`${name}: runtime config, no-store, private env isolation, CSP, and applicable SDK/WebSocket transport passed`);
    } finally {
      if (server.exitCode === null && server.signalCode === null) {
        server.kill();
        await once(server, "exit");
      }
    }
  }
} finally {
  terminals.close();
  standIn.close();
}
