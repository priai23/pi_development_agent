"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { API_URL, getSetupStatus, setCsrfToken, setupAdmin } from "@/lib/api";

export default function SetupPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    async function checkStatus() {
      try {
        const status = await getSetupStatus();
        if (!status.needs_setup) {
          router.replace("/login");
        }
      } catch (caught) {
        console.error("Setup check error:", caught);
      } finally {
        setChecking(false);
      }
    }
    void checkStatus();
  }, [router]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError("");

    if (password.length < 12) {
      setError("Password must be at least 12 characters");
      return;
    }
    if (password !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }

    setLoading(true);
    try {
      const state = await setupAdmin(email, password);
      setCsrfToken(state.csrf_token);
      router.replace("/projects");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Setup failed");
    } finally {
      setLoading(false);
    }
  };

  if (checking) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-50 dark:bg-black">
        <p className="text-gray-500">Checking system setup status…</p>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-gray-50 p-6 dark:bg-black">
      <div className="w-full max-w-md space-y-6">
        <div className="text-center">
          <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-blue-600 font-bold text-white shadow-lg shadow-blue-500/30 text-xl">
            PI
          </div>
          <h1 className="text-3xl font-bold tracking-tight">Initial Setup</h1>
          <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
            Create your primary administrator account to configure PI ERP Implementation Agent.
          </p>
        </div>

        <form
          onSubmit={submit}
          className="space-y-4 rounded-2xl border bg-white p-8 shadow-xl dark:border-white/10 dark:bg-gray-950"
        >
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400 mb-1">
              Administrator Email
            </label>
            <input
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="admin@example.com"
              className="w-full rounded-xl border px-4 py-3 dark:border-white/10 dark:bg-black text-sm"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400 mb-1">
              Password (min 12 characters)
            </label>
            <input
              type="password"
              required
              minLength={12}
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••••••"
              className="w-full rounded-xl border px-4 py-3 dark:border-white/10 dark:bg-black text-sm"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400 mb-1">
              Confirm Password
            </label>
            <input
              type="password"
              required
              minLength={12}
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="••••••••••••"
              className="w-full rounded-xl border px-4 py-3 dark:border-white/10 dark:bg-black text-sm"
            />
          </div>

          {error && (
            <div className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900/30 dark:bg-red-950/40 dark:text-red-400">
              {error}
            </div>
          )}

          <button
            disabled={loading}
            type="submit"
            className="w-full rounded-xl bg-blue-600 py-3 font-medium text-white shadow-lg shadow-blue-500/25 hover:bg-blue-700 disabled:opacity-50 transition"
          >
            {loading ? "Initializing Administrator…" : "Create Admin Account & Launch"}
          </button>
        </form>

        <div className="flex items-center justify-between text-xs text-gray-500 px-2">
          <span>Backend API: <code className="font-mono text-gray-700 dark:text-gray-300">{API_URL}</code></span>
          <a
            href={API_URL}
            target="_blank"
            rel="noreferrer"
            className="text-blue-600 hover:underline"
          >
            Open Backend UI &rarr;
          </a>
        </div>
      </div>
    </div>
  );
}
