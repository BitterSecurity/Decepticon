"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  Target, FileText, Shield, Radio,
  Loader2, CheckCircle2, XCircle, Clock, AlertTriangle,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { RenderStructuredDocument } from "@/components/plan/structured-document";

/* eslint-disable @typescript-eslint/no-explicit-any */

// ── Document metadata ──────────────────────────────────────────────

const DOC_META = [
  { key: "opplan", label: "OPPLAN", desc: "Operations Plan", icon: Target, color: "text-amber-400" },
  { key: "roe", label: "ROE", desc: "Rules of Engagement", icon: Shield, color: "text-emerald-400" },
  { key: "threat-profile", label: "Threat Profile", desc: "Adversary Persona and TTPs", icon: Target, color: "text-red-400" },
  { key: "conops", label: "CONOPS", desc: "Concept of Operations", icon: FileText, color: "text-cyan-400" },
  { key: "deconfliction", label: "Deconfliction", desc: "Deconfliction Plan", icon: Radio, color: "text-purple-400" },
  { key: "contact", label: "Contacts", desc: "Contact and Escalation Plan", icon: FileText, color: "text-blue-400" },
  { key: "data-handling", label: "Data Handling", desc: "Evidence Retention and Custody", icon: Shield, color: "text-teal-400" },
  { key: "abort", label: "Abort Plan", desc: "Emergency Halt Conditions", icon: AlertTriangle, color: "text-orange-400" },
  { key: "cleanup", label: "Cleanup Plan", desc: "Artifact Removal Plan", icon: CheckCircle2, color: "text-lime-400" },
] as const;

// ── Status helpers ─────────────────────────────────────────────────

const statusIcon: Record<string, { icon: typeof CheckCircle2; color: string }> = {
  completed: { icon: CheckCircle2, color: "text-green-400" },
  blocked: { icon: XCircle, color: "text-red-400" },
  "in-progress": { icon: Loader2, color: "text-amber-400" },
  pending: { icon: Clock, color: "text-muted-foreground" },
  cancelled: { icon: XCircle, color: "text-muted-foreground/50" },
};

const severityColor: Record<string, string> = {
  loud: "text-red-400",
  standard: "text-amber-400",
  careful: "text-yellow-400",
  quiet: "text-blue-400",
  silent: "text-emerald-400",
};

// ── Section component ──────────────────────────────────────────────

/** Safely render a value that might be an object instead of a string. */
function SafeText({ value, className }: { value: unknown; className?: string }) {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "string") return <p className={className}>{value}</p>;
  if (typeof value === "boolean" || typeof value === "number") return <p className={className}>{String(value)}</p>;
  return (
    <pre className="whitespace-pre-wrap text-xs text-foreground/80 rounded bg-muted/50 p-2 font-mono">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-3">
      <h3 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </h3>
      {children}
    </div>
  );
}

function Field({ label, value }: { label: string; value: unknown }) {
  if (value === null || value === undefined || value === "") return null;
  const display = typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
  const isBlock = display.includes("\n");
  return (
    <div className={isBlock ? "space-y-1 text-sm" : "flex gap-3 text-sm"}>
      <span className="w-40 shrink-0 text-muted-foreground">{label}</span>
      {isBlock ? (
        <pre className="whitespace-pre-wrap text-xs text-foreground/80 rounded bg-muted/50 p-2 font-mono">{display}</pre>
      ) : (
        <span className="text-foreground">{display}</span>
      )}
    </div>
  );
}

// ── OPPLAN renderer ────────────────────────────────────────────────

