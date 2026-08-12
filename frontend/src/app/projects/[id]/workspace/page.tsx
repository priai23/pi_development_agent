"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { apiFetch, API_URL, AgentRun, Artifact, Deployment, Instance, Project, Requirement, WorkspaceEntry } from "@/lib/api";

type Commit = { hash: string; created_at: string; message: string };
type Validation = { id: string; status: string; report: { checks?: { name: string; passed: boolean; message?: string }[] }; created_at: string };

export default function WorkspaceLifecyclePage() {
  const params = useParams<{ id: string }>();
  const projectId = Number(params.id);
  const requestedFile = useSearchParams().get("file");
  const [project, setProject] = useState<Project | null>(null);
  const [entries, setEntries] = useState<WorkspaceEntry[]>([]);
  const [commits, setCommits] = useState<Commit[]>([]);
  const [requirements, setRequirements] = useState<Requirement[]>([]);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [validations, setValidations] = useState<Validation[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [instances, setInstances] = useState<Instance[]>([]);
  const [selected, setSelected] = useState("");
  const [content, setContent] = useState("");
  const [title, setTitle] = useState("");
  const [criteria, setCriteria] = useState("");
  const [error, setError] = useState("");
  const [artifactId, setArtifactId] = useState("");
  const [instanceId, setInstanceId] = useState("");
  const [rollbackPlan, setRollbackPlan] = useState("");
  const load = useCallback(async () => {
    try {
      const [projectRow, tree, history, requirementRows, runRows, validationRows, artifactRows, deploymentRows, instanceRows] = await Promise.all([
        apiFetch<Project>(`/projects/${projectId}`), apiFetch<WorkspaceEntry[]>(`/projects/${projectId}/workspace/tree`),
        apiFetch<Commit[]>(`/projects/${projectId}/workspace/commits`), apiFetch<Requirement[]>(`/projects/${projectId}/requirements`),
        apiFetch<AgentRun[]>(`/projects/${projectId}/runs`), apiFetch<Validation[]>(`/projects/${projectId}/validations`),
        apiFetch<Artifact[]>(`/projects/${projectId}/artifacts`), apiFetch<Deployment[]>(`/projects/${projectId}/deployments`), apiFetch<Instance[]>(`/projects/${projectId}/instances`),
      ]);
      setProject(projectRow); setEntries(tree); setCommits(history); setRequirements(requirementRows); setRuns(runRows); setValidations(validationRows); setArtifacts(artifactRows); setDeployments(deploymentRows); setInstances(instanceRows);
      if (requestedFile) {
        const file = await apiFetch<{ content: string }>(`/projects/${projectId}/workspace/files?path=${encodeURIComponent(requestedFile)}`);
        setSelected(requestedFile); setContent(file.content);
      }
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not load workspace"); }
  }, [projectId, requestedFile]);
  useEffect(() => {
    // State changes occur after the API promises resolve.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);
  const openFile = async (path: string) => { const file = await apiFetch<{ content: string }>(`/projects/${projectId}/workspace/files?path=${encodeURIComponent(path)}`); setSelected(path); setContent(file.content); };
  const addRequirement = async (event: FormEvent) => { event.preventDefault(); await apiFetch(`/projects/${projectId}/requirements`, { method: "POST", body: JSON.stringify({ title, acceptance_criteria: criteria }) }); setTitle(""); setCriteria(""); await load(); };
  const requestDeployment = async (event: FormEvent) => { event.preventDefault(); try { await apiFetch(`/projects/${projectId}/deployments`, { method: "POST", body: JSON.stringify({ artifact_id: artifactId, instance_id: Number(instanceId), rollback_plan: rollbackPlan }) }); setRollbackPlan(""); setError("Deployment submitted for separate approval."); await load(); } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not request deployment"); } };
  const refreshDeployment = async (id: string) => { await apiFetch(`/deployments/${id}/refresh`, { method: "POST" }); await load(); };

  if (!project) return <main className="p-8">{error || "Loading…"}</main>;
  return <main className="space-y-8 p-8">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><Link href={`/projects/${projectId}`} className="text-sm text-blue-600">← Agent</Link><h1 className="text-3xl font-bold">{project.name}</h1><p className="text-gray-500">Phase: <span className="font-medium capitalize text-current">{project.phase.replaceAll("_", " ")}</span></p></div><a href={`${API_URL}/projects/${projectId}/workspace/archive`} className="rounded-lg border px-4 py-2">Download workspace ZIP</a></div>
    <div className="grid gap-6 xl:grid-cols-2">
      <section className="rounded-2xl border p-5 dark:border-white/10"><h2 className="text-xl font-semibold">Files</h2><div className="mt-4 grid gap-4 md:grid-cols-[220px_1fr]"><ul className="max-h-96 overflow-auto text-sm">{entries.map((entry) => <li key={entry.path}><button disabled={entry.type !== "file"} onClick={() => void openFile(entry.path)} className="w-full truncate rounded px-2 py-1 text-left hover:bg-black/5 disabled:text-gray-400">{entry.type === "directory" ? "📁" : "📄"} {entry.path}</button></li>)}</ul><pre className="max-h-96 overflow-auto rounded-xl bg-gray-950 p-4 text-xs text-gray-100">{selected ? content : "Select a file to review it."}</pre></div></section>
      <section className="rounded-2xl border p-5 dark:border-white/10"><h2 className="text-xl font-semibold">Requirements</h2><form onSubmit={addRequirement} className="mt-4 space-y-2"><input required value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Requirement" className="w-full rounded-lg border px-3 py-2 dark:bg-black"/><textarea required value={criteria} onChange={(e) => setCriteria(e.target.value)} placeholder="Acceptance criteria" className="w-full rounded-lg border px-3 py-2 dark:bg-black"/><button className="rounded-lg bg-blue-600 px-4 py-2 text-white">Add requirement</button></form><ul className="mt-4 divide-y dark:divide-white/10">{requirements.map((item) => <li className="py-3" key={item.id}><div className="flex justify-between"><span>{item.title}</span><span className="text-xs uppercase text-gray-500">{item.status}</span></div><p className="mt-1 text-sm text-gray-500">{item.acceptance_criteria}</p></li>)}</ul></section>
      <section className="rounded-2xl border p-5 dark:border-white/10"><h2 className="text-xl font-semibold">Run history</h2><ul className="mt-3 divide-y dark:divide-white/10">{runs.map((run) => <li className="py-3" key={run.id}><div className="flex justify-between"><span className="truncate pr-4">{run.prompt}</span><span className="text-xs uppercase">{run.status}</span></div>{run.error_message && <p className="text-sm text-red-600">{run.error_message} · Support {run.support_id}</p>}</li>)}</ul></section>
      <section className="rounded-2xl border p-5 dark:border-white/10"><h2 className="text-xl font-semibold">Evidence</h2><h3 className="mt-4 font-medium">Validation reports</h3><ul className="mt-2 divide-y dark:divide-white/10">{validations.map((item) => <li className="py-2" key={item.id}>{item.status} · {item.report.checks?.filter((check) => check.passed).length || 0}/{item.report.checks?.length || 0} checks</li>)}</ul><h3 className="mt-5 font-medium">Workspace commits</h3><ul className="mt-2 divide-y dark:divide-white/10">{commits.map((commit) => <li className="py-2 text-sm" key={commit.hash}><span className="font-mono">{commit.hash.slice(0, 10)}</span> {commit.message}</li>)}</ul></section>
      <section className="rounded-2xl border p-5 dark:border-white/10"><h2 className="text-xl font-semibold">Deploy validated artifact</h2><form onSubmit={requestDeployment} className="mt-4 space-y-3"><select required value={artifactId} onChange={(e) => setArtifactId(e.target.value)} className="w-full rounded-lg border px-3 py-2 dark:bg-black"><option value="">Select artifact</option>{artifacts.filter((item) => item.status === "validated").map((item) => <option value={item.id} key={item.id}>{item.name} {item.version}</option>)}</select><select required value={instanceId} onChange={(e) => setInstanceId(e.target.value)} className="w-full rounded-lg border px-3 py-2 dark:bg-black"><option value="">Select environment</option>{instances.map((item) => <option value={item.id} key={item.id}>{item.environment} · {item.url}</option>)}</select><textarea required minLength={10} value={rollbackPlan} onChange={(e) => setRollbackPlan(e.target.value)} placeholder="Backup and rollback plan" className="w-full rounded-lg border px-3 py-2 dark:bg-black"/><button className="rounded-lg bg-blue-600 px-4 py-2 text-white">Request deployment</button></form><ul className="mt-5 divide-y dark:divide-white/10">{deployments.map((item) => <li className="py-3" key={item.id}><div className="flex justify-between"><span>{item.environment} · {artifacts.find((artifact) => artifact.id === item.artifact_id)?.name}</span><span className="text-xs uppercase">{item.status}</span></div>{item.status === "deploying" && <button onClick={() => void refreshDeployment(item.id)} className="mt-2 rounded border px-2 py-1 text-xs">Refresh bridge status</button>}{item.logs && <pre className="mt-2 max-h-32 overflow-auto bg-black p-2 text-xs text-white">{item.logs}</pre>}</li>)}</ul></section>
    </div>
  </main>;
}
