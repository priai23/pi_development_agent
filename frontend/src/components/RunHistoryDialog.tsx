"use client";

import { useEffect, useRef, useState } from "react";
import { History, Trash2 } from "lucide-react";
import { AgentRun } from "@/lib/api";

export default function RunHistoryDialog({ open, runs, activeRunId, selectedRunId, onOpenRun, onDeleteRun, onClose }: { open: boolean; runs: AgentRun[]; activeRunId: string | null; selectedRunId: string | null; onOpenRun: (run: AgentRun) => void; onDeleteRun: (id: string) => void; onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const confirmRef = useRef<HTMLDialogElement>(null);
  const [deleteId, setDeleteId] = useState<string | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (open && dialog && !dialog.open) dialog.showModal();
    if (!open && dialog?.open) dialog.close();
  }, [open]);

  const requestDelete = (id: string) => { setDeleteId(id); confirmRef.current?.showModal(); };
  const confirmDelete = () => { if (deleteId) onDeleteRun(deleteId); setDeleteId(null); confirmRef.current?.close(); };

  // Group individual runs into chat threads
  const threadMap = new Map<string, AgentRun[]>();
  for (const run of runs) {
    const key = run.thread_id || run.id;
    if (!threadMap.has(key)) threadMap.set(key, []);
    threadMap.get(key)!.push(run);
  }

  const chatThreads = Array.from(threadMap.entries()).map(([threadId, threadRuns]) => {
    const sorted = [...threadRuns].sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
    const initialRun = sorted[0];
    const latestRun = sorted[sorted.length - 1];
    const totalCostUsd = threadRuns.reduce((sum, r) => sum + (r.cost_usd || 0), 0);
    return {
      threadId,
      initialPrompt: initialRun.prompt,
      latestPrompt: latestRun.prompt,
      latestRun,
      turnCount: threadRuns.length,
      totalCostUsd,
      lastActive: latestRun.created_at,
      runs: sorted,
    };
  }).sort((a, b) => new Date(b.lastActive).getTime() - new Date(a.lastActive).getTime());

  return <>
    <dialog ref={dialogRef} onClose={onClose} className="m-auto max-h-[85vh] w-[min(42rem,calc(100%-2rem))] rounded-2xl border border-white/10 bg-zinc-950 p-0 text-white shadow-2xl backdrop:bg-black/70">
      <div className="flex max-h-[85vh] flex-col p-6">
        <header className="flex items-center justify-between border-b border-white/10 pb-4">
          <div className="flex items-center gap-2">
            <History className="h-5 w-5 text-purple-400" />
            <h2 className="text-lg font-bold">Chat history ({chatThreads.length})</h2>
          </div>
          <button onClick={() => dialogRef.current?.close()} className="rounded-lg border border-white/10 px-3 py-1.5 text-xs hover:bg-white/5 transition">Close</button>
        </header>
        <div className="mt-4 flex-1 space-y-3 overflow-y-auto">
          {!chatThreads.length && <p className="p-8 text-center text-sm text-gray-500">No past chats recorded.</p>}
          {chatThreads.map((chat) => {
            const isSelected = chat.runs.some((r) => r.id === selectedRunId);
            const isActive = chat.runs.some((r) => r.id === activeRunId);
            return (
              <article key={chat.threadId} className={`flex items-start gap-3 rounded-xl border p-4 transition ${isSelected ? "border-blue-500/50 bg-blue-950/20" : "border-white/10 bg-zinc-900 hover:border-white/20"}`}>
                <button className="min-w-0 flex-1 text-left" onClick={() => onOpenRun(chat.latestRun)}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase ${isActive ? "bg-blue-600/30 text-blue-300" : "bg-white/10 text-gray-300"}`}>
                      {chat.latestRun.status}
                    </span>
                    <span className="rounded-full bg-purple-900/40 border border-purple-500/30 px-2 py-0.5 text-[10px] font-medium text-purple-200">
                      {chat.turnCount === 1 ? "1 message" : `${chat.turnCount} messages`}
                    </span>
                    <time className="text-[11px] text-gray-500">{new Date(chat.lastActive).toLocaleString()}</time>
                    {chat.totalCostUsd > 0 && <span className="text-[10px] text-purple-300">${chat.totalCostUsd.toFixed(4)}</span>}
                  </div>
                  <p className="mt-2 line-clamp-2 text-xs font-medium text-gray-200">{chat.initialPrompt}</p>
                  {chat.turnCount > 1 && (
                    <p className="mt-1 line-clamp-1 text-[11px] text-gray-400">
                      <span className="text-gray-500">Latest:</span> {chat.latestPrompt}
                    </p>
                  )}
                  {chat.latestRun.error_message && <p className="mt-1 line-clamp-1 text-[11px] text-red-400">{chat.latestRun.error_message}</p>}
                </button>
                <button
                  disabled={isActive}
                  onClick={() => requestDelete(chat.latestRun.id)}
                  className="rounded-lg p-2 text-gray-500 hover:bg-red-500/20 hover:text-red-400 disabled:opacity-30 transition"
                  aria-label={`Delete chat ${chat.initialPrompt}`}
                  title="Delete chat"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </article>
            );
          })}
        </div>
      </div>
    </dialog>
    <dialog ref={confirmRef} className="m-auto w-[min(26rem,calc(100%-2rem))] rounded-2xl border border-white/10 bg-zinc-950 p-6 text-white shadow-2xl backdrop:bg-black/70">
      <h2 className="text-lg font-semibold">Delete this chat?</h2>
      <p className="mt-2 text-sm text-gray-400">Its messages and durable run events will be removed permanently.</p>
      <div className="mt-5 flex justify-end gap-2">
        <button onClick={() => confirmRef.current?.close()} className="rounded-lg border border-white/10 px-4 py-2 text-xs">Cancel</button>
        <button onClick={confirmDelete} className="rounded-lg bg-red-600 px-4 py-2 text-xs font-semibold hover:bg-red-500 transition">Delete</button>
      </div>
    </dialog>
  </>;
}
