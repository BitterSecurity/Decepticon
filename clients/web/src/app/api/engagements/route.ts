import { requireAuth, AuthError } from "@/lib/auth-bridge";
import { prisma } from "@/lib/prisma";
import { SLUG_RE, VALID_TARGET_TYPES } from "@/lib/workspace";
import { NextRequest, NextResponse } from "next/server";
import * as fs from "fs/promises";
import * as path from "path";
import { directApproval, directRoe, validateDirectTarget } from "@/lib/direct-scope";
import { isValidEngagementSlug } from "@/lib/engagement-slug";

const WORKSPACE = process.env.WORKSPACE_PATH ?? path.join(process.env.HOME ?? "", ".decepticon", "workspace");

const WORKSPACE_SUBDIRS = ["plan"];

function isEngagementWorkspaceDir(name: string) {
  return SLUG_RE.test(name) && !name.startsWith(".");
}

export async function GET() {
  try {
    const { userId } = await requireAuth();

    const engagements = (await prisma.engagement.findMany({
      where: { userId },
      orderBy: { createdAt: "desc" },
    })).filter((eng) => isEngagementWorkspaceDir(eng.name));

    // Auto-import workspace dirs created by CLI that are not yet in DB
    try {
      const entries = await fs.readdir(WORKSPACE, { withFileTypes: true });
      const wsDirs = entries
        .filter((e) => e.isDirectory() && isEngagementWorkspaceDir(e.name))
        .map((e) => e.name);
      const knownNames = new Set(engagements.map((e) => e.name));

      for (const dir of wsDirs) {
        if (!knownNames.has(dir)) {
          const wsPath = path.join(WORKSPACE, dir);
          const imported = await prisma.engagement.create({
            data: {
              name: dir,
              targetType: "web_url",
              targetValue: "imported from CLI",
              status: "running",
              userId,
              workspacePath: wsPath,
            },
          });
          engagements.unshift(imported);
        }
      }
    } catch {
      // Workspace dir may not exist yet — skip
    }

    return NextResponse.json(engagements);
  } catch (e) {
    if (e instanceof AuthError) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    console.error("GET /api/engagements error:", e);
    return NextResponse.json(
      { error: e instanceof Error ? e.message : "Internal server error" },
      { status: 500 }
    );
  }
}

export async function POST(req: NextRequest) {
  try {
    const { userId } = await requireAuth();

    const body = await req.json();
    const { name, targetType, targetValue, authorizationConfirmed, authorizationReference, initialMode } = body;

    if (!name || !targetType || !targetValue) {
      return NextResponse.json(
        { error: "Missing required fields: name, targetType, targetValue" },
        { status: 400 }
      );
    }
    if (authorizationConfirmed !== true) {
      return NextResponse.json(
        { error: "Explicit authorization confirmation is required" },
        { status: 400 }
      );
    }

    if (initialMode !== undefined && !["plan", "red", "blue"].includes(initialMode)) {
      return NextResponse.json({ error: "Invalid initial mode" }, { status: 400 });
    }

    if (!isValidEngagementSlug(name)) {
      return NextResponse.json(
        { error: "Invalid engagement name — must be 3-64 chars, lowercase letters / digits / internal hyphens" },
        { status: 400 },
      );
    }

    let roeBytes: Buffer;
    let normalizedTarget: string;
    try {
      normalizedTarget = validateDirectTarget(targetType, targetValue);
      roeBytes = Buffer.from(`${JSON.stringify(directRoe(name, targetType, normalizedTarget, authorizationReference ?? ""), null, 2)}\n`);
    } catch (error) {
      return NextResponse.json({ error: error instanceof Error ? error.message : "Invalid direct scope" }, { status: 400 });
    }

    if (!VALID_TARGET_TYPES.includes(targetType)) {
      return NextResponse.json(
        { error: `Invalid targetType. Must be one of: ${VALID_TARGET_TYPES.join(", ")}` },
        { status: 400 }
      );
    }

    const existing = await prisma.engagement.findFirst({
      where: { name, userId },
    });
    if (existing) {
      return NextResponse.json(
        { error: `An engagement named '${name}' already exists` },
        { status: 409 },
      );
    }
    const workspaceRoot = path.resolve(WORKSPACE);
    const wsPath = path.resolve(workspaceRoot, name);
    if (path.dirname(wsPath) !== workspaceRoot) {
      return NextResponse.json({ error: "Invalid engagement workspace path" }, { status: 400 });
    }
    await fs.mkdir(workspaceRoot, { recursive: true });
    try {
      await fs.mkdir(wsPath, { recursive: false, mode: 0o700 });
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "EEXIST") {
        return NextResponse.json({ error: "An engagement workspace with this name already exists" }, { status: 409 });
      }
      throw error;
    }

    try {
      await Promise.all(WORKSPACE_SUBDIRS.map((sub) => fs.mkdir(path.join(wsPath, sub), { mode: 0o700 })));
      await fs.writeFile(path.join(wsPath, "plan", "roe.json"), roeBytes, { flag: "wx", mode: 0o600 });
      if (initialMode !== "plan" && initialMode !== "blue") {
        await fs.writeFile(path.join(wsPath, ".red-approved"), directApproval(roeBytes), { flag: "wx", mode: 0o600 });
      }
      const engagement = await prisma.engagement.create({
        data: {
          name,
          targetType,
          targetValue: normalizedTarget,
          userId,
          workspacePath: wsPath,
          authorizationConfirmed,
        },
      });
      return NextResponse.json(engagement, { status: 201 });
    } catch (error) {
      await fs.rm(wsPath, { recursive: true, force: true });
      throw error;
    }
  } catch (e) {
    if (e instanceof AuthError) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    console.error("POST /api/engagements error:", e);
    return NextResponse.json(
      { error: e instanceof Error ? e.message : "Internal server error" },
      { status: 500 }
    );
  }
}
