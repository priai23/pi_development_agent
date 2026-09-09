"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { FolderGit2, Plus, Sparkles, Trash2 } from "lucide-react";
import { useAuth } from "@/components/AppShell";
import { apiFetch, Organization, Project } from "@/lib/api";
import ConfirmDialog from "@/components/ConfirmDialog";
import ProjectCreationWizard from "@/components/ProjectCreationWizard";

export default function ProjectsPage() {
  const { user } = useAuth();
  const [projects, setProjects] = useState<Project[]>([]);
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [newOrganization, setNewOrganization] = useState("");
  const [error, setError] = useState("");
  const [projectToDelete, setProjectToDelete] = useState<Project | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      const [projectData, organizationData] = await Promise.all([
        apiFetch<Project[]>("/projects"),
        apiFetch<Organization[]>("/organizations"),
      ]);
      setProjects(projectData);
      setOrganizations(organizationData);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load projects");
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  const createOrganization = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    try {
      await apiFetch<Organization>("/organizations", {
        method: "POST",
        body: JSON.stringify({ name: newOrganization }),
      });
      setNewOrganization("");
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not create organization");
    }
  };

  const remove = async (project: Project) => {
    try {
      await apiFetch<void>(`/projects/${project.id}`, { method: "DELETE" });
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not delete project");
    }
  };

  return (
    <div className="mx-auto max-w-6xl p-8">
      <div className="mb-8 flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold">Implementation Projects</h1>
          <p className="mt-1 text-gray-500">AI-driven ERP customization and deployment workspaces</p>
        </div>
        <button
          onClick={() => setWizardOpen(true)}
          className="flex items-center gap-2 rounded-xl bg-gradient-to-r from-blue-600 to-indigo-600 px-5 py-2.5 font-semibold text-white shadow-lg shadow-blue-500/20 hover:from-blue-500 hover:to-indigo-500 transition-all self-start md:self-auto"
        >
          <Sparkles className="h-4 w-4" />
          + New Implementation Project
        </button>
      </div>

      {user?.role === "admin" && (
        <form onSubmit={createOrganization} className="mb-6 flex max-w-lg gap-2 rounded-xl border p-4 dark:border-white/10">
          <input
            required
            value={newOrganization}
            onChange={(event) => setNewOrganization(event.target.value)}
            placeholder="Create organization (e.g. Primacy Infotech)"
            className="min-w-0 flex-1 rounded-lg border px-3 py-2 dark:border-white/10 dark:bg-black"
          />
          <button className="rounded-lg bg-gray-900 px-4 text-white dark:bg-white dark:text-black font-medium">Create</button>
        </form>
      )}

      {error && <p className="mb-4 text-sm text-red-600">{error}</p>}

      {projects.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-white/10 p-12 text-center bg-slate-900/20">
          <FolderGit2 className="h-12 w-12 text-slate-500 mb-3" />
          <h3 className="text-lg font-semibold text-white">No projects yet</h3>
          <p className="text-sm text-slate-400 mt-1 max-w-md">
            Start a new ERP implementation project to connect your Odoo or PRI ERP server and generate an automated implementation plan.
          </p>
          <button
            onClick={() => setWizardOpen(true)}
            className="mt-5 flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-500 transition-colors"
          >
            <Plus className="h-4 w-4" /> Create First Project
          </button>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {projects.map((project) => {
            const instance = project.instances[0];
            const erpName = instance?.erp_type === "pri_erp" ? "PRI ERP" : instance ? "Odoo" : null;
            return (
              <div key={project.id} className="group relative rounded-2xl border bg-white p-5 shadow-sm dark:border-white/10 dark:bg-white/5 hover:border-blue-500/50 transition-all">
                <Link href={`/projects/${project.id}`} className="block">
                  <div className="flex items-center justify-between mb-3">
                    <FolderGit2 className="h-7 w-7 text-blue-500" />
                    {erpName && (
                      <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full border ${
                        erpName === "PRI ERP"
                          ? "bg-purple-500/10 text-purple-400 border-purple-500/20"
                          : "bg-amber-500/10 text-amber-400 border-amber-500/20"
                      }`}>
                        {erpName}
                      </span>
                    )}
                  </div>
                  <h2 className="font-semibold text-base text-slate-100">{project.name}</h2>
                  <p className="mt-1 text-xs text-gray-500">
                    {instance ? `${instance.db_name || "Connected"} · ${instance.status}` : "No ERP instance configured"}
                  </p>
                </Link>
                {(user?.role === "admin" || user?.id === project.created_by_id) && (
                  <button
                    aria-label={`Delete ${project.name}`}
                    onClick={() => setProjectToDelete(project)}
                    className="absolute right-4 top-4 rounded p-1 text-gray-400 opacity-0 hover:text-red-600 focus:opacity-100 group-hover:opacity-100 transition-opacity"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Guided Project Creation Wizard */}
      <ProjectCreationWizard
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
        organizations={organizations}
        onProjectCreated={() => void load()}
      />

      <ConfirmDialog
        open={Boolean(projectToDelete)}
        title="Delete project?"
        description={projectToDelete ? `${projectToDelete.name} and its workspace history will be removed.` : ""}
        onClose={() => setProjectToDelete(null)}
        onConfirm={() => {
          if (projectToDelete) void remove(projectToDelete);
          setProjectToDelete(null);
        }}
      />
    </div>
  );
}
