"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  FileText, Radar, Zap, Key, AlertTriangle,
  Folder, Loader2, File, ChevronRight,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface FileEntry {
  name: string;
  folder: string;
  path: string;
  size: number;
}

interface FolderGroup {
  folder: string;
  files: FileEntry[];
}

const FOLDER_META: Record<string, { icon: typeof Folder; color: string; label: string }> = {
  recon: { icon: Radar, color: "text-cyan-800 dark:text-cyan-400", label: "Recon" },
  exploit: { icon: Zap, color: "text-amber-800 dark:text-amber-400", label: "Exploit" },
  "post-exploit": { icon: Key, color: "text-purple-800 dark:text-purple-400", label: "Post-Exploit" },
  findings: { icon: AlertTriangle, color: "text-red-800 dark:text-red-400", label: "Findings" },
  report: { icon: FileText, color: "text-emerald-800 dark:text-emerald-400", label: "Report" },
};

/** Folders managed by the Plan page — excluded from Documents. */
const EXCLUDED_FOLDERS = new Set(["plan"]);

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function DocumentsPage() {
  const params = useParams();
  const id = params.id as string;
  const [folders, setFolders] = useState<FolderGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedFile, setSelectedFile] = useState<{ path: string; content: string } | null>(null);
  const [fileLoading, setFileLoading] = useState(false);
  const [workspaceName, setWorkspaceName] = useState<string | null>(null);
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set());

  // Resolve engagement ID to workspace name
  useEffect(() => {
    let active = true;
    async function resolve() {
      try {
        const engRes = await fetch(`/api/engagements/${id}`);
        if (!active) return;
        if (!engRes.ok) return;
        const eng = await engRes.json();
        if (!active) return;
        if (eng.workspacePath) {
          const name = eng.workspacePath.split("/").pop();
          setWorkspaceName(name);
          return;
        }
        setWorkspaceName(eng.name);
      } catch (err) {
        if (active) console.error("Failed to resolve workspace", err);
      }
    }
    resolve();
    return () => {
      active = false;
    };
  }, [id]);

  // Load files when workspace name is resolved
  useEffect(() => {
    if (!workspaceName) {
      setLoading(false);
      return;
    }
    let active = true;
    async function loadFiles() {
      try {
        const res = await fetch(`/api/workspace/${encodeURIComponent(workspaceName!)}/files`);
        if (!active) return;
        if (res.ok) {
          const data = await res.json();
          if (!active) return;
          const filtered = (data.folders ?? []).filter(
            (g: FolderGroup) => !EXCLUDED_FOLDERS.has(g.folder)
          );
          setFolders(filtered);
          // Auto-expand first folder with files
          if (filtered.length > 0) {
            setExpandedFolders(new Set([filtered[0].folder]));
          }
        }
      } catch (err) {
        if (active) console.error("Failed to load workspace files", err);
      } finally {
        if (active) setLoading(false);
      }
    }
    loadFiles();
    return () => {
      active = false;
    };
  }, [workspaceName]);

  function toggleFolder(folder: string) {
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(folder)) next.delete(folder);
      else next.add(folder);
      return next;
    });
  }

  async function openFile(filePath: string) {
    if (!workspaceName) return;
    setFileLoading(true);
    try {
      const res = await fetch(`/api/workspace/${encodeURIComponent(workspaceName)}/files/${filePath}`);
      if (res.ok) {
        const contentType = res.headers.get("content-type") ?? "";
        let content: string;
        if (contentType.includes("json")) {
          const json = await res.json();
          content = JSON.stringify(json, null, 2);
        } else {
          content = await res.text();
        }
        setSelectedFile({ path: filePath, content });
      }
    } catch {
      // ignore
    } finally {
      setFileLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (folders.length === 0) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Documents</h1>
          <p className="text-sm text-muted-foreground">Engagement workspace files</p>
        </div>
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-16">
            <FileText className="mb-3 h-10 w-10 text-muted-foreground/30" />
            <p className="text-sm font-medium">No workspace files found</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Start an engagement from the Live tab to generate documents
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const totalFiles = folders.reduce((sum, g) => sum + g.files.length, 0);

  return (
    <div className="flex h-full gap-4 overflow-hidden">
      {/* Left: Folder tree */}
      <div className="w-64 shrink-0 overflow-hidden flex flex-col">
        <div className="mb-3">
          <h1 className="text-lg font-bold tracking-tight">Documents</h1>
          <p className="text-xs text-muted-foreground">
            {totalFiles} files in {folders.length} folders
          </p>
        </div>

        <ScrollArea className="flex-1">
          <div className="space-y-0.5 pr-2">
            {folders.map((group) => {
              const meta = FOLDER_META[group.folder];
              const Icon = meta?.icon ?? Folder;
              const color = meta?.color ?? "text-muted-foreground";
              const expanded = expandedFolders.has(group.folder);

              return (
                <div key={group.folder}>
                  {/* Folder header */}
                  <button
                    onClick={() => toggleFolder(group.folder)}
                    className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left transition-colors hover:bg-accent"
                  >
                    <ChevronRight
                      className={cn(
                        "h-3 w-3 shrink-0 text-muted-foreground transition-transform",
                        expanded && "rotate-90"
                      )}
                    />
                    <Icon className={cn("h-4 w-4 shrink-0", color)} />
                    <span className="flex-1 text-sm font-medium">
                      {meta?.label ?? group.folder}
                    </span>
                    <span className="text-[10px] text-muted-foreground/70">
                      {group.files.length}
                    </span>
                  </button>

                  {/* File list */}
                  {expanded && (
                    <div className="ml-5 space-y-0.5 pb-1">
                      {group.files.map((file) => {
                        const active = selectedFile?.path === file.path;
                        return (
                          <button
                            key={file.path}
                            onClick={() => openFile(file.path)}
                            className={cn(
                              "flex w-full items-center gap-2 rounded-md px-3 py-1.5 text-left text-sm transition-colors",
                              active
                                ? "bg-primary/10 text-foreground"
                                : "text-muted-foreground hover:bg-accent hover:text-foreground"
                            )}
                          >
                            <File className="h-3 w-3 shrink-0" />
                            <span className="flex-1 truncate text-xs">{file.name}</span>
                            <span className="text-[10px] text-muted-foreground/50">
                              {formatSize(file.size)}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </ScrollArea>
      </div>

      {/* Right: File content viewer */}
      <Card className="flex-1 overflow-hidden">
        <CardHeader className="border-b border-border/50 pb-3">
          <CardTitle className="flex items-center gap-2 text-sm font-mono">
            <FileText className="h-4 w-4" />
            {selectedFile?.path ?? "Select a file"}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <ScrollArea className="h-[calc(100vh-14rem)]">
            <div className="p-6">
              {fileLoading ? (
                <div className="flex items-center justify-center py-16">
                  <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                </div>
              ) : selectedFile ? (
                <pre className="whitespace-pre-wrap text-sm text-muted-foreground font-mono">
                  {selectedFile.content}
                </pre>
              ) : (
                <div className="flex items-center justify-center py-16 text-sm text-muted-foreground">
                  Select a file from the sidebar
                </div>
              )}
            </div>
          </ScrollArea>
        </CardContent>
      </Card>
    </div>
  );
}
