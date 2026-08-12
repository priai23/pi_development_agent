"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import { Plus, ArrowRight, Activity, X, Loader2, Trash2 } from "lucide-react";
import { API_URL } from "@/lib/api";

export default function ProjectsPage() {
  const [projects, setProjects] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchProjects = async () => {
    try {
      const res = await fetch(`${API_URL}/projects/`);
      const data = await res.json();
      setProjects(data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchProjects();
  }, []);

  const handleDeleteProject = async (e: React.MouseEvent, projectId: number) => {
    e.preventDefault();
    if (!confirm("Are you sure you want to delete this project? This will delete all instances and chat history.")) return;
    
    try {
      const res = await fetch(`${API_URL}/projects/${projectId}`, {
        method: "DELETE"
      });
      if (res.ok) {
        fetchProjects();
      } else {
        alert("Failed to delete project");
      }
    } catch (err) {
      console.error(err);
      alert("Error deleting project");
    }
  };

  const handleCreateProject = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newProjectName.trim()) return;
    setCreating(true);
    setError(null);

    try {
      // 1. Get or Create Organization
      let orgId = 1;
      const orgsRes = await fetch(`${API_URL}/organizations/`);
      const orgs = await orgsRes.json();
      
      if (orgs.length === 0) {
        const createOrgRes = await fetch(`${API_URL}/organizations/`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: "Primacy Internal" })
        });
        const newOrg = await createOrgRes.json();
        orgId = newOrg.id;
      } else {
        orgId = orgs[0].id;
      }

      // 2. Create Project
      const createProjRes = await fetch(`${API_URL}/projects/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newProjectName, organization_id: orgId })
      });

      if (!createProjRes.ok) {
        throw new Error("Failed to create project");
      }

      setShowModal(false);
      setNewProjectName("");
      fetchProjects();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="p-8 max-w-6xl mx-auto w-full">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-black dark:text-white">Implementation Projects</h1>
          <p className="text-gray-500 dark:text-gray-400 mt-1">Manage your ERP implementations</p>
        </div>
        <button 
          onClick={() => setShowModal(true)}
          className="flex items-center space-x-2 bg-blue-500 hover:bg-blue-600 text-white px-4 py-2 rounded-xl font-medium transition-colors shadow-sm"
        >
          <Plus className="w-5 h-5" />
          <span>New Project</span>
        </button>
      </div>

      {loading ? (
        <div className="flex justify-center py-20">
          <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {projects.map((proj) => (
            <Link href={`/projects/${proj.id}`} key={proj.id} className="block group">
              <motion.div 
                whileTap={{ scale: 0.97 }}
                transition={{ type: "spring", bounce: 0, duration: 0.4 }}
                className="p-6 rounded-2xl border border-black/5 dark:border-white/10 bg-white dark:bg-white/5 shadow-sm hover:shadow-md transition-shadow cursor-pointer h-full flex flex-col"
              >
                <div className="flex justify-between items-start mb-4">
                  <div className="p-2 bg-blue-500/10 text-blue-500 rounded-lg">
                    <Activity className="w-6 h-6" />
                  </div>
                  <div className="flex items-center space-x-2">
                    <span className="text-xs font-medium px-2 py-1 bg-black/5 dark:bg-white/10 rounded-full text-gray-600 dark:text-gray-300">
                      Active
                    </span>
                    <button 
                      onClick={(e) => handleDeleteProject(e, proj.id)}
                      className="p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-500/10 rounded-md transition-colors"
                      title="Delete Project"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
                <h3 className="text-xl font-semibold mb-2 text-black dark:text-white tracking-tight">{proj.name}</h3>
                <p className="text-sm text-gray-500 dark:text-gray-400 mb-6 flex-1">
                  Started on {new Date(proj.created_at).toLocaleDateString()}
                </p>
                
                <div className="flex items-center justify-between pt-4 border-t border-black/5 dark:border-white/10 text-sm font-medium text-blue-500 group-hover:text-blue-600 dark:text-blue-400 dark:group-hover:text-blue-300">
                  <span>View Workspace</span>
                  <ArrowRight className="w-4 h-4 transform group-hover:translate-x-1 transition-transform" />
                </div>
              </motion.div>
            </Link>
          ))}

          {projects.length === 0 && (
            <div className="col-span-full p-12 text-center rounded-2xl border border-black/10 dark:border-white/10 bg-black/5 dark:bg-white/5 border-dashed">
              <h3 className="text-xl font-medium mb-2 text-black dark:text-white">No projects yet</h3>
              <p className="text-gray-500 dark:text-gray-400">Create your first implementation project to get started.</p>
            </div>
          )}
        </div>
      )}

      {/* New Project Modal */}
      <AnimatePresence>
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <motion.div 
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute inset-0 bg-black/20 dark:bg-black/40 backdrop-blur-sm"
            onClick={() => setShowModal(false)}
          />
          <motion.div 
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.95 }}
            transition={{ type: "spring", bounce: 0, duration: 0.4 }}
            className="bg-white dark:bg-[#1c1c1e] border border-black/10 dark:border-white/10 rounded-2xl p-6 w-full max-w-md shadow-2xl relative z-10"
          >
            <button 
              onClick={() => setShowModal(false)}
              className="absolute top-4 right-4 text-gray-400 hover:text-black dark:hover:text-white transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
            <h2 className="text-xl font-bold mb-6 tracking-tight text-black dark:text-white">Create New Project</h2>
            <form onSubmit={handleCreateProject}>
              <div className="mb-6">
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Project Name
                </label>
                <input
                  type="text"
                  required
                  value={newProjectName}
                  onChange={(e) => setNewProjectName(e.target.value)}
                  placeholder="e.g., Primacy Infotech Odoo Migration"
                  className="w-full px-4 py-3 bg-gray-50 dark:bg-black/40 border border-black/10 dark:border-white/10 rounded-xl focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 text-black dark:text-white transition-all"
                />
              </div>
              
              {error && (
                <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/50 text-red-600 dark:text-red-400 text-sm">
                  {error}
                </div>
              )}

              <div className="flex justify-end space-x-3">
                <button
                  type="button"
                  onClick={() => setShowModal(false)}
                  className="px-4 py-2 rounded-xl font-medium text-gray-600 dark:text-gray-300 hover:bg-black/5 dark:hover:bg-white/5 transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={creating || !newProjectName.trim()}
                  className="px-4 py-2 rounded-xl bg-blue-500 hover:bg-blue-600 text-white font-medium disabled:opacity-50 transition-colors flex items-center shadow-sm"
                >
                  {creating && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                  Create Project
                </button>
              </div>
            </form>
          </motion.div>
        </div>
      )}
      </AnimatePresence>
    </div>
  );
}
