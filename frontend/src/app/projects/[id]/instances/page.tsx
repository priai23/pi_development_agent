"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { apiFetch, Instance, Project } from "@/lib/api";
import ConfirmDialog from "@/components/ConfirmDialog";

export default function ConnectionsPage() {
  const projectId = Number(useParams<{ id: string }>().id);
  const [project, setProject] = useState<Project | null>(null);
  const [instances, setInstances] = useState<Instance[]>([]);
  const [editing, setEditing] = useState<Instance | null>(null);
  const [password, setPassword] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [message, setMessage] = useState("");
  const [bridgeUrl, setBridgeUrl] = useState("");
  const [bridgeToken, setBridgeToken] = useState("");
  const [repositoryUrl, setRepositoryUrl] = useState("");
  const [deployKey, setDeployKey] = useState("");
  const [publicKey, setPublicKey] = useState("");
  const [deleteId, setDeleteId] = useState<number | null>(null);

  const load = useCallback(async () => {
    const [projectRow, rows] = await Promise.all([apiFetch<Project>(`/projects/${projectId}`), apiFetch<Instance[]>(`/projects/${projectId}/instances`)]);
    setProject(projectRow); setInstances(rows);
  }, [projectId]);
  useEffect(() => { const timer = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(timer); }, [load]);

  const retest = async (id: number) => {
    setMessage("Testing connection…");
    try {
      const result = await apiFetch<Instance>(`/instances/${id}/test`, { method: "POST" });
      setMessage(result.status === "connected" ? "Connection verified." : `Connection failed: ${result.last_error || "unknown error"}`);
      await load();
    } catch (caught) { setMessage(caught instanceof Error ? caught.message : "Connection test failed"); }
  };
  const save = async (event: FormEvent) => {
    event.preventDefault(); if (!editing) return;
    try {
      await apiFetch(`/instances/${editing.id}`, { method: "PATCH", body: JSON.stringify({ environment: editing.environment, hosting_type: editing.hosting_type, auth_method: editing.auth_method, username: editing.username, password: password || undefined, api_key: apiKey || undefined, is_active: editing.is_active }) });
      setEditing(null); setPassword(""); setApiKey(""); setMessage("Connection updated. Retest it before use."); await load();
    } catch (caught) { setMessage(caught instanceof Error ? caught.message : "Could not update connection"); }
  };
  const remove = async (id: number) => {
    try { await apiFetch(`/instances/${id}`, { method: "DELETE" }); await load(); }
    catch (caught) { setMessage(caught instanceof Error ? caught.message : "Could not delete connection"); }
  };
  const configureDeployment = async () => {
    if (!editing) return;
    try {
      const payload = editing.hosting_type === "odoo_sh" ? { repository_url: repositoryUrl, staging_branch: "primacy/{project}/{release}", production_branch: "production", module_directory: ".", git_deploy_key: deployKey } : { bridge_url: bridgeUrl, bridge_token: bridgeToken };
      const result = await apiFetch<{ public_key_base64: string }>(`/instances/${editing.id}/deployment-config`, { method: "PUT", body: JSON.stringify(payload) });
      setPublicKey(result.public_key_base64); setBridgeToken(""); setDeployKey(""); setMessage("Deployment target configured. Copy the public key to the host runner.");
    } catch (caught) { setMessage(caught instanceof Error ? caught.message : "Deployment configuration failed"); }
  };

  if (!project) return <main className="p-8">Loading…</main>;
  return <main className="space-y-6 p-4 sm:p-8">
    <div><Link href={`/projects/${projectId}`} className="text-sm text-blue-600">← Agent</Link><h1 className="text-3xl font-bold">Odoo connections</h1><p className="text-gray-500">Replace credentials, retest health, deactivate, or remove an instance.</p></div>
    {message && <p className="rounded-xl bg-black/5 p-3 text-sm dark:bg-white/10" aria-live="polite">{message}</p>}
    <div className="space-y-4">{instances.map((instance) => <article className="rounded-2xl border p-5 dark:border-white/10" key={instance.id}>
      <div className="flex flex-wrap items-start justify-between gap-4"><div><h2 className="font-semibold">{instance.url}</h2><p className="text-sm text-gray-500">{instance.db_name} · {instance.environment} · {instance.auth_method.toUpperCase()}</p><p className={`mt-2 text-sm ${instance.status === "connected" ? "text-green-600" : "text-red-600"}`}>{instance.status}{instance.last_tested_at ? ` · tested ${new Date(instance.last_tested_at).toLocaleString()}` : ""}</p></div><div className="flex gap-2"><button onClick={() => void retest(instance.id)} className="rounded-lg border px-3 py-2">Retest</button><button onClick={() => setEditing(instance)} className="rounded-lg border px-3 py-2">Edit</button><button onClick={() => setDeleteId(instance.id)} className="rounded-lg border border-red-300 px-3 py-2 text-red-600">Delete</button></div></div>
    </article>)}</div>
    {editing && <div className="space-y-4">
      <form onSubmit={save} className="rounded-2xl border p-5 dark:border-white/10"><h2 className="text-xl font-semibold">Edit connection</h2><div className="mt-4 grid gap-4 md:grid-cols-2">
        <label className="text-sm">Environment<select value={editing.environment} onChange={(event) => setEditing({ ...editing, environment: event.target.value as Instance["environment"] })} className="mt-1 w-full rounded-lg border px-3 py-2 dark:bg-black"><option value="staging">Staging</option><option value="production">Production</option></select></label>
        <label className="text-sm">Hosting<select value={editing.hosting_type} onChange={(event) => setEditing({ ...editing, hosting_type: event.target.value as Instance["hosting_type"] })} className="mt-1 w-full rounded-lg border px-3 py-2 dark:bg-black"><option value="on_premise">On-premise</option><option value="odoo_sh">Odoo.sh</option></select></label>
        <label className="text-sm">Username<input value={editing.username || ""} onChange={(event) => setEditing({ ...editing, username: event.target.value })} className="mt-1 w-full rounded-lg border px-3 py-2 dark:bg-black" /></label>
        <label className="text-sm">Replacement password<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Leave blank to retain" className="mt-1 w-full rounded-lg border px-3 py-2 dark:bg-black" /></label>
        <label className="text-sm">Replacement JSON-2 API key<input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="Leave blank to retain" className="mt-1 w-full rounded-lg border px-3 py-2 dark:bg-black" /></label>
        <label className="flex items-center gap-2 pt-6 text-sm"><input type="checkbox" checked={editing.is_active} onChange={(event) => setEditing({ ...editing, is_active: event.target.checked })} /> Active</label>
      </div><div className="mt-4 flex gap-2"><button className="rounded-lg bg-blue-600 px-4 py-2 text-white">Save</button><button type="button" onClick={() => setEditing(null)} className="rounded-lg border px-4 py-2">Cancel</button></div></form>
      <section className="rounded-2xl border p-5 dark:border-white/10"><h2 className="text-xl font-semibold">Deployment target</h2>{editing.hosting_type === "on_premise" ? <div className="mt-4 grid gap-3 md:grid-cols-2"><input type="url" value={bridgeUrl} onChange={(event) => setBridgeUrl(event.target.value)} placeholder="Bridge URL" className="rounded-lg border px-3 py-2 dark:bg-black" /><input type="password" value={bridgeToken} onChange={(event) => setBridgeToken(event.target.value)} placeholder="Bridge token" className="rounded-lg border px-3 py-2 dark:bg-black" /></div> : <div className="mt-4 space-y-3"><input value={repositoryUrl} onChange={(event) => setRepositoryUrl(event.target.value)} placeholder="Odoo.sh Git repository" className="w-full rounded-lg border px-3 py-2 dark:bg-black" /><textarea value={deployKey} onChange={(event) => setDeployKey(event.target.value)} placeholder="Encrypted Git deploy private key" className="w-full rounded-lg border px-3 py-2 font-mono text-xs dark:bg-black" /></div>}<button type="button" onClick={() => void configureDeployment()} className="mt-4 rounded-lg bg-blue-600 px-4 py-2 text-white">Configure deployment</button>{publicKey && <div className="mt-4"><p className="text-sm font-medium">Runner verification public key</p><code className="mt-1 block break-all rounded bg-black/5 p-3 text-xs">{publicKey}</code></div>}</section>
    </div>}
    <ConfirmDialog open={deleteId !== null} title="Delete Odoo connection?" description="The encrypted credentials and deployment configuration for this connection will be removed." onClose={() => setDeleteId(null)} onConfirm={() => { if (deleteId !== null) void remove(deleteId); setDeleteId(null); }} />
  </main>;
}
