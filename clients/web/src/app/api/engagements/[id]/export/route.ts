import { requireAuth, AuthError } from "@/lib/auth-bridge";
import { prisma } from "@/lib/prisma";
import { NextRequest, NextResponse } from "next/server";
import * as fs from "fs/promises";
import * as path from "path";

const PLAN_DOCS = [
  "roe.json",
  "conops.json",
  "deconfliction.json",
  "opplan.json",
  "threat-profile.json",
  "contact.json",
  "data-handling.json",
  "abort.json",
  "cleanup.json",
] as const;

export async function GET(
  req: NextRequest,
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

  const format = req.nextUrl.searchParams.get("format") ?? "json";
  const WORKSPACE = process.env.WORKSPACE_PATH ?? path.join(process.env.HOME ?? "", ".decepticon", "workspace");
  const wsPath = path.join(WORKSPACE, engagement.name);

  // Collect all engagement data
  const exportData: Record<string, unknown> = {
    engagement: {
      id: engagement.id,
      name: engagement.name,
      status: engagement.status,
      targetType: engagement.targetType,
      targetValue: engagement.targetValue,
      createdAt: engagement.createdAt,
      updatedAt: engagement.updatedAt,
    },
    planDocs: {} as Record<string, unknown>,
    findings: [] as unknown[],
  };

  // Read plan documents
  const planDir = path.join(wsPath, "plan");
  for (const docName of PLAN_DOCS) {
    try {
      const content = await fs.readFile(path.join(planDir, docName), "utf-8");
      (exportData.planDocs as Record<string, unknown>)[docName.replace(".json", "")] = JSON.parse(content);
    } catch {
      // File doesn't exist
    }
  }

  // Read findings
  const findingsDir = path.join(wsPath, "findings");
  try {
    const files = await fs.readdir(findingsDir);
    for (const file of files.sort()) {
      if (file.startsWith("FIND-") && file.endsWith(".md")) {
        try {
          const content = await fs.readFile(path.join(findingsDir, file), "utf-8");
          (exportData.findings as unknown[]).push({
            id: file.replace(".md", ""),
            content,
          });
        } catch {
          // skip unreadable
        }
      }
    }
  } catch {
    // No findings dir
  }

  if (format === "json") {
    return new NextResponse(JSON.stringify(exportData, null, 2), {
      headers: {
        "Content-Type": "application/json",
        "Content-Disposition": `attachment; filename="${engagement.name}-export.json"`,
      },
    });
  }

  // Markdown format
  const md = buildMarkdownExport(exportData);
  return new NextResponse(md, {
    headers: {
      "Content-Type": "text/markdown",
      "Content-Disposition": `attachment; filename="${engagement.name}-export.md"`,
    },
  });
}

function buildMarkdownExport(data: Record<string, unknown>): string {
  const eng = data.engagement as Record<string, unknown>;
  const findings = data.findings as Array<{ id: string; content: string }>;
  const planDocs = data.planDocs as Record<string, unknown>;

  const lines: string[] = [
    `# ${eng.name} — Engagement Export`,
    "",
    `**Status:** ${eng.status}`,
    `**Target:** ${eng.targetValue} (${eng.targetType})`,
    `**Created:** ${eng.createdAt}`,
    "",
  ];

  if (planDocs.opplan) {
    const opplan = planDocs.opplan as { objectives?: Array<{ id: string; title: string; status: string }> };
    lines.push("## OPPLAN", "");
    lines.push("| ID | Title | Status |", "|---|---|---|");
    for (const obj of opplan.objectives ?? []) {
      lines.push(`| ${obj.id} | ${obj.title} | ${obj.status} |`);
    }
    lines.push("");
  }

  if (findings.length > 0) {
    lines.push("## Findings", "");
    for (const f of findings) {
      lines.push(`### ${f.id}`, "", f.content, "");
    }
  }

  return lines.join("\n");
}
