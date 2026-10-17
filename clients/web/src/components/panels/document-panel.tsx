"use client";

import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { FileText, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { DocumentRef } from "@/lib/chat/types";

interface DocumentPanelProps {
  open: boolean;
  onClose: () => void;
  document: DocumentRef | null;
  content?: string;
}

const typeColors: Record<string, string> = {
  roe: "bg-blue-500/10 text-blue-800 dark:text-blue-400 border-blue-500/20",
  conops: "bg-violet-500/10 text-violet-800 dark:text-violet-400 border-violet-500/20",
  opplan: "bg-emerald-500/10 text-emerald-800 dark:text-emerald-400 border-emerald-500/20",
  finding: "bg-red-500/10 text-red-800 dark:text-red-400 border-red-500/20",
  reference: "bg-amber-500/10 text-amber-800 dark:text-amber-400 border-amber-500/20",
};

// Mock document content for demo
const mockDocuments: Record<string, string> = {
  roe: `# Rules of Engagement

## Scope
- **In-scope**: app.acme-corp.com, api.acme-corp.com, 10.0.1.0/24
- **Out-of-scope**: production database direct access, DDoS testing

## Constraints
- Testing window: 2026-04-10 09:00 — 2026-04-12 18:00 UTC
- No destructive actions on production data
- Credential brute-force limited to 100 attempts/min
- All findings must be documented within 24h

## Authorization
- Authorized by: CISO, ACME Corp
- Emergency contact: security@acme-corp.com
- Kill switch: Notify SOC immediately if unintended impact detected`,

  conops: `# Concept of Operations

## Threat Actor Profile
- **Type**: Advanced Persistent Threat (APT)
- **Motivation**: Financial gain, data exfiltration
- **Capability**: High — custom tooling, zero-day exploitation
- **Target**: Customer PII, financial records, API keys

## Kill Chain
1. Reconnaissance — OSINT, service enumeration
2. Initial Access — Web application exploitation
3. Privilege Escalation — Horizontal & vertical
4. Lateral Movement — Internal network pivoting
5. Collection — Database access, credential harvesting
6. Exfiltration — Data staging and extraction`,

  opplan: `# Operations Plan

## Objectives

| ID | Phase | Title | Priority | Status |
|----|-------|-------|----------|--------|
| OBJ-001 | recon | Service Discovery | 1 | COMPLETED |
| OBJ-002 | recon | Web App Vuln Scan | 2 | COMPLETED |
| OBJ-003 | initial-access | Auth Bypass | 3 | COMPLETED |
| OBJ-004 | initial-access | SQLi Exploitation | 4 | COMPLETED |
| OBJ-005 | privesc | Horizontal Escalation | 5 | COMPLETED |
| OBJ-006 | lateral | DB Server Pivot | 6 | IN PROGRESS |
| OBJ-007 | collection | PII Access | 7 | PENDING |
| OBJ-008 | credential | Cloud Cred Harvest | 8 | PENDING |`,

  finding: `# FIND-001: SQL Injection in User Search

**Severity**: Critical (CVSS 9.8)

## Description
The /api/users/search endpoint is vulnerable to SQL injection via the \`q\` parameter.

## Evidence
\`\`\`
sqlmap -u 'https://app.acme-corp.com/api/users/search?q=test' --dbs

available databases [3]:
[*] acme_production
[*] information_schema
[*] mysql
\`\`\`

## Affected Assets
- app.acme-corp.com
- /api/users/search
- PostgreSQL database`,

  reference: `# References

| Source | Reference |
|--------|-----------|
| SUSE | CVE-2022-1292 |
| Oracle | cpujul2022.html |
| OpenSSL | vulnerabilities.html |
| Debian | dsa-5139 |
| NetApp | ntap-20220729-0004 |
| NVD | CVE-2022-1292 |
| Microsoft | security-guidance |`,
};

export function DocumentPanel({ open, onClose, document }: DocumentPanelProps) {
  if (!document) return null;

  const content = mockDocuments[document.type] ?? `# ${document.title}\n\nNo content available.`;

  return (
    <>
      {/* Backdrop blur overlay */}
      {open && (
        <div
          className="fixed inset-0 z-40 bg-background/60 backdrop-blur-sm"
          onClick={onClose}
        />
      )}

      <Sheet open={open} onOpenChange={(v) => !v && onClose()}>
        <SheetContent
          side="right"
          className="z-50 w-full border-l border-border/50 bg-card/95 p-0 backdrop-blur-md sm:w-[540px] sm:max-w-[540px]"
        >
          <SheetHeader className="border-b border-border/50 px-6 py-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
                  <FileText className="h-4 w-4 text-primary" />
                </div>
                <div>
                  <SheetTitle className="text-base">{document.title}</SheetTitle>
                  <Badge
                    variant="outline"
                    className={`mt-1 text-xs ${typeColors[document.type] ?? ""}`}
                  >
                    {document.type.toUpperCase()}
                  </Badge>
                </div>
              </div>
              <Button variant="ghost" size="icon" onClick={onClose} className="h-8 w-8">
                <X className="h-4 w-4" />
              </Button>
            </div>
          </SheetHeader>

          <ScrollArea className="h-[calc(100vh-80px)] px-6 py-4">
            <div className="prose prose-invert prose-sm max-w-none">
              {content.split("\n").map((line, i) => {
                if (line.startsWith("# ")) {
                  return (
                    <h1 key={i} className="mb-3 mt-0 text-lg font-bold text-foreground">
                      {line.slice(2)}
                    </h1>
                  );
                }
                if (line.startsWith("## ")) {
                  return (
                    <h2 key={i} className="mb-2 mt-4 text-sm font-semibold text-foreground">
                      {line.slice(3)}
                    </h2>
                  );
                }
                if (line.startsWith("```")) {
                  return <Separator key={i} className="my-2" />;
                }
                if (line.startsWith("| ")) {
                  return (
                    <div key={i} className="font-mono text-xs text-muted-foreground">
                      {line}
                    </div>
                  );
                }
                if (line.startsWith("- ")) {
                  return (
                    <div key={i} className="flex gap-2 py-0.5 text-sm text-muted-foreground">
                      <span className="text-primary">-</span>
                      <span>{line.slice(2)}</span>
                    </div>
                  );
                }
                if (line.trim() === "") return <div key={i} className="h-2" />;
                return (
                  <p key={i} className="text-sm leading-relaxed text-muted-foreground">
                    {line}
                  </p>
                );
              })}
            </div>
          </ScrollArea>
        </SheetContent>
      </Sheet>
    </>
  );
}
