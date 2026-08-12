"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { FolderGit2, Plus, Trash2 } from "lucide-react";
import { useAuth } from "@/components/AppShell";
import { apiFetch, Organization, Project } from "@/lib/api";

export default function ProjectsPage() {
  const { user } = useAuth();
  const [projects, setProjects] = useState<Project[]>([]);
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [name, setName] = useState("");
  const [organizationId, setOrganizationId] = useState(0);
  const [newOrganization, setNewOrganization] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [projectData, organizationData] = await Promise.all([
        apiFetch<Project[]>("/projects"), apiFetch<Organization[]>("/organizations"),
      ]);
      setProjects(projectData); setOrganizations(organizationData);
      setOrganizationId((current) => current || organizationData[0]?.id || 0);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not load projects"); }
  }, []);

  useEffect(() => {
    // State changes occur after the API promises resolve.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  const createProject = async (event: FormEvent) => {
    event.preventDefault(); setError("");
    try {
      await apiFetch<Project>("/projects", { method: "POST", body: JSON.stringify({ name, organization_id: organizationId }) });
      setName(""); await load();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not create project"); }
  };

  const createOrganization = async (event: FormEvent) => {
    event.preventDefault(); setError("");
    try {
      await apiFetch<Organization>("/organizations", { method: "POST", body: JSON.stringify({ name: newOrganization }) });
      setNewOrganization(""); await load();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not create organization"); }
  };

  const remove = async (project: Project) => {
    if (!window.confirm(`Delete ${project.name}?`)) return;
    try { await apiFetch<void>(`/projects/${project.id}`, { method: "DELETE" }); await load(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not delete project"); }
  };

  return (
    <div className="mx-auto max-w-6xl p-8">
      <div className="mb-8"><h1 className="text-3xl font-bold">Projects</h1><p className="mt-1 text-gray-500">Organization-scoped implementation workspaces</p></div>
      {user?.role === "admin" && organizations.length === 0 && (
        <form onSubmit={createOrganization} className="mb-6 flex max-w-lg gap-2 rounded-xl border p-4 dark:border-white/10">
          <input required value={newOrganization} onChange={(event) => setNewOrganization(event.target.value)} placeholder="First organization" className="min-w-0 flex-1 rounded-lg border px-3 py-2 dark:border-white/10 dark:bg-black" />
          <button className="rounded-lg bg-gray-900 px-4 text-white dark:bg-white dark:text-black">Create</button>
        </form>
      )}
      {organizations.length > 0 && (
        <form onSubmit={createProject} className="mb-8 grid max-w-2xl grid-cols-[1fr_220px_auto] gap-2 rounded-xl border p-4 dark:border-white/10">
          <input required value={name} onChange={(event) => setName(event.target.value)} placeholder="Project name" className="rounded-lg border px-3 py-2 dark:border-white/10 dark:bg-black" />
          <select value={organizationId} onChange={(event) => setOrganizationId(Number(event.target.value))} className="rounded-lg border px-3 py-2 dark:border-white/10 dark:bg-black">{organizations.map((organization) => <option key={organization.id} value={organization.id}>{organization.name}</option>)}</select>
          <button className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 text-white"><Plus className="h-4 w-4" /> Create</button>
        </form>
      )}
      {error && <p className="mb-4 text-sm text-red-600">{error}</p>}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {projects.map((project) => (
          <div key={project.id} className="group relative rounded-2xl border bg-white p-5 shadow-sm dark:border-white/10 dark:bg-white/5">
            <Link href={`/projects/${project.id}`} className="block"><FolderGit2 className="mb-4 h-7 w-7 text-blue-600" /><h2 className="font-semibold">{project.name}</h2><p className="mt-1 text-xs text-gray-500">{project.instances.length ? "ERP connected" : "No ERP connection"}</p></Link>
            {(user?.role === "admin" || user?.id === project.created_by_id) && <button aria-label={`Delete ${project.name}`} onClick={() => void remove(project)} className="absolute right-4 top-4 rounded p-1 text-gray-400 opacity-0 hover:text-red-600 group-hover:opacity-100"><Trash2 className="h-4 w-4" /></button>}
          </div>
        ))}
      </div>
    </div>
  );
}
