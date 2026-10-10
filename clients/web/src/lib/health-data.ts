export function liteLlmModelCount(payload: unknown): number {
  if (typeof payload !== "object" || payload === null) return 0;
  const data = (payload as Record<string, unknown>).data;
  return Array.isArray(data) ? data.length : 0;
}

function record(payload: unknown): Record<string, unknown> | null {
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) return null;
  return payload as Record<string, unknown>;
}

function textField(payload: Record<string, unknown> | null, ...keys: string[]): string | null {
  if (!payload) return null;
  for (const key of keys) {
    const value = payload[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return null;
}

function version(value: string | null): string | null {
  if (!value) return null;
  return value.startsWith("v") ? value : `v${value}`;
}

/**
 * Convert machine-oriented health payloads into stable operator-facing text.
 * Raw endpoint JSON belongs in diagnostics, not in the compact Settings card.
 */
export function serviceHealthDetail(service: string, payload: unknown): string {
  const data = record(payload);

  if (service === "langgraph") {
    const apiVersion = version(textField(data, "langgraph_api_version"));
    if (apiVersion) return `API ready (${apiVersion})`;

    const graphVersion = version(textField(data, "langgraph_version", "version"));
    return graphVersion ? `API ready (LangGraph ${graphVersion})` : "API ready";
  }

  if (service === "neo4j") {
    const neo4jVersion = version(textField(data, "neo4j_version", "version"));
    const edition = textField(data, "neo4j_edition", "edition");
    const qualifiers = [neo4jVersion, edition].filter((value): value is string => value !== null);
    return qualifiers.length > 0 ? `Database ready (${qualifiers.join(", ")})` : "Database ready";
  }

  return "Connected";
}
