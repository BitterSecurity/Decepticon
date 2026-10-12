"use client";

import { useState, useEffect } from "react";
import { useParams, usePathname } from "next/navigation";
import { EngagementProvider } from "@/lib/engagement-context";
import { useRunObserver } from "@/hooks/useRunObserver";
import { WebTerminal } from "@/components/terminal/web-terminal";
import { cn } from "@/lib/utils";

type RunMode = "plan" | "red" | "blue";
const MODE_ASSISTANTS: Record<RunMode, "soundwave" | "decepticon" | "blue_cell"> = {
  plan: "soundwave", red: "decepticon", blue: "blue_cell",
};

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
  const [mode, setMode] = useState<RunMode>("red");
  const [visitedModes, setVisitedModes] = useState<RunMode[]>([]);
  const [threadIds, setThreadIds] = useState<Partial<Record<RunMode, string>>>({});
  const [redApproved, setRedApproved] = useState(false);
  const [modeNotice, setModeNotice] = useState<string | null>(null);
  const agentId = MODE_ASSISTANTS[mode];
  const threadId = threadIds[mode] ?? null;

  // Resolve engagement metadata — determines agentId and slug for WS
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [engRes, planRes, threadsRes] = await Promise.all([
          fetch(`/api/engagements/${engagementId}`),
          fetch(`/api/engagements/${engagementId}/plan-docs`).catch(() => null),
          fetch(`/api/engagements/${engagementId}/threads`).catch(() => null),
        ]);
        if (!engRes.ok) return;
        const eng = (await engRes.json()) as {
          name: string;
          targetType: string;
          targetValue: string;
          authorizationConfirmed: boolean;
          threadId?: string | null;
        };
        const planDocs = planRes?.ok ? ((await planRes.json()) as Record<string, unknown>) : {};
        if (cancelled) return;
        setEngagement(eng);
        const approved = planDocs.redApproved === true;
        setRedApproved(approved);
        const requested = new URLSearchParams(window.location.search).get("mode");
        const next: RunMode = requested === "plan" || requested === "blue" || (requested === "red" && approved)
          ? requested : approved ? "red" : "plan";
        setMode(next);
        setVisitedModes([next]);
        if (threadsRes?.ok) {
          const threads = await threadsRes.json() as Array<{ thread_id: string; metadata?: { decepticon_assistant?: string } }>;
          const restored: Partial<Record<RunMode, string>> = {};
          for (const entry of threads) {
            for (const candidate of ["plan", "red", "blue"] as const) {
              if (!restored[candidate] && entry.metadata?.decepticon_assistant === MODE_ASSISTANTS[candidate]) restored[candidate] = entry.thread_id;
            }
          }
          if (!cancelled) setThreadIds(restored);
        }
      } catch (err) {
        console.error("[EngagementLayout] Failed to resolve engagement:", err);
      }
    };
    load();
    return () => { cancelled = true; };
  }, [engagementId]);

  async function switchMode(next: RunMode) {
    if (next === mode) return;
    try {
      if (next === "red") {
        const response = await fetch(`/api/engagements/${engagementId}/plan-docs`);
        const docs = response.ok ? await response.json() as { redApproved?: boolean } : {};
        if (!docs.redApproved) {
          if (!window.confirm("Approve the validated Plan documents for Red? Review the Plan tab before continuing.")) return;
          const approval = await fetch(`/api/engagements/${engagementId}/plan-docs`, { method: "POST" });
          if (!approval.ok) {
            const detail = await approval.json().catch(() => ({})) as { error?: string };
            setModeNotice(detail.error ?? "Red needs an approved scope.");
            return;
          }
        }
        setRedApproved(true);
      }
      if (next === "blue") {
        const response = await fetch("/api/blue/status");
        if (!response.ok) {
          setModeNotice("Blue needs a connected local sensor. Start it from the host CLI with /blue up.");
          return;
        }
      }
      setModeNotice(null);
      setMode(next);
      setVisitedModes((current) => current.includes(next) ? current : [...current, next]);
      window.history.replaceState(null, "", `${pathname}?mode=${next}`);
    } catch (error) {
      setModeNotice(error instanceof Error ? error.message : "Mode switch failed");
    }
  }

  // Persistent observer — survives tab navigation
  const { events, isRunning, activeRunId } = useRunObserver({ threadId });

  const isLivePath = pathname.endsWith("/live");

  // Don't render terminal until we know the slug and assistant
  const terminalReady = engagement != null;

  return (
    <EngagementProvider
      engagementId={engagementId}
      engagementSlug={engagement?.name ?? ""}
      agentId={agentId}
      threadId={threadId}
      setThreadId={(id) => setThreadIds((current) => ({ ...current, [mode]: id }))}
      events={events}
      isRunning={isRunning}
      activeRunId={activeRunId}
    >
      <div className="flex h-full overflow-hidden">
        <div className="flex flex-1 min-w-0 flex-col overflow-hidden">
          {isLivePath && (
            <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.08] px-4 py-2">
              <div className="flex rounded-lg border border-border p-0.5" role="group" aria-label="Run mode">
                {(["plan", "red", "blue"] as const).map((candidate) => (
                  <button key={candidate} type="button" onClick={() => void switchMode(candidate)} aria-pressed={mode === candidate} className={cn("rounded-md px-3 py-1 text-sm capitalize", mode === candidate ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")}>
                    {candidate}
                  </button>
                ))}
              </div>
              <span className="text-xs text-muted-foreground">{mode === "plan" ? "Soundwave planning" : mode === "red" ? "Authorized testing" : "Defensive investigation"}</span>
              {modeNotice && <span role="status" className="text-xs text-amber-400">{modeNotice}</span>}
              {!redApproved && mode === "plan" && <span className="text-xs text-muted-foreground">Red scope pending review</span>}
            </div>
          )}
          <div className="min-h-0 flex-1 overflow-auto">{children}</div>
        </div>
        {/* Terminal: always mounted, visibility controlled by route */}
        <div
          className={cn(
            "shrink-0 overflow-hidden border-l border-white/[0.08] transition-[width] duration-200",
            isLivePath ? "w-[35%] min-w-[350px]" : "w-0 min-w-0",
          )}
        >
          {terminalReady && visitedModes.map((candidate) => (
            <div key={candidate} className={candidate === mode ? "h-full" : "hidden"}>
              <WebTerminal
                engagementId={engagementId}
                engagementSlug={engagement!.name}
                targetType={engagement!.targetType}
                targetValue={engagement!.targetValue}
                authorizationConfirmed={engagement!.authorizationConfirmed}
                agentId={MODE_ASSISTANTS[candidate]}
                threadId={threadIds[candidate]}
                className="h-full"
                onThreadId={(id) => setThreadIds((current) => ({ ...current, [candidate]: id }))}
              />
            </div>
          ))}
        </div>
      </div>
    </EngagementProvider>
  );
}
