/**
 * Thread persistence — query/update LangGraph threads via the SDK.
 *
 * Replaces the previous file-based storage (~/.decepticon/threads.json)
 * with server-side thread metadata, so all clients (CLI, Web) share
 * the same thread list.
 */

import { Client } from "@langchain/langgraph-sdk";

export interface ThreadEntry {
  /** LangGraph thread ID. */
  threadId: string;
  /** Assistant ID used with this thread. */
  assistantId: string;
  /** ISO timestamp when the thread was last used. */
  lastUsed: string;
  /** First user message — serves as session title. */
  title: string;
}

const MAX_ENTRIES = 20;

let _client: Client | null = null;

function getClient(): Client {
  if (!_client) {
    const apiUrl = process.env.DECEPTICON_API_URL || "http://localhost:2024";
    _client = new Client({ apiUrl });
  }
  return _client;
}

/** Save or update a thread's metadata on the server. */
export async function saveThread(
  threadId: string,
  assistantId: string,
  title: string,
): Promise<void> {
  try {
    const client = getClient();
    await client.threads.update(threadId, {
      metadata: {
        assistantId,
        title: title.slice(0, 100),
        lastUsed: new Date().toISOString(),
      },
    });
  } catch {
    // Non-critical — don't crash if metadata update fails
  }
}

/** Update the lastUsed timestamp for an existing thread. */
export async function touchThread(threadId: string): Promise<void> {
  try {
    const client = getClient();
    await client.threads.update(threadId, {
      metadata: {
        lastUsed: new Date().toISOString(),
      },
    });
  } catch {
    // Non-critical
  }
}

/** Load all saved thread entries from the server, most recent first. */
export async function listThreads(): Promise<ThreadEntry[]> {
  const client = getClient();
  const threads = await client.threads.search({
    sortBy: "updated_at",
    sortOrder: "desc",
    limit: MAX_ENTRIES,
  });
  return threads.map((t) => ({
    threadId: t.thread_id,
    assistantId: (t.metadata?.assistantId as string) ?? "",
    lastUsed: t.updated_at,
    title: (t.metadata?.title as string) || `Session ${t.thread_id.slice(0, 8)}`,
  }));
}

/** Load a single thread by index (0-based) from the server list. */
export async function loadThreadByIndex(
  index: number,
): Promise<ThreadEntry | null> {
  const entries = await listThreads();
  return entries[index] ?? null;
}
