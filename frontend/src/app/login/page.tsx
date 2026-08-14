"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { API_URL, apiFetch, AuthState, getSetupStatus, setCsrfToken } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [needsSetup, setNeedsSetup] = useState(false);

  useEffect(() => {
    async function checkSetup() {
      try {
        const status = await getSetupStatus();
        if (status.needs_setup) {
          setNeedsSetup(true);
        }
      } catch (err) {
        console.error("Failed to check setup status:", err);
      }
    }
    void checkSetup();
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      const state = await apiFetch<AuthState>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      setCsrfToken(state.csrf_token);
      router.replace("/projects");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Sign in failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-gray-50 p-6 dark:bg-black">
      <div className="w-full max-w-sm space-y-4">
        {needsSetup && (
          <div className="rounded-2xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/40 dark:text-amber-300 shadow-sm">
            <p className="font-semibold">🚀 System Setup Required</p>
            <p className="mt-1 text-xs text-amber-800 dark:text-amber-400">
              No user accounts exist yet. Please create your primary administrator account.
            </p>
            <a
              href="/setup"
              className="mt-3 block text-center rounded-xl bg-amber-600 py-2 text-xs font-semibold text-white shadow hover:bg-amber-700 transition"
            >
              Go to Initial Setup &rarr;
            </a>
          </div>
        )}

        <form
          onSubmit={submit}
          className="space-y-5 rounded-2xl border bg-white p-8 shadow-xl dark:border-white/10 dark:bg-gray-950"
        >
          <div>
            <h1 className="text-2xl font-bold">Sign in</h1>
            <p className="mt-1 text-sm text-gray-500">PI ERP Implementation Agent</p>
          </div>

          <input
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="Email"
            className="w-full rounded-xl border px-4 py-3 dark:border-white/10 dark:bg-black"
          />
          <input
            type="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="Password"
            className="w-full rounded-xl border px-4 py-3 dark:border-white/10 dark:bg-black"
          />

          {error && <p className="text-sm text-red-600">{error}</p>}

          <button
            disabled={loading}
            className="w-full rounded-xl bg-blue-600 py-3 font-medium text-white shadow-lg shadow-blue-500/25 hover:bg-blue-700 disabled:opacity-50 transition"
          >
            {loading ? "Signing in…" : "Sign in"}
          </button>

          <div className="pt-3 border-t dark:border-white/10 flex items-center justify-between text-xs text-gray-500">
            <span>Backend Control Console</span>
            <a
              href={API_URL}
              target="_blank"
              rel="noreferrer"
              className="text-blue-600 font-medium hover:underline"
            >
              Open Backend UI &rarr;
            </a>
          </div>
        </form>
      </div>
    </div>
  );
}

