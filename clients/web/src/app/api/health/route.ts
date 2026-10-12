import { NextResponse } from "next/server";
import { liteLlmModelCount, serviceHealthDetail } from "@/lib/health-data";

const LANGGRAPH_URL = process.env.LANGGRAPH_API_URL ?? "http://langgraph:2024";
const LITELLM_URL = process.env.LITELLM_URL ?? "http://litellm:4000";
// Read the operator-configured LiteLLM key. The stack sets LITELLM_MASTER_KEY
// (compose + .env.example), so accept it as the source of truth while keeping
// LITELLM_API_KEY supported for overrides. Still NO fallback to the public
// default — probing with the public key would mask a misconfigured stack as
// "ok"; an unset key correctly reports degraded.
const LITELLM_KEY = process.env.LITELLM_API_KEY ?? process.env.LITELLM_MASTER_KEY ?? "";
const NEO4J_HTTP_URL = process.env.NEO4J_HTTP_URL ?? "http://neo4j:7474";

interface ServiceHealth {
  name: string;
  status: "ok" | "error";
  detail: string;
  latencyMs?: number;
}

async function checkService(
  name: string,
  url: string,
  headers?: Record<string, string>,
  timeout = 5000,
): Promise<ServiceHealth> {
  const start = Date.now();
  try {
    const res = await fetch(url, {
      headers,
      signal: AbortSignal.timeout(timeout),
    });
    const latency = Date.now() - start;
    if (res.ok) {
      const data: unknown = await res.json().catch(() => null);
      return { name, status: "ok", detail: serviceHealthDetail(name, data), latencyMs: latency };
    }
    return { name, status: "error", detail: `HTTP ${res.status}`, latencyMs: latency };
  } catch (err) {
    return { name, status: "error", detail: err instanceof Error ? err.message : "Unreachable" };
  }
}

async function checkPostgres(): Promise<ServiceHealth> {
  // Actually probe Postgres rather than always returning ok. Reuses the
  // app's prisma client so we exercise the same connection pool the rest
  // of the API uses.
  const start = Date.now();
  try {
    const { prisma } = await import("@/lib/prisma");
    await prisma.$queryRaw`SELECT 1`;
    return {
      name: "postgres",
      status: "ok",
      detail: "connected",
      latencyMs: Date.now() - start,
    };
  } catch (err) {
    return {
      name: "postgres",
      status: "error",
      detail: err instanceof Error ? err.message : "Unreachable",
      latencyMs: Date.now() - start,
    };
  }
}

async function checkLiteLlm(): Promise<{ health: ServiceHealth; modelCount: number }> {
  if (!LITELLM_KEY) {
    return {
      health: {
        name: "litellm",
        status: "error",
        detail: "LITELLM_API_KEY not configured",
      },
      modelCount: 0,
    };
  }

  const start = Date.now();
  try {
    const res = await fetch(`${LITELLM_URL}/v1/models`, {
      headers: { Authorization: `Bearer ${LITELLM_KEY}` },
      signal: AbortSignal.timeout(5000),
    });
    const latencyMs = Date.now() - start;
    if (!res.ok) {
      return {
        health: { name: "litellm", status: "error", detail: `HTTP ${res.status}`, latencyMs },
        modelCount: 0,
      };
    }
    const payload: unknown = await res.json().catch(() => null);
    const modelCount = liteLlmModelCount(payload);
    return {
      health: {
        name: "litellm",
        status: "ok",
        detail: `${modelCount} models loaded`,
        latencyMs,
      },
      modelCount,
    };
  } catch (err) {
    return {
      health: {
        name: "litellm",
        status: "error",
        detail: err instanceof Error ? err.message : "Unreachable",
      },
      modelCount: 0,
    };
  }
}

export async function GET() {
  const [langgraph, litellmResult, neo4j, postgres] = await Promise.all([
    checkService("langgraph", `${LANGGRAPH_URL}/info`),
    checkLiteLlm(),
    checkService("neo4j", `${NEO4J_HTTP_URL}/`),
    checkPostgres(),
  ]);

  const { health: litellm, modelCount } = litellmResult;

  const services: ServiceHealth[] = [langgraph, litellm, neo4j, postgres];
  const allOk = services.every((s) => s.status === "ok");

  return NextResponse.json({
    status: allOk ? "healthy" : "degraded",
    services,
    modelCount,
  });
}
