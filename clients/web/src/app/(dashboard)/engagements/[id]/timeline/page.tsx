"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ClipboardList, FileWarning, FolderOpen, Clock, Download } from "lucide-react";
import { cn } from "@/lib/utils";

interface TimelineEvent {
  timestamp: string;
  type: "plan_created" | "objective_changed" | "finding_discovered" | "file_created";
  title: string;
  detail: string;
  severity?: string;
}

const typeConfig: Record<string, { icon: typeof Clock; color: string; bg: string }> = {
  plan_created: { icon: ClipboardList, color: "text-violet-800 dark:text-violet-400", bg: "bg-violet-500/10" },
  objective_changed: { icon: ClipboardList, color: "text-amber-800 dark:text-amber-400", bg: "bg-amber-500/10" },
  finding_discovered: { icon: FileWarning, color: "text-red-800 dark:text-red-400", bg: "bg-red-500/10" },
  file_created: { icon: FolderOpen, color: "text-cyan-800 dark:text-cyan-400", bg: "bg-cyan-500/10" },
};

const sevColor: Record<string, string> = {
  critical: "bg-red-500/20 text-red-800 dark:text-red-300",
  high: "bg-orange-500/20 text-orange-800 dark:text-orange-300",
  medium: "bg-yellow-500/20 text-yellow-800 dark:text-yellow-300",
  low: "bg-blue-500/20 text-blue-800 dark:text-blue-300",
};

export default function TimelinePage() {
  const params = useParams();
  const id = params.id as string;
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    fetch(`/api/engagements/${id}/timeline`)
      .then((r) => {
        if (!r.ok) throw new Error("fetch failed");
        return r.json();
      })
      .then((data) => { if (active) setEvents(data); })
      .catch(() => {})
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [id]);

  if (loading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-48" />
        {[1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-16 w-full" />)}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Timeline</h1>
          <p className="text-sm text-muted-foreground">{events.length} events</p>
        </div>
        <a
          href={`/api/engagements/${id}/export?format=json`}
          className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs transition-colors hover:bg-accent"
        >
          <Download className="h-3 w-3" /> Export JSON
        </a>
      </div>

      <Card>
        <CardContent className="p-0">
          <ScrollArea className="h-[calc(100vh-14rem)]">
            <div className="divide-y divide-border/50">
              {events.length === 0 ? (
                <div className="flex items-center justify-center py-16 text-sm text-muted-foreground">
                  <Clock className="mr-2 h-5 w-5" /> No activity recorded yet
                </div>
              ) : (
                events.map((event, i) => {
                  const cfg = typeConfig[event.type] ?? typeConfig.file_created;
                  const Icon = cfg.icon;
                  return (
                    <div key={i} className="flex items-start gap-4 px-6 py-4">
                      <div className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-lg", cfg.bg)}>
                        <Icon className={cn("h-4 w-4", cfg.color)} />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium">{event.title}</span>
                          {event.severity && (
                            <Badge className={cn("text-[10px]", sevColor[event.severity])}>
                              {event.severity}
                            </Badge>
                          )}
                        </div>
                        <p className="mt-0.5 text-xs text-muted-foreground">{event.detail}</p>
                      </div>
                      <time className="shrink-0 text-xs text-muted-foreground">
                        {new Date(event.timestamp).toLocaleString()}
                      </time>
                    </div>
                  );
                })
              )}
            </div>
          </ScrollArea>
        </CardContent>
      </Card>
    </div>
  );
}
