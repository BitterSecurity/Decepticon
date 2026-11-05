import { runtimeWorkspace, acceptsRuntimeWorkspace } from "./runtimeWorkspace.js";
import { Client } from "@langchain/langgraph-sdk";

export interface ThreadEntry {
  readonly threadId: string;
  readonly assistantId: string;
  readonly lastUsed: string;
  readonly title: string;
  readonly engagementName?: string;
  readonly workspacePath?: string;
  readonly legacyMetadata: boolean;
}
export interface ThreadListOptions {
  readonly scope?: "current" | "all" | "legacy";
  readonly limit?: number;
  readonly offset?: number;
  readonly assistantId?: string;
}
let client: Client | undefined;
function getClient(): Client {
  return client ??= new Client({ apiUrl: process.env.DECEPTICON_API_URL || "http://localhost:2024" });
}
function entry(thread: { thread_id: string; updated_at: string; metadata?: Record<string, unknown> | null }): ThreadEntry {
  const metadata = thread.metadata ?? {};
  const engagementName = typeof metadata.engagement_name === "string" ? metadata.engagement_name : undefined;
  return {
    threadId: thread.thread_id,
    assistantId: typeof metadata.assistantId === "string" ? metadata.assistantId : typeof metadata.decepticon_assistant === "string" ? metadata.decepticon_assistant : "",
    title: typeof metadata.title === "string" ? metadata.title : `Session ${thread.thread_id.slice(0, 8)}`,
    lastUsed: thread.updated_at,
    engagementName,
    workspacePath: typeof metadata.workspace_path === "string" ? metadata.workspace_path : undefined,
    legacyMetadata: engagementName === undefined,
  };
}
export async function saveThread(threadId: string, assistantId: string, title: string, workspacePath = runtimeWorkspace()): Promise<void> {
  await getClient().threads.update(threadId, { metadata: {
    assistantId, title: title.slice(0, 100), lastUsed: new Date().toISOString(),
    engagement_name: process.env.DECEPTICON_ENGAGEMENT ?? "",
    workspace_path: workspacePath,
  } });
}
export async function touchThread(threadId: string): Promise<void> {
  await getClient().threads.update(threadId, { metadata: { lastUsed: new Date().toISOString() } });
}
export async function listThreads(options: ThreadListOptions = {}): Promise<ThreadEntry[]> {
  const scope = options.scope ?? "current";
  const metadata: Record<string, unknown> = {};
  if (scope === "current") metadata.engagement_name = process.env.DECEPTICON_ENGAGEMENT ?? "";
  if (options.assistantId) metadata.assistantId = options.assistantId;
  const result: ThreadEntry[] = [];
  let offset = options.offset ?? 0;
  const limit = options.limit ?? 100;
  for (;;) {
    const page = await getClient().threads.search({ metadata, sortBy: "updated_at", sortOrder: "desc", limit, offset });
    result.push(...page.map(entry).filter((thread) => scope !== "legacy" || thread.legacyMetadata));
    if (options.limit !== undefined || page.length < limit) return result;
    offset += page.length;
  }
}
/**
 * Detect a checkpoint whose graph state was hydrated under a different
 * runtime engagement. The daemon's binding guard rejects such sessions on
 * the next run ("Session does not belong to the launcher runtime
 * engagement"), so the resume path skips them and starts fresh instead of
 * dead-ending the operator. Returns the mismatched label, or null when the
 * checkpoint carries no engagement binding (fresh thread) or matches.
 */
export function staleEngagementCheckpoint(
  state: unknown,
  envEngagement: string,
): string | null {
  if (!envEngagement) return null;
  const values = state !== null && typeof state === "object" && !Array.isArray(state)
    ? (state as Record<string, unknown>).values
    : undefined;
  const record = values !== null && typeof values === "object" && !Array.isArray(values)
    ? values as Record<string, unknown>
    : {};
  const label = record.engagement_name;
  return typeof label === "string" && label !== "" && label !== envEngagement ? label : null;
}

export async function loadThreadByIndex(index: number): Promise<ThreadEntry | null> {
  return (await listThreads({ limit: 1, offset: index }))[0] ?? null;
}
export class SessionScopeError extends Error {
  constructor(readonly threadId: string, message: string) { super(message); this.name = "SessionScopeError"; }
}
export async function validateThread(threadId: string, signal?: AbortSignal): Promise<ThreadEntry> {
  const saved = entry(await getClient().threads.get(threadId, { signal }));
  if (saved.legacyMetadata) throw new SessionScopeError(threadId, "Legacy session has no engagement metadata. Review and migrate its metadata before resuming.");
  if (saved.engagementName !== (process.env.DECEPTICON_ENGAGEMENT ?? "") ||
      (saved.workspacePath && !acceptsRuntimeWorkspace(saved.workspacePath))) {
    throw new SessionScopeError(threadId, "Session belongs to a different engagement or workspace. Open that engagement to resume.");
  }
  if (!["soundwave", "decepticon", "autobot"].includes(saved.assistantId)) throw new SessionScopeError(threadId, "Session assistant metadata is missing or unsupported.");
  return saved;
}

/** Explicit user-confirmed adoption; callers must show the target engagement first. */
export async function claimLegacyThread(threadId: string): Promise<ThreadEntry> {
  const saved = entry(await getClient().threads.get(threadId));
  if (!saved.legacyMetadata) return validateThread(threadId);
  if (!["soundwave", "decepticon", "autobot"].includes(saved.assistantId)) throw new SessionScopeError(threadId, "Legacy session has no supported assistant; metadata must be repaired before adoption.");
  const state = await getClient().threads.getState(threadId);
  const values = state.values;
  if (values && typeof values === "object") {
    if ("engagement_name" in values && values.engagement_name && values.engagement_name !== process.env.DECEPTICON_ENGAGEMENT) throw new SessionScopeError(threadId, "Legacy checkpoint belongs to a different engagement.");
    if ("workspace_path" in values && values.workspace_path && !acceptsRuntimeWorkspace(String(values.workspace_path))) throw new SessionScopeError(threadId, "Legacy checkpoint belongs to a different workspace.");
  }
  await saveThread(threadId, saved.assistantId, saved.title, values && typeof values === "object" && "workspace_path" in values && typeof values.workspace_path === "string" && values.workspace_path ? values.workspace_path : runtimeWorkspace());
  return validateThread(threadId);
}

/** Read a scoped checkpoint for diagnostics without running or mutating it. */
export async function readThreadSnapshot(threadId: string): Promise<unknown> {
  const signal = AbortSignal.timeout(10_000);
  const saved = await validateThread(threadId, signal);
  const state = await getClient().threads.getState(threadId, undefined, { signal });
  const values = state.values;
  if (values && typeof values === "object") {
    if (("engagement_name" in values && values.engagement_name && values.engagement_name !== process.env.DECEPTICON_ENGAGEMENT) ||
        ("workspace_path" in values && values.workspace_path && values.workspace_path !== (saved.workspacePath ?? runtimeWorkspace()))) {
      throw new SessionScopeError(threadId, "Checkpoint belongs to a different engagement or workspace.");
    }
  }
  return state;
}
