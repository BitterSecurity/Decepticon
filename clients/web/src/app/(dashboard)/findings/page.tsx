"use client";

import { useEffect, useState } from "react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { FileWarning } from "lucide-react";
import Link from "next/link";
interface Engagement {
  id: string;
  name: string;
  status: string;
}

export default function FindingsPage() {
  const [engagements, setEngagements] = useState<Engagement[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    fetch("/api/engagements")
      .then((res) => {
        if (!res.ok) throw new Error("fetch failed");
        return res.json();
      })
      .then((data: Engagement[]) => {
        if (!active) return;
        setEngagements(data.filter((e) => e.status === "completed"));
      })
      .catch(() => {
        if (!active) return;
        setEngagements([]);
      })
      .finally(() => {
        if (!active) return;
        setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Findings</h1>
        <p className="text-sm text-muted-foreground">
          Discovered vulnerabilities across all engagements
        </p>
      </div>

      {loading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-20 w-full" />
          ))}
        </div>
      ) : engagements.length === 0 ? (
        <Card>
          <CardContent className="flex items-center justify-center py-12">
            <div className="text-center text-sm text-muted-foreground">
              <FileWarning className="mx-auto mb-3 h-8 w-8 opacity-50" />
              <p>No completed engagements yet.</p>
              <p className="mt-1 text-xs">
                Complete an engagement to see findings here.
              </p>
            </div>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {engagements.map((eng) => (
            <Link key={eng.id} href={`/engagements/${eng.id}/findings`}>
              <Card className="transition-colors hover:border-primary/50">
                <CardHeader>
                  <CardTitle className="text-base">{eng.name}</CardTitle>
                  <CardDescription>
                    <Badge
                      variant="outline"
                      className="bg-green-500/10 text-green-800 dark:text-green-400 border-green-500/20"
                    >
                      {eng.status}
                    </Badge>
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <p className="text-xs text-muted-foreground">
                    Click to view findings
                  </p>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
