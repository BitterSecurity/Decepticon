"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { Plus, Globe, Server } from "lucide-react";
interface Engagement {
  id: string;
  name: string;
  targetType: string;
  targetValue: string;
  status: string;
  createdAt: string;
}

const statusColors: Record<string, string> = {
  draft: "bg-slate-500/10 text-slate-800 dark:text-slate-400 border-slate-500/20",
  planning: "bg-blue-500/10 text-blue-800 dark:text-blue-400 border-blue-500/20",
  running: "bg-amber-500/10 text-amber-800 dark:text-amber-400 border-amber-500/20",
  completed: "bg-green-500/10 text-green-800 dark:text-green-400 border-green-500/20",
  failed: "bg-red-500/10 text-red-800 dark:text-red-400 border-red-500/20",
};

const targetIcons: Record<string, typeof Globe> = {
  web_url: Globe,
  ip_range: Server,
};

export default function EngagementsPage() {
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
        setEngagements(data);
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
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Engagements</h1>
          <p className="text-sm text-muted-foreground">
            Manage your red team testing operations
          </p>
        </div>
        <Link href="/engagements/new">
          <Button>
            <Plus className="mr-2 h-4 w-4" />
            New Engagement
          </Button>
        </Link>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">All Engagements</CardTitle>
          <CardDescription>
            A list of all your security testing engagements
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="space-y-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : engagements.length === 0 ? (
            <div className="flex items-center justify-center py-12 text-sm text-muted-foreground">
              No engagements yet. Click &quot;New Engagement&quot; to create
              one.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Target</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Created</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {engagements.map((eng) => {
                  const Icon = targetIcons[eng.targetType] ?? Globe;
                  return (
                    <TableRow key={eng.id}>
                      <TableCell>
                        <Link
                          href={`/engagements/${eng.id}`}
                          className="font-medium text-foreground hover:underline"
                        >
                          {eng.name}
                        </Link>
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-2 text-sm text-muted-foreground">
                          <Icon className="h-3.5 w-3.5" />
                          <span className="truncate max-w-[200px]">
                            {eng.targetValue}
                          </span>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant="outline"
                          className={statusColors[eng.status] ?? ""}
                        >
                          {eng.status}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {new Date(eng.createdAt).toLocaleDateString()}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
