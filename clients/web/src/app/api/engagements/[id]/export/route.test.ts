import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { after, before, test } from "node:test";
import { registerHooks } from "node:module";
import { NextRequest } from "next/server";
import { pathToFileURL } from "node:url";

const planDocuments = [
  "opplan", "roe", "threat-profile", "conops", "deconfliction",
  "contact", "data-handling", "abort", "cleanup",
] as const;
const testTempRoot = process.env.RED_TEAM_LABS_TEST_TMPDIR ?? process.cwd();

type TestEngagement = {
  id: string;
  name: string;
  userId: string;
  status: string;
  targetType: string;
  targetValue: string;
  createdAt: Date;
  updatedAt: Date;
};

declare global {
  var __engagementExportTestRows: TestEngagement[] | undefined;
}

const moduleTempRoot = join(testTempRoot, "engagement-export-modules");
const authModule = pathToFileURL(join(moduleTempRoot, "auth.cjs")).href;
const prismaModule = pathToFileURL(join(moduleTempRoot, "prisma.cjs")).href;

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "@/lib/auth-bridge") return { url: authModule, shortCircuit: true };
    if (specifier === "@/lib/prisma") return { url: prismaModule, shortCircuit: true };
    return nextResolve(specifier, context);
  },
});

type ExportGet = typeof import("./route").GET;
let exportGet: ExportGet;

before(async () => {
  await mkdir(moduleTempRoot, { recursive: true });
  await writeFile(join(moduleTempRoot, "auth.cjs"), `
    class AuthError extends Error {}
    async function requireAuth() { return { userId: "owner" }; }
    module.exports = { AuthError, requireAuth };
  `);
  await writeFile(join(moduleTempRoot, "prisma.cjs"), `
    module.exports = {
      prisma: {
        engagement: {
          findFirst: async ({ where }) => globalThis.__engagementExportTestRows?.find(
            (row) => row.id === where.id && row.userId === where.userId,
          ) ?? null,
        },
      },
    };
  `);
  exportGet = (await import("./route")).GET;
});

after(async () => {
  await rm(moduleTempRoot, { recursive: true, force: true });
});

async function exportJson(directory: string, existingDocuments: readonly string[]): Promise<Record<string, unknown>> {
  const engagement: TestEngagement = {
    id: "owned-engagement",
    name: "owned-fixture",
    userId: "owner",
    status: "draft",
    targetType: "web_url",
    targetValue: "https://fixture.invalid",
    createdAt: new Date("2026-10-01T00:00:00.000Z"),
    updatedAt: new Date("2026-10-01T00:00:00.000Z"),
  };
  globalThis.__engagementExportTestRows = [engagement];
  process.env.WORKSPACE_PATH = directory;
  const planDirectory = join(directory, engagement.name, "plan");
  await mkdir(planDirectory, { recursive: true });
  await Promise.all(existingDocuments.map((name) =>
    writeFile(join(planDirectory, `${name}.json`), JSON.stringify({ name })),
  ));
  const response = await exportGet(
    new NextRequest("http://localhost/api/engagements/owned-engagement/export?format=json"),
    { params: Promise.resolve({ id: engagement.id }) },
  );
  assert.equal(response.status, 200);
  return response.json() as Promise<Record<string, unknown>>;
}

test("JSON engagement export includes all nine existing plan documents", async () => {
  const directory = await mkdtemp(join(testTempRoot, "engagement-json-export-"));
  const previousWorkspace = process.env.WORKSPACE_PATH;
  try {
    const exported = await exportJson(directory, planDocuments);
    assert.deepEqual(Object.keys(exported.planDocs as object).sort(), [...planDocuments].sort());
  } finally {
    if (previousWorkspace === undefined) delete process.env.WORKSPACE_PATH;
    else process.env.WORKSPACE_PATH = previousWorkspace;
    globalThis.__engagementExportTestRows = undefined;
    await rm(directory, { recursive: true, force: true });
  }
});

test("JSON engagement export omits plan documents that do not exist", async () => {
  const directory = await mkdtemp(join(testTempRoot, "engagement-json-export-"));
  const previousWorkspace = process.env.WORKSPACE_PATH;
  try {
    const exported = await exportJson(directory, ["roe", "cleanup"]);
    assert.deepEqual(Object.keys(exported.planDocs as object).sort(), ["cleanup", "roe"]);
  } finally {
    if (previousWorkspace === undefined) delete process.env.WORKSPACE_PATH;
    else process.env.WORKSPACE_PATH = previousWorkspace;
    globalThis.__engagementExportTestRows = undefined;
    await rm(directory, { recursive: true, force: true });
  }
});
