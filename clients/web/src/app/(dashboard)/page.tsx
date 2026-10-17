"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Crosshair, FileWarning, Shield, AlertTriangle, ArrowRight } from "lucide-react";

interface Engagement {
  id: string;
  name: string;
  status: string;
  targetType: string;
  createdAt: string;
}

interface Finding {
  id: string;
  title: string;
  severity: string;
  engagementId?: string;
  engagementName?: string;
}

const metricDefs = [
  {
    key: "active",
    title: "Active Engagements",
    icon: Crosshair,
    gradient: "from-violet-500/20 to-purple-500/20",
    iconColor: "text-violet-800 dark:text-violet-400",
    borderGlow: "hover:border-violet-500/30",
  },
  {
    key: "findings",
    title: "Total Findings",
    icon: FileWarning,
    gradient: "from-amber-500/20 to-orange-500/20",
    iconColor: "text-amber-800 dark:text-amber-400",
    borderGlow: "hover:border-amber-500/30",
  },
  {
    key: "critical",
    title: "Critical Vulnerabilities",
    icon: AlertTriangle,
    gradient: "from-red-500/20 to-rose-500/20",
    iconColor: "text-red-800 dark:text-red-400",
    borderGlow: "hover:border-red-500/30",
  },
  {
    key: "verified",
    title: "Defenses Verified",
    icon: Shield,
    gradient: "from-emerald-500/20 to-green-500/20",
    iconColor: "text-emerald-800 dark:text-emerald-400",
    borderGlow: "hover:border-emerald-500/30",
  },
];

const severityColors: Record<string, { color: string; barColor: string }> = {
  critical: { color: "bg-red-500", barColor: "bg-red-500/80" },
  high: { color: "bg-orange-500", barColor: "bg-orange-500/80" },
  medium: { color: "bg-yellow-500", barColor: "bg-yellow-500/80" },
  low: { color: "bg-blue-500", barColor: "bg-blue-500/80" },
  informational: { color: "bg-slate-500", barColor: "bg-slate-500/80" },
};

const severityOrder = ["critical", "high", "medium", "low", "informational"];
const severityBadge: Record<string, string> = {
  critical: "bg-red-500/20 text-red-800 dark:text-red-300",
  high: "bg-orange-500/20 text-orange-800 dark:text-orange-300",
  medium: "bg-yellow-500/20 text-yellow-800 dark:text-yellow-300",
  low: "bg-blue-500/20 text-blue-800 dark:text-blue-300",
  informational: "bg-slate-500/20 text-slate-800 dark:text-slate-300",
};

const statusBadge: Record<string, string> = {
  running: "bg-amber-500/20 text-amber-800 dark:text-amber-300",
  completed: "bg-emerald-500/20 text-emerald-800 dark:text-emerald-300",
  planning: "bg-violet-500/20 text-violet-800 dark:text-violet-300",
  draft: "bg-zinc-500/20 text-zinc-800 dark:text-zinc-300",
  failed: "bg-red-500/20 text-red-800 dark:text-red-300",
};

