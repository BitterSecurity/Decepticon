"use client";

import { useState, useEffect, useRef } from "react";
import { useParams, usePathname } from "next/navigation";
import { EngagementProvider } from "@/lib/engagement-context";
import { useRunObserver } from "@/hooks/useRunObserver";
import { WebTerminal } from "@/components/terminal/web-terminal";
import { cn } from "@/lib/utils";

const REQUIRED_PLAN_DOCS = ["roe", "conops", "deconfliction"] as const;

function pickAssistant(planDocs: Record<string, unknown>): "soundwave" | "decepticon" {
  for (const name of REQUIRED_PLAN_DOCS) {
    if (planDocs[name] == null) return "soundwave";
  }
  return "decepticon";
}

export default function EngagementLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const params = useParams();
  const pathname = usePathname();
  const engagementId = params.id as string;

  const [engagement, setEngagement] = useState<{
    name: string;
    targetType: string;
    targetValue: string;
    authorizationConfirmed: boolean;
    status: string;
  } | null>(null);
  // Guards the one-shot draft→running status PATCH so it fires at most once.
  const statusPatchedRef = useRef(false);
  const [agentId, setAgentId] = useState<"soundwave" | "decepticon" | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);

  // Resolve engagement metadata — determines agentId and slug for WS
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [engRes, planRes] = await Promise.all([
          fetch(`/api/engagements/${engagementId}`),
          fetch(`/api/engagements/${engagementId}/plan-docs`),
        ]);
        if (!engRes.ok) return;
        const eng = (await engRes.json()) as {
          name: string;
          targetType: string;
          targetValue: string;
          authorizationConfirmed: boolean;
          status: string;
          threadId?: string | null;
        };
        const planDocs = planRes.ok ? ((await planRes.json()) as Record<string, unknown>) : {};
        if (cancelled) return;
        setEngagement(eng);
        setAgentId(pickAssistant(planDocs));
        // Seed the observer from the persisted thread so the dashboard attaches
        // to the engagement's real thread on load, not a brand-new empty one.
        // The `langgraph dev` server keeps threads in memory, so a backend
        // restart leaves a dead threadId here. Validate it first; if it's gone,
        // clear it so the terminal opens a fresh thread instead of 404-ing.
        if (eng.threadId) {
          const lgUrl =
            process.env.NEXT_PUBLIC_LANGGRAPH_API_URL ?? "http://localhost:2024";
          let alive = true;
          try {
            const res = await fetch(`${lgUrl}/threads/${eng.threadId}/state`);
            alive = res.ok;
          } catch {
            alive = false;
          }
          if (cancelled) return;
          if (alive) {
            setThreadId(eng.threadId);
          } else {
            fetch(`/api/engagements/${engagementId}`, {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ threadId: null }),
            }).catch(() => {});
          }
        }
      } catch (err) {
        console.error("[EngagementLayout] Failed to resolve engagement:", err);
      }
    };
    load();
    return () => { cancelled = true; };
  }, [engagementId]);

  // Persistent observer — survives tab navigation
  const { events, isRunning, activeRunId } = useRunObserver({ threadId });

  // Advance the engagement status out of "draft" once a run is actually active.
  // The web client is the only place that observes live run state, so it owns
  // this transition; a ref makes it one-shot per mount.
  useEffect(() => {
    if (!isRunning || statusPatchedRef.current) return;
    if (!engagement || engagement.status !== "draft") return;
    statusPatchedRef.current = true;
    setEngagement((prev) => (prev ? { ...prev, status: "running" } : prev));
    fetch(`/api/engagements/${engagementId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "running" }),
    }).catch((err) => {
      console.error("[EngagementLayout] Failed to advance status:", err);
      statusPatchedRef.current = false;
    });
  }, [isRunning, engagement, engagementId]);

  const isLivePath = pathname.endsWith("/live");

  // Don't render terminal until we know the slug and assistant
  const terminalReady = engagement != null && agentId != null;

  return (
    <EngagementProvider
      engagementId={engagementId}
      engagementSlug={engagement?.name ?? ""}
      agentId={agentId ?? "soundwave"}
      threadId={threadId}
      setThreadId={setThreadId}
      events={events}
      isRunning={isRunning}
      activeRunId={activeRunId}
    >
      <div className="flex h-full overflow-hidden">
        <div className="flex-1 min-w-0 overflow-auto">
          {children}
        </div>
        {/* Terminal: always mounted, visibility controlled by route */}
        <div
          className={cn(
            "shrink-0 overflow-hidden border-l border-white/[0.08] transition-[width] duration-200",
            isLivePath ? "w-[35%] min-w-[350px]" : "w-0 min-w-0",
          )}
        >
          {terminalReady && (
            <WebTerminal
              engagementId={engagementId}
              engagementSlug={engagement!.name}
              targetType={engagement!.targetType}
              targetValue={engagement!.targetValue}
              authorizationConfirmed={engagement!.authorizationConfirmed}
              agentId={agentId!}
              threadId={threadId ?? undefined}
              className="h-full"
              onThreadId={setThreadId}
            />
          )}
        </div>
      </div>
    </EngagementProvider>
  );
}
