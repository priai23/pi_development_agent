"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, AuthState, setCsrfToken } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setLoading(true); setError("");
    try {
      const state = await apiFetch<AuthState>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
      setCsrfToken(state.csrf_token); router.replace("/projects");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Sign in failed"); }
    finally { setLoading(false); }
  };
  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 p-6 dark:bg-black">
      <form onSubmit={submit} className="w-full max-w-sm space-y-5 rounded-2xl border bg-white p-8 shadow-xl dark:border-white/10 dark:bg-gray-950">
        <div><h1 className="text-2xl font-bold">Sign in</h1><p className="mt-1 text-sm text-gray-500">PI ERP Implementation Agent</p></div>
        <input type="email" required autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="Email" className="w-full rounded-xl border px-4 py-3 dark:border-white/10 dark:bg-black" />
        <input type="password" required autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Password" className="w-full rounded-xl border px-4 py-3 dark:border-white/10 dark:bg-black" />
        {error && <p className="text-sm text-red-600">{error}</p>}
        <button disabled={loading} className="w-full rounded-xl bg-blue-600 py-3 font-medium text-white disabled:opacity-50">{loading ? "Signing in…" : "Sign in"}</button>
      </form>
    </div>
  );
}