export default function DashboardPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [engagements, setEngagements] = useState<Engagement[]>([]);
  const [allFindings, setAllFindings] = useState<Finding[]>([]);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const engRes = await fetch("/api/engagements");
        if (!engRes.ok || !active) return;
        const engs: Engagement[] = await engRes.json();
        if (!active) return;
        setEngagements(engs);

        // Fetch findings only for engagements that may have results
        const activeEngs = engs.filter(e => e.status !== "draft" && e.status !== "planning");
        const findingsPromises = activeEngs.map(async (eng) => {
          try {
            const res = await fetch(`/api/engagements/${eng.id}/findings`);
            if (!res.ok) return [];
            const findings: Finding[] = await res.json();
            return findings.map((f) => ({ ...f, engagementId: eng.id, engagementName: eng.name }));
          } catch {
            return [];
          }
        });
        const results = await Promise.all(findingsPromises);
        if (!active) return;
        setAllFindings(results.flat());
      } catch {
        if (active) setError("Failed to load dashboard data");

      } finally {
        if (active) setLoading(false);
      }
    }
    load();
    return () => { active = false; };
  }, []);

  const activeCount = engagements.filter((e) => e.status === "running").length;
  const completedCount = engagements.filter((e) => e.status === "completed").length;
  const criticalCount = allFindings.filter((f) => f.severity === "critical").length;

  const metricValues: Record<string, string> = {
    active: String(activeCount),
    findings: String(allFindings.length),
    critical: String(criticalCount),
    verified: String(completedCount),
  };

  const severityCounts: Record<string, number> = {};
  for (const s of severityOrder) severityCounts[s] = 0;
  for (const f of allFindings) {
    const s = f.severity?.toLowerCase() ?? "medium";
    if (s in severityCounts) severityCounts[s]++;
  }
  const totalFindings = allFindings.length || 1; // avoid div by zero

  const recentEngagements = [...engagements]
    .sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime())
    .slice(0, 5);

  const latestFindings = allFindings.slice(0, 5);

  if (loading) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Dashboard</h1>
          <p className="text-sm text-muted-foreground">Overview of your security testing operations</p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[1, 2, 3, 4].map((i) => (
            <Card key={i}><CardContent className="pt-6"><Skeleton className="h-16 w-full" /></CardContent></Card>
          ))}
        </div>
        <Card><CardContent className="pt-6"><Skeleton className="h-40 w-full" /></CardContent></Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Dashboard</h1>
        <p className="text-sm text-muted-foreground">Overview of your security testing operations</p>
      </div>
      {error && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-3 text-sm text-red-800 dark:text-red-400">
          {error}
        </div>
      )}

      {/* Metric Cards — CTEM style with gradient backgrounds */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {metricDefs.map((metric) => (
          <Card
            key={metric.title}
            className={`group relative overflow-hidden transition-colors duration-200 ${metric.borderGlow}`}
          >
            <div className={`absolute inset-0 bg-gradient-to-br ${metric.gradient} opacity-0 transition-opacity duration-300 group-hover:opacity-100`} />
            <CardHeader className="relative flex flex-row items-center justify-between pb-2">
              <CardTitle className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                {metric.title}
              </CardTitle>
              <div className={`flex h-8 w-8 items-center justify-center rounded-lg bg-white/5 ${metric.iconColor}`}>
                <metric.icon className="h-4 w-4" />
              </div>
            </CardHeader>
            <CardContent className="relative">
              <div className="flex items-end gap-2">
                <span className="text-4xl font-bold tracking-tight">{metricValues[metric.key]}</span>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Severity Distribution */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Severity Distribution</CardTitle>
          <CardDescription>Findings breakdown by severity level</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {severityOrder.map((sev) => {
              const count = severityCounts[sev] ?? 0;
              const pct = allFindings.length > 0 ? (count / totalFindings) * 100 : 0;
              const colors = severityColors[sev];
              return (
                <div key={sev} className="flex items-center gap-3">
                  <div className="flex w-20 items-center gap-2">
                    <div className={`h-2.5 w-2.5 rounded-full ${colors.color}`} />
                    <span className="text-sm capitalize text-muted-foreground">{sev === "informational" ? "Info" : sev}</span>
                  </div>
                  <div className="flex-1">
                    <div className="h-2 overflow-hidden rounded-full bg-secondary">
                      <div
                        className={`h-full rounded-full ${colors.barColor} transition-all duration-500`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                  <Badge variant="secondary" className="min-w-[2rem] justify-center font-mono text-xs">
                    {count}
                  </Badge>
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* Recent Activity Grid */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <div>
              <CardTitle className="text-base">Recent Engagements</CardTitle>
              <CardDescription>Your latest red team operations</CardDescription>
            </div>
            <Link href="/engagements" className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1">
              View all <ArrowRight className="h-3 w-3" />
            </Link>
          </CardHeader>
          <CardContent>
            {recentEngagements.length === 0 ? (
              <div className="flex items-center justify-center py-8 text-sm text-muted-foreground">
                No engagements yet
              </div>
            ) : (
              <div className="space-y-2">
                {recentEngagements.map((eng) => (
                  <Link key={eng.id} href={`/engagements/${eng.id}`} className="flex items-center justify-between rounded-lg border border-border/50 p-3 transition-colors hover:bg-accent/50">
                    <div>
                      <span className="text-sm font-medium">{eng.name}</span>
                      <p className="text-xs text-muted-foreground">{new Date(eng.createdAt).toLocaleDateString()}</p>
                    </div>
                    <Badge className={`text-xs ${statusBadge[eng.status] ?? "bg-zinc-500/20 text-zinc-800 dark:text-zinc-300"}`}>
                      {eng.status}
                    </Badge>
                  </Link>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Latest Findings</CardTitle>
            <CardDescription>Recently discovered vulnerabilities</CardDescription>
          </CardHeader>
          <CardContent>
            {latestFindings.length === 0 ? (
              <div className="flex items-center justify-center py-8 text-sm text-muted-foreground">
                No findings yet
              </div>
            ) : (
              <div className="space-y-2">
                {latestFindings.map((f) => (
                  <div key={`${f.engagementId}-${f.id}`} className="flex items-center justify-between rounded-lg border border-border/50 p-3">
                    <div className="min-w-0 flex-1">
                      <span className="text-sm font-medium truncate block">{f.title}</span>
                      <p className="text-xs text-muted-foreground">{f.engagementName}</p>
                    </div>
                    <Badge className={`shrink-0 text-xs ${severityBadge[f.severity] ?? "bg-zinc-500/20 text-zinc-800 dark:text-zinc-300"}`}>
                      {f.severity}
                    </Badge>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
