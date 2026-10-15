import { requireAuth, AuthError } from "@/lib/auth-bridge";
import { prisma } from "@/lib/prisma";
import { resolveEngagementDir } from "@/lib/workspace";
import { NextRequest, NextResponse } from "next/server";
import * as fs from "fs/promises";
import * as path from "path";

interface Finding {
  id: string;
  title: string;
  severity: string;
  description: string;
  evidence: string;
  attackVector: string;
  affectedAssets: string[];
  cvssScore?: number;
  cvssVector?: string;
  cwe?: string[];
  mitre?: string[];
  remediation?: string;
  resultKind?: string;
  verificationStatus?: string;
  verificationRationale?: string;
  reportStatus?: string;
}

// ponytail: flat-scalar frontmatter only, matching the finding-protocol
// template's `key: value` lines. Nested YAML (e.g. evidence_manifest) isn't
// needed by this projection and isn't parsed; add a real YAML parser if a
// future field here needs list/map frontmatter.
function parseFrontmatter(content: string): { metadata: Record<string, string>; body: string } {
  const lines = content.split("\n");
  if (lines[0]?.trim() !== "---") {
    return { metadata: {}, body: content };
  }
  const end = lines.findIndex((line, i) => i > 0 && line.trim() === "---");
  if (end === -1) {
    return { metadata: {}, body: content };
  }
  const metadata: Record<string, string> = {};
  for (const line of lines.slice(1, end)) {
    const match = line.match(/^([A-Za-z_][\w]*):\s*(.*)$/);
    if (!match) continue;
    let value = match[2].trim();
    const quoted = value.match(/^"([^"]*)"$|^'([^']*)'$/);
    if (quoted) {
      value = quoted[1] ?? quoted[2] ?? "";
    } else {
      value = value.replace(/\s+#.*$/, "").trim();
    }
    if (value) metadata[match[1]] = value;
  }
  return { metadata, body: lines.slice(end + 1).join("\n") };
}

function parseFindingMarkdown(content: string, filename: string): Finding {
  const { metadata, body } = parseFrontmatter(content);
  const lines = body.split("\n");
  let title = metadata.title ?? filename;
  let severity = metadata.severity;
  let description = "";
  let evidence = "";
  let attackVector = "";
  const affectedAssets: string[] = [];
  let cvssScore: number | undefined;
  let cvssVector: string | undefined;
  const cwe: string[] = [];
  const mitre: string[] = [];
  let remediation = "";

  let currentSection = "";

  for (const line of lines) {
    const trimmed = line.trim();

    if (trimmed.startsWith("# ")) {
      if (!metadata.title) title = trimmed.slice(2).trim();
      continue;
    }

    if (trimmed.startsWith("## ")) {
      currentSection = trimmed.slice(3).trim().toLowerCase();
      continue;
    }

    if (!metadata.severity) {
      const severityMatch = trimmed.toLowerCase();
      if (severityMatch.includes("severity") && trimmed.includes(":")) {
        const val = trimmed.split(":")[1]?.trim().replace(/\*/g, "").toLowerCase();
        if (["critical", "high", "medium", "low", "informational"].includes(val)) {
          severity = val;
        }
      }
    }
    if (trimmed.toLowerCase().startsWith("cvss score") && trimmed.includes(":")) {
      const val = trimmed.split(":")[1]?.trim();
      if (val) cvssScore = parseFloat(val) || undefined;
    }
    if (trimmed.toLowerCase().startsWith("cvss vector") && trimmed.includes(":")) {
      cvssVector = trimmed.split(":").slice(1).join(":").trim() || undefined;
    }

    if ((currentSection === "summary" || currentSection === "description") && trimmed) {
      description += (description ? "\n" : "") + trimmed;
    }
    if (currentSection === "evidence" && trimmed) {
      evidence += (evidence ? "\n" : "") + trimmed;
    }
    if (
      (currentSection === "attack vector" ||
        currentSection === "attack-vector") &&
      trimmed
    ) {
      attackVector += (attackVector ? "\n" : "") + trimmed;
    }
    if (
      (currentSection === "affected" ||
        currentSection === "affected assets" ||
        currentSection === "assets") &&
      trimmed
    ) {
      if (trimmed.startsWith("-") || trimmed.startsWith("*")) {
        affectedAssets.push(trimmed.replace(/^[-*]\s*/, ""));
      }
    }
    if (currentSection === "remediation" && trimmed) {
      remediation += (remediation ? "\n" : "") + trimmed;
    }
    if ((currentSection === "cwe" || currentSection === "weaknesses") && trimmed) {
      const cweMatch = trimmed.match(/CWE-\d+/g);
      if (cweMatch) cwe.push(...cweMatch);
    }
    if ((currentSection === "mitre" || currentSection === "mitre att&ck" || currentSection === "techniques") && trimmed) {
      const mitreMatch = trimmed.match(/T\d{4}(\.\d+)?/g);
      if (mitreMatch) mitre.push(...mitreMatch);
    }
  }

  return {
    id: filename.replace(".md", ""),
    title,
    severity: severity || "unspecified",
    description: description || "No description available.",
    evidence: evidence || "No evidence recorded.",
    attackVector: attackVector || "Unknown",
    affectedAssets,
    ...(cvssScore != null && { cvssScore }),
    ...(cvssVector && { cvssVector }),
    ...(cwe.length > 0 && { cwe }),
    ...(mitre.length > 0 && { mitre }),
    ...(remediation && { remediation }),
    ...(metadata.result_kind && { resultKind: metadata.result_kind }),
    ...(metadata.verification_status && { verificationStatus: metadata.verification_status }),
    ...(metadata.verification_rationale && { verificationRationale: metadata.verification_rationale }),
    ...(metadata.report_status && { reportStatus: metadata.report_status }),
  };
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

  const findings: Finding[] = [];

  {
    const WORKSPACE = process.env.WORKSPACE_PATH ?? path.join(process.env.HOME ?? "", ".decepticon", "workspace");
    try {
      const wsPath = resolveEngagementDir(engagement.name, WORKSPACE);
      const findingsDir = path.join(wsPath, "findings");
      const files = await fs.readdir(findingsDir);
      for (const file of files.sort()) {
        if (file.startsWith("FIND-") && file.endsWith(".md")) {
          const content = await fs.readFile(
            path.join(findingsDir, file),
            "utf-8"
          );
          findings.push(parseFindingMarkdown(content, file));
        }
      }
    } catch {
      // Directory doesn't exist yet
    }
  }

  return NextResponse.json(findings);
}
