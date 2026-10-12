import { requireAuth, AuthError } from "@/lib/auth-bridge";
import { prisma } from "@/lib/prisma";
import { resolveEngagementDir } from "@/lib/workspace";
import { NextRequest, NextResponse } from "next/server";
import * as fs from "fs/promises";
import * as path from "path";
import { createHash } from "node:crypto";

const WORKSPACE = process.env.WORKSPACE_PATH ?? path.join(process.env.HOME ?? "", ".decepticon", "workspace");

const PLAN_DOCS = ["opplan", "roe", "threat-profile", "conops", "deconfliction",
  "contact", "data-handling", "abort", "cleanup"] as const;
const BUNDLE_DOCS = [
  "roe.json", "threat-profile.json", "conops.json", "deconfliction.json",
  "contact.json", "data-handling.json", "abort.json", "cleanup.json",
] as const;

async function redApproved(root: string): Promise<boolean> {
  try {
    const approved = (await fs.readFile(path.join(root, ".red-approved"), "utf-8")).trim();
    const roeBytes = await fs.readFile(path.join(root, "plan", "roe.json"));
    if (approved === `roe:${createHash("sha256").update(roeBytes).digest("hex")}`) return true;
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

export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  let userId: string;
  try { ({ userId } = await requireAuth()); }
  catch (error) {
    if (error instanceof AuthError) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    throw error;
  }
  const { id } = await params;
  const engagement = await prisma.engagement.findFirst({ where: { id, userId } });
  if (!engagement) return NextResponse.json({ error: "Not found" }, { status: 404 });
  if (!engagement.authorizationConfirmed) return NextResponse.json({ error: "Authorization is not confirmed" }, { status: 403 });
  let root: string;
  try { root = resolveEngagementDir(engagement.name, WORKSPACE); }
  catch { return NextResponse.json({ error: "Invalid engagement workspace" }, { status: 400 }); }
  try {
    const roe = JSON.parse(await fs.readFile(path.join(root, "plan", "roe.json"), "utf-8")) as {
      in_scope?: Array<{ target?: string }>;
      authorization_reference?: string;
    };
    if (!roe.authorization_reference?.trim() || !roe.in_scope?.some((entry) => entry.target === engagement.targetValue)) {
      return NextResponse.json({ error: "RoE does not match the authorized target" }, { status: 409 });
    }
    const digest = createHash("sha256");
    for (const filename of BUNDLE_DOCS) {
      digest.update(filename).update("\0").update(await fs.readFile(path.join(root, "plan", filename))).update("\0");
    }
    const current = digest.digest("hex");
    const ready = (await fs.readFile(path.join(root, ".planning-draft-ready"), "utf-8")).trim();
    if (ready !== current) return NextResponse.json({ error: "Plan changed after validation. Complete Plan validation again." }, { status: 409 });
    await fs.writeFile(path.join(root, ".red-approved"), `${current}\n`, { mode: 0o600 });
    return NextResponse.json({ redApproved: true, digest: current });
  } catch {
    return NextResponse.json({ error: "Complete the eight-document Plan before approving Red." }, { status: 409 });
  }
}
