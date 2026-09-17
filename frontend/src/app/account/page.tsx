"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { ApiError, apiFetch } from "@/lib/api";

type Session = {
  id: string;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  is_current?: boolean;
};

export default function AccountPage() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [message, setMessage] = useState("");
  const [revokingId, setRevokingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await apiFetch<Session[]>("/auth/sessions");
      setSessions(data);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        return;
      }
      setMessage(caught instanceof Error ? caught.message : "Could not load sessions");
    }
  }, []);

  useEffect(() => {
    // State changes occur after the API promise resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  const changePassword = async (event: FormEvent) => {
    event.preventDefault();
    try {
      await apiFetch("/auth/change-password", {
        method: "POST",
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
      setCurrentPassword("");
      setNewPassword("");
      setMessage("Password changed and other sessions were revoked.");
      await load();
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "Password change failed");
    }
  };

  const revoke = async (id: string) => {
    setRevokingId(id);
    try {
      await apiFetch(`/auth/sessions/${id}`, { method: "DELETE" });
      await load();
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        return;
      }
      setMessage(caught instanceof Error ? caught.message : "Failed to revoke session");
    } finally {
      setRevokingId(null);
    }
  };

  return (
    <main className="space-y-8 p-8">
      <div>
        <h1 className="text-3xl font-bold">Account & sessions</h1>
        <p className="text-gray-500">Change your password and revoke signed-in devices.</p>
      </div>
      {message && <p className="rounded-xl bg-black/5 p-3 text-sm dark:bg-white/10">{message}</p>}
      <form onSubmit={changePassword} className="max-w-xl space-y-4 rounded-2xl border p-5 dark:border-white/10">
        <h2 className="text-xl font-semibold">Change password</h2>
        <input
          type="password"
          required
          value={currentPassword}
          onChange={(e) => setCurrentPassword(e.target.value)}
          placeholder="Current password"
          className="w-full rounded-lg border px-3 py-2 dark:bg-black"
        />
        <input
          type="password"
          required
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
          placeholder="New password"
          className="w-full rounded-lg border px-3 py-2 dark:bg-black"
        />
        <button className="rounded-lg bg-blue-600 px-4 py-2 text-white">Change password</button>
      </form>
      <section className="max-w-3xl rounded-2xl border p-5 dark:border-white/10">
        <h2 className="text-xl font-semibold">Active sessions</h2>
        <ul className="mt-3 divide-y dark:divide-white/10">
          {sessions.map((session) => (
            <li className="flex items-center justify-between py-3" key={session.id}>
              <div>
                <div className="flex items-center gap-2">
                  <p className="text-sm">Last active {new Date(session.last_seen_at).toLocaleString()}</p>
                  {session.is_current && (
                    <span className="rounded-full bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-700 dark:bg-blue-900/50 dark:text-blue-300">
                      This device
                    </span>
                  )}
                </div>
                <p className="text-xs text-gray-500">Expires {new Date(session.expires_at).toLocaleString()}</p>
              </div>
              <button
                disabled={revokingId === session.id}
                onClick={() => void revoke(session.id)}
                className="rounded-lg border px-3 py-1 text-sm hover:bg-black/5 disabled:opacity-40 dark:hover:bg-white/10"
              >
                {revokingId === session.id ? "Revoking…" : session.is_current ? "Revoke (Log out)" : "Revoke"}
              </button>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
