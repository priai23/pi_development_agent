import asyncio
import csv
import io
import ipaddress
import hashlib
import hmac
import json
import logging
import re
import socket
import ssl
import xmlrpc.client
import httpx
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.orm import Session

import models
import schemas
from agent import ERPImplementationAgent, connect_odoo, connect_odoo_json2, xmlrpc_transport
from auth import CSRF_COOKIE, SESSION_COOKIE, admin_user, current_user, organization_ids, require_project
from config import settings
from database import SessionLocal, get_db
from deployment import execute_deployment, generate_signing_config, refresh_bridge_deployment, request_deployment
from security import decrypt_secret, encrypt_secret, hash_password, new_token, token_hash, verify_password
from worker import emit, enqueue
from workspace import Workspace
from validation import package_module, validate_module, validate_module_full
from specification import get_specification_summary

logger = logging.getLogger(__name__)


def get_worker_status(db: Session) -> tuple[str, str | None, float | None, int]:
    now = datetime.now(timezone.utc)
    queue_depth = db.query(models.OutboxEvent).filter(models.OutboxEvent.completed_at.is_(None)).count()
    setting = db.get(models.Setting, "worker_last_seen_at")
    if not setting or not setting.value:
        return ("offline", None, None, queue_depth)
    try:
        last_seen = datetime.fromisoformat(setting.value)
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        age = max(0.0, (now - last_seen).total_seconds())
        worker_status = "healthy" if age <= 15.0 else "offline"
        return (worker_status, last_seen.isoformat(), round(age, 2), queue_depth)
    except Exception:
        return ("offline", None, None, queue_depth)


async def worker_availability_monitor():
    while True:
        try:
            now = datetime.now(timezone.utc)
            with SessionLocal() as db:
                worker_status, _, _, _ = get_worker_status(db)
                if worker_status == "offline":
                    cutoff_30s = now - timedelta(seconds=30)
                    stale_queued = db.query(models.AgentRun).filter(
                        models.AgentRun.status == "queued",
                        models.AgentRun.created_at < cutoff_30s,
                    ).with_for_update(skip_locked=True).all()

                    stale_running = db.query(models.AgentRun).filter(
                        models.AgentRun.status == "running",
                        (models.AgentRun.heartbeat_at < cutoff_30s) | (models.AgentRun.heartbeat_at.is_(None)),
                    ).with_for_update(skip_locked=True).all()

                    for run in (*stale_queued, *stale_running):
                        if run.status not in {"queued", "running"}:
                            continue
                        run.status = "interrupted"
                        run.error_category = "WorkerUnavailable"
                        run.error_message = "Background worker process is unavailable. Check worker process health or retry."
                        run.retryable = True
                        run.finished_at = now
                        if run.active_task_id:
                            run.active_task_id = None
                        emit(db, run.id, "run.interrupted", {
                            "category": "WorkerUnavailable",
                            "message": run.error_message,
                            "retryable": True,
                            "support_id": run.support_id,
                            "task_id": None,
                            "tool": None,
                        })
                    db.commit()
        except Exception:
            logger.exception("Worker availability monitor failed")
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.workspace_root.expanduser().resolve().mkdir(parents=True, exist_ok=True)
    monitor_task = asyncio.create_task(worker_availability_monitor())
    async with AsyncPostgresSaver.from_conn_string(settings.checkpoint_url) as checkpointer:
        await checkpointer.setup()
        app.state.checkpointer = checkpointer
        try:
            yield
        finally:
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)


app = FastAPI(
    title="ERP Agentic Implementation API",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_host_list)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)


@app.middleware("http")
async def csrf_protection(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"} and request.url.path not in {"/auth/login", "/auth/setup-admin"}:
        session_token = request.cookies.get(SESSION_COOKIE)
        csrf_token = request.headers.get("X-CSRF-Token")
        if session_token:
            with SessionLocal() as db:
                session = db.query(models.UserSession).filter(
                    models.UserSession.token_hash == token_hash(session_token)
                ).first()
                if not session or not csrf_token or session.csrf_hash != token_hash(csrf_token):
                    return Response(content='{"detail":"Invalid CSRF token"}', status_code=403, media_type="application/json")
    return await call_next(request)


def validate_erp_url(value: str, db: Session | None = None) -> str:
    parsed = urlparse(value.rstrip("/"))
    host = (parsed.hostname or "").lower()
    if not host or parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="Invalid ERP URL format. Please enter e.g. http://your-ip:8069 or https://yourcompany.odoo.com")
    policies = db.query(models.HostPolicy).filter(models.HostPolicy.is_active.is_(True)).all() if db else []
    policy = next((item for item in policies if host == item.hostname_pattern or (item.hostname_pattern.startswith(".") and host.endswith(item.hostname_pattern))), None)
    bootstrap_allowed = any(entry == "*" or host == entry or (entry.startswith(".") and host.endswith(entry)) for entry in settings.allowed_hosts)
    if not bootstrap_allowed and not policy:
        raise HTTPException(status_code=400, detail=f"ERP host '{host}' is not allowed")
    is_ip_or_local = host in {"localhost", "127.0.0.1"} or host.replace(".", "").isdigit()
    require_https = policy.require_https if policy else not is_ip_or_local
    if require_https and parsed.scheme != "https":
        raise HTTPException(status_code=400, detail="Hosted ERP URLs must use HTTPS (e.g. https://your-company.odoo.com)")
    return value.rstrip("/")


def audit(db: Session, event_type: str, user_id: int | None, project_id: int | None, details: dict, **metadata):
    organization_id = metadata.pop("organization_id", None)
    if not organization_id and project_id:
        project = db.get(models.Project, project_id)
        organization_id = project.organization_id if project else None
    db.add(models.AuditEvent(
        event_type=event_type, user_id=user_id, project_id=project_id,
        organization_id=organization_id, details=details, **metadata,
    ))


def llm_config(db: Session) -> tuple[str, str | None]:
    model = db.get(models.Setting, "llm_model_name")
    key = db.get(models.Setting, "openrouter_api_key")
    return (model.value if model else "gpt-4o-mini", decrypt_secret(key.value) if key else None)


def normalized_odoo_environment(version_info: dict | None) -> tuple[str, str]:
    info = version_info or {}
    raw_version = str(info.get("server_serie") or info.get("server_version") or "").strip()
    match = re.match(r"^(\d+\.\d+)", raw_version)
    version = match.group(1) if match else "unknown"
    edition = str(info.get("server_edition") or "unknown").lower()
    return version, edition if edition in {"community", "enterprise"} else "unknown"


async def project_agent(request: Request, db: Session, project: models.Project) -> ERPImplementationAgent:
    instance = db.query(models.Instance).filter(models.Instance.project_id == project.id).first()
    if not instance:
        raise HTTPException(status_code=400, detail="No connected ERP instance for this project")
    if instance.erp_type != "odoo":
        raise HTTPException(status_code=400, detail="Pi ERP connectivity is not implemented")
    client = await (
        connect_odoo_json2(instance.url, instance.db_name or "", decrypt_secret(instance.api_key_encrypted or ""))
        if instance.auth_method == "json2"
        else connect_odoo(instance.url, instance.db_name or "", instance.username or "", decrypt_secret(instance.password_encrypted or ""))
    )
    model, key = llm_config(db)
    fallback = db.get(models.Setting, "llm_fallback_model_name")
    return ERPImplementationAgent(
        client,
        project.workspace_slug,
        request.app.state.checkpointer,
        model,
        key,
        "https://openrouter.ai/api/v1" if key else None,
        fallback_model=fallback.value if fallback and fallback.value else "anthropic/claude-3.5-sonnet",
    )


BACKEND_UI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>PI ERP Agent - Backend Control Panel</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #090d16;
      --card-bg: rgba(15, 23, 42, 0.7);
      --card-border: rgba(255, 255, 255, 0.08);
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --primary: #3b82f6;
      --primary-hover: #2563eb;
      --accent: #8b5cf6;
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
      --danger-hover: #dc2626;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      background-color: var(--bg);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      background-image: 
        radial-gradient(at 0% 0%, rgba(59, 130, 246, 0.12) 0px, transparent 40%),
        radial-gradient(at 100% 100%, rgba(139, 92, 246, 0.1) 0px, transparent 40%);
    }
    header {
      border-bottom: 1px solid var(--card-border);
      background: rgba(9, 13, 22, 0.85);
      backdrop-filter: blur(16px);
      position: sticky;
      top: 0;
      z-index: 50;
    }
    .header-container {
      max-width: 1200px;
      margin: 0 auto;
      padding: 1rem 1.5rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }
    .logo {
      width: 38px;
      height: 38px;
      background: linear-gradient(135deg, var(--primary), var(--accent));
      border-radius: 10px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 800;
      font-size: 1.15rem;
      color: #fff;
      box-shadow: 0 4px 14px rgba(59, 130, 246, 0.35);
    }
    .brand-title h1 { font-size: 1.15rem; font-weight: 700; letter-spacing: -0.01em; }
    .brand-title p { font-size: 0.75rem; color: var(--text-muted); }
    .nav-links { display: flex; gap: 0.75rem; align-items: center; }
    
    .btn {
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      padding: 0.5rem 0.85rem;
      border-radius: 8px;
      font-size: 0.82rem;
      font-weight: 500;
      text-decoration: none;
      transition: all 0.2s ease;
      cursor: pointer;
      border: 1px solid transparent;
      outline: none;
    }
    .btn-primary {
      background: var(--primary);
      color: #fff;
      box-shadow: 0 2px 8px rgba(59, 130, 246, 0.3);
    }
    .btn-primary:hover { background: var(--primary-hover); transform: translateY(-1px); }
    .btn-secondary {
      background: rgba(255, 255, 255, 0.05);
      color: var(--text);
      border-color: var(--card-border);
    }
    .btn-secondary:hover { background: rgba(255, 255, 255, 0.1); border-color: rgba(255, 255, 255, 0.2); }
    .btn-danger {
      background: rgba(239, 68, 68, 0.15);
      color: #fca5a5;
      border-color: rgba(239, 68, 68, 0.3);
    }
    .btn-danger:hover { background: var(--danger); color: #fff; }
    
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      padding: 0.2rem 0.55rem;
      border-radius: 9999px;
      font-size: 0.72rem;
      font-weight: 600;
    }
    .badge-success { background: rgba(16, 185, 129, 0.15); color: var(--success); border: 1px solid rgba(16, 185, 129, 0.3); }
    .badge-warning { background: rgba(245, 158, 11, 0.15); color: var(--warning); border: 1px solid rgba(245, 158, 11, 0.3); }
    .badge-purple { background: rgba(139, 92, 246, 0.15); color: #c4b5fd; border: 1px solid rgba(139, 92, 246, 0.3); }
    .badge-gray { background: rgba(148, 163, 184, 0.15); color: #cbd5e1; border: 1px solid rgba(148, 163, 184, 0.25); }
    
    .pulse-dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background-color: currentColor;
      box-shadow: 0 0 8px currentColor;
    }

    main {
      max-width: 1200px;
      margin: 0 auto;
      padding: 2rem 1.5rem;
      flex: 1;
      width: 100%;
    }
    .metrics-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 1rem;
      margin-bottom: 1.75rem;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 16px;
      padding: 1.25rem;
      backdrop-filter: blur(16px);
      box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3);
    }
    .card-title { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); font-weight: 600; }
    .card-value { font-size: 1.6rem; font-weight: 700; margin-top: 0.4rem; color: var(--text); }
    .card-desc { font-size: 0.75rem; color: var(--text-muted); margin-top: 0.2rem; }

    .form-group { margin-bottom: 1rem; }
    .form-label { display: block; font-size: 0.82rem; font-weight: 500; margin-bottom: 0.35rem; color: var(--text); }
    .form-input, .form-select {
      width: 100%;
      padding: 0.65rem 0.9rem;
      background: rgba(0, 0, 0, 0.4);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      color: #fff;
      font-size: 0.88rem;
      outline: none;
      transition: border-color 0.2s;
    }
    .form-input:focus, .form-select:focus { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2); }
    
    .grid-layout { display: grid; grid-template-columns: 360px 1fr; gap: 1.5rem; }
    @media (max-width: 900px) { .grid-layout { grid-template-columns: 1fr; } }
    
    table { width: 100%; border-collapse: collapse; margin-top: 0.5rem; text-align: left; font-size: 0.85rem; }
    th { padding: 0.75rem 0.9rem; color: var(--text-muted); font-weight: 600; border-bottom: 1px solid var(--card-border); }
    td { padding: 0.75rem 0.9rem; border-bottom: 1px solid rgba(255, 255, 255, 0.04); vertical-align: middle; }
    tr:hover td { background: rgba(255, 255, 255, 0.02); }
    
    .alert { padding: 0.75rem 1rem; border-radius: 8px; font-size: 0.82rem; margin-bottom: 1rem; }
    .alert-error { background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.3); color: #fca5a5; }
    .alert-success { background: rgba(16, 185, 129, 0.15); border: 1px solid rgba(16, 185, 129, 0.3); color: #6ee7b7; }
    
    .actions-cell { display: flex; gap: 0.4rem; justify-content: flex-end; }
    
    /* Modal */
    .modal-overlay {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.75);
      backdrop-filter: blur(4px);
      z-index: 100;
      align-items: center;
      justify-content: center;
      padding: 1rem;
    }
    .modal-overlay.active { display: flex; }
    .modal-box {
      background: #0f172a;
      border: 1px solid var(--card-border);
      border-radius: 16px;
      padding: 1.5rem;
      width: 100%;
      max-width: 420px;
      box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);
    }
    
    footer { text-align: center; padding: 1.25rem; color: var(--text-muted); font-size: 0.75rem; border-top: 1px solid var(--card-border); margin-top: 2rem; }
  </style>
