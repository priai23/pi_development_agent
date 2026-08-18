"use client";

import { useEffect, useState } from "react";
import CodeDiffViewer from "./CodeDiffViewer";
import { PendingAction } from "@/lib/api";

export default function ApprovalCard({ action, busy = false, onDecision }: { action: PendingAction; busy?: boolean; onDecision: (decision: "approve" | "reject", autoApproveTask?: boolean) => void }) {
  const [expired, setExpired] = useState(false);
  useEffect(() => {
    if (!action.expires_at) return;
    const timer = window.setTimeout(() => setExpired(true), Math.max(0, new Date(action.expires_at).getTime() - Date.now()));
    return () => window.clearTimeout(timer);
  }, [action.expires_at]);
  const diff = typeof action.preview.diff === "string" ? action.preview.diff : "";
  const files = Array.isArray(action.preview.files) ? action.preview.files.map(String) : action.preview.path ? [String(action.preview.path)] : [];
  const verification = String(action.preview.verification_plan || action.preview.verification || "Verify the action result before continuing.");
  const disabled = busy || expired || action.status !== "pending";

  return (
    <article className="rounded-2xl border border-amber-400/40 bg-amber-950/20 p-5 text-sm" aria-label={`Approval required for ${action.tool}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div><p className="font-semibold text-amber-200">Approval required</p><p className="mt-1 font-mono text-xs text-amber-100/80">{action.tool}</p></div>
        <span className="rounded-full border border-amber-400/30 px-2.5 py-1 text-xs text-amber-300">Class {action.risk_class}</span>
      </div>
      {files.length > 0 && <p className="mt-3 text-xs text-gray-300"><span className="font-semibold">Affected:</span> {files.join(", ")}</p>}
      {diff ? (
        <div className="mt-3 max-h-80 overflow-auto rounded-xl border border-white/10"><CodeDiffViewer path={files[0] || "Pending change"} content={diff} isDiff /></div>
      ) : (
        <dl className="mt-3 grid gap-2 rounded-xl bg-black/40 p-3 text-xs">{Object.entries(action.preview).filter(([key]) => !["diff", "files", "verification", "verification_plan"].includes(key)).map(([key, value]) => <div key={key} className="grid grid-cols-[8rem_1fr] gap-2"><dt className="font-semibold text-gray-400">{key.replace(/_/g, " ")}</dt><dd className="break-all font-mono text-gray-200">{typeof value === "string" ? value : JSON.stringify(value)}</dd></div>)}</dl>
      )}
      {action.arguments && Object.keys(action.arguments).length > 0 && <details className="mt-3 text-xs"><summary className="cursor-pointer font-semibold text-gray-300">Tool arguments</summary><dl className="mt-2 grid gap-2 rounded bg-black/40 p-3">{Object.entries(action.arguments).map(([key, value]) => <div key={key} className="grid grid-cols-[8rem_1fr] gap-2"><dt className="text-gray-500">{key.replace(/_/g, " ")}</dt><dd className="break-all font-mono text-gray-300">{typeof value === "string" ? value : JSON.stringify(value)}</dd></div>)}</dl></details>}
      <p className="mt-3 text-xs text-gray-400"><span className="font-semibold text-gray-300">Verification:</span> {verification}</p>
      {action.expires_at && <p className={`mt-2 text-xs ${expired ? "text-red-400" : "text-gray-500"}`}>{expired ? "This approval has expired." : `Expires ${new Date(action.expires_at).toLocaleString()}`}</p>}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button disabled={disabled} onClick={() => onDecision("approve", false)} className="rounded-lg bg-emerald-600 px-4 py-2 font-medium text-white transition hover:bg-emerald-500 active:scale-95 disabled:opacity-40">{busy ? "Submitting…" : "Approve"}</button>
        {Number(action.risk_class) <= 2 && (
          <button disabled={disabled} onClick={() => onDecision("approve", true)} title="Auto-approves remaining file writes for this task" className="rounded-lg border border-emerald-500/40 bg-emerald-950/40 px-3.5 py-2 text-xs font-medium text-emerald-300 transition hover:bg-emerald-900/60 active:scale-95 disabled:opacity-40">
            ⚡ Approve All for Task
          </button>
        )}
        <button disabled={disabled} onClick={() => onDecision("reject")} className="rounded-lg border border-white/20 px-4 py-2 text-gray-200 transition hover:bg-white/5 active:scale-95 disabled:opacity-40">Reject</button>
      </div>
    </article>
  );
}
