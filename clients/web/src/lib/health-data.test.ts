import assert from "node:assert/strict";
import { test } from "node:test";
import { liteLlmModelCount, serviceHealthDetail } from "./health-data";

test("LiteLLM model count reads the complete response payload", () => {
  assert.equal(
    liteLlmModelCount({ data: [{ id: "model-a" }, { id: "model-b" }] }),
    2,
  );
});

test("LiteLLM model count safely handles malformed responses", () => {
  for (const payload of [null, undefined, [], {}, { data: null }, { data: {} }]) {
    assert.equal(liteLlmModelCount(payload), 0);
  }
});

test("health details normalize LangGraph and Neo4j JSON for Settings cards", () => {
  assert.equal(
    serviceHealthDetail("langgraph", {
      langgraph_api_version: "0.5.29",
      langgraph_version: "0.4.10",
      langchain_core_version: "0.3.80",
    }),
    "API ready (v0.5.29)",
  );
  assert.equal(
    serviceHealthDetail("neo4j", {
      bolt_direct: "bolt://localhost:7687",
      neo4j_version: "5.26.12",
      neo4j_edition: "community",
    }),
    "Database ready (v5.26.12, community)",
  );
});

test("health details never expose raw or malformed JSON in Settings cards", () => {
  assert.equal(serviceHealthDetail("langgraph", { arbitrary: { nested: true } }), "API ready");
  assert.equal(serviceHealthDetail("neo4j", null), "Database ready");
  assert.equal(serviceHealthDetail("unknown", { secret: "not-for-the-card" }), "Connected");
});
