"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { KeyRound, Save, Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/components/AppShell";
import { apiFetch } from "@/lib/api";

type LLMSettings = {
  model_name: string;
  api_key_configured: boolean;
  fallback_model_name: string | null;
  timeout_seconds: number;
  max_output_tokens: number;
};

type ModelOption = {
  id: string;
  name: string;
  context_length: number | null;
};

export default function SettingsPage() {
  const { user } = useAuth();
  const router = useRouter();
  const [modelName, setModelName] = useState("");
  const [newKey, setNewKey] = useState("");
  const [keyConfigured, setKeyConfigured] = useState(false);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [fallbackModel, setFallbackModel] = useState("");
  const [timeoutSeconds, setTimeoutSeconds] = useState(120);
  const [maxOutputTokens, setMaxOutputTokens] = useState(2048);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [modelSearch, setModelSearch] = useState("");

  const load = useCallback(async () => {
    try {
      const settings = await apiFetch<LLMSettings>("/admin/settings/llm");
      setModelName(settings.model_name);
      setKeyConfigured(settings.api_key_configured);
      setFallbackModel(settings.fallback_model_name || "");
      setTimeoutSeconds(settings.timeout_seconds);
      setMaxOutputTokens(settings.max_output_tokens || 2048);
      if (settings.api_key_configured) {
        const fetchedModels = await apiFetch<ModelOption[]>("/admin/settings/llm/models");
        setModels(fetchedModels);
      }
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "Could not load settings");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (user && user.role !== "admin") {
      router.replace("/projects");
      return;
    }
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (user) void load();
  }, [load, router, user]);

  const filteredModels = useMemo(() => {
    if (!modelSearch.trim()) return models;
    const term = modelSearch.toLowerCase();
    return models.filter(
      (m) => m.id.toLowerCase().includes(term) || m.name.toLowerCase().includes(term)
    );
  }, [models, modelSearch]);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setLoading(true);
    setMessage("");
    try {
      const settings = await apiFetch<LLMSettings>("/admin/settings/llm", {
        method: "PUT",
        body: JSON.stringify({
          model_name: modelName,
          fallback_model_name: fallbackModel || null,
          timeout_seconds: timeoutSeconds,
          max_output_tokens: maxOutputTokens,
          openrouter_api_key: newKey || null,
        }),
      });
      setKeyConfigured(settings.api_key_configured);
      setNewKey("");
      setMessage("Settings saved successfully.");
      if (settings.api_key_configured) {
        const fetchedModels = await apiFetch<ModelOption[]>("/admin/settings/llm/models");
        setModels(fetchedModels);
      }
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "Could not save settings");
    } finally {
      setLoading(false);
    }
  };

  if (user?.role !== "admin") return null;

  return (
    <div className="mx-auto max-w-3xl p-8">
      <h1 className="text-3xl font-bold">Agent Settings</h1>
      <p className="mt-1 text-sm text-gray-500">
        Configure your OpenRouter model catalogue, API keys, and token limits. Secrets remain encrypted.
      </p>

      <form onSubmit={save} className="mt-8 space-y-6 rounded-2xl border p-6 dark:border-white/10">
        {/* OpenRouter API Key */}
        <label className="block text-sm font-medium">
          <span className="flex items-center gap-2 mb-1">
            <KeyRound className="h-4 w-4 text-blue-500" /> OpenRouter API Key
          </span>
          <input
            type="password"
            value={newKey}
            onChange={(event) => setNewKey(event.target.value)}
            placeholder={keyConfigured ? "Configured — enter replacement only" : "sk-or-v1-..."}
            autoComplete="new-password"
            className="w-full rounded-xl border px-4 py-3 text-sm dark:border-white/10 dark:bg-black"
          />
          <span className="mt-1 block text-xs text-gray-500">
            {keyConfigured ? "✓ Active API key configured." : "No API key configured."}
          </span>
        </label>

        {/* Primary Model Selection with Search */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <label className="block text-sm font-medium">Primary Agent Model</label>
            {models.length > 0 && (
              <span className="text-xs text-gray-500">{models.length} models available</span>
            )}
          </div>

          {models.length > 0 && (
            <div className="relative">
              <Search className="absolute left-3 top-3 h-4 w-4 text-gray-400" />
              <input
                type="text"
                placeholder="Filter models (e.g. llama, gemini, claude, free)..."
                value={modelSearch}
                onChange={(e) => setModelSearch(e.target.value)}
                className="w-full rounded-xl border pl-9 pr-4 py-2 text-xs dark:border-white/10 dark:bg-black"
              />
            </div>
          )}

          {models.length > 0 ? (
            <select
              required
              value={modelName}
              onChange={(event) => setModelName(event.target.value)}
              className="w-full rounded-xl border px-4 py-3 text-sm dark:border-white/10 dark:bg-black"
            >
              <option value="" disabled>
                Select a model ({filteredModels.length} shown)
              </option>
              {filteredModels.map((model) => (
                <option value={model.id} key={model.id}>
                  {model.name} ({model.id})
                </option>
              ))}
            </select>
          ) : (
            <input
              required
              value={modelName}
              onChange={(event) => setModelName(event.target.value)}
              placeholder="e.g. meta-llama/llama-3.3-70b-instruct:free or anthropic/claude-3.5-sonnet"
              className="w-full rounded-xl border px-4 py-3 text-sm dark:border-white/10 dark:bg-black"
            />
          )}
          <p className="text-xs text-gray-500">Selected ID: <code className="font-mono text-blue-400">{modelName || "none"}</code></p>
        </div>

        {/* Fallback Model */}
        <div className="space-y-2">
          <label className="block text-sm font-medium">Fallback Model (Optional)</label>
          {models.length > 0 ? (
            <select
              value={fallbackModel}
              onChange={(event) => setFallbackModel(event.target.value)}
              className="w-full rounded-xl border px-4 py-3 text-sm dark:border-white/10 dark:bg-black"
            >
              <option value="">No fallback model</option>
              {filteredModels.map((model) => (
                <option value={model.id} key={model.id}>
                  {model.name} ({model.id})
                </option>
              ))}
            </select>
          ) : (
            <input
              value={fallbackModel}
              onChange={(event) => setFallbackModel(event.target.value)}
              placeholder="Optional validated fallback"
              className="w-full rounded-xl border px-4 py-3 text-sm dark:border-white/10 dark:bg-black"
            />
          )}
        </div>

        {/* Timeout & Tokens */}
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block text-sm font-medium">
            Request Timeout (seconds)
            <input
              type="number"
              min={10}
              max={600}
              value={timeoutSeconds}
              onChange={(event) => setTimeoutSeconds(Number(event.target.value))}
              className="mt-1 w-full rounded-xl border px-4 py-3 text-sm dark:border-white/10 dark:bg-black"
            />
          </label>
          <label className="block text-sm font-medium">
            Maximum Output Tokens
            <input
              type="number"
              min={256}
              max={100000}
              value={maxOutputTokens}
              onChange={(event) => setMaxOutputTokens(Number(event.target.value))}
              className="mt-1 w-full rounded-xl border px-4 py-3 text-sm dark:border-white/10 dark:bg-black"
            />
          </label>
        </div>

        {message && (
          <p className="text-sm font-medium text-blue-600 dark:text-blue-400">{message}</p>
        )}

        <div className="flex gap-3 pt-2">
          <button
            disabled={loading}
            className="flex items-center gap-2 rounded-xl bg-blue-600 px-5 py-3 font-medium text-white transition active:scale-95 disabled:opacity-50"
          >
            <Save className="h-4 w-4" /> Save Settings
          </button>
          <button
            type="button"
            disabled={!keyConfigured}
            onClick={async () => {
              try {
                const result = await apiFetch<{ models: number }>("/admin/settings/llm/test", {
                  method: "POST",
                });
                setMessage(`OpenRouter connected · ${result.models} models available in full catalogue.`);
              } catch (caught) {
                setMessage(caught instanceof Error ? caught.message : "Connection test failed");
              }
            }}
            className="rounded-xl border px-5 py-3 text-sm font-medium hover:bg-white/5 disabled:opacity-50"
          >
            Test Connection
          </button>
        </div>
      </form>
    </div>
  );
}
