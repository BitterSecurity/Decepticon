import { staleEngagementCheckpoint } from "./threadStore.js";
import { once } from "node:events";
import { createServer } from "node:http";
import { describe, expect, it, vi } from "vitest";

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
    server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
});

it("retrieves scoped history beyond twenty and refuses foreign or legacy resumes", async () => {
  // Given a real HTTP fixture serving paginated SDK thread requests.
  const requests: Record<string, unknown>[] = [];
  const server = createServer(async (request, response) => {
    let body = "";
    for await (const chunk of request) body += chunk;
    response.setHeader("Content-Type", "application/json");
    if (request.url === "/threads/search") {
      const query = JSON.parse(body);
      requests.push(query);
      const count = query.offset === 0 ? 100 : 5;
      response.end(JSON.stringify(Array.from({ length: count }, (_, i) => ({ thread_id: `t-${query.offset + i}`, updated_at: "2026-10-17", metadata: { engagement_name: "A", assistantId: "soundwave" } }))));
    } else response.end(JSON.stringify({ thread_id: "foreign", updated_at: "2026-10-17", metadata: request.url === "/threads/legacy" ? {} : { engagement_name: "B", assistantId: "soundwave" } }));
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  if (!address || typeof address === "string") throw new TypeError("Expected TCP port");
  vi.resetModules();
  vi.stubEnv("DECEPTICON_API_URL", `http://127.0.0.1:${address.port}`);
  vi.stubEnv("DECEPTICON_ENGAGEMENT", "A");
  try {
    const { listThreads, validateThread } = await import("./threadStore.js");
    // When the default listing traverses every page.
    const entries = await listThreads();
    // Then older entries remain accessible and scope is supplied on each request.
    expect(entries).toHaveLength(105);
    expect(requests.map(request => request.offset)).toEqual([0, 100]);
    expect(requests.every(request => JSON.stringify(request.metadata) === JSON.stringify({ engagement_name: "A" }))).toBe(true);
    await expect(validateThread("foreign")).rejects.toThrow("different engagement");
    await expect(validateThread("legacy")).rejects.toThrow("Legacy session");
  } finally {
    vi.unstubAllEnvs();
    server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
});

it("adopts a legacy session only after explicit confirmation and refuses foreign checkpoint state", async () => {
  // Given old metadata with a supported assistant and a matching checkpoint.
  let metadata: Record<string, unknown> = { decepticon_assistant: "soundwave", title: "old history" };
  let checkpointEngagement = "A";
  let updates = 0;
  const server = createServer(async (request, response) => {
    let body = "";
    for await (const chunk of request) body += chunk;
    response.setHeader("Content-Type", "application/json");
    if (request.url?.endsWith("/state")) { response.end(JSON.stringify({ values: { engagement_name: checkpointEngagement, workspace_path: "/workspace" } })); return; }
    if (request.method === "PATCH") { updates++; metadata = { ...metadata, ...JSON.parse(body).metadata }; }
    response.end(JSON.stringify({ thread_id: "legacy", updated_at: "2026-10-17", metadata }));
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  if (!address || typeof address === "string") throw new TypeError("Expected TCP port");
  vi.resetModules();
  vi.stubEnv("DECEPTICON_API_URL", `http://127.0.0.1:${address.port}`);
  vi.stubEnv("DECEPTICON_ENGAGEMENT", "A");
  vi.stubEnv("DECEPTICON_WORKSPACE_PATH", "/workspace");
  try {
    const { claimLegacyThread } = await import("./threadStore.js");
    // When the operator explicitly adopts that session.
    const adopted = await claimLegacyThread("legacy");
    // Then scope and assistant persist, while a conflicting checkpoint cannot be adopted.
    expect(adopted.engagementName).toBe("A");
    expect(adopted.assistantId).toBe("soundwave");
    expect(updates).toBe(1);
    metadata = { assistantId: "soundwave" };
    checkpointEngagement = "B";
    await expect(claimLegacyThread("legacy")).rejects.toThrow("different engagement");
    expect(updates).toBe(1);
  } finally {
    vi.unstubAllEnvs();
    server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
});

it("keeps canonical and pre-upgrade web sessions in their original server namespaces", async () => {
  const writes: Record<string, unknown>[] = [];
  const server = createServer(async (request, response) => {
    response.setHeader("Content-Type", "application/json");
    const path = request.url?.includes("legacy") ? "/workspace/owned-case" : request.url?.includes("foreign") ? "/workspace/sibling" : "/workspace";
    if (request.method === "PATCH") {
      let body = "";
      for await (const chunk of request) body += chunk;
      writes.push(JSON.parse(body));
    }
    response.end(JSON.stringify(request.url?.endsWith("/state") ? { values: { engagement_name: "owned-case", workspace_path: path } } : { thread_id: "session", updated_at: "2026-10-25", metadata: { engagement_name: "owned-case", assistantId: "decepticon", workspace_path: path } }));
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Expected TCP port");
  vi.resetModules();
  vi.stubEnv("DECEPTICON_API_URL", `http://127.0.0.1:${address.port}`);
  vi.stubEnv("DECEPTICON_ENGAGEMENT", "owned-case");
  vi.stubEnv("DECEPTICON_RUNTIME_ENGAGEMENT", "owned-case");
  vi.stubEnv("DECEPTICON_WEB_TERMINAL", "true");
  vi.stubEnv("DECEPTICON_WORKSPACE_PATH", "/host-view/owned-case");
  vi.stubEnv("DECEPTICON_RUNTIME_WORKSPACE_PATH", "/workspace");
  try {
    const { validateThread, readThreadSnapshot, saveThread } = await import("./threadStore.js");
    expect((await validateThread("canonical")).workspacePath).toBe("/workspace");
    expect((await validateThread("legacy")).workspacePath).toBe("/workspace/owned-case");
    await expect(readThreadSnapshot("legacy")).resolves.toBeTruthy();
    await expect(validateThread("foreign")).rejects.toThrow("different engagement or workspace");
    await saveThread("canonical", "decepticon", "new");
    await saveThread("legacy", "decepticon", "old", "/workspace/owned-case");
    expect(writes.map(write => (write.metadata as Record<string, unknown>).workspace_path)).toEqual(["/workspace", "/workspace/owned-case"]);
    vi.stubEnv("DECEPTICON_RUNTIME_ENGAGEMENT", "sibling");
    await expect(validateThread("legacy")).rejects.toThrow("different engagement or workspace");
  } finally {
    vi.unstubAllEnvs();
    server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
});

describe("staleEngagementCheckpoint", () => {
  const stateOf = (values: unknown): unknown => ({ values, checkpoint: { checkpoint_id: "cp-1" } });

  it("returns the mismatched label when the checkpoint belongs to another engagement", () => {
    const state = stateOf({ engagement_name: "roe-tui-verify", messages: [] });
    expect(staleEngagementCheckpoint(state, "oss-verify")).toBe("roe-tui-verify");
  });

  it("returns null when the checkpoint matches the runtime engagement", () => {
    const state = stateOf({ engagement_name: "oss-verify", messages: [] });
    expect(staleEngagementCheckpoint(state, "oss-verify")).toBeNull();
  });

  it("returns null for a fresh thread with no engagement binding", () => {
    expect(staleEngagementCheckpoint(stateOf({ messages: [] }), "oss-verify")).toBeNull();
    expect(staleEngagementCheckpoint(stateOf({}), "oss-verify")).toBeNull();
    expect(staleEngagementCheckpoint(null, "oss-verify")).toBeNull();
  });

  it("returns null when no runtime engagement is set (shared-root compose)", () => {
    const state = stateOf({ engagement_name: "roe-tui-verify" });
    expect(staleEngagementCheckpoint(state, "")).toBeNull();
  });

  it("ignores non-string engagement values", () => {
    const state = stateOf({ engagement_name: 12345 });
    expect(staleEngagementCheckpoint(state, "oss-verify")).toBeNull();
  });
});
