"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, FileText, Folder } from "lucide-react";
import { apiFetch, WorkspaceEntry } from "@/lib/api";

const sorted = (entries: WorkspaceEntry[]) => [...entries].sort((a, b) => Number(b.type === "directory") - Number(a.type === "directory") || a.path.localeCompare(b.path));

export default function WorkspaceFileTree({ projectId, runId, entries, selected, onOpen, onError }: { projectId: number; runId?: string | null; entries: WorkspaceEntry[]; selected: string; onOpen: (path: string) => void; onError: (message: string) => void }) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [children, setChildren] = useState<Record<string, WorkspaceEntry[]>>({});
  const [loading, setLoading] = useState<Set<string>>(new Set());

  const toggle = async (entry: WorkspaceEntry) => {
    const next = new Set(expanded);
    if (next.has(entry.path)) { next.delete(entry.path); setExpanded(next); return; }
    next.add(entry.path); setExpanded(next);
    if (children[entry.path]) return;
    setLoading((current) => new Set(current).add(entry.path));
    try {
      const runQuery = runId ? `&run_id=${encodeURIComponent(runId)}` : "";
      const rows = await apiFetch<WorkspaceEntry[]>(`/projects/${projectId}/workspace/tree?path=${encodeURIComponent(entry.path)}${runQuery}`);
      setChildren((current) => ({ ...current, [entry.path]: sorted(rows) }));
    } catch (caught) {
      const collapsed = new Set(next); collapsed.delete(entry.path); setExpanded(collapsed);
      onError(caught instanceof Error ? caught.message : `Could not open ${entry.path}`);
    } finally {
      setLoading((current) => { const value = new Set(current); value.delete(entry.path); return value; });
    }
  };

  const render = (rows: WorkspaceEntry[], depth = 0): React.ReactNode => sorted(rows).map((entry) => {
    const directory = entry.type === "directory";
    const open = expanded.has(entry.path);
    return <li key={entry.path}>
      <button
        type="button"
        onClick={() => directory ? void toggle(entry) : onOpen(entry.path)}
        aria-expanded={directory ? open : undefined}
        className={`flex w-full items-center gap-1 truncate rounded py-1 pr-1 text-left hover:bg-white/5 focus-visible:outline-2 focus-visible:outline-blue-500 ${selected === entry.path ? "bg-purple-600/30 text-purple-300" : "text-gray-400"}`}
        style={{ paddingLeft: `${depth * 12 + 4}px` }}
      >
        {directory ? loading.has(entry.path) ? <span className="w-3 animate-pulse">…</span> : open ? <ChevronDown className="h-3 w-3 shrink-0" /> : <ChevronRight className="h-3 w-3 shrink-0" /> : <span className="w-3" />}
        {directory ? <Folder className="h-3 w-3 shrink-0 text-amber-500" /> : <FileText className="h-3 w-3 shrink-0 text-blue-400" />}
        <span className="truncate">{entry.path.split("/").pop()}</span>
      </button>
      {directory && open && <ul>{render(children[entry.path] || [], depth + 1)}</ul>}
    </li>;
  });

  return <ul className="space-y-0.5 text-xs font-mono">{render(entries)}</ul>;
}
