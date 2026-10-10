import assert from "node:assert/strict";
import { test } from "node:test";
import {
  isAgentConfig,
  isEngagementSummary,
  readCollectionResponse,
} from "./settings-data";

function response(payload: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() {
      return payload;
    },
  };
}

test("settings collections accept validated array payloads", async () => {
  const engagements = [
    { id: "eng-1", name: "example", status: "running" },
  ];
  const agents = [
    {
      id: "decepticon",
      name: "Decepticon",
      description: "Orchestrator",
      role: "Orchestrator",
      color: "#ef4444",
    },
  ];

  assert.deepEqual(
    await readCollectionResponse(response(engagements), "Engagements", isEngagementSummary),
    engagements,
  );
  assert.deepEqual(
    await readCollectionResponse(response(agents), "Agents", isAgentConfig),
    agents,
  );
});

test("settings collections reject API error objects before render", async () => {
  await assert.rejects(
    readCollectionResponse(
      response({ error: "DATABASE_URL environment variable is not set" }, 500),
      "Engagements",
      isEngagementSummary,
    ),
    /Engagements request failed \(HTTP 500\)/,
  );
});

test("settings collections reject successful non-array and malformed payloads", async () => {
  await assert.rejects(
    readCollectionResponse(response({ engagements: [] }), "Engagements", isEngagementSummary),
    /invalid response/,
  );
  await assert.rejects(
    readCollectionResponse(response([{ id: "missing-fields" }]), "Agents", isAgentConfig),
    /invalid records/,
  );
});

test("settings collections reject invalid JSON", async () => {
  await assert.rejects(
    readCollectionResponse(
      {
        ok: true,
        status: 200,
        async json() {
          throw new SyntaxError("invalid JSON");
        },
      },
      "Agents",
      isAgentConfig,
    ),
    /invalid JSON/,
  );
});
