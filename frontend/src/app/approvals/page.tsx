"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch, Deployment, PendingAction } from "@/lib/api";
import ApprovalCard from "@/components/ApprovalCard";

type Approval = Omit<PendingAction, "tool"> & { tool_name: string; expires_at: string };

export default function ApprovalsPage() {
  const [actions, setActions] = useState<Approval[]>([]);
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [message, setMessage] = useState("");
  const [decidingId, setDecidingId] = useState<string | null>(null);
  const load = useCallback(async () => { try { const [actionRows, deploymentRows] = await Promise.all([apiFetch<Approval[]>("/actions/pending"), apiFetch<Deployment[]>("/deployments/pending")]); setActions(actionRows); setDeployments(deploymentRows); } catch (caught) { setMessage(caught instanceof Error ? caught.message : "Could not load approvals"); } }, []);
  useEffect(() => {
    // State changes occur after the API promise resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);
  const decide = async (id: string, decision: "approve" | "reject") => {
    if (decidingId) return;
    setDecidingId(id);
    try { await apiFetch(`/actions/${id}/decision`, { method: "POST", body: JSON.stringify({ decision }) }); setMessage(`Action ${decision === "approve" ? "approved" : "rejected"}.`); await load(); }
    catch (caught) { setMessage(caught instanceof Error ? caught.message : "Decision failed"); }
    finally { setDecidingId(null); }
  };
  const decideDeployment = async (id: string, decision: "approve" | "reject") => { if (decidingId) return; setDecidingId(id); try { await apiFetch(`/deployments/${id}/decision`, { method: "POST", body: JSON.stringify({ decision }) }); setMessage(`Deployment ${decision === "approve" ? "approved" : "rejected"}.`); await load(); } catch (caught) { setMessage(caught instanceof Error ? caught.message : "Decision failed"); } finally { setDecidingId(null); } };
  return <main className="p-8"><h1 className="text-3xl font-bold">Approvals</h1><p className="mt-1 text-gray-500">Review exactly what will change before it executes.</p>{message && <p className="mt-4 rounded-xl bg-black/5 p-3 text-sm dark:bg-white/10" aria-live="polite">{message}</p>}<div className="mt-6 space-y-4">{actions.map((action) => <ApprovalCard key={action.id} action={{ ...action, tool: action.tool_name }} busy={decidingId === action.id} onDecision={(decision) => void decide(action.id, decision)} />)}{deployments.map((deployment) => <article className="rounded-2xl border border-amber-300 p-5" key={deployment.id}><div className="flex justify-between"><h2 className="font-semibold">Deploy validated artifact</h2><span className="rounded-full bg-red-100 px-3 py-1 text-xs text-red-800">Class E</span></div><p className="mt-3 text-sm">Environment: {deployment.environment}</p><p className="mt-2 whitespace-pre-wrap rounded bg-black/5 p-3 text-sm">Rollback plan: {deployment.rollback_plan}</p><div className="mt-4 flex gap-2"><button disabled={decidingId !== null} onClick={() => void decideDeployment(deployment.id, "approve")} className="rounded-lg bg-green-600 px-4 py-2 text-white disabled:opacity-40">{decidingId === deployment.id ? "Submitting…" : "Approve deployment"}</button><button disabled={decidingId !== null} onClick={() => void decideDeployment(deployment.id, "reject")} className="rounded-lg border px-4 py-2 disabled:opacity-40">Reject</button></div></article>)}{!actions.length && !deployments.length && <p className="rounded-2xl border p-8 text-center text-gray-500 dark:border-white/10">No actions need your approval.</p>}</div></main>;
}
