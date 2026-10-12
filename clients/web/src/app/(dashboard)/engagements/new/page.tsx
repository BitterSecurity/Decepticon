"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Globe, Server, Loader2 } from "lucide-react";
import { isValidEngagementSlug } from "@/lib/engagement-slug";

export default function NewEngagementPage() {
  const router = useRouter();
  const [targetType, setTargetType] = useState("web_url");
  const [name, setName] = useState("");
  const [targetValue, setTargetValue] = useState("");
  const [initialMode, setInitialMode] = useState<"plan" | "red" | "blue">("red");
  const [authorizationConfirmed, setAuthorizationConfirmed] = useState(false);
  const [authorizationReference, setAuthorizationReference] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const nameValid = isValidEngagementSlug(name);
  const nameError =
    name.length > 0 && !nameValid
      ? "Name must be 3-64 chars, lowercase letters / digits with internal hyphens only"
      : null;

  async function handleSubmit() {
    if (!nameValid || !targetValue.trim() || !authorizationConfirmed || !authorizationReference.trim()) {
      setError("Enter one target and authorization reference, then confirm authorization");
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      const res = await fetch("/api/engagements", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, targetType, targetValue, authorizationConfirmed, authorizationReference, initialMode }),
      });

      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.error || "Failed to create engagement");
      }

      const engagement = await res.json();
      router.push(`/engagements/${engagement.id}/live?mode=${initialMode}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">New Engagement</h1>
        <p className="text-sm text-muted-foreground">
          Configure a new red team testing operation (DAST)
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Engagement Details</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="name">Engagement Name</Label>
            <Input
              id="name"
              placeholder="e.g., q2-security-assessment"
              value={name}
              onChange={(e) => setName(e.target.value)}
              aria-invalid={nameError ? true : undefined}
            />
            {nameError ? (
              <p className="text-xs text-destructive">{nameError}</p>
            ) : (
              <p className="text-xs text-muted-foreground">
                Used as the workspace folder name — 3-64 chars, lowercase
                letters / digits with internal hyphens
              </p>
            )}
          </div>
          <div className="space-y-2">
            <Label>Starting mode</Label>
            <div className="grid grid-cols-3 gap-2" role="group" aria-label="Starting mode">
              {(["plan", "red", "blue"] as const).map((mode) => (
                <Button key={mode} type="button" variant={initialMode === mode ? "default" : "outline"} onClick={() => setInitialMode(mode)} aria-pressed={initialMode === mode}>
                  {mode === "plan" ? "Plan" : mode === "red" ? "Red" : "Blue"}
                </Button>
              ))}
            </div>
            <p className="text-xs text-muted-foreground">Plan opens Soundwave for deeper planning. Red starts from the authorized target. Blue needs a connected local sensor.</p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Target Configuration</CardTitle>
        </CardHeader>
        <CardContent>
          <Tabs
            value={targetType}
            onValueChange={(v) => {
              setTargetType(v);
              setTargetValue("");
            }}
          >
            <TabsList className="w-full">
              <TabsTrigger value="web_url" className="flex-1 gap-2">
                <Globe className="h-4 w-4" />
                Web URL
              </TabsTrigger>
              <TabsTrigger value="ip_range" className="flex-1 gap-2">
                <Server className="h-4 w-4" />
                IP Range
              </TabsTrigger>
            </TabsList>

            <TabsContent value="web_url" className="mt-4 space-y-4">
              <div className="space-y-2">
                <Label htmlFor="url">Target URL</Label>
                <Input
                  id="url"
                  type="url"
                  placeholder="https://example.com"
                  value={targetValue}
                  onChange={(e) => setTargetValue(e.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  The web application URL to test
                </p>
              </div>
            </TabsContent>

            <TabsContent value="ip_range" className="mt-4 space-y-4">
              <div className="space-y-2">
                <Label htmlFor="ip">IP Range</Label>
                <Input
                  id="ip"
                  placeholder="192.168.1.0/24"
                  value={targetValue}
                  onChange={(e) => setTargetValue(e.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  One IP address or CIDR range to scan
                </p>
              </div>
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Authorization</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="mb-4 space-y-2">
            <Label htmlFor="authorization-reference">Authorization reference</Label>
            <Input id="authorization-reference" value={authorizationReference} onChange={(event) => setAuthorizationReference(event.target.value)} placeholder="e.g., signed engagement agreement ID" />
          </div>
          <label className="flex cursor-pointer items-start gap-3 text-sm">
            <input
              type="checkbox"
              checked={authorizationConfirmed}
              onChange={(event) => setAuthorizationConfirmed(event.target.checked)}
              className="mt-0.5 h-4 w-4"
            />
            <span>
              I confirm I am authorized to test exactly the declared target. This does
              not authorize adjacent assets, destructive testing, DoS, social
              engineering, uncontrolled data mutation, or scope expansion.
            </span>
          </label>
        </CardContent>
      </Card>

      <div className="flex justify-end gap-3">
        <Button variant="outline" onClick={() => router.back()}>
          Cancel
        </Button>
        <Button onClick={handleSubmit} disabled={submitting || !nameValid || !authorizationConfirmed || !authorizationReference.trim()}>
          {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          Create Engagement
        </Button>
      </div>
    </div>
  );
}
