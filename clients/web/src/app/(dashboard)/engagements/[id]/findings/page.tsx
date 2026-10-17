"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { FileWarning, ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
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

const severityColors: Record<string, string> = {
  critical: "bg-red-500/10 text-red-800 dark:text-red-400 border-red-500/20",
  high: "bg-orange-500/10 text-orange-800 dark:text-orange-400 border-orange-500/20",
  medium: "bg-yellow-500/10 text-yellow-800 dark:text-yellow-400 border-yellow-500/20",
  low: "bg-blue-500/10 text-blue-800 dark:text-blue-400 border-blue-500/20",
  informational: "bg-slate-500/10 text-slate-800 dark:text-slate-400 border-slate-500/20",
  unspecified: "bg-slate-500/10 text-slate-800 dark:text-slate-400 border-slate-500/20",
};

const severityOrder: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  informational: 4,
  unspecified: 5,
};

const verificationColors: Record<string, string> = {
  verified: "bg-emerald-500/10 text-emerald-800 dark:text-emerald-400 border-emerald-500/20",
  false_positive: "bg-slate-500/10 text-slate-800 dark:text-slate-400 border-slate-500/20 line-through",
  unverified: "bg-amber-500/10 text-amber-800 dark:text-amber-400 border-amber-500/20",
};

