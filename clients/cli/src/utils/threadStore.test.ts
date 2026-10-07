import { once } from "node:events";
import { createServer } from "node:http";
import { expect, it, vi } from "vitest";

it("reports session-list authentication failure instead of an empty history", async () => {
  const server = createServer((_request, response) => {
    response.writeHead(401, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ detail: "Session access denied" }));
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  if (!address || typeof address === "string") {
    throw new TypeError("Expected the fixture to listen on a TCP port");
  }
  vi.resetModules();
  vi.stubEnv("DECEPTICON_API_URL", `http://127.0.0.1:${address.port}`);

  try {
    const { listThreads } = await import("./threadStore.js");

    await expect(listThreads()).rejects.toThrow("Session access denied");
  } finally {
    vi.unstubAllEnvs();
    server.close();
    await once(server, "close");
  }
});
