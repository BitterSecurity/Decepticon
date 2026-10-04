import { requireAuth, AuthError } from "@/lib/auth-bridge";
import { prisma } from "@/lib/prisma";
import { resolveEngagementDir } from "@/lib/workspace";
import { NextRequest, NextResponse } from "next/server";
import * as fs from "fs/promises";
import * as path from "path";
import { createHash } from "node:crypto";

const WORKSPACE = process.env.WORKSPACE_PATH ?? path.join(process.env.HOME ?? "", ".decepticon", "workspace");

const PLAN_DOCS = ["opplan", "conops", "roe", "deconfliction"] as const;
const BUNDLE_DOCS = [
  "roe.json", "threat-profile.json", "conops.json", "deconfliction.json",
  "contact.json", "data-handling.json", "abort.json", "cleanup.json",
] as const;

async function redApproved(root: string): Promise<boolean> {
  try {
    const approved = (await fs.readFile(path.join(root, ".red-approved"), "utf-8")).trim();
    const digest = createHash("sha256");
    for (const filename of BUNDLE_DOCS) {
      digest.update(filename);
      digest.update("\0");
      digest.update(await fs.readFile(path.join(root, "plan", filename)));
      digest.update("\0");
    }
    return approved === digest.digest("hex");
  } catch {
    return false;
  }
}

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  let userId: string;
  try {
    ({ userId } = await requireAuth());
  } catch (e) {
    if (e instanceof AuthError) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    throw e;
  }

  const { id } = await params;
  const engagement = await prisma.engagement.findFirst({
    where: { id, userId },
  });

  if (!engagement) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  const docs: Record<string, unknown> = { redApproved: false };

  let engagementDir: string;
  let planDir: string;
  try {
    engagementDir = resolveEngagementDir(engagement.name, WORKSPACE);
    planDir = path.join(engagementDir, "plan");
  } catch {
    return NextResponse.json(docs);
  }

  for (const name of PLAN_DOCS) {
    try {
      const content = await fs.readFile(path.join(planDir, `${name}.json`), "utf-8");
      docs[name] = JSON.parse(content);
    } catch {
      // File doesn't exist yet
    }
  }

  docs.redApproved = await redApproved(engagementDir);

  return NextResponse.json(docs);
}