export default function FindingsPage() {
  const params = useParams();
  const engagementId = params.id as string;
  const [findings, setFindings] = useState<Finding[]>([]);
  const [loading, setLoading] = useState(true);
  const [filterSeverity, setFilterSeverity] = useState<string>("all");
  const [selectedFinding, setSelectedFinding] = useState<Finding | null>(null);

  useEffect(() => {
    let active = true;
    fetch(`/api/engagements/${engagementId}/findings`)
      .then((res) => {
        if (!res.ok) throw new Error("fetch failed");
        return res.json();
      })
      .then((data: Finding[]) => {
        if (!active) return;
        const sorted = [...data].sort(
          (a, b) =>
            (severityOrder[a.severity] ?? 5) - (severityOrder[b.severity] ?? 5)
        );
        setFindings(sorted);
      })
      .catch(() => {
        if (!active) return;
        setFindings([]);
      })
      .finally(() => {
        if (!active) return;
        setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [engagementId]);

  const filtered =
    filterSeverity === "all"
      ? findings
      : findings.filter((f) => f.severity === filterSeverity);

  if (selectedFinding) {
    return (
      <div className="space-y-6">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setSelectedFinding(null)}
        >
          <ArrowLeft className="mr-2 h-4 w-4" />
          Back to findings
        </Button>

        <div className="space-y-1">
          <h1 className="text-2xl font-bold tracking-tight">
            {selectedFinding.title}
          </h1>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline" className="text-xs">
              {selectedFinding.id}
            </Badge>
            <Badge
              variant="outline"
              className={severityColors[selectedFinding.severity] ?? ""}
            >
              {selectedFinding.severity}
            </Badge>
            {selectedFinding.cvssScore != null && (
              <Badge variant="outline" className="font-mono text-xs">
                CVSS {selectedFinding.cvssScore.toFixed(1)}
              </Badge>
            )}
            {selectedFinding.resultKind && selectedFinding.resultKind !== "vulnerability" && (
              <Badge variant="outline" className="text-xs capitalize">
                {selectedFinding.resultKind.replace(/_/g, " ")}
              </Badge>
            )}
            {selectedFinding.verificationStatus && (
              <Badge
                variant="outline"
                className={verificationColors[selectedFinding.verificationStatus] ?? ""}
              >
                {selectedFinding.verificationStatus.replace(/_/g, " ")}
              </Badge>
            )}
            {selectedFinding.reportStatus && (
              <Badge
                variant={selectedFinding.reportStatus === "complete" ? "secondary" : "outline"}
                className="text-xs capitalize"
              >
                report: {selectedFinding.reportStatus}
              </Badge>
            )}
            {selectedFinding.cwe?.map((c) => (
              <Badge key={c} variant="secondary" className="text-[10px] font-mono">{c}</Badge>
            ))}
            {selectedFinding.mitre?.map((m) => (
              <Badge key={m} variant="secondary" className="text-[10px] font-mono">{m}</Badge>
            ))}
          </div>
        </div>

        {selectedFinding.cvssVector && (
          <div className="rounded-lg border border-border/50 bg-muted/30 px-4 py-2">
            <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">CVSS Vector</span>
            <p className="mt-0.5 font-mono text-xs text-muted-foreground">{selectedFinding.cvssVector}</p>
          </div>
        )}

        {selectedFinding.verificationRationale && (
          <div className="rounded-lg border border-border/50 bg-muted/30 px-4 py-2">
            <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
              Verification Rationale
            </span>
            <p className="mt-0.5 whitespace-pre-wrap text-xs text-muted-foreground">
              {selectedFinding.verificationRationale}
            </p>
          </div>
        )}

        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Description</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="whitespace-pre-wrap text-sm text-muted-foreground">
                {selectedFinding.description}
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Attack Vector</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="whitespace-pre-wrap text-sm text-muted-foreground">
                {selectedFinding.attackVector}
              </p>
            </CardContent>
          </Card>

          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle className="text-base">Evidence</CardTitle>
            </CardHeader>
            <CardContent>
              <pre className="overflow-auto whitespace-pre-wrap rounded-lg bg-muted p-4 font-mono text-xs text-muted-foreground">
                {selectedFinding.evidence}
              </pre>
            </CardContent>
          </Card>

          {selectedFinding.affectedAssets.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Affected Assets</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="space-y-1">
                  {selectedFinding.affectedAssets.map((asset, i) => (
                    <li
                      key={i}
                      className="text-sm text-muted-foreground"
                    >
                      {asset}
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}

          {selectedFinding.remediation && (
            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle className="text-base">Remediation</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="whitespace-pre-wrap text-sm text-muted-foreground">
                  {selectedFinding.remediation}
                </p>
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Findings</h1>
          <p className="text-sm text-muted-foreground">
            Vulnerabilities discovered in this engagement
          </p>
        </div>
        <Select value={filterSeverity} onValueChange={(v) => setFilterSeverity(v ?? "all")}>
          <SelectTrigger className="w-40">
            <SelectValue placeholder="Filter severity" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All severities</SelectItem>
            <SelectItem value="critical">Critical</SelectItem>
            <SelectItem value="high">High</SelectItem>
            <SelectItem value="medium">Medium</SelectItem>
            <SelectItem value="low">Low</SelectItem>
            <SelectItem value="informational">Informational</SelectItem>
            <SelectItem value="unspecified">Unspecified</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <Card>
        <CardContent className="pt-6">
          {loading ? (
            <div className="space-y-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex items-center justify-center py-12 text-sm text-muted-foreground">
              <div className="text-center">
                <FileWarning className="mx-auto mb-3 h-8 w-8 opacity-50" />
                <p>No findings yet.</p>
              </div>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>ID</TableHead>
                  <TableHead>Title</TableHead>
                  <TableHead>Severity</TableHead>
                  <TableHead>CVSS</TableHead>
                  <TableHead>Assets</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((finding) => (
                  <TableRow
                    key={finding.id}
                    className="cursor-pointer"
                    onClick={() => setSelectedFinding(finding)}
                  >
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {finding.id}
                    </TableCell>
                    <TableCell className="font-medium">
                      {finding.title}
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant="outline"
                        className={severityColors[finding.severity] ?? ""}
                      >
                        {finding.severity}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {finding.cvssScore != null ? finding.cvssScore.toFixed(1) : "—"}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {finding.affectedAssets.length > 0
                        ? finding.affectedAssets.join(", ")
                        : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
