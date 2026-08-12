"use client";

import { FormEvent, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { apiFetch } from "@/lib/api";

export default function ResetPasswordPage() {
  const token = useSearchParams().get("token") || "";
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const submit = async (event: FormEvent) => { event.preventDefault(); try { await apiFetch("/auth/reset-password", { method: "POST", body: JSON.stringify({ token, new_password: password }) }); router.replace("/login"); } catch (caught) { setError(caught instanceof Error ? caught.message : "Reset failed"); } };
  return <main className="flex min-h-screen items-center justify-center p-6"><form onSubmit={submit} className="w-full max-w-md space-y-4 rounded-2xl border p-6 dark:border-white/10"><h1 className="text-2xl font-bold">Set a new password</h1><input type="password" minLength={12} required value={password} onChange={(e) => setPassword(e.target.value)} placeholder="New password (12+ characters)" className="w-full rounded-lg border px-3 py-2 dark:bg-black"/>{error && <p className="text-sm text-red-600">{error}</p>}<button disabled={!token} className="w-full rounded-lg bg-blue-600 px-4 py-2 text-white disabled:opacity-50">Reset password</button></form></main>;
}
