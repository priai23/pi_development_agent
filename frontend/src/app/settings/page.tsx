"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { KeyRound, Save } from "lucide-react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/components/AppShell";
import { apiFetch } from "@/lib/api";

type LLMSettings = { model_name: string; api_key_configured: boolean; fallback_model_name: string | null; timeout_seconds: number; max_output_tokens: number };
type ModelOption = { id: string; name: string; context_length: number | null };

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
  const [maxOutputTokens, setMaxOutputTokens] = useState(8000);
  const [models, setModels] = useState<ModelOption[]>([]);

  const load = useCallback(async () => {
    try {
      const settings = await apiFetch<LLMSettings>("/admin/settings/llm");
      setModelName(settings.model_name); setKeyConfigured(settings.api_key_configured); setFallbackModel(settings.fallback_model_name || ""); setTimeoutSeconds(settings.timeout_seconds); setMaxOutputTokens(settings.max_output_tokens);
      if (settings.api_key_configured) setModels(await apiFetch<ModelOption[]>("/admin/settings/llm/models"));
    } catch (caught) { setMessage(caught instanceof Error ? caught.message : "Could not load settings"); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => {
    if (user && user.role !== "admin") { router.replace("/projects"); return; }
    // State changes occur after the API promise resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (user) void load();
  }, [load, router, user]);

  const save = async (event: FormEvent) => {
    event.preventDefault(); setLoading(true); setMessage("");
    try {
      const settings = await apiFetch<LLMSettings>("/admin/settings/llm", { method: "PUT", body: JSON.stringify({ model_name: modelName, fallback_model_name: fallbackModel || null, timeout_seconds: timeoutSeconds, max_output_tokens: maxOutputTokens, openrouter_api_key: newKey || null }) });
      setKeyConfigured(settings.api_key_configured); setNewKey(""); setMessage("Settings saved.");
      if (settings.api_key_configured) setModels(await apiFetch<ModelOption[]>("/admin/settings/llm/models"));
    } catch (caught) { setMessage(caught instanceof Error ? caught.message : "Could not save settings"); }
    finally { setLoading(false); }
  };

  if (user?.role !== "admin") return null;
  return (
    <div className="mx-auto max-w-2xl p-8">
      <h1 className="text-3xl font-bold">Agent settings</h1><p className="mt-1 text-gray-500">Secrets remain encrypted and are never displayed again.</p>
      <form onSubmit={save} className="mt-8 space-y-6 rounded-2xl border p-6 dark:border-white/10">
        <label className="block text-sm font-medium">Primary model
          {models.length > 0 ? (
            <select required value={modelName} onChange={(event) => setModelName(event.target.value)} className="mt-2 w-full rounded-xl border px-4 py-3 dark:border-white/10 dark:bg-black">
              <option value="" disabled>Select a model</option>
              {models.map((model) => <option value={model.id} key={model.id}>{model.name} ({model.id})</option>)}
            </select>
          ) : (
            <input required value={modelName} onChange={(event) => setModelName(event.target.value)} placeholder="openai/gpt-4o-mini" className="mt-2 w-full rounded-xl border px-4 py-3 dark:border-white/10 dark:bg-black" />
          )}
        </label>
        <label className="block text-sm font-medium">Fallback model
          {models.length > 0 ? (
            <select value={fallbackModel} onChange={(event) => setFallbackModel(event.target.value)} className="mt-2 w-full rounded-xl border px-4 py-3 dark:border-white/10 dark:bg-black">
              <option value="">No fallback model</option>
              {models.map((model) => <option value={model.id} key={model.id}>{model.name} ({model.id})</option>)}
            </select>
          ) : (
            <input value={fallbackModel} onChange={(event) => setFallbackModel(event.target.value)} placeholder="Optional validated fallback" className="mt-2 w-full rounded-xl border px-4 py-3 dark:border-white/10 dark:bg-black" />
          )}
        </label>
        <div className="grid gap-4 sm:grid-cols-2"><label className="block text-sm font-medium">Request timeout (seconds)<input type="number" min={10} max={600} value={timeoutSeconds} onChange={(event) => setTimeoutSeconds(Number(event.target.value))} className="mt-2 w-full rounded-xl border px-4 py-3 dark:border-white/10 dark:bg-black" /></label><label className="block text-sm font-medium">Maximum output tokens<input type="number" min={256} max={100000} value={maxOutputTokens} onChange={(event) => setMaxOutputTokens(Number(event.target.value))} className="mt-2 w-full rounded-xl border px-4 py-3 dark:border-white/10 dark:bg-black" /></label></div>
        <label className="block text-sm font-medium"><span className="flex items-center gap-2"><KeyRound className="h-4 w-4" /> OpenRouter API key</span><input type="password" value={newKey} onChange={(event) => setNewKey(event.target.value)} placeholder={keyConfigured ? "Configured — enter a replacement only" : "Enter API key"} autoComplete="new-password" className="mt-2 w-full rounded-xl border px-4 py-3 dark:border-white/10 dark:bg-black" /><span className="mt-1 block text-xs text-gray-500">{keyConfigured ? "A key is configured." : "No key is configured."}</span></label>
        {message && <p className="text-sm text-gray-600 dark:text-gray-300">{message}</p>}
        <div className="flex gap-3"><button disabled={loading} className="flex items-center gap-2 rounded-xl bg-blue-600 px-5 py-3 font-medium text-white disabled:opacity-50"><Save className="h-4 w-4" /> Save settings</button><button type="button" disabled={!keyConfigured} onClick={async () => { try { const result = await apiFetch<{ models: number }>("/admin/settings/llm/test", { method: "POST" }); setMessage(`OpenRouter connected · ${result.models} models available.`); } catch (caught) { setMessage(caught instanceof Error ? caught.message : "Connection test failed"); } }} className="rounded-xl border px-5 py-3 disabled:opacity-50">Test connection</button></div>
      </form>
    </div>
  );
}