function RenderOpplan({ data }: { data: any }) {
  const objectives: any[] = data.objectives ?? [];
  const completed = objectives.filter((o: any) => o.status === "completed").length;
  const blocked = objectives.filter((o: any) => o.status === "blocked").length;
  const total = objectives.length;
  const progress = total > 0 ? ((completed + blocked) / total) * 100 : 0;

  return (
    <div className="space-y-6">
      <Section title="Engagement">
        <Field label="Name" value={data.engagement_name} />
        <Field label="Threat Profile" value={data.threat_profile} />
      </Section>

      <Separator className="opacity-30" />

      <Section title="Progress">
        <div className="space-y-2">
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">
              {completed + blocked}/{total} objectives
            </span>
            <span className="font-medium">{Math.round(progress)}%</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-secondary">
            <div
              className="h-full rounded-full bg-primary transition-all"
              style={{ width: `${progress}%` }}
            />
          </div>
          <div className="flex gap-4 text-xs text-muted-foreground">
            <span className="text-green-400">{completed} passed</span>
            <span className="text-red-400">{blocked} blocked</span>
            <span>{total - completed - blocked} remaining</span>
          </div>
        </div>
      </Section>

      {objectives.length > 0 && (
        <>
          <Separator className="opacity-30" />
          <Section title="Objectives">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-20">ID</TableHead>
                  <TableHead>Title</TableHead>
                  <TableHead className="w-28">Phase</TableHead>
                  <TableHead className="w-24">OPSEC</TableHead>
                  <TableHead className="w-24">Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {objectives
                  .sort((a: any, b: any) => (a.priority ?? 0) - (b.priority ?? 0))
                  .map((obj: any) => {
                    const st = statusIcon[obj.status] ?? statusIcon.pending;
                    const StIcon = st.icon;
                    return (
                      <TableRow key={obj.id}>
                        <TableCell className="font-mono text-xs">{obj.id}</TableCell>
                        <TableCell>
                          <div className="text-sm font-medium">{obj.title}</div>
                          {obj.description && (
                            <div className="mt-0.5 text-xs text-muted-foreground line-clamp-2">
                              {obj.description}
                            </div>
                          )}
                          {obj.mitre?.length > 0 && (
                            <div className="mt-1 flex flex-wrap gap-1">
                              {obj.mitre.map((t: string) => (
                                <Badge key={t} variant="outline" className="text-[10px] px-1 py-0">
                                  {t}
                                </Badge>
                              ))}
                            </div>
                          )}
                        </TableCell>
                        <TableCell>
                          <Badge variant="outline" className="text-xs">{obj.phase}</Badge>
                        </TableCell>
                        <TableCell>
                          <span className={cn("text-xs", severityColor[obj.opsec])}>
                            {obj.opsec ?? "standard"}
                          </span>
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center gap-1.5">
                            <StIcon className={cn("h-3.5 w-3.5", st.color, obj.status === "in-progress" && "animate-spin")} />
                            <span className={cn("text-xs", st.color)}>{obj.status}</span>
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })}
              </TableBody>
            </Table>
          </Section>
        </>
      )}
    </div>
  );
}

// ── CONOPS renderer ────────────────────────────────────────────────

function RenderConops({ data }: { data: any }) {
  const actors: any[] = data.threat_actors ?? [];
  const killchain: any[] = data.kill_chain ?? [];
  const criteria: string[] = data.success_criteria ?? [];
  const timeline: Record<string, string> = data.phases_timeline ?? {};

  return (
    <div className="space-y-6">
      <Section title="Engagement">
        <Field label="Name" value={data.engagement_name} />
        <Field label="Methodology" value={data.methodology} />
      </Section>

      {data.executive_summary && (
        <>
          <Separator className="opacity-30" />
          <Section title="Executive Summary">
            <SafeText value={data.executive_summary} className="text-sm text-foreground/90 leading-relaxed whitespace-pre-wrap" />
          </Section>
        </>
      )}

      {actors.length > 0 && (
        <>
          <Separator className="opacity-30" />
          <Section title="Threat Actors">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Actor</TableHead>
                  <TableHead>Sophistication</TableHead>
                  <TableHead>Motivation</TableHead>
                  <TableHead>Key TTPs</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {actors.map((a: any, i: number) => (
                  <TableRow key={i}>
                    <TableCell className="font-medium text-sm">{a.name}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className="text-xs">{a.sophistication}</Badge>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">{a.motivation}</TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {(a.ttps ?? []).slice(0, 5).map((t: string) => (
                          <Badge key={t} variant="outline" className="text-[10px] px-1 py-0">{t}</Badge>
                        ))}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Section>
        </>
      )}

      {data.attack_narrative && (
        <>
          <Separator className="opacity-30" />
          <Section title="Attack Narrative">
            <SafeText value={data.attack_narrative} className="text-sm text-foreground/90 leading-relaxed whitespace-pre-wrap" />
          </Section>
        </>
      )}

      {killchain.length > 0 && (
        <>
          <Separator className="opacity-30" />
          <Section title="Kill Chain">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-32">Phase</TableHead>
                  <TableHead>Description</TableHead>
                  <TableHead className="w-40">Tools</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {killchain.map((k: any, i: number) => (
                  <TableRow key={i}>
                    <TableCell>
                      <Badge variant="outline" className="text-xs">{k.phase}</Badge>
                    </TableCell>
                    <TableCell className="text-sm">{k.description}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {(k.tools ?? []).join(", ") || "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Section>
        </>
      )}

      {Object.keys(timeline).length > 0 && (
        <>
          <Separator className="opacity-30" />
          <Section title="Timeline">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Phase</TableHead>
                  <TableHead>Duration</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {Object.entries(timeline).map(([phase, range]) => (
                  <TableRow key={phase}>
                    <TableCell className="font-medium text-sm">{phase}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{range}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Section>
        </>
      )}

      {criteria.length > 0 && (
        <>
          <Separator className="opacity-30" />
          <Section title="Success Criteria">
            <ul className="space-y-1.5">
              {criteria.map((c, i) => (
                <li key={i} className="flex items-start gap-2 text-sm">
                  <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-400" />
                  <span>{c}</span>
                </li>
              ))}
            </ul>
          </Section>
        </>
      )}

      {data.communication_plan && (
        <>
          <Separator className="opacity-30" />
          <Section title="Communication Plan">
            {typeof data.communication_plan === "string" ? (
              <p className="text-sm text-foreground/90 whitespace-pre-wrap">{data.communication_plan}</p>
            ) : (
              <div className="space-y-2">
                {Object.entries(data.communication_plan).map(([k, v]) => (
                  <Field key={k} label={k.replace(/_/g, " ")} value={v} />
                ))}
              </div>
            )}
          </Section>
        </>
      )}
    </div>
  );
}

// ── ROE renderer ───────────────────────────────────────────────────

function RenderRoe({ data }: { data: any }) {
  const inScope: any[] = data.in_scope ?? [];
  const outScope: any[] = data.out_of_scope ?? [];
  const prohibited: string[] = data.prohibited_actions ?? [];
  const permitted: string[] = data.permitted_actions ?? [];
  const contacts: any[] = data.escalation_contacts ?? [];

  return (
    <div className="space-y-6">
      <Section title="Engagement Details">
        <Field label="Name" value={data.engagement_name} />
        <Field label="Client" value={data.client} />
        <Field label="Type" value={data.engagement_type} />
        <Field label="Start Date" value={data.start_date} />
        <Field label="End Date" value={data.end_date} />
        <Field label="Testing Window" value={data.testing_window} />
        <Field label="Authorization" value={data.authorization_reference} />
        <Field label="Version" value={data.version} />
      </Section>

      {inScope.length > 0 && (
        <>
          <Separator className="opacity-30" />
          <Section title="In Scope">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Target</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Notes</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {inScope.map((s: any, i: number) => (
                  <TableRow key={i}>
                    <TableCell className="font-mono text-sm">{s.target}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className="text-xs">{s.type}</Badge>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">{s.notes || "—"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Section>
        </>
      )}

      {outScope.length > 0 && (
        <>
          <Separator className="opacity-30" />
          <Section title="Out of Scope">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Target</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Notes</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {outScope.map((s: any, i: number) => (
                  <TableRow key={i}>
                    <TableCell className="font-mono text-sm">{s.target}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className="text-xs">{s.type}</Badge>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">{s.notes || "—"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Section>
        </>
      )}

      {prohibited.length > 0 && (
        <>
          <Separator className="opacity-30" />
          <Section title="Prohibited Actions">
            <ul className="space-y-1.5">
              {prohibited.map((p, i) => (
                <li key={i} className="flex items-start gap-2 text-sm">
                  <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-400" />
                  <span>{p}</span>
                </li>
              ))}
            </ul>
          </Section>
        </>
      )}

      {permitted.length > 0 && (
        <>
          <Separator className="opacity-30" />
          <Section title="Permitted Actions">
            <ul className="space-y-1.5">
              {permitted.map((p, i) => (
                <li key={i} className="flex items-start gap-2 text-sm">
                  <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-400" />
                  <span>{p}</span>
                </li>
              ))}
            </ul>
          </Section>
        </>
      )}

      {contacts.length > 0 && (
        <>
          <Separator className="opacity-30" />
          <Section title="Escalation Contacts">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Channel</TableHead>
                  <TableHead>Available</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {contacts.map((c: any, i: number) => (
                  <TableRow key={i}>
                    <TableCell className="font-medium text-sm">{c.name}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{c.role}</TableCell>
                    <TableCell className="font-mono text-xs">{c.channel}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{c.available ?? "24/7"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Section>
        </>
      )}

      {data.incident_procedure && (
        <>
          <Separator className="opacity-30" />
          <Section title="Incident Procedure">
            <SafeText value={data.incident_procedure} className="text-sm text-foreground/90 whitespace-pre-wrap" />
          </Section>
        </>
      )}

      {data.data_handling && (
        <>
          <Separator className="opacity-30" />
          <Section title="Data Handling">
            <SafeText value={data.data_handling} className="text-sm text-foreground/90 whitespace-pre-wrap" />
          </Section>
        </>
      )}

      <Separator className="opacity-30" />
      <Section title="Cleanup">
        <Field label="Cleanup Required" value={data.cleanup_required ? "Yes" : "No"} />
      </Section>
    </div>
  );
}

// ── Deconfliction renderer ─────────────────────────────────────────

function RenderDeconfliction({ data }: { data: any }) {
  const identifiers: any[] = data.identifiers ?? [];

  return (
    <div className="space-y-6">
      <Section title="Engagement">
        <Field label="Name" value={data.engagement_name} />
        <Field label="SOC Contact" value={data.soc_contact} />
        <Field label="Deconfliction Code" value={data.deconfliction_code} />
      </Section>

      {identifiers.length > 0 && (
        <>
          <Separator className="opacity-30" />
          <Section title="Red Team Identifiers">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Type</TableHead>
                  <TableHead>Value</TableHead>
                  <TableHead>Description</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {identifiers.map((id: any, i: number) => (
                  <TableRow key={i}>
                    <TableCell>
                      <Badge variant="outline" className="text-xs">{id.type}</Badge>
                    </TableCell>
                    <TableCell className="font-mono text-sm">{id.value}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{id.description || "—"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Section>
        </>
      )}

      {data.notification_procedure && (
        <>
          <Separator className="opacity-30" />
          <Section title="Notification Procedure">
            <SafeText value={data.notification_procedure} className="text-sm text-foreground/90 whitespace-pre-wrap" />
          </Section>
        </>
      )}
    </div>
  );
}

// ── Renderer dispatch ──────────────────────────────────────────────

const renderers: Record<string, React.FC<{ data: any }>> = {
  opplan: RenderOpplan,
  conops: RenderConops,
  roe: RenderRoe,
  deconfliction: RenderDeconfliction,
  "threat-profile": RenderStructuredDocument,
  contact: RenderStructuredDocument,
  "data-handling": RenderStructuredDocument,
  abort: RenderStructuredDocument,
  cleanup: RenderStructuredDocument,
};

// ── Page ───────────────────────────────────────────────────────────

export default function PlanPage() {
  const params = useParams();
  const id = params.id as string;
  const [docs, setDocs] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetch(`/api/engagements/${id}/plan-docs`)
      .then((r) => (r.ok ? r.json() : {}))
      .then((data: Record<string, any>) => {
        if (!active) return;
        setDocs(data);
        const first = DOC_META.find((d) => data[d.key]);
        if (first) setSelected(first.key);
      })
      .catch((err) => {
        if (active) console.error("Failed to load plan docs", err);
      })
      .finally(() => {
        if (!active) return;
        setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [id]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const hasAnyDoc = DOC_META.some((d) => docs[d.key]);

  if (!hasAnyDoc) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Plan</h1>
          <p className="text-sm text-muted-foreground">Engagement planning documents</p>
        </div>
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-16">
            <Target className="mb-3 h-10 w-10 text-muted-foreground/30" />
            <p className="text-sm font-medium">No plan documents yet</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Start the engagement from the Live tab — Soundwave will generate planning documents.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const selectedData = selected ? docs[selected] : null;
  const Renderer = selected ? renderers[selected] : null;
  const selectedMeta = DOC_META.find((d) => d.key === selected);

  return (
    <div className="flex h-full gap-4 overflow-hidden">
      {/* Left: Document list */}
      <div className="w-64 shrink-0 space-y-1.5">
        <div className="mb-3">
          <h1 className="text-lg font-bold tracking-tight">Plan</h1>
          <p className="text-xs text-muted-foreground">Engagement documents</p>
        </div>

        {DOC_META.map((doc) => {
          const exists = !!docs[doc.key];
          const active = selected === doc.key;
          return (
            <button
              key={doc.key}
              onClick={() => exists && setSelected(doc.key)}
              disabled={!exists}
              className={cn(
                "flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-colors",
                active
                  ? "bg-primary/10 border border-primary/20"
                  : exists
                    ? "hover:bg-accent"
                    : "opacity-30 cursor-not-allowed"
              )}
            >
              <doc.icon className={cn("h-4 w-4 shrink-0", active ? doc.color : "text-muted-foreground")} />
              <div className="min-w-0 flex-1">
                <div className={cn("text-sm font-medium", active ? "text-foreground" : "text-muted-foreground")}>
                  {doc.label}
                </div>
                <div className="text-[10px] text-muted-foreground/70 truncate">{doc.desc}</div>
              </div>
              {exists ? (
                <CheckCircle2 className="h-3 w-3 shrink-0 text-emerald-500/70" />
              ) : (
                <AlertTriangle className="h-3 w-3 shrink-0 text-muted-foreground/30" />
              )}
            </button>
          );
        })}
      </div>

      {/* Right: Document content */}
      <Card className="flex-1 overflow-hidden">
        <CardHeader className="border-b border-border/50 pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            {selectedMeta && (
              <selectedMeta.icon className={cn("h-4 w-4", selectedMeta.color)} />
            )}
            {selectedMeta?.label ?? "Select a document"}
            <span className="text-xs font-normal text-muted-foreground">
              {selectedMeta?.desc}
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <ScrollArea className="h-[calc(100vh-14rem)]">
            <div className="p-6">
              {selectedData && Renderer ? (
                <Renderer data={selectedData} />
              ) : (
                <div className="flex items-center justify-center py-16 text-sm text-muted-foreground">
                  Select a document from the sidebar
                </div>
              )}
            </div>
          </ScrollArea>
        </CardContent>
      </Card>
    </div>
  );
}
