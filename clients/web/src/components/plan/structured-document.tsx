import React from "react";

function labelForKey(key: string): string {
  return key
    .split(/[_-]/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function itemKey(value: unknown, index: number): string {
  if (value === null || typeof value !== "object") {
    return `${typeof value}:${String(value)}:${index}`;
  }
  try {
    return `${JSON.stringify(value)}:${index}`;
  } catch {
    return `structured-value:${index}`;
  }
}

function StructuredValue({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "") {
    return <span className="text-muted-foreground">Not specified</span>;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-muted-foreground">None</span>;
    return (
      <div className="space-y-2">
        {value.map((item, index) => (
          <div key={itemKey(item, index)} className="rounded-md border border-border/40 bg-muted/20 p-3">
            <StructuredValue value={item} />
          </div>
        ))}
      </div>
    );
  }
  if (typeof value === "object") {
    return (
      <div className="space-y-3">
        {Object.entries(value as Record<string, unknown>).map(([key, item]) => (
          <div key={key} className="grid gap-1 md:grid-cols-[12rem_1fr]">
            <span className="text-xs font-medium text-muted-foreground">{labelForKey(key)}</span>
            <div className="text-sm text-foreground"><StructuredValue value={item} /></div>
          </div>
        ))}
      </div>
    );
  }
  return <span className="whitespace-pre-wrap break-words">{String(value)}</span>;
}

export function RenderStructuredDocument({ data }: { data: unknown }) {
  return <StructuredValue value={data} />;
}
