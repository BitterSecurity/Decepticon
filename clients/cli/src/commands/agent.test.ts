/** Exercise /agent discovery and selection against a paginated HTTP registry. */
import { createServer, type Server } from "node:http";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import agent from "./agent.js";
import { getAssistantOverride, setAssistantOverride } from "./assistantOverride.js";

describe("/agent catalog discovery", () => {
  let server: Server;
  let graphs: string[];
  let offsets: number[];
  let errorOffset: number;
  let delayMs: number;
  let selections: string[];
  let responseTimers: ReturnType<typeof setTimeout>[];

  beforeEach(async () => {
    graphs = Array.from({ length: 101 }, (_, index) => `graph-${index}`);
    offsets = [];
    selections = [];
    responseTimers = [];
    errorOffset = -1;
    delayMs = 0;
    setAssistantOverride("previous");
    vi.stubEnv("DECEPTICON_WEB_TERMINAL", "false");
    vi.stubEnv("DECEPTICON_ASSISTANT_ID", "launcher-default");
    server = createServer(async (request, response) => {
      if (request.method !== "POST" || request.url !== "/assistants/search") {
        response.writeHead(404).end();
        return;
      }
      let body = "";
      for await (const chunk of request) body += chunk;
      const { offset = 0, limit = 10 }: { offset?: number; limit?: number } = JSON.parse(body);
      offsets.push(offset);
      responseTimers.push(setTimeout(() => {
        if (offset === errorOffset) {
          response.writeHead(503).end("catalog unavailable");
          return;
        }
        response.writeHead(200, { "Content-Type": "application/json" });
        response.end(JSON.stringify(graphs.slice(offset, offset + limit).map((graph, index) => ({
          assistant_id: `assistant-${offset + index}`,
          graph_id: graph,
          name: graph,
        }))));
      }, delayMs));
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    if (!address || typeof address === "string") throw new TypeError("Expected TCP listener");
    vi.stubEnv("DECEPTICON_API_URL", `http://127.0.0.1:${address.port}`);
  });

  afterEach(async () => {
    for (const timer of responseTimers) clearTimeout(timer);
    server.closeAllConnections();
    await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
    setAssistantOverride("");
    vi.unstubAllEnvs();
  });

  function runAgent(args: string): Promise<string> {
    return new Promise((resolve) => {
      agent.execute(args, {
        addSystemEvent: resolve,
        clearEvents: vi.fn(),
        submit: vi.fn(),
        resume: vi.fn(),
        exit: vi.fn(),
        selectAssistant: (assistantId) => {
          selections.push(assistantId);
          return true;
        },
      });
    });
  }

  it("lists a graph beyond the first page", async () => {
    expect(await runAgent("")).toContain("graph-100");
  });

  it("selects a graph beyond the first page", async () => {
    expect(await runAgent("graph-100")).toContain("Active orchestrator: graph-100");
    expect(getAssistantOverride()).toBe("graph-100");
    expect(selections).toEqual(["graph-100"]);
  });

  it("deduplicates graphs across pages without stopping at duplicate-only pages", async () => {
    graphs = [...Array<string>(200).fill("shared-graph"), "later-graph"];
    const listing = await runAgent("");
    expect(listing.match(/shared-graph/g)).toHaveLength(1);
    expect(listing).toContain("later-graph");
  });

  it("preserves selection when a later page fails even if the requested graph was found", async () => {
    errorOffset = 100;
    expect(await runAgent("graph-0")).toContain("Failed to switch orchestrator: assistants/search HTTP 503");
    expect(getAssistantOverride()).toBe("previous");
    expect(selections).toEqual([]);
  });

  it("reports a later-page listing error without displaying a partial catalog", async () => {
    errorOffset = 100;
    const listing = await runAgent("");
    expect(listing).toContain("Could not list orchestrators: assistants/search HTTP 503");
    expect(listing).not.toContain("Available orchestrators:");
  });

  it("reads the empty page following an exactly full final page", async () => {
    graphs = Array.from({ length: 200 }, (_, index) => `graph-${index}`);
    expect(await runAgent("")).toContain("graph-199");
    expect(offsets).toEqual([0, 100, 200]);
  });

  it("bounds the entire catalog read by one deadline and retains selection", async () => {
    delayMs = 6000;
    const started = performance.now();
    expect(await runAgent("graph-0")).toMatch(/Failed to switch orchestrator:.*(timeout|timed out)/i);
    expect(performance.now() - started).toBeLessThan(11500);
    expect(offsets).toEqual([0, 100]);
    expect(getAssistantOverride()).toBe("previous");
    expect(selections).toEqual([]);
  }, 14000);

  it("rejects an unregistered graph with the complete catalog and retains selection", async () => {
    const rejection = await runAgent("missing-graph");
    expect(rejection).toContain("isn't currently registered");
    expect(rejection).toContain("graph-100");
    expect(getAssistantOverride()).toBe("previous");
    expect(selections).toEqual([]);
  });

  it("clears a later-page selection without fetching the catalog again", async () => {
    await runAgent("graph-100");
    const requestsBeforeClear = offsets.length;
    expect(await runAgent("clear")).toContain("Orchestrator override cleared (was 'graph-100')");
    expect(getAssistantOverride()).toBe("");
    expect(selections).toEqual(["graph-100", "launcher-default"]);
    expect(offsets).toHaveLength(requestsBeforeClear);
  });
});
