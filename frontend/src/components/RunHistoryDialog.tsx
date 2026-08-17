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

  return <>
    <dialog ref={dialogRef} onClose={onClose} className="m-auto max-h-[85vh] w-[min(42rem,calc(100%-2rem))] rounded-2xl border border-white/10 bg-zinc-950 p-0 text-white shadow-2xl backdrop:bg-black/70">
      <div className="flex max-h-[85vh] flex-col p-6">
        <header className="flex items-center justify-between border-b border-white/10 pb-4"><div className="flex items-center gap-2"><History className="h-5 w-5 text-purple-400" /><h2 className="text-lg font-bold">Run history</h2></div><button onClick={() => dialogRef.current?.close()} className="rounded-lg border border-white/10 px-3 py-1.5 text-xs">Close</button></header>
        <div className="mt-4 flex-1 space-y-3 overflow-y-auto">
          {!runs.length && <p className="p-8 text-center text-sm text-gray-500">No past runs recorded.</p>}
          {runs.map((run) => <article key={run.id} className={`flex items-start gap-3 rounded-xl border p-4 ${selectedRunId === run.id ? "border-purple-500/50 bg-purple-950/20" : "border-white/10 bg-zinc-900"}`}>
            <button disabled={Boolean(activeRunId && activeRunId !== run.id)} className="min-w-0 flex-1 text-left disabled:cursor-not-allowed disabled:opacity-50" onClick={() => onOpenRun(run)}>
              <div className="flex flex-wrap items-center gap-2"><span className="rounded-full bg-white/10 px-2 py-0.5 text-[10px] font-bold uppercase">{run.status}</span><time className="text-[11px] text-gray-500">{new Date(run.created_at).toLocaleString()}</time>{run.cost_usd > 0 && <span className="text-[10px] text-purple-300">${run.cost_usd.toFixed(4)}</span>}</div>
              <p className="mt-2 line-clamp-2 text-xs text-gray-200">{run.prompt}</p>
              {run.error_message && <p className="mt-1 line-clamp-1 text-[11px] text-red-400">{run.error_message}</p>}
            </button>
            <button disabled={activeRunId === run.id} onClick={() => requestDelete(run.id)} className="rounded-lg p-2 text-gray-500 hover:bg-red-500/20 hover:text-red-400 disabled:opacity-30" aria-label={`Delete run ${run.prompt}`}><Trash2 className="h-4 w-4" /></button>
          </article>)}
        </div>
      </div>
    </dialog>
    <dialog ref={confirmRef} className="m-auto w-[min(26rem,calc(100%-2rem))] rounded-2xl border border-white/10 bg-zinc-950 p-6 text-white shadow-2xl backdrop:bg-black/70">
      <h2 className="text-lg font-semibold">Delete this run?</h2><p className="mt-2 text-sm text-gray-400">Its transcript and durable events will be removed permanently.</p>
      <div className="mt-5 flex justify-end gap-2"><button onClick={() => confirmRef.current?.close()} className="rounded-lg border px-4 py-2">Cancel</button><button onClick={confirmDelete} className="rounded-lg bg-red-600 px-4 py-2">Delete</button></div>
    </dialog>
  </>;
}