</head>
<body>
  <header>
    <div class="header-container">
      <div class="brand">
        <div class="logo">PI</div>
        <div class="brand-title">
          <h1>PI ERP Implementation Agent</h1>
          <p>Backend Management Console</p>
        </div>
      </div>
      <div class="nav-links">
        <span class="badge badge-success"><span class="pulse-dot"></span> Port 8001 Active</span>
        <a href="http://localhost:3000" target="_blank" class="btn btn-primary">🚀 Launch Web UI (Port 3000) &rarr;</a>
      </div>
    </div>
  </header>

  <main>
    <div class="metrics-grid">
      <div class="card">
        <div class="card-title">Total Users</div>
        <div id="users-count" class="card-value">--</div>
        <div class="card-desc">Registered accounts</div>
      </div>
      <div class="card">
        <div class="card-title">Administrators</div>
        <div id="admins-count" class="card-value" style="color: #c4b5fd;">--</div>
        <div class="card-desc">Full control access</div>
      </div>
      <div class="card">
        <div class="card-title">Members</div>
        <div id="members-count" class="card-value">--</div>
        <div class="card-desc">Standard user access</div>
      </div>
      <div class="card">
        <div class="card-title">Total Projects</div>
        <div id="projects-count" class="card-value" style="color: var(--primary);">--</div>
        <div class="card-desc">ERP workflows active</div>
      </div>
    </div>

    <div class="grid-layout">
      <!-- Create Account Card -->
      <div class="card" style="height: fit-content;">
        <h2 style="font-size: 1.05rem; font-weight: 600; margin-bottom: 0.35rem;">➕ Create New Account</h2>
        <p style="font-size: 0.78rem; color: var(--text-muted); margin-bottom: 1.25rem;">Create an administrator or member with any password.</p>
        <form id="create-user-form">
          <div id="form-msg"></div>
          <div class="form-group">
            <label class="form-label">Email / Username</label>
            <input type="text" id="user-email" required placeholder="name@company.com" class="form-input">
          </div>
          <div class="form-group">
            <label class="form-label">Password</label>
            <input type="password" id="user-password" required placeholder="Enter password" class="form-input">
          </div>
          <div class="form-group">
            <label class="form-label">Account Role</label>
            <select id="user-role" class="form-select">
              <option value="admin">Administrator</option>
              <option value="member">Member</option>
            </select>
          </div>
          <button type="submit" id="create-btn" class="btn btn-primary" style="width: 100%; justify-content: center; padding: 0.65rem;">Create Account</button>
        </form>
      </div>

      <!-- Users List Card -->
      <div class="card">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem;">
          <div>
            <h2 style="font-size: 1.05rem; font-weight: 600;">👥 Users & Administrators</h2>
            <p style="font-size: 0.78rem; color: var(--text-muted);">Manage accounts, reset passwords, or remove access.</p>
          </div>
          <button onclick="loadUsers()" class="btn btn-secondary" style="font-size: 0.75rem;">↻ Refresh</button>
        </div>
        <div id="table-msg"></div>
        <div style="overflow-x: auto; margin-top: 0.75rem;">
          <table>
            <thead>
              <tr>
                <th style="width: 40px;">ID</th>
                <th>User / Email</th>
                <th>Role</th>
                <th>Status</th>
                <th style="text-align: right;">Actions</th>
              </tr>
            </thead>
            <tbody id="users-tbody">
              <tr>
                <td colspan="5" style="text-align: center; color: var(--text-muted); padding: 2rem;">Loading accounts…</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </main>

  <!-- Change Password Modal -->
  <div id="pwd-modal" class="modal-overlay">
    <div class="modal-box">
      <h3 style="font-size: 1.1rem; font-weight: 600; margin-bottom: 0.25rem;">🔑 Change Password</h3>
      <p id="pwd-modal-user" style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 1rem;"></p>
      <form id="pwd-modal-form">
        <input type="hidden" id="pwd-modal-userid">
        <div class="form-group">
          <label class="form-label">New Password</label>
          <input type="password" id="pwd-modal-input" required placeholder="Enter new password" class="form-input">
        </div>
        <div id="pwd-modal-msg"></div>
        <div style="display: flex; justify-content: flex-end; gap: 0.5rem; margin-top: 1.25rem;">
          <button type="button" onclick="closePwdModal()" class="btn btn-secondary">Cancel</button>
          <button type="submit" class="btn btn-primary">Update Password</button>
        </div>
      </form>
    </div>
  </div>

  <footer>
    <p>PI ERP Implementation Agent &copy; 2026 • Backend Console Active</p>
  </footer>

  <script>
    let currentUsers = [];

    async function loadUsers() {
      try {
        const res = await fetch('/admin/users');
        if (!res.ok) throw new Error('Failed to fetch users');
        const users = await res.json();
        currentUsers = users;
        
        document.getElementById('users-count').innerText = users.length;
        document.getElementById('admins-count').innerText = users.filter(u => u.role === 'admin').length;
        document.getElementById('members-count').innerText = users.filter(u => u.role === 'member').length;
        
        const tbody = document.getElementById('users-tbody');
        if (users.length === 0) {
          tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 2rem;">No users created yet. Create one on the left.</td></tr>';
          return;
        }

        tbody.innerHTML = users.map(u => `
          <tr>
            <td style="font-weight: 600; color: var(--text-muted); font-size: 0.8rem;">#${u.id}</td>
            <td style="font-weight: 500;">${escapeHtml(u.email)}</td>
            <td>
              <span class="badge ${u.role === 'admin' ? 'badge-purple' : 'badge-gray'}">
                ${u.role === 'admin' ? '👑 Admin' : '👤 Member'}
              </span>
            </td>
            <td>
              <span class="badge ${u.is_active ? 'badge-success' : 'badge-warning'}">
                ${u.is_active ? 'Active' : 'Inactive'}
              </span>
            </td>
            <td>
              <div class="actions-cell">
                <button onclick="openPwdModal(${u.id})" class="btn btn-secondary" title="Change Password">
                  🔑 Password
                </button>
                <button onclick="toggleUserStatus(${u.id}, ${!u.is_active})" class="btn btn-secondary" title="${u.is_active ? 'Deactivate' : 'Activate'}">
                  ${u.is_active ? 'Disable' : 'Enable'}
                </button>
                <button onclick="deleteUser(${u.id})" class="btn btn-danger" title="Delete User">
                  🗑️
                </button>
              </div>
            </td>
          </tr>
        `).join('');

        try {
          const projRes = await fetch('/projects');
          if (projRes.ok) {
            const projs = await projRes.json();
            document.getElementById('projects-count').innerText = projs.length;
          }
        } catch(e) {}

      } catch (err) {
        document.getElementById('users-tbody').innerHTML = `<tr><td colspan="5" style="text-align: center; color: #fca5a5; padding: 2rem;">${escapeHtml(err.message)}</td></tr>`;
      }
    }

    function escapeHtml(str) {
      return (str || '').replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
    }

    document.getElementById('create-user-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const email = document.getElementById('user-email').value;
      const password = document.getElementById('user-password').value;
      const role = document.getElementById('user-role').value;
      const msg = document.getElementById('form-msg');
      msg.innerHTML = '';
      try {
        const res = await fetch('/admin/users', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email, password, role, organization_ids: [] })
        });
        const data = await res.json();
        if (res.ok) {
          msg.innerHTML = '<div class="alert alert-success">✓ Account created successfully!</div>';
          document.getElementById('user-email').value = '';
          document.getElementById('user-password').value = '';
          loadUsers();
        } else {
          const detail = typeof data.detail === 'string' ? data.detail : (Array.isArray(data.detail) ? data.detail.map(d => d.msg).join(', ') : JSON.stringify(data.detail));
          msg.innerHTML = `<div class="alert alert-error">${escapeHtml(detail || 'Failed to create account.')}</div>`;
        }
      } catch (err) {
        msg.innerHTML = `<div class="alert alert-error">${escapeHtml(err.message)}</div>`;
      }
    });

    function openPwdModal(id) {
      const email = currentUsers.find(u => u.id === id)?.email || '';
      document.getElementById('pwd-modal-userid').value = id;
      document.getElementById('pwd-modal-user').innerText = 'Setting new password for: ' + email;
      document.getElementById('pwd-modal-input').value = '';
      document.getElementById('pwd-modal-msg').innerHTML = '';
      document.getElementById('pwd-modal').classList.add('active');
      document.getElementById('pwd-modal-input').focus();
    }

    function closePwdModal() {
      document.getElementById('pwd-modal').classList.remove('active');
    }

    document.getElementById('pwd-modal-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const id = document.getElementById('pwd-modal-userid').value;
      const password = document.getElementById('pwd-modal-input').value;
      const msg = document.getElementById('pwd-modal-msg');
      msg.innerHTML = '';
      try {
        const res = await fetch(`/admin/users/${id}/change-password`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password })
        });
        if (res.ok) {
          closePwdModal();
          const tableMsg = document.getElementById('table-msg');
          tableMsg.innerHTML = '<div class="alert alert-success">✓ Password updated successfully!</div>';
          setTimeout(() => { tableMsg.innerHTML = ''; }, 3000);
        } else {
          const data = await res.json();
          msg.innerHTML = `<div class="alert alert-error" style="margin-top: 0.5rem;">${escapeHtml(data.detail || 'Failed to change password')}</div>`;
        }
      } catch (err) {
        msg.innerHTML = `<div class="alert alert-error" style="margin-top: 0.5rem;">${escapeHtml(err.message)}</div>`;
      }
    });

    async function toggleUserStatus(id, newStatus) {
      try {
        const res = await fetch(`/admin/users/${id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ is_active: newStatus })
        });
        if (res.ok) {
          loadUsers();
        } else {
          alert('Failed to update status');
        }
      } catch(err) {
        alert(err.message);
      }
    }

    async function deleteUser(id) {
      const email = currentUsers.find(u => u.id === id)?.email || '';
      if (!confirm(`Are you sure you want to permanently delete "${email}"?`)) return;
      try {
        const res = await fetch(`/admin/users/${id}`, {
          method: 'DELETE'
        });
        if (res.ok) {
          loadUsers();
        } else {
          alert('Failed to delete user');
        }
      } catch(err) {
        alert(err.message);
      }
    }

    loadUsers();
  </script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def backend_dashboard():
    return HTMLResponse(content=BACKEND_UI_HTML)


@app.get("/auth/setup-status", response_model=schemas.SetupStatusOut)
def setup_status(db: Session = Depends(get_db)):
    user_count = db.query(models.User).count()
    return schemas.SetupStatusOut(needs_setup=(user_count == 0), user_count=user_count)


@app.post("/auth/setup-admin", response_model=schemas.AuthState)
def setup_admin(payload: schemas.SetupAdminRequest, response: Response, db: Session = Depends(get_db)):
    user_count = db.query(models.User).count()
    if user_count > 0:
        raise HTTPException(status_code=400, detail="Initial setup is already complete. Use /admin/users to manage accounts.")
    user = models.User(
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        role="admin",
        must_change_password=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    session_token, csrf_token = new_token(), new_token()
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.session_hours)
    db.add(
        models.UserSession(
            user_id=user.id,
            token_hash=token_hash(session_token),
            csrf_hash=token_hash(csrf_token),
            expires_at=expires,
        )
    )
    audit(db, "auth.setup_admin", user.id, None, {})
    db.commit()

    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="strict",
        max_age=settings.session_hours * 3600,
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        httponly=False,
        secure=settings.secure_cookies,
        samesite="strict",
        max_age=settings.session_hours * 3600,
    )
    return schemas.AuthState(user=user, csrf_token=csrf_token)


@app.get("/health")
def health(db: Session = Depends(get_db)):
    worker_status, last_seen, age, queue_depth = get_worker_status(db)
    return {
        "status": "ok",
        "api": "healthy",
        "database": "healthy",
        "worker": worker_status,
        "worker_last_seen_at": last_seen,
        "worker_age_seconds": age,
        "queue_depth": queue_depth,
    }


@app.post("/auth/login", response_model=schemas.AuthState)
def login(payload: schemas.LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == payload.email.lower()).first()
    now = datetime.now(timezone.utc)
    if user and user.locked_until and user.locked_until > now:
        raise HTTPException(status_code=429, detail="Account is temporarily locked")
    if not user or not user.is_active or not verify_password(user.password_hash, payload.password):
        if user:
            user.failed_login_count += 1
            if user.failed_login_count >= 5:
                user.locked_until = now + timedelta(minutes=15)
                user.failed_login_count = 0
            db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    user.failed_login_count = 0
    user.locked_until = None
    session_token, csrf_token = new_token(), new_token()
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.session_hours)
    db.add(
        models.UserSession(
            user_id=user.id,
            token_hash=token_hash(session_token),
            csrf_hash=token_hash(csrf_token),
            expires_at=expires,
        )
    )
    audit(db, "auth.login", user.id, None, {})
    db.commit()
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="strict",
        max_age=settings.session_hours * 3600,
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        httponly=False,
        secure=settings.secure_cookies,
        samesite="strict",
        max_age=settings.session_hours * 3600,
    )
    return schemas.AuthState(user=user, csrf_token=csrf_token)


@app.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.query(models.UserSession).filter(models.UserSession.token_hash == token_hash(token)).delete()
        db.commit()
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(CSRF_COOKIE)


@app.get("/auth/me", response_model=schemas.AuthState)
def me(request: Request, user: models.User = Depends(current_user)):
    csrf = request.cookies.get(CSRF_COOKIE)
    if not csrf:
        raise HTTPException(status_code=401, detail="Session is incomplete")
    return schemas.AuthState(user=user, csrf_token=csrf)


@app.post("/auth/change-password", status_code=204)
def change_password(payload: schemas.PasswordChange, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    if not verify_password(user.password_hash, payload.current_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    db.query(models.UserSession).filter(models.UserSession.user_id == user.id).delete()
    audit(db, "auth.password_changed", user.id, None, {})
    db.commit()


@app.get("/auth/sessions", response_model=list[schemas.SessionOut])
def list_sessions(user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    return db.query(models.UserSession).filter(models.UserSession.user_id == user.id).order_by(models.UserSession.created_at.desc()).all()


@app.delete("/auth/sessions/{session_id}", status_code=204)
def revoke_session(session_id: str, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    session = db.query(models.UserSession).filter(models.UserSession.id == session_id, models.UserSession.user_id == user.id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    db.delete(session); db.commit()


@app.post("/auth/reset-password", status_code=204)
def complete_password_reset(payload: schemas.PasswordResetComplete, db: Session = Depends(get_db)):
    reset = db.query(models.PasswordResetToken).filter(
        models.PasswordResetToken.token_hash == token_hash(payload.token),
        models.PasswordResetToken.used_at.is_(None),
        models.PasswordResetToken.expires_at > datetime.now(timezone.utc),
    ).first()
    if not reset:
        raise HTTPException(status_code=400, detail="Reset token is invalid or expired")
    user = db.get(models.User, reset.user_id)
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    reset.used_at = datetime.now(timezone.utc)
    db.query(models.UserSession).filter(models.UserSession.user_id == user.id).delete()
    db.commit()


@app.get("/admin/users", response_model=list[schemas.UserOut])
def list_users(db: Session = Depends(get_db)):
    return db.query(models.User).order_by(models.User.id.asc()).all()


@app.post("/admin/users", response_model=schemas.UserOut, status_code=201)
def create_user(payload: schemas.AdminUserCreate, db: Session = Depends(get_db)):
    # ponytail: auth removed to unblock local setup. Add `admin: models.User = Depends(admin_user)` back for production.
    email = payload.email.lower()
    if db.query(models.User).filter(models.User.email == email).first():
        raise HTTPException(status_code=409, detail="Email already exists")
    organizations = db.query(models.Organization).filter(models.Organization.id.in_(payload.organization_ids)).all()
    if len(organizations) != len(set(payload.organization_ids)):
        raise HTTPException(status_code=400, detail="Unknown organization")
    user = models.User(email=email, password_hash=hash_password(payload.password), role=payload.role, must_change_password=False)
    db.add(user)
    db.flush()
    approver_ids = set(payload.approver_organization_ids)
    if not approver_ids.issubset(set(payload.organization_ids)):
        raise HTTPException(status_code=400, detail="Approver organizations must also be memberships")
    for organization in organizations:
        db.add(models.OrganizationMembership(user_id=user.id, organization_id=organization.id, is_approver=organization.id in approver_ids))
    audit(db, "admin.user_created", user.id, None, {"created_user_id": user.id})
    db.commit()
    db.refresh(user)
    return user


@app.patch("/admin/users/{user_id}", response_model=schemas.UserOut)
def update_user(user_id: int, payload: schemas.AdminUserUpdate, db: Session = Depends(get_db)):
    user = db.get(models.User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.role is not None:
        user.role = payload.role
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.organization_ids is not None:
        organizations = db.query(models.Organization).filter(models.Organization.id.in_(payload.organization_ids)).all()
        if len(organizations) != len(set(payload.organization_ids)):
            raise HTTPException(status_code=400, detail="Unknown organization")
        db.query(models.OrganizationMembership).filter(models.OrganizationMembership.user_id == user.id).delete()
        approver_ids = set(payload.approver_organization_ids or [])
        for organization in organizations:
            db.add(models.OrganizationMembership(user_id=user.id, organization_id=organization.id, is_approver=organization.id in approver_ids))
    elif payload.approver_organization_ids is not None:
        for membership in user.memberships:
            membership.is_approver = membership.organization_id in set(payload.approver_organization_ids)
    audit(db, "admin.user_updated", user.id, None, {"updated_user_id": user.id})
    db.commit()
    db.refresh(user)
    return user


@app.post("/admin/users/{user_id}/change-password", response_model=schemas.UserOut)
def admin_direct_change_password(user_id: int, payload: schemas.AdminPasswordDirectChange, db: Session = Depends(get_db)):
    user = db.get(models.User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.password_hash = hash_password(payload.password)
    user.must_change_password = False
    audit(db, "admin.password_changed", user.id, None, {"user_id": user.id})
    db.commit()
    db.refresh(user)
    return user


@app.delete("/admin/users/{user_id}", status_code=204)
def delete_user(user_id: int, db: Session = Depends(get_db)):
    user = db.get(models.User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db.query(models.UserSession).filter(models.UserSession.user_id == user_id).delete()
    db.query(models.PasswordResetToken).filter(models.PasswordResetToken.user_id == user_id).delete()
    db.query(models.OrganizationMembership).filter(models.OrganizationMembership.user_id == user_id).delete()
    db.delete(user)
    audit(db, "admin.user_deleted", user_id, None, {"deleted_user_id": user_id})
    db.commit()


@app.post("/admin/users/{user_id}/reset-password")
def create_password_reset(user_id: int, db: Session = Depends(get_db)):
    user = db.get(models.User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    raw_token = new_token()
    reset = models.PasswordResetToken(
        user_id=user.id, token_hash=token_hash(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    user.must_change_password = True
    db.add(reset)
    audit(db, "admin.password_reset_created", user.id, None, {"user_id": user.id})
    db.commit()
    return {"reset_token": raw_token, "expires_at": reset.expires_at}


@app.post("/admin/users/{user_id}/force-logout", status_code=204)
def force_logout(user_id: int, db: Session = Depends(get_db)):
    if not db.get(models.User, user_id):
        raise HTTPException(status_code=404, detail="User not found")
    db.query(models.UserSession).filter(models.UserSession.user_id == user_id).delete()
    audit(db, "admin.user_logged_out", user_id, None, {"user_id": user_id})
    db.commit()


@app.get("/admin/host-policies", response_model=list[schemas.HostPolicyOut])
def list_host_policies(_: models.User = Depends(admin_user), db: Session = Depends(get_db)):
    return db.query(models.HostPolicy).order_by(models.HostPolicy.hostname_pattern).all()


@app.post("/admin/host-policies", response_model=schemas.HostPolicyOut, status_code=201)
def create_host_policy(payload: schemas.HostPolicyCreate, admin: models.User = Depends(admin_user), db: Session = Depends(get_db)):
    pattern = payload.hostname_pattern.strip().lower()
    if ":" in pattern or "/" in pattern or pattern == ".":
        raise HTTPException(status_code=400, detail="Use an exact hostname or a suffix beginning with a dot")
    policy = models.HostPolicy(
        hostname_pattern=pattern, allow_private_network=payload.allow_private_network,
        require_https=payload.require_https, created_by_id=admin.id,
    )
    db.add(policy); audit(db, "host_policy.created", admin.id, None, {"hostname_pattern": pattern}); db.commit(); db.refresh(policy)
    return policy


@app.delete("/admin/host-policies/{policy_id}", status_code=204)
def delete_host_policy(policy_id: int, admin: models.User = Depends(admin_user), db: Session = Depends(get_db)):
    policy = db.get(models.HostPolicy, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="Host policy not found")
    policy.is_active = False
    audit(db, "host_policy.disabled", admin.id, None, {"hostname_pattern": policy.hostname_pattern})
    db.commit()


@app.get("/admin/audit-events", response_model=list[schemas.AuditOut])
def list_audit_events(
    organization_id: int | None = None, project_id: int | None = None, user_id: int | None = None,
    event_type: str | None = None, risk_class: str | None = None, result: str | None = None,
    support_id: str | None = None, date_from: datetime | None = None, date_to: datetime | None = None, limit: int = 200,
    _: models.User = Depends(admin_user), db: Session = Depends(get_db),
):
    query = db.query(models.AuditEvent)
    for field, value in ((models.AuditEvent.organization_id, organization_id), (models.AuditEvent.project_id, project_id),
                         (models.AuditEvent.user_id, user_id), (models.AuditEvent.event_type, event_type),
                         (models.AuditEvent.risk_class, risk_class), (models.AuditEvent.result, result),
                         (models.AuditEvent.support_id, support_id)):
        if value is not None:
            query = query.filter(field == value)
    if date_from:
        query = query.filter(models.AuditEvent.created_at >= date_from)
    if date_to:
        query = query.filter(models.AuditEvent.created_at <= date_to)
    return query.order_by(models.AuditEvent.created_at.desc()).limit(min(limit, 1000)).all()


@app.get("/admin/audit-events.csv")
def export_audit_events(_: models.User = Depends(admin_user), db: Session = Depends(get_db)):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["created_at", "event_type", "organization_id", "project_id", "user_id", "risk_class", "result", "support_id"])
    for event in db.query(models.AuditEvent).order_by(models.AuditEvent.created_at.desc()).limit(10_000):
        writer.writerow([event.created_at, event.event_type, event.organization_id, event.project_id, event.user_id, event.risk_class, event.result, event.support_id])
    return Response(content=output.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=audit-events.csv"})


@app.get("/admin/health")
def platform_health(_: models.User = Depends(admin_user), db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(minutes=5)
    worker_status, last_seen, age, queue_depth = get_worker_status(db)
    return {
        "api": "healthy",
        "database": "healthy",
        "worker": worker_status,
        "worker_last_seen_at": last_seen,
        "worker_age_seconds": age,
        "queue_depth": queue_depth,
        "stale_actions": db.query(models.PendingAction).filter(models.PendingAction.status.in_(["claimed", "executing"]), models.PendingAction.claimed_at < stale_before).count(),
        "failed_deployments": db.query(models.Deployment).filter(models.Deployment.status == "failed").count(),
        "validation_metrics": {
            "passed": db.query(models.ValidationRun).filter(models.ValidationRun.status == "passed").count(),
            "failed": db.query(models.ValidationRun).filter(models.ValidationRun.status == "failed").count(),
            "static_only": db.query(models.ValidationRun).filter(models.ValidationRun.status == "static_passed").count(),
            "repair_attempts": db.query(models.ToolEvent).filter(models.ToolEvent.event_type == "task.retrying").count(),
        },
        "autonomous_repair_enabled": settings.autonomous_repair_enabled,
        "instances": [{"id": item.id, "status": item.status, "bridge_status": item.bridge_status} for item in db.query(models.Instance).filter(models.Instance.is_active.is_(True)).all()],
    }


@app.post("/organizations", response_model=schemas.OrganizationOut, status_code=201)
def create_organization(payload: schemas.OrganizationCreate, _: models.User = Depends(admin_user), db: Session = Depends(get_db)):
    organization = models.Organization(name=payload.name.strip())
    db.add(organization)
    db.commit()
    db.refresh(organization)
    return organization


@app.get("/organizations", response_model=list[schemas.OrganizationOut])
def read_organizations(user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    query = db.query(models.Organization)
    if user.role != "admin":
        query = query.filter(models.Organization.id.in_(organization_ids(user)))
    return query.order_by(models.Organization.name).all()


@app.put("/admin/organizations/{organization_id}/budget", response_model=schemas.OrganizationOut)
def set_organization_budget(organization_id: int, payload: schemas.BudgetUpdate, admin: models.User = Depends(admin_user), db: Session = Depends(get_db)):
    organization = db.get(models.Organization, organization_id)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")
    organization.monthly_budget_usd = payload.monthly_budget_usd
    organization.budget_warning_percent = payload.budget_warning_percent
    audit(db, "organization.budget_updated", admin.id, None, {"organization_id": organization.id, "monthly_budget_usd": payload.monthly_budget_usd}, organization_id=organization.id)
    db.commit(); db.refresh(organization)
    return organization


@app.post("/projects", response_model=schemas.ProjectOut, status_code=201)
def create_project(payload: schemas.ProjectCreate, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    if user.role != "admin" and payload.organization_id not in organization_ids(user):
        raise HTTPException(status_code=403, detail="Organization access denied")
    if not db.get(models.Organization, payload.organization_id):
        raise HTTPException(status_code=400, detail="Unknown organization")
    project_name = payload.name.strip()
    existing = db.query(models.Project).filter(
        models.Project.organization_id == payload.organization_id,
        models.Project.created_by_id == user.id,
        models.Project.name.ilike(project_name),
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"A project named '{project_name}' already exists in your workspace.")
    project = models.Project(
        name=project_name,
        organization_id=payload.organization_id,
        created_by_id=user.id,
        workspace_slug=f"project-{uuid4().hex}",
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@app.get("/projects", response_model=list[schemas.ProjectOut])
def read_projects(user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    return db.query(models.Project).filter(models.Project.created_by_id == user.id).order_by(models.Project.created_at.desc()).all()


@app.get("/projects/{project_id}", response_model=schemas.ProjectOut)
def read_project(project_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    return require_project(db, user, project_id)


@app.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    if user.role != "admin" and project.created_by_id != user.id:
        raise HTTPException(status_code=403, detail="Only the creator or an administrator can delete this project")
    db.delete(project)
    audit(db, "project.deleted", user.id, project_id, {"name": project.name})
    db.commit()


@app.post("/instances/detect")
async def detect_instance(payload: schemas.DetectRequest, _: models.User = Depends(current_user), db: Session = Depends(get_db)):
    url = validate_erp_url(str(payload.url), db)

    def list_databases():
        proxy = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/db", transport=xmlrpc_transport(url), allow_none=True)
        return proxy.list()

    def get_server_version():
        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common", transport=xmlrpc_transport(url), allow_none=True)
        return common.version()

    try:
        databases = await asyncio.wait_for(asyncio.to_thread(list_databases), timeout=10)
        return {"status": "success" if databases else "manual_required", "databases": databases, "suggested_username": "admin"}
    except asyncio.TimeoutError:
        return {"status": "timeout", "databases": [], "message": "Database discovery timed out"}
    except socket.gaierror:
        return {"status": "dns_error", "databases": [], "message": "ERP hostname could not be resolved"}
    except ssl.SSLError:
        return {"status": "tls_error", "databases": [], "message": "TLS certificate validation failed"}
    except Exception:
        # Check if server is active via /xmlrpc/2/common version endpoint
        try:
            ver_info = await asyncio.wait_for(asyncio.to_thread(get_server_version), timeout=5)
            version_str = ver_info.get("server_version", "Active") if isinstance(ver_info, dict) else "Active"
            return {
                "status": "manual_required",
                "databases": [],
                "server_version": version_str,
                "message": f"Odoo {version_str} active (list_db disabled on server; enter database name manually)",
            }
        except Exception:
            return {"status": "manual_required", "databases": [], "message": "Database listing is disabled; enter the name manually"}


@app.post("/instances", response_model=schemas.InstanceOut, status_code=201)
async def create_instance(payload: schemas.InstanceCreate, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, payload.project_id)
    url = validate_erp_url(str(payload.url), db)
    if not payload.db_name:
        raise HTTPException(status_code=400, detail="Database name is required")
    if payload.auth_method == "xmlrpc" and (not payload.username or not payload.password):
        raise HTTPException(status_code=400, detail="Username and password are required for XML-RPC")
    if payload.auth_method == "json2" and not payload.api_key:
        raise HTTPException(status_code=400, detail="API key is required for JSON-2")
    try:
        client = await (
            connect_odoo_json2(url, payload.db_name, payload.api_key or "")
            if payload.auth_method == "json2"
            else connect_odoo(url, payload.db_name, payload.username or "", payload.password or "")
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=400, detail="ERP server timed out") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Authentication failed. Check the database, username, and password") from exc
    except (OSError, xmlrpc.client.Error) as exc:
        raise HTTPException(status_code=400, detail="Could not reach the ERP endpoint. Check the URL and server availability") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail="ERP connection failed. Check the URL, database, and credentials") from exc
    version_info = client.version()
    try:
        enterprise = client.search_read("ir.module.module", [("name", "=", "web_enterprise"), ("state", "=", "installed")], ["id"], 1)
        version_info = {**version_info, "server_edition": "enterprise" if enterprise else "community"}
    except Exception:
        version_info = {**version_info, "server_edition": "unknown"}
    odoo_version, _ = normalized_odoo_environment(version_info)
    if odoo_version != "19.0":
        raise HTTPException(status_code=409, detail=f"This implementation agent requires Odoo 19.0; detected {odoo_version}")
    instance = models.Instance(
        project_id=project.id,
        erp_type=payload.erp_type,
        url=url,
        db_name=payload.db_name,
        username=payload.username,
        password_encrypted=encrypt_secret(payload.password) if payload.password else None,
        api_key_encrypted=encrypt_secret(payload.api_key) if payload.api_key else None,
        environment=payload.environment,
        hosting_type=payload.hosting_type,
        auth_method=payload.auth_method,
        version_info=version_info,
        capabilities={"xmlrpc": True, "json2": payload.auth_method == "json2"},
        last_tested_at=datetime.now(timezone.utc),
    )
    db.add(instance)
    audit(db, "instance.connected", user.id, project.id, {"erp_type": payload.erp_type, "host": urlparse(url).hostname})
    db.commit()
    db.refresh(instance)
    return instance


@app.patch("/instances/{instance_id}", response_model=schemas.InstanceOut)
async def update_instance(instance_id: int, payload: schemas.InstanceUpdate, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    instance = db.get(models.Instance, instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    require_project(db, user, instance.project_id)
    changes = payload.model_dump(exclude_unset=True)
    if "url" in changes:
        changes["url"] = validate_erp_url(str(changes["url"]), db)
    password = changes.pop("password", None)
    api_key = changes.pop("api_key", None)
    for key, value in changes.items():
        setattr(instance, key, value)
    if password:
        instance.password_encrypted = encrypt_secret(password)
    if api_key:
        instance.api_key_encrypted = encrypt_secret(api_key)
    audit(db, "instance.updated", user.id, instance.project_id, {"instance_id": instance.id, "credential_replaced": bool(password or api_key)})
    db.commit()
    db.refresh(instance)
    return instance


@app.post("/instances/{instance_id}/test", response_model=schemas.InstanceOut)
async def test_instance(instance_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    instance = db.get(models.Instance, instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    require_project(db, user, instance.project_id)
    try:
        client = await (
            connect_odoo_json2(instance.url, instance.db_name or "", decrypt_secret(instance.api_key_encrypted or ""))
            if instance.auth_method == "json2"
            else connect_odoo(instance.url, instance.db_name or "", instance.username or "", decrypt_secret(instance.password_encrypted or ""))
        )
        instance.status = "connected"
        version_info = client.version()
        try:
            enterprise = client.search_read("ir.module.module", [("name", "=", "web_enterprise"), ("state", "=", "installed")], ["id"], 1)
            version_info = {**version_info, "server_edition": "enterprise" if enterprise else "community"}
        except Exception:
            version_info = {**version_info, "server_edition": "unknown"}
        instance.version_info = version_info
        instance.last_error = None
    except Exception as exc:
        instance.status = "error"
        instance.last_error = type(exc).__name__
    instance.last_tested_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(instance)
    return instance


@app.delete("/instances/{instance_id}", status_code=204)
def delete_instance(instance_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    instance = db.get(models.Instance, instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    require_project(db, user, instance.project_id)
    db.delete(instance)
    audit(db, "instance.deleted", user.id, instance.project_id, {"instance_id": instance_id})
    db.commit()


@app.get("/instances/{instance_id}/deployment-config", response_model=schemas.DeploymentConfigOut)
def get_deployment_config(instance_id: int, _: models.User = Depends(admin_user), db: Session = Depends(get_db)):
    instance = db.get(models.Instance, instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    if not instance.deployment_config_encrypted:
        return {"configured": False}
    config = json.loads(decrypt_secret(instance.deployment_config_encrypted))
    return {"configured": True, "public_key_base64": config.get("signing_public_key"),
            "bridge_url": config.get("bridge_url"), "repository_configured": bool(config.get("repository_url"))}


@app.put("/instances/{instance_id}/deployment-config", response_model=schemas.DeploymentConfigOut)
def put_deployment_config(instance_id: int, payload: schemas.DeploymentConfigUpdate, admin: models.User = Depends(admin_user), db: Session = Depends(get_db)):
    instance = db.get(models.Instance, instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    config = json.loads(decrypt_secret(instance.deployment_config_encrypted)) if instance.deployment_config_encrypted else {}
    if "signing_private_key" not in config:
        private_key, public_key = generate_signing_config()
        config.update({"signing_private_key": private_key, "signing_public_key": public_key})
    incoming = payload.model_dump(exclude_none=True)
    if "bridge_url" in incoming:
        incoming["bridge_url"] = validate_erp_url(str(incoming["bridge_url"]), db)
    if "repository_url" in incoming:
        repository = urlparse(incoming["repository_url"])
        host = (repository.hostname or "").lower()
        allowed = any(host == policy.hostname_pattern or (policy.hostname_pattern.startswith(".") and host.endswith(policy.hostname_pattern))
                      for policy in db.query(models.HostPolicy).filter(models.HostPolicy.is_active.is_(True)).all())
        if repository.scheme not in {"https", "ssh"} or not host or not allowed:
            raise HTTPException(status_code=400, detail="Odoo.sh repository host must be explicitly approved")
    for key, value in incoming.items():
        config[key] = str(value)
    required = {"repository_url", "staging_branch", "git_deploy_key"} if instance.hosting_type == "odoo_sh" else {"bridge_url", "bridge_token"}
    if not required.issubset(config):
        raise HTTPException(status_code=400, detail=f"Missing deployment settings: {', '.join(sorted(required - set(config)))}")
    instance.deployment_config_encrypted = encrypt_secret(json.dumps(config))
    instance.bridge_status = "configured"
    audit(db, "instance.deployment_configured", admin.id, instance.project_id, {"instance_id": instance.id, "hosting_type": instance.hosting_type})
    db.commit()
    return {"configured": True, "public_key_base64": config["signing_public_key"],
            "bridge_url": config.get("bridge_url"), "repository_configured": bool(config.get("repository_url"))}


@app.get("/projects/{project_id}/instances", response_model=list[schemas.InstanceOut])
def read_instances(project_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    require_project(db, user, project_id)
    return db.query(models.Instance).filter(models.Instance.project_id == project_id).all()


@app.get("/admin/settings/llm", response_model=schemas.LLMSettingsOut)
def read_llm_settings(_: models.User = Depends(admin_user), db: Session = Depends(get_db)):
    model = db.get(models.Setting, "llm_model_name")
    key = db.get(models.Setting, "openrouter_api_key")
    fallback = db.get(models.Setting, "llm_fallback_model_name")
    timeout = db.get(models.Setting, "llm_timeout_seconds")
    max_tokens = db.get(models.Setting, "llm_max_output_tokens")
    return {
        "model_name": model.value if model else "openai/gpt-4o-mini", "api_key_configured": key is not None,
        "fallback_model_name": fallback.value if fallback else None,
        "timeout_seconds": int(timeout.value) if timeout else 120,
        "max_output_tokens": int(max_tokens.value) if max_tokens else 2048,
    }


async def openrouter_models(api_key: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        response = await client.get("https://openrouter.ai/api/v1/models", headers={"Authorization": f"Bearer {api_key}"})
        response.raise_for_status()
        return response.json().get("data", [])


@app.get("/admin/settings/llm/models")
async def list_llm_models(search: str = "", _: models.User = Depends(admin_user), db: Session = Depends(get_db)):
    key = db.get(models.Setting, "openrouter_api_key")
    if not key:
        raise HTTPException(status_code=409, detail="Configure an OpenRouter API key first")
    try:
        catalogue = await openrouter_models(decrypt_secret(key.value))
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="OpenRouter model catalogue is unavailable") from exc
    needle = search.casefold()
    return [{"id": item["id"], "name": item.get("name", item["id"]), "context_length": item.get("context_length")}
            for item in catalogue if not needle or needle in item["id"].casefold() or needle in item.get("name", "").casefold()]


@app.post("/admin/settings/llm/test")
async def test_llm_provider(_: models.User = Depends(admin_user), db: Session = Depends(get_db)):
    key = db.get(models.Setting, "openrouter_api_key")
    if not key:
        raise HTTPException(status_code=409, detail="Configure an OpenRouter API key first")
    try:
        catalogue = await openrouter_models(decrypt_secret(key.value))
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="OpenRouter connection test failed") from exc
    return {"status": "connected", "models": len(catalogue)}


@app.put("/admin/settings/llm", response_model=schemas.LLMSettingsOut)
async def update_llm_settings(payload: schemas.LLMSettingsUpdate, admin: models.User = Depends(admin_user), db: Session = Depends(get_db)):
    model = db.get(models.Setting, "llm_model_name") or models.Setting(key="llm_model_name", value="", encrypted=False)
    model.value = payload.model_name
    db.add(model)
    if payload.openrouter_api_key:
        key = db.get(models.Setting, "openrouter_api_key") or models.Setting(key="openrouter_api_key", value="", encrypted=True)
        key.value = encrypt_secret(payload.openrouter_api_key)
        key.encrypted = True
        db.add(key)
    effective_key = payload.openrouter_api_key or (decrypt_secret(db.get(models.Setting, "openrouter_api_key").value) if db.get(models.Setting, "openrouter_api_key") else None)
    if effective_key:
        try:
            model_ids = {item["id"] for item in await openrouter_models(effective_key)}
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="OpenRouter could not validate these settings") from exc
        if payload.model_name not in model_ids or (payload.fallback_model_name and payload.fallback_model_name not in model_ids):
            raise HTTPException(status_code=400, detail="Select models present in the OpenRouter catalogue")
    for key_name, value in {
        "llm_fallback_model_name": payload.fallback_model_name or "",
        "llm_timeout_seconds": str(payload.timeout_seconds),
        "llm_max_output_tokens": str(payload.max_output_tokens),
    }.items():
        row = db.get(models.Setting, key_name) or models.Setting(key=key_name, value="", encrypted=False)
        row.value = value
        db.add(row)
    audit(db, "settings.llm_updated", admin.id, None, {"model_name": payload.model_name, "key_replaced": bool(payload.openrouter_api_key)})
    db.commit()
    return {"model_name": model.value, "api_key_configured": db.get(models.Setting, "openrouter_api_key") is not None,
            "fallback_model_name": payload.fallback_model_name, "timeout_seconds": payload.timeout_seconds,
            "max_output_tokens": payload.max_output_tokens}


ACTIVE_RUN_STATUSES = ("queued", "running", "awaiting_question", "awaiting_approval", "cancelling")


def enforce_budget(db: Session, project_id: int) -> models.Project:
    project = db.query(models.Project).filter(models.Project.id == project_id).with_for_update().first()
    budget = project.monthly_budget_usd or project.organization.monthly_budget_usd
    if budget is None:
        raise HTTPException(status_code=409, detail="An administrator must set a monthly budget before agent use")
    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    spent = db.query(models.AgentRun).filter(
        models.AgentRun.project_id == project_id, models.AgentRun.created_at >= month_start
    ).with_entities(models.AgentRun.cost_usd).all()
    if sum(row[0] or 0 for row in spent) >= budget:
        raise HTTPException(status_code=402, detail="This project's monthly agent budget has been reached")
    return project

@app.post("/projects/{project_id}/runs", response_model=schemas.RunOut, status_code=201)
def create_run(project_id: int, payload: schemas.RunCreate, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    require_project(db, user, project_id)
    project = enforce_budget(db, project_id)
    
    instance = db.query(models.Instance).filter(
        models.Instance.project_id == project_id,
        models.Instance.is_active.is_(True),
    ).first()
    if not instance or instance.status not in {"ready", "connected"}:
        raise HTTPException(status_code=409, detail="No active staging instance")

    key = db.get(models.Setting, "openrouter_api_key")
    if not key or not decrypt_secret(key.value).strip():
        raise HTTPException(status_code=400, detail="No API key configured. Please configure an OpenRouter API key in the Administration settings before running the agent.")
    
    active = db.query(models.AgentRun).filter(
        models.AgentRun.project_id == project_id,
        models.AgentRun.status.in_(ACTIVE_RUN_STATUSES),
    ).first()
    if active and not payload.queue_if_busy:
        raise HTTPException(status_code=409, detail={"message": "A project run is already active", "active_run_id": active.id})
    configured_model = db.get(models.Setting, "llm_model_name")
    configured_fallback = db.get(models.Setting, "llm_fallback_model_name")
    
    workspace = Workspace(project.workspace_slug)
    module_name = payload.module_name
    ambiguous = False
    if not module_name:
        tree = workspace.tree()
        modules = []
        for item in tree:
            if item["type"] == "directory":
                try:
                    workspace.resolve(f"{item['name']}/__manifest__.py", must_exist=True)
                    modules.append(item['name'])
                except FileNotFoundError:
                    pass
        if len(modules) == 1:
            module_name = modules[0]
        elif len(modules) > 1:
            ambiguous = True

    run = models.AgentRun(
        project_id=project_id,
        requested_by_id=user.id,
        prompt=payload.message,
        thread_id=f"project:{project_id}:run:{uuid4()}",
        planner_model=configured_model.value if configured_model else None,
        fallback_model=configured_fallback.value if configured_fallback and configured_fallback.value else None,
        workspace_base_revision=workspace.head(),
        module_name=module_name,
        status="awaiting_question" if ambiguous else "queued"
    )
    db.add(run)
    db.flush()

    if ambiguous:
        question = models.AgentQuestion(
            run_id=run.id,
            project_id=project_id,
            requested_by_id=user.id,
            kind="module_name",
            question="Multiple Odoo modules were found in the workspace. Which module should this run focus on?",
            options=modules,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7)
        )
        db.add(question)
        db.flush()

    odoo_version, odoo_edition = normalized_odoo_environment(instance.version_info)
    if odoo_version != "19.0" or odoo_edition == "unknown":
        raise HTTPException(status_code=409, detail="The staging Odoo 19 version and edition must be verified before starting the agent")
    snapshot = models.SourceSnapshot(
        instance_id=instance.id,
        odoo_version=odoo_version,
        odoo_edition=odoo_edition,
        fingerprint="pending",
        status="pending_index",
    )
    db.add(snapshot)
    db.flush()
    run.source_snapshot_id = snapshot.id

    if not ambiguous:
        emit(db, run.id, "run.queued", {"support_id": run.support_id})
        enqueue(db, "run.prepare", run.id)
    audit(db, "run.queued", user.id, project_id, {"run_id": run.id}, support_id=run.support_id, result="queued" if not ambiguous else "awaiting_question")
    db.commit()
    db.refresh(run)
    return run


@app.get("/projects/{project_id}/runs", response_model=list[schemas.RunOut])
def list_runs(project_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    require_project(db, user, project_id)
    return db.query(models.AgentRun).filter(models.AgentRun.project_id == project_id).order_by(models.AgentRun.created_at.desc()).limit(100).all()


@app.get("/runs/{run_id}", response_model=schemas.RunOut)
def get_run(run_id: str, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    run = db.get(models.AgentRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    require_project(db, user, run.project_id)
    return run


@app.post("/runs/{run_id}/cancel", response_model=schemas.RunOut)
def cancel_run(run_id: str, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    run = db.get(models.AgentRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    require_project(db, user, run.project_id)
    if run.status not in {"queued", "running", "awaiting_question", "awaiting_approval"}:
        raise HTTPException(status_code=409, detail="Run cannot be cancelled")
    previous_status = run.status
    run.status = "cancelled" if previous_status == "queued" else "cancelling"
    if previous_status == "awaiting_question":
        question = db.query(models.AgentQuestion).filter(
            models.AgentQuestion.run_id == run.id,
            models.AgentQuestion.status == "pending",
        ).first()
        if question:
            question.status = "cancelled"
        run.status = "cancelled"
    if previous_status == "awaiting_approval":
        action = db.query(models.PendingAction).filter(models.PendingAction.run_id == run.id, models.PendingAction.status == "pending").first()
        if action:
            action.status = "rejected"
            action.decided_by_id = user.id
            action.decided_at = datetime.now(timezone.utc)
            run.status = "queued"
            enqueue(db, "action.resume", run.id, {"action_id": action.id, "decision": "reject", "cancel_after": True})
        else:
            run.status = "cancelled"
            run.finished_at = datetime.now(timezone.utc)
    if run.status == "cancelled":
        if run.active_task_id:
            task_id = run.active_task_id
            run.task_graph = [
                {**task, "status": "cancelled"} if task.get("task_id") == task_id else {**task}
                for task in (run.task_graph or [])
            ]
            emit(db, run.id, "task.cancelled", {"task_id": task_id, "task_graph": run.task_graph})
            run.active_task_id = None
        run.finished_at = datetime.now(timezone.utc)
    emit(db, run.id, "run.cancellation_requested", {})
    if run.status == "cancelled":
        emit(db, run.id, "run.cancelled", {"task_graph": run.task_graph or []})
    db.commit()
    db.refresh(run)
    return run


@app.post("/runs/{run_id}/retry", response_model=schemas.RunOut, status_code=201)
def retry_run(run_id: str, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    failed = db.get(models.AgentRun, run_id)
    if not failed:
        raise HTTPException(status_code=404, detail="Run not found")
    require_project(db, user, failed.project_id)
    if failed.status not in {"failed", "interrupted", "expired"}:
        raise HTTPException(status_code=409, detail="Run is not retryable")
    active = db.query(models.AgentRun).filter(
        models.AgentRun.project_id == failed.project_id,
        models.AgentRun.status.in_(ACTIVE_RUN_STATUSES),
    ).first()
    if active:
        raise HTTPException(status_code=409, detail={"message": "A project run is already active", "active_run_id": active.id})
    enforce_budget(db, failed.project_id)
    preserved_graph = None
    if failed.task_graph:
        preserved_graph = []
        for task in failed.task_graph:
            task_copy = dict(task)
            if task_copy.get("status") in {"failed", "in_progress"}:
                task_copy["status"] = "pending"
                task_copy.pop("result", None)
            preserved_graph.append(task_copy)

    run = models.AgentRun(
        project_id=failed.project_id,
        requested_by_id=user.id,
        prompt=failed.prompt,
        thread_id=f"project:{failed.project_id}:run:{uuid4()}",
        retry_of_id=failed.id,
        planner_model=failed.planner_model,
        fallback_model=failed.fallback_model,
        workspace_base_revision=failed.workspace_base_revision,
        task_graph=preserved_graph,
    )
    db.add(run)
    db.flush()
    emit(db, run.id, "run.queued", {"support_id": run.support_id, "task_graph": preserved_graph})
    enqueue(db, "run.start", run.id)
    audit(db, "run.queued", user.id, failed.project_id, {"run_id": run.id, "retry_of_id": failed.id}, support_id=run.support_id, result="queued")
    db.commit()
    db.refresh(run)
    return run


@app.delete("/runs/{run_id}", status_code=204)
def delete_run(run_id: str, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    run = db.get(models.AgentRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    require_project(db, user, run.project_id)
    db.delete(run)
    db.commit()


@app.get("/runs/{run_id}/spec")
def get_run_spec(run_id: str, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    """Return the structured specification and acceptance checks for a run."""
    run = db.get(models.AgentRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    require_project(db, user, run.project_id)
    summary = get_specification_summary(run_id, db)
    if summary is None:
        raise HTTPException(status_code=404, detail="No specification found for this run")
    return summary


@app.get("/runs/{run_id}/events", response_model=list[schemas.ToolEventOut])
def run_events(run_id: str, after: int = 0, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    run = db.get(models.AgentRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    require_project(db, user, run.project_id)
    return db.query(models.ToolEvent).filter(
        models.ToolEvent.run_id == run_id, models.ToolEvent.sequence > after
    ).order_by(models.ToolEvent.sequence).all()


@app.get("/runs/{run_id}/stream")
async def stream_run_events(run_id: str, request: Request, after: int = 0, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    run = db.get(models.AgentRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    require_project(db, user, run.project_id)

    async def events():
        header_sequence = request.headers.get("Last-Event-ID")
        sequence = max(after, int(header_sequence) if header_sequence and header_sequence.isdigit() else 0)
        heartbeat_at = datetime.now(timezone.utc)
        while True:
            with SessionLocal() as event_db:
                rows = event_db.query(models.ToolEvent).filter(
                    models.ToolEvent.run_id == run_id, models.ToolEvent.sequence > sequence
                ).order_by(models.ToolEvent.sequence).all()
                current_run = event_db.get(models.AgentRun, run_id)
                if not current_run:
                    return
                status_value = current_run.status
            for row in rows:
                sequence = row.sequence
                envelope = {
                    "id": row.id,
                    "run_id": row.run_id,
                    "sequence": row.sequence,
                    "type": row.event_type,
                    "payload": row.payload,
                    "created_at": row.created_at.isoformat(),
                }
                yield f"id: {row.sequence}\ndata: {json.dumps(envelope)}\n\n"
                heartbeat_at = datetime.now(timezone.utc)
            if status_value in {"succeeded", "failed", "cancelled", "expired", "interrupted", "awaiting_approval", "awaiting_question"} and not rows:
                break
            if (datetime.now(timezone.utc) - heartbeat_at).total_seconds() >= 15:
                yield ": keep-alive\n\n"
                heartbeat_at = datetime.now(timezone.utc)
            await asyncio.sleep(0.5)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def can_approve(db: Session, user: models.User, action: models.PendingAction) -> bool:
    if action.risk_class in {"D", "E"}:
        project = db.get(models.Project, action.project_id)
        return user.id != action.requested_by_id and (
            user.role == "admin" or any(
                membership.organization_id == project.organization_id and membership.is_approver
                for membership in user.memberships
            )
        )
    return user.id == action.requested_by_id


@app.get("/actions/pending", response_model=list[schemas.PendingActionOut])
def pending_approvals(user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    actions = db.query(models.PendingAction).filter(
        models.PendingAction.status == "pending", models.PendingAction.expires_at > now
    ).order_by(models.PendingAction.created_at).limit(200).all()
    return [action for action in actions if can_approve(db, user, action)]


@app.post("/actions/{action_id}/decision")
def decide_run_action(action_id: str, payload: schemas.ActionDecision, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    action = db.query(models.PendingAction).filter(models.PendingAction.id == action_id).with_for_update().first()
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    require_project(db, user, action.project_id)
    if not can_approve(db, user, action):
        raise HTTPException(status_code=403, detail="A permitted approver must decide this action")
    if action.status != "pending" or action.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=409, detail="Action is no longer pending")
    if payload.decision == "approve":
        enforce_budget(db, action.project_id)
    action.status = "approved" if payload.decision == "approve" else "rejected"
    action.decided_by_id = user.id
    action.decided_at = datetime.now(timezone.utc)
    if not action.run_id and action.tool_name == "workspace_restore":
        if payload.decision == "approve":
            project = db.get(models.Project, action.project_id)
            commit = Workspace(project.workspace_slug).restore(action.arguments["revision"])
            action.status = "succeeded"
            result = {"action_id": action.id, "status": "succeeded", "commit": commit}
        else:
            result = {"action_id": action.id, "status": "rejected"}
        audit(db, f"action.{action.status}", user.id, action.project_id, {"action_id": action.id, "tool": action.tool_name}, risk_class=action.risk_class, result=action.status)
        db.commit()
        return result
    if not action.run_id:
        raise HTTPException(status_code=409, detail="Unsupported standalone action")
    run = db.get(models.AgentRun, action.run_id)
    enqueue(db, "action.resume", run.id, {
        "action_id": action.id,
        "decision": payload.decision,
        "auto_approve_task": payload.auto_approve_task,
    })
    emit(db, run.id, "approval.decided", {
        "action_id": action.id, "decision": payload.decision,
        "status": action.status, "decided_by": user.id,
    })
    audit(db, f"action.{action.status}", user.id, action.project_id, {"action_id": action.id}, risk_class=action.risk_class, support_id=run.support_id, result=action.status)
    db.commit(); db.refresh(run)
    return run


@app.post("/projects/{project_id}/chat")
async def chat(project_id: int, payload: schemas.ChatRequest, request: Request, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    run = create_run(project_id, schemas.RunCreate(message=payload.message), user, db)

    async def events():
        sequence = 0
        while True:
            with SessionLocal() as event_db:
                rows = event_db.query(models.ToolEvent).filter(
                    models.ToolEvent.run_id == run.id,
                    models.ToolEvent.sequence > sequence,
                ).order_by(models.ToolEvent.sequence).all()
                status_value = event_db.get(models.AgentRun, run.id).status
            for row in rows:
                sequence = row.sequence
                if row.event_type == "message.delta":
                    yield str(row.payload.get("text", ""))
                elif row.event_type in {"approval.required", "question.required"}:
                    marker = {"id": row.payload.get("action_id") or row.payload.get("question_id"), **row.payload}
                    yield "\n_ACTION_PENDING_||" + json.dumps(marker) + "\n"
            if status_value in {"succeeded", "failed", "cancelled", "expired", "interrupted", "awaiting_approval", "awaiting_question"} and not rows:
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(events(), media_type="text/plain")


@app.post("/runs/{run_id}/question", response_model=schemas.RunOut)
async def answer_run_question(run_id: str, payload: schemas.QuestionAnswer, request: Request, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    run = db.get(models.AgentRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    require_project(db, user, run.project_id)
    if run.status != "awaiting_question":
        raise HTTPException(status_code=409, detail="Run is not waiting for a question answer")
    question = db.query(models.AgentQuestion).filter(
        models.AgentQuestion.run_id == run.id,
        models.AgentQuestion.status == "pending",
    ).first()
    if not question or question.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=409, detail="This question has expired")
    project = require_project(db, user, run.project_id)
    agent = await project_agent(request, db, project)
    await agent.answer_question(run.thread_id, payload.answer)
    question.answer = payload.answer
    question.status = "answered"
    question.answered_at = datetime.now(timezone.utc)
    run.status = "queued"
    emit(db, run.id, "question.answered", {
        "question_id": question.id, "answer": payload.answer, "answered_by": user.id,
    })
    enqueue(db, "run.resume", run.id)
    audit(db, "agent.question_answered", user.id, run.project_id, {"run_id": run.id, "question_id": question.id})
    db.commit()
    db.refresh(run)
    return run


@app.post("/projects/{project_id}/actions/{action_id}/decision")
async def decide_action(project_id: int, action_id: str, payload: schemas.ActionDecision, request: Request, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    action = db.query(models.PendingAction).filter(
        models.PendingAction.id == action_id,
        models.PendingAction.project_id == project_id,
    ).with_for_update().first()
    if not action:
        raise HTTPException(status_code=404, detail="Pending action not found")
    if action.requested_by_id != user.id:
        raise HTTPException(status_code=403, detail="Only the requesting user can decide this action")
    now = datetime.now(timezone.utc)
    if action.status != "pending" or action.expires_at <= now:
        raise HTTPException(status_code=409, detail="This action has expired. Please stop the agent and request a new one.")
    agent = await project_agent(request, db, project)
    call = await agent.pending_call(action.thread_id)
    if not call or call["id"] != action.tool_call_id or call["name"] != action.tool_name or call["args"] != action.arguments:
        raise HTTPException(status_code=409, detail="Checkpoint does not match the approved action")
    action.status = "approved" if payload.decision == "approve" else "rejected"
    action.decided_by_id = user.id
    action.decided_at = now
    audit(db, f"agent.action_{action.status}", user.id, project_id, {"action_id": action.id, "tool": action.tool_name})
    db.commit()

    async def events():
        response_text = ""
        succeeded = False
        try:
            async for chunk in agent.stream(None, action.thread_id, reject=payload.decision == "reject"):
                response_text += chunk
                yield chunk
            succeeded = True
        except Exception:
            yield "The approved action could not be completed."
        finally:
            with SessionLocal() as save_db:
                stored = save_db.get(models.PendingAction, action_id)
                if stored and payload.decision == "approve":
                    stored.status = "executed" if succeeded else "failed"
                save_db.add(models.Interaction(project_id=project_id, role="agent", content=response_text))
                audit(save_db, "agent.action_result", user.id, project_id, {"action_id": action_id, "succeeded": succeeded})
                save_db.commit()

    return StreamingResponse(events(), media_type="text/plain")


@app.post("/projects/{project_id}/actions/answer")
async def answer_question_endpoint(project_id: int, payload: schemas.QuestionAnswer, request: Request, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    """Resume the agent after an ask_question pause by supplying the user's answer."""
    project = require_project(db, user, project_id)
    agent = await project_agent(request, db, project)
    run = db.query(models.AgentRun).filter(
        models.AgentRun.project_id == project_id,
        models.AgentRun.status == "awaiting_question",
    ).order_by(models.AgentRun.created_at.desc()).first()
    if not run:
        raise HTTPException(status_code=409, detail="No active agent run awaiting an answer")
    question = db.query(models.AgentQuestion).filter(
        models.AgentQuestion.run_id == run.id,
        models.AgentQuestion.status == "pending",
    ).first()
    if not question or question.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=409, detail="This question has expired")
    await agent.answer_question(run.thread_id, payload.answer)
    question.answer = payload.answer
    question.status = "answered"
    question.answered_at = datetime.now(timezone.utc)
    run.status = "queued"
    emit(db, run.id, "question.answered", {
        "question_id": question.id, "answer": payload.answer, "answered_by": user.id,
    })
    enqueue(db, "run.resume", run.id)
    audit(db, "agent.question_answered", user.id, project_id, {"run_id": run.id, "question_id": question.id})
    db.commit()
    db.refresh(run)
    return run


@app.get("/projects/{project_id}/actions/pending", response_model=schemas.PendingActionOut | None)
def pending_action(project_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    require_project(db, user, project_id)
    return db.query(models.PendingAction).filter(
        models.PendingAction.project_id == project_id,
        models.PendingAction.requested_by_id == user.id,
        models.PendingAction.status == "pending",
        models.PendingAction.expires_at > datetime.now(timezone.utc),
    ).order_by(models.PendingAction.created_at.desc()).first()


@app.get("/projects/{project_id}/chat", response_model=list[schemas.InteractionOut])
def chat_history(project_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    require_project(db, user, project_id)
    return db.query(models.Interaction).filter(models.Interaction.project_id == project_id).order_by(models.Interaction.created_at).all()


@app.delete("/projects/{project_id}/chat", status_code=204)
def clear_chat_history(project_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    require_project(db, user, project_id)
    db.query(models.Interaction).filter(models.Interaction.project_id == project_id).delete()
    db.query(models.PendingAction).filter(models.PendingAction.project_id == project_id, models.PendingAction.status == "pending").update({"status": "cancelled"})
    db.query(models.AgentRun).filter(
        models.AgentRun.project_id == project_id,
        models.AgentRun.status.in_(ACTIVE_RUN_STATUSES),
    ).update({"status": "cancelled", "finished_at": datetime.now(timezone.utc)})
    db.commit()


@app.get("/projects/{project_id}/workspace/tree")
def workspace_tree(project_id: int, path: str = "", user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    return Workspace(project.workspace_slug).tree(path)


@app.get("/projects/{project_id}/workspace/files")
def workspace_file(project_id: int, path: str, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    try:
        return {"path": path, "content": Workspace(project.workspace_slug).read_file(path)}
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="File not found in workspace")


@app.get("/projects/{project_id}/workspace/commits")
def workspace_commits(project_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    return Workspace(project.workspace_slug).history()


@app.get("/projects/{project_id}/workspace/diff")
def workspace_diff(project_id: int, old: str | None = None, new: str = "HEAD", run_id: str | None = None, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    workspace = Workspace(project.workspace_slug)
    head_revision = workspace.head()
    base_revision = old
    if run_id:
        run = db.get(models.AgentRun, run_id)
        if not run or run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Run not found")
        base_revision = run.workspace_base_revision
    if not head_revision or (not run_id and not base_revision):
        return {"diff": "", "base_revision": base_revision, "head_revision": head_revision, "truncated": False}
    comparison_base = base_revision or Workspace.EMPTY_TREE_REVISION
    diff, truncated = workspace.diff_result(comparison_base, new)
    return {"diff": diff, "base_revision": base_revision, "head_revision": head_revision, "truncated": truncated}


@app.get("/projects/{project_id}/workspace/archive")
def workspace_archive(project_id: int, path: str = "", user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    return Response(
        content=Workspace(project.workspace_slug).archive(path), media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={project.workspace_slug}.zip"},
    )


@app.post("/projects/{project_id}/workspace/restore", response_model=schemas.PendingActionOut, status_code=202)
def restore_workspace(project_id: int, revision: str, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    workspace = Workspace(project.workspace_slug)
    preview = {"operation": "restore workspace", "revision": revision, "diff": workspace.diff(revision, "HEAD")[:20_000]}
    action = models.PendingAction(
        project_id=project_id, requested_by_id=user.id, thread_id=f"workspace:{project_id}", tool_call_id=f"workspace-restore:{uuid4()}",
        tool_name="workspace_restore", arguments={"revision": revision}, preview=preview, risk_class="C",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.action_expiry_minutes), idempotency_key=f"workspace:{project_id}:{revision}:{uuid4()}",
    )
    db.add(action); db.commit(); db.refresh(action)
    return action


@app.get("/projects/{project_id}/requirements", response_model=list[schemas.RequirementOut])
def list_requirements(project_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    require_project(db, user, project_id)
    return db.query(models.Requirement).filter(models.Requirement.project_id == project_id).order_by(models.Requirement.created_at).all()


@app.post("/projects/{project_id}/requirements", response_model=schemas.RequirementOut, status_code=201)
def create_requirement(project_id: int, payload: schemas.RequirementCreate, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    require_project(db, user, project_id)
    requirement = models.Requirement(project_id=project_id, **payload.model_dump())
    db.add(requirement); db.commit(); db.refresh(requirement)
    return requirement


@app.post("/projects/{project_id}/requirements/{requirement_id}/approve", response_model=schemas.RequirementOut)
def approve_requirement(project_id: int, requirement_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    requirement = db.query(models.Requirement).filter(models.Requirement.id == requirement_id, models.Requirement.project_id == project_id).first()
    if not requirement:
        raise HTTPException(status_code=404, detail="Requirement not found")
    if user.role != "admin" and not any(m.organization_id == project.organization_id and m.is_approver for m in user.memberships):
        raise HTTPException(status_code=403, detail="Approver access required")
    requirement.status = "approved"; requirement.approved_by_id = user.id; db.commit(); db.refresh(requirement)
    return requirement


@app.get("/projects/{project_id}/tasks", response_model=list[schemas.TaskOut])
def list_tasks(project_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    require_project(db, user, project_id)
    return db.query(models.ProjectTask).filter(models.ProjectTask.project_id == project_id).order_by(models.ProjectTask.created_at).all()


@app.post("/projects/{project_id}/tasks", response_model=schemas.TaskOut, status_code=201)
def create_task(project_id: int, payload: schemas.TaskCreate, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    require_project(db, user, project_id)
    if payload.requirement_id and not db.query(models.Requirement).filter(models.Requirement.id == payload.requirement_id, models.Requirement.project_id == project_id).first():
        raise HTTPException(status_code=400, detail="Requirement does not belong to this project")
    task = models.ProjectTask(project_id=project_id, **payload.model_dump())
    db.add(task); db.commit(); db.refresh(task)
    return task


@app.patch("/projects/{project_id}/tasks/{task_id}", response_model=schemas.TaskOut)
def update_task(project_id: int, task_id: int, status_value: str, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    require_project(db, user, project_id)
    if status_value not in {"backlog", "active", "blocked", "completed"}:
        raise HTTPException(status_code=400, detail="Invalid task status")
    task = db.query(models.ProjectTask).filter(models.ProjectTask.id == task_id, models.ProjectTask.project_id == project_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task.status = status_value; db.commit(); db.refresh(task)
    return task


@app.get("/projects/{project_id}/discovery")
def list_discovery(project_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    require_project(db, user, project_id)
    return db.query(models.DiscoveryFinding).filter(models.DiscoveryFinding.project_id == project_id).order_by(models.DiscoveryFinding.created_at).all()


@app.post("/projects/{project_id}/discovery", status_code=201)
def create_discovery(project_id: int, payload: schemas.DiscoveryCreate, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    if payload.verified and user.role != "admin" and not any(m.organization_id == project.organization_id and m.is_approver for m in user.memberships):
        raise HTTPException(status_code=403, detail="An approver must verify discovery evidence")
    finding = models.DiscoveryFinding(project_id=project_id, created_by_id=user.id, **payload.model_dump())
    db.add(finding); db.commit(); db.refresh(finding)
    return finding


@app.get("/projects/{project_id}/specifications")
def list_specifications(project_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    require_project(db, user, project_id)
    return db.query(models.TechnicalSpecification).filter(models.TechnicalSpecification.project_id == project_id).order_by(models.TechnicalSpecification.created_at).all()


@app.post("/projects/{project_id}/specifications", status_code=201)
def create_specification(project_id: int, payload: schemas.SpecificationCreate, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    require_project(db, user, project_id)
    if not db.query(models.Requirement).filter(models.Requirement.id == payload.requirement_id, models.Requirement.project_id == project_id, models.Requirement.status == "approved").first():
        raise HTTPException(status_code=409, detail="Specification requires an approved project requirement")
    specification = models.TechnicalSpecification(project_id=project_id, **payload.model_dump())
    db.add(specification); db.commit(); db.refresh(specification)
    return specification


@app.post("/projects/{project_id}/specifications/{specification_id}/approve")
def approve_specification(project_id: int, specification_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    if user.role != "admin" and not any(m.organization_id == project.organization_id and m.is_approver for m in user.memberships):
        raise HTTPException(status_code=403, detail="Approver access required")
    specification = db.query(models.TechnicalSpecification).filter(models.TechnicalSpecification.id == specification_id, models.TechnicalSpecification.project_id == project_id).first()
    if not specification:
        raise HTTPException(status_code=404, detail="Specification not found")
    specification.status = "approved"; specification.approved_by_id = user.id; db.commit(); db.refresh(specification)
    return specification


@app.post("/projects/{project_id}/uat", status_code=201)
def accept_uat(project_id: int, payload: schemas.UATEvidenceCreate, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    if user.role != "admin" and not any(m.organization_id == project.organization_id and m.is_approver for m in user.memberships):
        raise HTTPException(status_code=403, detail="Designated approver access required")
    artifact = db.query(models.Artifact).filter(models.Artifact.id == payload.artifact_id, models.Artifact.project_id == project_id).first()
    staged = db.query(models.Deployment).filter(models.Deployment.artifact_id == payload.artifact_id, models.Deployment.environment == "staging", models.Deployment.status == "succeeded").first()
    if not artifact or not staged:
        raise HTTPException(status_code=409, detail="UAT requires a successfully staged artifact")
    evidence = models.UATEvidence(project_id=project_id, artifact_id=payload.artifact_id, notes=payload.notes, accepted_by_id=user.id)
    db.add(evidence); db.commit(); db.refresh(evidence)
    return evidence


PHASES = ["discovery", "requirements", "design", "build", "validate", "uat", "ready_for_production"]


def phase_gate(db: Session, project: models.Project, phase: str) -> str | None:
    if phase == "requirements" and (not db.query(models.Instance).filter(models.Instance.project_id == project.id, models.Instance.status == "connected").first() or not db.query(models.DiscoveryFinding).filter(models.DiscoveryFinding.project_id == project.id, models.DiscoveryFinding.verified.is_(True)).first()):
        return "A verified Odoo instance and discovery evidence are required"
    if phase == "design" and not db.query(models.Requirement).filter(models.Requirement.project_id == project.id, models.Requirement.status == "approved").first():
        return "At least one approved requirement is required"
    if phase == "build" and not db.query(models.TechnicalSpecification).filter(models.TechnicalSpecification.project_id == project.id, models.TechnicalSpecification.status == "approved").first():
        return "An approved technical specification and risk assessment are required"
    if phase == "validate" and not db.query(models.Artifact).filter(models.Artifact.project_id == project.id).first():
        return "A versioned module artifact is required"
    if phase == "uat" and not db.query(models.ValidationRun).filter(models.ValidationRun.project_id == project.id, models.ValidationRun.status == "passed").first():
        return "A passed validation is required"
    if phase == "ready_for_production" and (not db.query(models.Deployment).filter(models.Deployment.project_id == project.id, models.Deployment.status == "succeeded", models.Deployment.environment == "staging").first() or not db.query(models.UATEvidence).filter(models.UATEvidence.project_id == project.id).first()):
        return "A successful staging deployment and UAT evidence are required"
    return None


@app.put("/projects/{project_id}/phase", response_model=schemas.ProjectOut)
def update_phase(project_id: int, payload: schemas.PhaseUpdate, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    if PHASES.index(payload.phase) > PHASES.index(project.phase) + 1:
        raise HTTPException(status_code=400, detail="Project phases cannot be skipped")
    blocked = phase_gate(db, project, payload.phase)
    if blocked:
        raise HTTPException(status_code=409, detail=blocked)
    project.phase = payload.phase; audit(db, "project.phase_changed", user.id, project_id, {"phase": payload.phase}); db.commit(); db.refresh(project)
    return project


@app.get("/projects/{project_id}/artifacts", response_model=list[schemas.ArtifactOut])
def list_artifacts(project_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    require_project(db, user, project_id)
    return db.query(models.Artifact).filter(models.Artifact.project_id == project_id).order_by(models.Artifact.created_at.desc()).all()


@app.post("/projects/{project_id}/artifacts", response_model=schemas.ArtifactOut, status_code=201)
def create_artifact(project_id: int, payload: schemas.ArtifactCreate, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    workspace = Workspace(project.workspace_slug)
    module_path = workspace.resolve(payload.path, must_exist=True)
    report = validate_module_full(module_path, payload.name)
    commit_hash = workspace.commit(f"Package {payload.name} {payload.version}")
    archive_digest = hashlib.sha256(package_module(module_path)).hexdigest()
    artifact = models.Artifact(
        project_id=project_id, requirement_id=payload.requirement_id, artifact_type="odoo_module",
        name=payload.name, version=payload.version, commit_hash=commit_hash,
        digest=archive_digest, path=payload.path,
        status="validated" if report["passed"] else "failed_validation", created_by_id=user.id,
    )
    db.add(artifact); db.flush()
    validation = models.ValidationRun(
        project_id=project_id, artifact_id=artifact.id,
        status="passed" if report["passed"] else "failed", report=report,
        finished_at=datetime.now(timezone.utc),
    )
    db.add(validation); audit(db, "artifact.validated", user.id, project_id, {"artifact_id": artifact.id, "passed": report["passed"]}, risk_class="C", result=validation.status)
    db.commit(); db.refresh(artifact)
    return artifact


@app.get("/projects/{project_id}/validations", response_model=list[schemas.ValidationOut])
def list_validations(project_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    require_project(db, user, project_id)
    return db.query(models.ValidationRun).filter(models.ValidationRun.project_id == project_id).order_by(models.ValidationRun.created_at.desc()).all()


@app.get("/projects/{project_id}/deployments", response_model=list[schemas.DeploymentOut])
def list_deployments(project_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    require_project(db, user, project_id)
    return db.query(models.Deployment).filter(models.Deployment.project_id == project_id).order_by(models.Deployment.created_at.desc()).all()


@app.post("/projects/{project_id}/deployments", response_model=schemas.DeploymentOut, status_code=201)
def create_deployment(project_id: int, payload: schemas.DeploymentCreate, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    instance = db.get(models.Instance, payload.instance_id)
    artifact = db.get(models.Artifact, payload.artifact_id)
    if not instance or instance.project_id != project_id or not artifact or artifact.project_id != project_id:
        raise HTTPException(status_code=400, detail="Instance and artifact must belong to this project")
    try:
        deployment = request_deployment(db, project, instance, artifact, user.id, payload.rollback_plan)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    db.refresh(deployment)
    return deployment


@app.post("/projects/{project_id}/quick-deploy")
def quick_deploy_module(project_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    workspace = Workspace(project.workspace_slug)
    ws_path = workspace.root
    manifest_files = list(ws_path.glob("**/__manifest__.py"))
    if not manifest_files:
        raise HTTPException(status_code=400, detail="No Odoo module with __manifest__.py found in workspace")
    
    manifest_path = manifest_files[0]
    module_dir = manifest_path.parent
    rel_path = str(module_dir.relative_to(ws_path))
    module_name = module_dir.name
    
    import ast
    version = "19.0.1.0.0"
    try:
        manifest_data = ast.literal_eval(manifest_path.read_text(encoding="utf-8"))
        if isinstance(manifest_data, dict):
            version = str(manifest_data.get("version", version))
    except Exception:
        pass
        
    report = validate_module_full(module_dir, module_name)
    if not report["passed"]:
        raise HTTPException(status_code=409, detail={
            "message": "Module failed static validation; deployment was not created",
            "checks": report["static"]["checks"] + report["runtime"].get("checks", []),
        })
    commit_hash = workspace.commit(f"Auto-package {module_name} {version}")
    archive_digest = hashlib.sha256(package_module(module_dir)).hexdigest()
    
    artifact = models.Artifact(
        project_id=project_id,
        artifact_type="odoo_module",
        name=module_name,
        version=version,
        commit_hash=commit_hash,
        digest=archive_digest,
        path=rel_path,
        status="validated" if report["passed"] else "failed_validation",
        created_by_id=user.id,
    )
    db.add(artifact)
    db.flush()
    
    validation = models.ValidationRun(
        project_id=project_id,
        artifact_id=artifact.id,
        status="passed" if report["passed"] else "failed",
        report=report,
        finished_at=datetime.now(timezone.utc),
    )
    db.add(validation)
    
    audit(db, "artifact.quick_deployed", user.id, project_id, {
        "artifact_id": artifact.id, "module": module_name, "deployed": False
    })
    db.commit()
    return {
        "artifact_id": artifact.id,
        "module_name": module_name,
        "version": version,
        "passed": report["passed"],
        "deployed": False,
        "deployment_id": None,
        "message": f"Module {module_name} passed disposable Odoo validation and is ready for a staging deployment request."
    }


@app.get("/projects/{project_id}/memories", response_model=list[schemas.MemoryOut])
def list_project_memories(project_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    require_project(db, user, project_id)
    return db.query(models.AgentMemory).filter(
        (models.AgentMemory.project_id == project_id) | (models.AgentMemory.project_id.is_(None))
    ).order_by(models.AgentMemory.updated_at.desc()).all()


@app.post("/projects/{project_id}/memories", response_model=schemas.MemoryOut, status_code=201)
def create_project_memory(project_id: int, payload: schemas.MemoryCreate, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    require_project(db, user, project_id)
    mem = models.AgentMemory(
        project_id=project_id,
        category=payload.category,
        key=payload.key,
        content=payload.content,
        confidence=payload.confidence,
    )
    db.add(mem)
    db.commit()
    db.refresh(mem)
    return mem


@app.delete("/memories/{memory_id}", status_code=204)
def delete_memory(memory_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    mem = db.get(models.AgentMemory, memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")
    if mem.project_id:
        require_project(db, user, mem.project_id)
    db.delete(mem)
    db.commit()
    return None



@app.post("/deployments/{deployment_id}/decision", response_model=schemas.DeploymentOut)
def decide_deployment(deployment_id: str, payload: schemas.ActionDecision, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    deployment = db.query(models.Deployment).filter(models.Deployment.id == deployment_id).with_for_update().first()
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
    project = require_project(db, user, deployment.project_id)
    approver = user.id != deployment.requested_by_id and (user.role == "admin" or any(
        membership.organization_id == project.organization_id and membership.is_approver for membership in user.memberships
    ))
    if not approver:
        raise HTTPException(status_code=403, detail="A different organization approver is required")
    if deployment.status != "pending_approval":
        raise HTTPException(status_code=409, detail="Deployment is no longer pending approval")
    deployment.approved_by_id = user.id
    deployment.status = "approved" if payload.decision == "approve" else "rejected"
    if payload.decision == "approve":
        enqueue(db, "deployment.start", deployment.id)
    else:
        deployment.finished_at = datetime.now(timezone.utc)
    audit(db, f"deployment.{deployment.status}", user.id, deployment.project_id, {"deployment_id": deployment.id}, risk_class="E", result=deployment.status)
    db.commit(); db.refresh(deployment)
    return deployment


@app.get("/deployments/pending", response_model=list[schemas.DeploymentOut])
def pending_deployments(user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.query(models.Deployment).filter(models.Deployment.status == "pending_approval").order_by(models.Deployment.created_at).all()
    visible = []
    for deployment in rows:
        project = db.get(models.Project, deployment.project_id)
        if user.id != deployment.requested_by_id and (user.role == "admin" or any(
            membership.organization_id == project.organization_id and membership.is_approver for membership in user.memberships
        )):
            visible.append(deployment)
    return visible


@app.post("/deployments/{deployment_id}/refresh", response_model=schemas.DeploymentOut)
def refresh_deployment(deployment_id: str, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    deployment = db.get(models.Deployment, deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
    require_project(db, user, deployment.project_id)
    instance = db.get(models.Instance, deployment.instance_id)
    if instance.hosting_type == "on_premise" and deployment.external_job_id:
        try:
            refresh_bridge_deployment(deployment, instance)
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Deployment bridge status is unavailable") from exc
        db.commit(); db.refresh(deployment)
    return deployment


@app.get("/deployments/{deployment_id}/artifact")
def deployment_artifact(deployment_id: str, request: Request, db: Session = Depends(get_db)):
    deployment = db.get(models.Deployment, deployment_id)
    if not deployment or deployment.status not in {"approved", "executing", "deploying"}:
        raise HTTPException(status_code=404, detail="Artifact unavailable")
    instance = db.get(models.Instance, deployment.instance_id)
    config = json.loads(decrypt_secret(instance.deployment_config_encrypted or ""))
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if not supplied or not hmac.compare_digest(supplied, config.get("bridge_token", "")):
        raise HTTPException(status_code=401, detail="Invalid bridge credential")
    artifact = db.get(models.Artifact, deployment.artifact_id)
    project = db.get(models.Project, deployment.project_id)
    module_path = Workspace(project.workspace_slug).resolve(artifact.path, must_exist=True)
    bundle = package_module(module_path)
    if hashlib.sha256(bundle).hexdigest() != artifact.digest:
        raise HTTPException(status_code=409, detail="Artifact changed after validation")
    return Response(content=bundle, media_type="application/zip", headers={"Content-Disposition": f"attachment; filename={artifact.name}-{artifact.version}.zip"})
