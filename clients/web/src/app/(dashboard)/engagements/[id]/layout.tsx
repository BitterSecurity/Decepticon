"use client";

import { useState, useEffect } from "react";
import { useParams, usePathname } from "next/navigation";
import { EngagementProvider } from "@/lib/engagement-context";
import { useRunObserver } from "@/hooks/useRunObserver";
import { WebTerminal } from "@/components/terminal/web-terminal";
import { cn } from "@/lib/utils";

function pickAssistant(planDocs: Record<string, unknown>): "soundwave" | "decepticon" {
  return planDocs.redApproved === true ? "decepticon" : "soundwave";
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
  } | null>(null);
  const [agentId, setAgentId] = useState<"soundwave" | "decepticon" | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [threadConfirmed, setThreadConfirmed] = useState(false);
  const isLivePath = pathname.endsWith("/live");
  const [terminalActivated, setTerminalActivated] = useState(isLivePath);

  useEffect(() => {
    if (isLivePath) setTerminalActivated(true);
  }, [isLivePath]);

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
          threadId?: string | null;
        };
        const planDocs = planRes.ok ? ((await planRes.json()) as Record<string, unknown>) : {};
        if (cancelled) return;
        setEngagement(eng);
        setAgentId(pickAssistant(planDocs));
        // Seed the observer from the persisted thread so the dashboard attaches
        // to the engagement's real thread on load, not a brand-new empty one.
        if (eng.threadId) {
          setThreadId(eng.threadId);
          setThreadConfirmed(false);
        }
      } catch (err) {
        console.error("[EngagementLayout] Failed to resolve engagement:", err);
      }
    };
    load();
    return () => { cancelled = true; };
  }, [engagementId]);

  const confirmThreadId = (id: string) => {
    setThreadId(id);
    setThreadConfirmed(true);
  };
  // Wait for the terminal server to confirm a usable thread before polling.
  const { events, isRunning, activeRunId } = useRunObserver({
    threadId: terminalActivated && threadConfirmed ? threadId : null,
  });

  // Don't render terminal until we know the slug and assistant
  const terminalReady = terminalActivated && engagement != null && agentId != null;

  return (
    <EngagementProvider
      engagementId={engagementId}
      engagementSlug={engagement?.name ?? ""}
      agentId={agentId ?? "soundwave"}
      threadId={threadId}
      setThreadId={confirmThreadId}
      events={events}
      isRunning={isRunning}
      activeRunId={activeRunId}
    >
      <div className="flex h-full overflow-hidden">
        <div className="flex-1 min-w-0 overflow-auto">
          {children}
        </div>
        {/* Activate on first Live visit, then preserve the terminal across tabs. */}
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
              onThreadId={confirmThreadId}
            />
          )}
        </div>
      </div>
    </EngagementProvider>
  );
}
