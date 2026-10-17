"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { FileWarning, Network, Play, ArrowRight, ClipboardList, Download, Clock } from "lucide-react";

interface Objective {
  id: string;
  title: string;
  status: string;
  phase: string;
}

interface Finding {
  id: string;
  title: string;
  severity: string;
}

const severityBadge: Record<string, string> = {
  critical: "bg-red-500/20 text-red-800 dark:text-red-300",
  high: "bg-orange-500/20 text-orange-800 dark:text-orange-300",
  medium: "bg-yellow-500/20 text-yellow-800 dark:text-yellow-300",
  low: "bg-blue-500/20 text-blue-800 dark:text-blue-300",
  informational: "bg-slate-500/20 text-slate-800 dark:text-slate-300",
};

export default function EngagementOverviewPage() {
  const params = useParams();
  const router = useRouter();
  const id = params.id as string;
  const [loading, setLoading] = useState(true);
  const [objectives, setObjectives] = useState<Objective[]>([]);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [graphNodeCount, setGraphNodeCount] = useState(0);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        // Check if opplan exists first
        const opplanRes = await fetch(`/api/engagements/${id}/opplan`);
        if (!active) return;
        if (!opplanRes.ok) {
          if (opplanRes.status === 404) {
            router.replace(`/engagements/${id}/live?new=true`);
            return;
          }
          // Other errors — show empty state
          if (active) setLoading(false);
          return;
        }
        const opplanData = await opplanRes.json();
        if (!active) return;
        const objs: Objective[] = opplanData.objectives ?? [];
        if (objs.length === 0) {
          router.replace(`/engagements/${id}/live?new=true`);
          return;
        }
        setObjectives(objs);

        // Fetch findings and graph in parallel
        const [findingsRes, graphRes] = await Promise.all([
          fetch(`/api/engagements/${id}/findings`).catch(() => null),
          fetch(`/api/engagements/${id}/graph`).catch(() => null),
        ]);

        if (!active) return;

        if (findingsRes?.ok) {
          const f: Finding[] = await findingsRes.json();
          setFindings(f);
        }

        if (graphRes?.ok) {
          const g = await graphRes.json();
          setGraphNodeCount(g.nodes?.length ?? 0);
        }
      } catch {
        // Network error — don't redirect, just show empty state
        if (active) setLoading(false);
        return;
      }
      if (active) setLoading(false);
    }
    load();
    return () => { active = false; };
  }, [id, router]);

  if (loading) {
    return (
      <div className="space-y-6">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[1, 2, 3, 4].map((i) => (
            <Card key={i}><CardContent className="pt-6"><Skeleton className="h-20 w-full" /></CardContent></Card>
          ))}
        </div>
      </div>
    );
  }

  const completedCount = objectives.filter((o) => o.status === "completed").length;
  const blockedCount = objectives.filter((o) => o.status === "blocked").length;
  const totalObj = objectives.length;
  const progress = totalObj > 0 ? Math.round(((completedCount + blockedCount) / totalObj) * 100) : 0;
  const criticalFindings = findings.filter((f) => f.severity === "critical").length;

  const stats = [
    {
      label: "Objectives",
      value: totalObj,
      subValue: `${completedCount} completed`,
      icon: ClipboardList,
      href: "plan",
      color: "text-emerald-800 dark:text-emerald-400",
    },
    {
      label: "Findings",
      value: findings.length,
      subValue: `${criticalFindings} critical`,
      icon: FileWarning,
      href: "findings",
      color: "text-red-800 dark:text-red-400",
    },
    {
      label: "Attack Graph",
      value: graphNodeCount,
      subValue: "nodes discovered",
      icon: Network,
      href: "graph",
      color: "text-cyan-800 dark:text-cyan-400",
    },
  ];

  return (
    <div className="space-y-6">
      {/* Stats grid */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {stats.map((stat) => (
          <Link key={stat.label} href={`/engagements/${id}/${stat.href}`}>
            <Card className="group cursor-pointer transition-colors hover:border-primary/30">
              <CardHeader className="flex flex-row items-center justify-between pb-2">
                <CardTitle className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                  {stat.label}
                </CardTitle>
                <stat.icon className={`h-4 w-4 ${stat.color}`} />
              </CardHeader>
              <CardContent>
                <div className="flex items-end justify-between">
                  <div>
                    <span className="text-3xl font-bold">{stat.value}</span>
                    <p className="mt-0.5 text-xs text-muted-foreground">{stat.subValue}</p>
                  </div>
                  <ArrowRight className="h-4 w-4 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
                </div>
              </CardContent>
            </Card>
          </Link>
        ))}

        {/* Progress card */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              Progress
            </CardTitle>
            <Play className="h-4 w-4 text-amber-800 dark:text-amber-400" />
          </CardHeader>
          <CardContent>
            <div>
              <span className="text-3xl font-bold">{progress}%</span>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-secondary">
                <div
                  className="h-full rounded-full bg-primary transition-all duration-500"
                  style={{ width: `${progress}%` }}
                />
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                {completedCount}/{totalObj} objectives resolved
              </p>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Quick actions */}
      <div className="flex gap-2">
        <Link href={`/engagements/${id}/timeline`} className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs transition-colors hover:bg-accent">
          <Clock className="h-3 w-3" /> Timeline
        </Link>
        <a href={`/api/engagements/${id}/export?format=json`} className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs transition-colors hover:bg-accent">
          <Download className="h-3 w-3" /> Export JSON
        </a>
        <a href={`/api/engagements/${id}/export?format=markdown`} className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs transition-colors hover:bg-accent">
          <Download className="h-3 w-3" /> Export Markdown
        </a>
      </div>

      {/* Recent findings */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-base">Recent Findings</CardTitle>
          {findings.length > 0 && (
            <Link href={`/engagements/${id}/findings`} className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1">
              View all <ArrowRight className="h-3 w-3" />
            </Link>
          )}
        </CardHeader>
        <CardContent>
          {findings.length === 0 ? (
            <div className="flex items-center justify-center py-8 text-sm text-muted-foreground">
              No findings yet — run the engagement to discover vulnerabilities
            </div>
          ) : (
            <div className="space-y-2">
              {findings.slice(-5).reverse().map((f) => (
                <div key={f.id} className="flex items-center justify-between rounded-lg border border-border/50 p-3">
                  <div className="min-w-0 flex-1">
                    <span className="text-sm font-medium truncate block">{f.title}</span>
                    <span className="text-xs text-muted-foreground">{f.id}</span>
                  </div>
                  <Badge className={`shrink-0 text-xs ${severityBadge[f.severity] ?? "bg-zinc-500/20"}`}>
                    {f.severity}
                  </Badge>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
