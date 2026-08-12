"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { apiFetch, API_URL, AuditEvent, HostPolicy, Organization, User } from "@/lib/api";

export default function AdministrationPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [hosts, setHosts] = useState<HostPolicy[]>([]);
  const [audits, setAudits] = useState<AuditEvent[]>([]);
  const [error, setError] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [organizationId, setOrganizationId] = useState("");
  const [organizationName, setOrganizationName] = useState("");
  const [hostname, setHostname] = useState("");
  const [resetLink, setResetLink] = useState("");

  const load = useCallback(async () => {
    try {
      const [userRows, organizationRows, hostRows, auditRows] = await Promise.all([
        apiFetch<User[]>("/admin/users"), apiFetch<Organization[]>("/organizations"),
        apiFetch<HostPolicy[]>("/admin/host-policies"), apiFetch<AuditEvent[]>("/admin/audit-events?limit=100"),
      ]);
      setUsers(userRows); setOrganizations(organizationRows); setHosts(hostRows); setAudits(auditRows);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not load administration"); }
  }, []);
  useEffect(() => {
    // State changes occur after the API promises resolve.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  const createUser = async (event: FormEvent) => {
    event.preventDefault(); setError("");
    try {
      await apiFetch("/admin/users", { method: "POST", body: JSON.stringify({ email, password, organization_ids: organizationId ? [Number(organizationId)] : [] }) });
      setEmail(""); setPassword(""); await load();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not create user"); }
  };
  const createOrganization = async (event: FormEvent) => {
    event.preventDefault();
    try { await apiFetch("/organizations", { method: "POST", body: JSON.stringify({ name: organizationName }) }); setOrganizationName(""); await load(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not create organization"); }
  };
  const createHost = async (event: FormEvent) => {
    event.preventDefault();
    try { await apiFetch("/admin/host-policies", { method: "POST", body: JSON.stringify({ hostname_pattern: hostname, require_https: true, allow_private_network: false }) }); setHostname(""); await load(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not approve host"); }
  };
  const deactivate = async (user: User) => { await apiFetch(`/admin/users/${user.id}`, { method: "PATCH", body: JSON.stringify({ is_active: !user.is_active }) }); await load(); };
  const setBudget = async (organization: Organization) => { const entered = window.prompt("Monthly OpenRouter budget in USD", String(organization.monthly_budget_usd || 100)); if (!entered) return; await apiFetch(`/admin/organizations/${organization.id}/budget`, { method: "PUT", body: JSON.stringify({ monthly_budget_usd: Number(entered), budget_warning_percent: 80 }) }); await load(); };
  const resetPassword = async (user: User) => { const result = await apiFetch<{ reset_token: string }>(`/admin/users/${user.id}/reset-password`, { method: "POST" }); setResetLink(`${window.location.origin}/reset-password?token=${encodeURIComponent(result.reset_token)}`); };

  return <main className="space-y-8 p-8">
    <div><h1 className="text-3xl font-bold">Administration</h1><p className="text-gray-500">People, organizations, approved Odoo hosts, and audit history.</p></div>
    {error && <p className="rounded-xl bg-red-50 p-3 text-red-700">{error}</p>}{resetLink && <div className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900"><p className="font-medium">Single-use reset link (shown once)</p><code className="mt-1 block break-all">{resetLink}</code></div>}
    <section className="grid gap-6 lg:grid-cols-2">
      <div className="rounded-2xl border p-5 dark:border-white/10"><h2 className="text-xl font-semibold">Organizations</h2><form onSubmit={createOrganization} className="mt-4 flex gap-2"><input required value={organizationName} onChange={(e) => setOrganizationName(e.target.value)} placeholder="Organization name" className="min-w-0 flex-1 rounded-lg border px-3 py-2 dark:bg-black"/><button className="rounded-lg bg-blue-600 px-4 text-white">Add</button></form><ul className="mt-4 divide-y dark:divide-white/10">{organizations.map((org) => <li className="flex items-center justify-between py-2" key={org.id}><span>{org.name}</span><button onClick={() => void setBudget(org)} className="rounded border px-2 py-1 text-xs">{org.monthly_budget_usd ? `$${org.monthly_budget_usd}/month` : "Set agent budget"}</button></li>)}</ul></div>
      <div className="rounded-2xl border p-5 dark:border-white/10"><h2 className="text-xl font-semibold">Approved Odoo hosts</h2><form onSubmit={createHost} className="mt-4 flex gap-2"><input required value={hostname} onChange={(e) => setHostname(e.target.value)} placeholder="odoo.example.com" className="min-w-0 flex-1 rounded-lg border px-3 py-2 dark:bg-black"/><button className="rounded-lg bg-blue-600 px-4 text-white">Approve</button></form><ul className="mt-4 divide-y dark:divide-white/10">{hosts.map((host) => <li className="flex justify-between py-2" key={host.id}><span>{host.hostname_pattern}</span><span className="text-xs text-gray-500">{host.require_https ? "HTTPS" : "HTTP allowed"}</span></li>)}</ul></div>
    </section>
    <section className="rounded-2xl border p-5 dark:border-white/10"><h2 className="text-xl font-semibold">Users</h2><form onSubmit={createUser} className="mt-4 grid gap-2 md:grid-cols-4"><input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Email" className="rounded-lg border px-3 py-2 dark:bg-black"/><input type="password" minLength={12} required value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Temporary password" className="rounded-lg border px-3 py-2 dark:bg-black"/><select value={organizationId} onChange={(e) => setOrganizationId(e.target.value)} className="rounded-lg border px-3 py-2 dark:bg-black"><option value="">No organization</option>{organizations.map((org) => <option value={org.id} key={org.id}>{org.name}</option>)}</select><button className="rounded-lg bg-blue-600 px-4 text-white">Create user</button></form><div className="mt-4 overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr><th className="py-2">Email</th><th>Role</th><th>Status</th><th></th></tr></thead><tbody>{users.map((user) => <tr className="border-t dark:border-white/10" key={user.id}><td className="py-3">{user.email}</td><td>{user.role}</td><td>{user.is_active ? "Active" : "Inactive"}</td><td className="space-x-2 text-right"><button onClick={() => void resetPassword(user)} className="rounded border px-3 py-1">Reset password</button><button onClick={() => void deactivate(user)} className="rounded border px-3 py-1">{user.is_active ? "Deactivate" : "Activate"}</button></td></tr>)}</tbody></table></div></section>
    <section className="rounded-2xl border p-5 dark:border-white/10"><div className="flex justify-between"><h2 className="text-xl font-semibold">Recent audit activity</h2><a href={`${API_URL}/admin/audit-events.csv`} className="text-sm text-blue-600">Download CSV</a></div><div className="mt-4 overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr><th className="py-2">Time</th><th>Event</th><th>Result</th><th>Support ID</th></tr></thead><tbody>{audits.map((event) => <tr className="border-t dark:border-white/10" key={event.id}><td className="py-3">{new Date(event.created_at).toLocaleString()}</td><td>{event.event_type}</td><td>{event.result || "—"}</td><td className="font-mono text-xs">{event.support_id || "—"}</td></tr>)}</tbody></table></div></section>
  </main>;
}
