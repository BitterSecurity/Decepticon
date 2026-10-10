import type { AgentConfig } from "./agents";

export interface EngagementSummary {
  id: string;
  name: string;
  status: string;
}

interface JsonResponse {
  ok: boolean;
  status: number;
  json(): Promise<unknown>;
}

export function isAgentConfig(value: unknown): value is AgentConfig {
  if (typeof value !== "object" || value === null) return false;
  const agent = value as Record<string, unknown>;
  return (
    typeof agent.id === "string" &&
    typeof agent.name === "string" &&
    typeof agent.description === "string" &&
    typeof agent.role === "string" &&
    typeof agent.color === "string"
  );
}

export function isEngagementSummary(value: unknown): value is EngagementSummary {
  if (typeof value !== "object" || value === null) return false;
  const engagement = value as Record<string, unknown>;
  return (
    typeof engagement.id === "string" &&
    typeof engagement.name === "string" &&
    typeof engagement.status === "string"
  );
}

export async function readCollectionResponse<T>(
  response: JsonResponse,
  label: string,
  isItem: (value: unknown) => value is T,
): Promise<T[]> {
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`${label} returned invalid JSON`);
  }

  if (!response.ok) {
    throw new Error(`${label} request failed (HTTP ${response.status})`);
  }
  if (!Array.isArray(payload)) {
    throw new Error(`${label} returned an invalid response`);
  }
  if (!payload.every(isItem)) {
    throw new Error(`${label} returned invalid records`);
  }
  return payload;
}
