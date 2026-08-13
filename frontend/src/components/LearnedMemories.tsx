"use client";

import { useCallback, useEffect, useState } from "react";
import { Brain, Trash2, Search, Plus, Sparkles, Tag, RefreshCw } from "lucide-react";
import { AgentMemory, apiFetch } from "@/lib/api";

interface LearnedMemoriesProps {
  projectId: number;
}

const CATEGORY_COLORS: Record<string, string> = {
  schema_insight: "bg-purple-500/10 text-purple-400 border-purple-500/20",
  user_preference: "bg-blue-500/10 text-blue-400 border-blue-500/20",
  odoo_gotcha: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  module_pattern: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
};

export default function LearnedMemories({ projectId }: LearnedMemoriesProps) {
  const [memories, setMemories] = useState<AgentMemory[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [selectedCategory, setSelectedCategory] = useState<string>("all");
  const [showAddForm, setShowAddForm] = useState(false);
  const [newKey, setNewKey] = useState("");
  const [newContent, setNewContent] = useState("");
  const [newCategory, setNewCategory] = useState("schema_insight");
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const fetchMemories = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiFetch<AgentMemory[]>(`/projects/${projectId}/memories`);
      setMemories(data);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void fetchMemories();
  }, [fetchMemories]);

  const handleDelete = async (id: number) => {
    setDeletingId(id);
    try {
      await apiFetch(`/memories/${id}`, { method: "DELETE" });
      setMemories((prev) => prev.filter((m) => m.id !== id));
    } catch {
      // ignore
    } finally {
      setDeletingId(null);
    }
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newKey.trim() || !newContent.trim()) return;
    setSaving(true);
    try {
      const created = await apiFetch<AgentMemory>(`/projects/${projectId}/memories`, {
        method: "POST",
        body: JSON.stringify({
          category: newCategory,
          key: newKey.trim(),
          content: newContent.trim(),
          confidence: 1.0,
        }),
      });
      setMemories((prev) => [created, ...prev]);
      setNewKey("");
      setNewContent("");
      setShowAddForm(false);
    } catch {
      // ignore
    } finally {
      setSaving(false);
    }
  };

  const categories = ["all", "schema_insight", "user_preference", "odoo_gotcha", "module_pattern"];

  const filtered = memories.filter((m) => {
    const matchesSearch =
      !search ||
      m.key.toLowerCase().includes(search.toLowerCase()) ||
      m.content.toLowerCase().includes(search.toLowerCase());
    const matchesCat = selectedCategory === "all" || m.category === selectedCategory;
    return matchesSearch && matchesCat;
  });

  return (
    <div className="flex h-full flex-col bg-zinc-900 text-white">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
        <div className="flex items-center gap-2">
          <Brain className="h-4 w-4 text-purple-400" />
          <span className="text-sm font-semibold">Self-Learned Memories ({memories.length})</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => void fetchMemories()}
            className="rounded p-1 text-gray-400 hover:bg-white/10 hover:text-white"
            title="Refresh memories"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          </button>
          <button
            onClick={() => setShowAddForm(!showAddForm)}
            className="flex items-center gap-1.5 rounded bg-purple-600 px-2.5 py-1 text-xs font-medium text-white transition hover:bg-purple-500"
          >
            <Plus className="h-3.5 w-3.5" />
            Add Insight
          </button>
        </div>
      </div>

      {/* Filter & Search Bar */}
      <div className="border-b border-white/5 bg-zinc-950/50 p-3 space-y-2">
        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-gray-500" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search learned insights & preferences…"
            className="w-full rounded-md border border-white/10 bg-zinc-800 py-1.5 pl-8 pr-3 text-xs text-white placeholder:text-gray-500 focus:outline-none focus:ring-1 focus:ring-purple-500"
          />
        </div>
        <div className="flex items-center gap-1.5 overflow-x-auto pb-1 text-[11px]">
          {categories.map((cat) => (
            <button
              key={cat}
              onClick={() => setSelectedCategory(cat)}
              className={`rounded-full px-2.5 py-0.5 font-medium transition ${
                selectedCategory === cat
                  ? "bg-purple-600 text-white"
                  : "bg-zinc-800 text-gray-400 hover:bg-zinc-700 hover:text-gray-200"
              }`}
            >
              {cat.replace("_", " ")}
            </button>
          ))}
        </div>
      </div>

      {/* Manual Creation Form */}
      {showAddForm && (
        <form onSubmit={handleCreate} className="border-b border-white/10 bg-zinc-800/80 p-3 space-y-2">
          <div className="flex items-center justify-between text-xs font-medium text-purple-300">
            <span>Add Custom Agent Insight / Preference</span>
            <button
              type="button"
              onClick={() => setShowAddForm(false)}
              className="text-gray-400 hover:text-white"
            >
              ✕
            </button>
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            <input
              type="text"
              required
              value={newKey}
              onChange={(e) => setNewKey(e.target.value)}
              placeholder="Key (e.g. res.partner:custom_field)"
              className="rounded border border-white/10 bg-zinc-900 px-2.5 py-1 text-xs text-white placeholder:text-gray-500 focus:outline-none focus:ring-1 focus:ring-purple-500"
            />
            <select
              value={newCategory}
              onChange={(e) => setNewCategory(e.target.value)}
              className="rounded border border-white/10 bg-zinc-900 px-2.5 py-1 text-xs text-white focus:outline-none focus:ring-1 focus:ring-purple-500"
            >
              <option value="schema_insight">schema_insight</option>
              <option value="user_preference">user_preference</option>
              <option value="odoo_gotcha">odoo_gotcha</option>
              <option value="module_pattern">module_pattern</option>
            </select>
          </div>
          <textarea
            required
            rows={2}
            value={newContent}
            onChange={(e) => setNewContent(e.target.value)}
            placeholder="Insight or instruction description…"
            className="w-full rounded border border-white/10 bg-zinc-900 p-2 text-xs text-white placeholder:text-gray-500 focus:outline-none focus:ring-1 focus:ring-purple-500"
          />
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setShowAddForm(false)}
              className="rounded px-2.5 py-1 text-xs text-gray-400 hover:text-white"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving}
              className="rounded bg-purple-600 px-3 py-1 text-xs font-medium text-white hover:bg-purple-500 disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save Memory"}
            </button>
          </div>
        </form>
      )}

      {/* Memories List */}
      <div className="flex-1 overflow-y-auto p-3 space-y-2.5">
        {loading && memories.length === 0 ? (
          <div className="p-4 text-center text-xs text-gray-500">Loading agent memories…</div>
        ) : filtered.length === 0 ? (
          <div className="p-8 text-center text-xs text-gray-500">
            {search ? "No memories match search." : "No memories saved yet. The AI automatically records insights as it works!"}
          </div>
        ) : (
          filtered.map((mem) => {
            const badgeStyle = CATEGORY_COLORS[mem.category] || "bg-gray-500/10 text-gray-400 border-gray-500/20";
            return (
              <div
                key={mem.id}
                className="group relative rounded-lg border border-white/10 bg-zinc-800/60 p-3 transition hover:border-purple-500/40 hover:bg-zinc-800"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-[10px] font-medium ${badgeStyle}`}>
                      <Tag className="h-2.5 w-2.5" />
                      {mem.category}
                    </span>
                    <span className="font-mono text-xs font-semibold text-purple-300">{mem.key}</span>
                  </div>
                  <button
                    onClick={() => void handleDelete(mem.id)}
                    disabled={deletingId === mem.id}
                    className="opacity-0 group-hover:opacity-100 rounded p-1 text-gray-400 transition hover:bg-red-500/20 hover:text-red-400 disabled:opacity-50"
                    title="Delete memory"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
                <p className="mt-2 text-xs leading-relaxed text-gray-300 font-mono bg-zinc-950/40 p-2 rounded border border-white/5 whitespace-pre-wrap">
                  {mem.content}
                </p>
                <div className="mt-2 flex items-center justify-between text-[10px] text-gray-500">
                  <span className="flex items-center gap-1">
                    <Sparkles className="h-2.5 w-2.5 text-purple-400" />
                    Used {mem.usage_count} times
                  </span>
                  <span>{new Date(mem.updated_at).toLocaleDateString()}</span>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
