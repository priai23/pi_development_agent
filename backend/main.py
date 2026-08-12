import asyncio
import csv
import io
import ipaddress
import hashlib
import hmac
import json
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
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.orm import Session

import models
import schemas
from agent import ERPImplementationAgent, connect_odoo, connect_odoo_json2, xmlrpc_transport
from auth import CSRF_COOKIE, SESSION_COOKIE, admin_user, current_user, organization_ids, require_project
from config import settings
from database import SessionLocal, get_db
from deployment import execute_deployment, generate_signing_config, refresh_bridge_deployment
from security import decrypt_secret, encrypt_secret, hash_password, new_token, token_hash, verify_password
from worker import emit, enqueue
from workspace import Workspace
from validation import package_module, validate_module


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.workspace_root.expanduser().resolve().mkdir(parents=True, exist_ok=True)
    async with AsyncPostgresSaver.from_conn_string(settings.checkpoint_url) as checkpointer:
        await checkpointer.setup()
        app.state.checkpointer = checkpointer
        yield


app = FastAPI(
    title="ERP Agentic Implementation API",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
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
    if request.method not in {"GET", "HEAD", "OPTIONS"} and request.url.path != "/auth/login":
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
        raise HTTPException(status_code=400, detail="Invalid ERP URL")
    policies = db.query(models.HostPolicy).filter(models.HostPolicy.is_active.is_(True)).all() if db else []
    policy = next((item for item in policies if host == item.hostname_pattern or (item.hostname_pattern.startswith(".") and host.endswith(item.hostname_pattern))), None)
    bootstrap_allowed = any(host == entry or (entry.startswith(".") and host.endswith(entry)) for entry in settings.allowed_hosts)
    if not bootstrap_allowed and not policy:
        raise HTTPException(status_code=400, detail="ERP host is not allowed")
    require_https = policy.require_https if policy else host not in {"localhost", "127.0.0.1"}
    if require_https and parsed.scheme != "https":
        raise HTTPException(status_code=400, detail="ERP URL must use HTTPS")
    try:
        addresses = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise HTTPException(status_code=400, detail="ERP host could not be resolved") from exc
    allow_private = policy.allow_private_network if policy else host in {"localhost", "127.0.0.1"}
    if not allow_private and any(ipaddress.ip_address(item[4][0]).is_private for item in addresses):
        raise HTTPException(status_code=400, detail="ERP host resolves to a private network address")
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
    return ERPImplementationAgent(
        client,
        project.workspace_slug,
        request.app.state.checkpointer,
        model,
        key,
        "https://openrouter.ai/api/v1" if key else None,
    )


@app.get("/health")
def health():
    return {"status": "ok"}


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
def list_users(_: models.User = Depends(admin_user), db: Session = Depends(get_db)):
    return db.query(models.User).order_by(models.User.email).all()


@app.post("/admin/users", response_model=schemas.UserOut, status_code=201)
def create_user(payload: schemas.AdminUserCreate, admin: models.User = Depends(admin_user), db: Session = Depends(get_db)):
    email = payload.email.lower()
    if db.query(models.User).filter(models.User.email == email).first():
        raise HTTPException(status_code=409, detail="Email already exists")
    organizations = db.query(models.Organization).filter(models.Organization.id.in_(payload.organization_ids)).all()
    if len(organizations) != len(set(payload.organization_ids)):
        raise HTTPException(status_code=400, detail="Unknown organization")
    user = models.User(email=email, password_hash=hash_password(payload.password), role=payload.role, must_change_password=True)
    db.add(user)
    db.flush()
    approver_ids = set(payload.approver_organization_ids)
    if not approver_ids.issubset(set(payload.organization_ids)):
        raise HTTPException(status_code=400, detail="Approver organizations must also be memberships")
    for organization in organizations:
        db.add(models.OrganizationMembership(user_id=user.id, organization_id=organization.id, is_approver=organization.id in approver_ids))
    audit(db, "admin.user_created", admin.id, None, {"created_user_id": user.id})
    db.commit()
    db.refresh(user)
    return user


@app.patch("/admin/users/{user_id}", response_model=schemas.UserOut)
def update_user(user_id: int, payload: schemas.AdminUserUpdate, admin: models.User = Depends(admin_user), db: Session = Depends(get_db)):
    user = db.get(models.User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.role is not None:
        user.role = payload.role
    if payload.is_active is not None:
        if user.id == admin.id and not payload.is_active:
            raise HTTPException(status_code=400, detail="You cannot deactivate your own account")
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
    audit(db, "admin.user_updated", admin.id, None, {"updated_user_id": user.id})
    db.commit()
    db.refresh(user)
    return user


@app.post("/admin/users/{user_id}/reset-password")
def create_password_reset(user_id: int, admin: models.User = Depends(admin_user), db: Session = Depends(get_db)):
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
    audit(db, "admin.password_reset_created", admin.id, None, {"user_id": user.id})
    db.commit()
    return {"reset_token": raw_token, "expires_at": reset.expires_at}


@app.post("/admin/users/{user_id}/force-logout", status_code=204)
def force_logout(user_id: int, admin: models.User = Depends(admin_user), db: Session = Depends(get_db)):
    if not db.get(models.User, user_id):
        raise HTTPException(status_code=404, detail="User not found")
    db.query(models.UserSession).filter(models.UserSession.user_id == user_id).delete()
    audit(db, "admin.user_logged_out", admin.id, None, {"user_id": user_id})
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
    return {
        "api": "healthy",
        "database": "healthy",
        "worker": "healthy" if db.query(models.AgentRun).filter(models.AgentRun.status == "running", models.AgentRun.heartbeat_at < stale_before).count() == 0 else "stale_runs",
        "queue_depth": db.query(models.OutboxEvent).filter(models.OutboxEvent.completed_at.is_(None)).count(),
        "stale_actions": db.query(models.PendingAction).filter(models.PendingAction.status.in_(["claimed", "executing"]), models.PendingAction.claimed_at < stale_before).count(),
        "failed_deployments": db.query(models.Deployment).filter(models.Deployment.status == "failed").count(),
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
    project = models.Project(
        name=payload.name.strip(),
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
    query = db.query(models.Project)
    if user.role != "admin":
        query = query.filter(models.Project.organization_id.in_(organization_ids(user)))
    return query.order_by(models.Project.created_at.desc()).all()


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
    if payload.erp_type != "odoo":
        raise HTTPException(status_code=501, detail="Pi ERP connectivity is not implemented")

    def list_databases():
        proxy = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/db", transport=xmlrpc_transport(url), allow_none=True)
        return proxy.list()

    try:
        databases = await asyncio.wait_for(asyncio.to_thread(list_databases), timeout=10)
        return {"status": "success" if databases else "manual_required", "databases": databases, "suggested_username": "admin"}
    except asyncio.TimeoutError:
        return {"status": "timeout", "databases": [], "message": "Database discovery timed out"}
    except socket.gaierror:
        return {"status": "dns_error", "databases": [], "message": "ERP hostname could not be resolved"}
    except xmlrpc.client.ProtocolError as exc:
        status_name = "authentication_required" if exc.errcode in {401, 403} else "endpoint_unsupported" if exc.errcode == 404 else "network_error"
        return {"status": status_name, "databases": [], "message": f"Database endpoint returned HTTP {exc.errcode}"}
    except ssl.SSLError:
        return {"status": "tls_error", "databases": [], "message": "TLS certificate validation failed"}
    except OSError as exc:
        return {"status": "network_error", "databases": [], "message": str(exc)[:300]}
    except Exception:
        return {"status": "manual_required", "databases": [], "message": "Database listing is disabled; enter the name manually"}


@app.post("/instances", response_model=schemas.InstanceOut, status_code=201)
async def create_instance(payload: schemas.InstanceCreate, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, payload.project_id)
    url = validate_erp_url(str(payload.url), db)
    if payload.erp_type != "odoo":
        raise HTTPException(status_code=501, detail="Pi ERP connectivity is not implemented")
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
        raise HTTPException(status_code=400, detail="Odoo authentication failed. Check the database, username, and password") from exc
    except (OSError, xmlrpc.client.Error) as exc:
        raise HTTPException(status_code=400, detail="Could not reach the Odoo XML-RPC endpoint. Check the URL and server availability") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Odoo connection failed. Check the URL, database, and credentials") from exc
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
        version_info=client.version(),
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
        instance.version_info = client.version()
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
        "max_output_tokens": int(max_tokens.value) if max_tokens else 8000,
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
            for item in catalogue if not needle or needle in item["id"].casefold() or needle in item.get("name", "").casefold()][:100]


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


ACTIVE_RUN_STATUSES = ("running", "awaiting_approval", "cancelling")


@app.post("/projects/{project_id}/runs", response_model=schemas.RunOut, status_code=201)
def create_run(project_id: int, payload: schemas.RunCreate, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    budget = project.monthly_budget_usd or project.organization.monthly_budget_usd
    if budget is None:
        raise HTTPException(status_code=409, detail="An administrator must set a monthly budget before agent use")

    key = db.get(models.Setting, "openrouter_api_key")
    if not key or not decrypt_secret(key.value).strip():
        raise HTTPException(status_code=400, detail="No API key configured. Please configure an OpenRouter API key in the Administration settings before running the agent.")
    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    spent = db.query(models.AgentRun).filter(
        models.AgentRun.project_id == project_id, models.AgentRun.created_at >= month_start
    ).with_entities(models.AgentRun.cost_usd).all()
    if sum(row[0] or 0 for row in spent) >= budget:
        raise HTTPException(status_code=402, detail="This project's monthly agent budget has been reached")
    active = db.query(models.AgentRun).filter(
        models.AgentRun.project_id == project_id,
        models.AgentRun.status.in_(ACTIVE_RUN_STATUSES),
    ).first()
    if active and not payload.queue_if_busy:
        raise HTTPException(status_code=409, detail={"message": "A project run is already active", "active_run_id": active.id})
    run = models.AgentRun(
        project_id=project_id,
        requested_by_id=user.id,
        prompt=payload.message,
        thread_id=f"project:{project_id}:run:{uuid4()}",
    )
    db.add(run)
    db.flush()
    enqueue(db, "run.start", run.id)
    audit(db, "run.queued", user.id, project_id, {"run_id": run.id}, support_id=run.support_id, result="queued")
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
    if run.status not in {"queued", "running", "awaiting_approval"}:
        raise HTTPException(status_code=409, detail="Run cannot be cancelled")
    previous_status = run.status
    run.status = "cancelled" if previous_status == "queued" else "cancelling"
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
        run.finished_at = datetime.now(timezone.utc)
    emit(db, run.id, "run.cancellation_requested", {})
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
    run = models.AgentRun(
        project_id=failed.project_id, requested_by_id=user.id, prompt=failed.prompt,
        thread_id=f"project:{failed.project_id}:run:{uuid4()}", retry_of_id=failed.id,
    )
    db.add(run); db.flush(); enqueue(db, "run.start", run.id); db.commit(); db.refresh(run)
    return run


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
async def stream_run_events(run_id: str, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    run = db.get(models.AgentRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    require_project(db, user, run.project_id)

    async def events():
        sequence = 0
        while True:
            with SessionLocal() as event_db:
                rows = event_db.query(models.ToolEvent).filter(
                    models.ToolEvent.run_id == run_id, models.ToolEvent.sequence > sequence
                ).order_by(models.ToolEvent.sequence).all()
                status_value = event_db.get(models.AgentRun, run_id).status
            for row in rows:
                sequence = row.sequence
                yield f"data: {json.dumps({'sequence': row.sequence, 'type': row.event_type, 'payload': row.payload})}\n\n"
            if status_value in {"succeeded", "failed", "cancelled", "expired", "interrupted", "awaiting_approval"} and not rows:
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(events(), media_type="text/event-stream")


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
    run.status = "queued"
    enqueue(db, "action.resume", run.id, {"action_id": action.id, "decision": payload.decision})
    emit(db, run.id, f"approval.{action.status}", {"action_id": action.id, "decided_by": user.id})
    audit(db, f"action.{action.status}", user.id, action.project_id, {"action_id": action.id}, risk_class=action.risk_class, support_id=run.support_id, result=action.status)
    db.commit(); db.refresh(run)
    return run


@app.post("/projects/{project_id}/chat")
async def chat(project_id: int, payload: schemas.ChatRequest, request: Request, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    pending = db.query(models.PendingAction).filter(
        models.PendingAction.project_id == project_id,
        models.PendingAction.status == "pending",
        models.PendingAction.expires_at > datetime.now(timezone.utc),
    ).first()
    if pending:
        raise HTTPException(status_code=409, detail="Resolve the pending action before sending another message")
    interaction = models.Interaction(project_id=project_id, role="user", content=payload.message)
    db.add(interaction)
    audit(db, "agent.request", user.id, project_id, {})
    db.commit()
    history = db.query(models.Interaction).filter(
        models.Interaction.project_id == project_id,
        models.Interaction.id < interaction.id,
    ).order_by(models.Interaction.created_at.desc()).limit(20).all()[::-1]
    agent = await project_agent(request, db, project)
    thread_id = f"project:{project_id}"

    async def events():
        response_text = ""
        try:
            async for chunk in agent.stream(payload.message, thread_id, history):
                response_text += chunk
                yield chunk
            call = await agent.pending_call(thread_id)
            if call:
                preview = agent.preview(call)
                action = models.PendingAction(
                    project_id=project_id,
                    requested_by_id=user.id,
                    thread_id=thread_id,
                    tool_call_id=call["id"],
                    tool_name=call["name"],
                    arguments=call["args"],
                    preview=preview,
                    risk_class=agent.RISK_CLASSES[call["name"]],
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.action_expiry_minutes),
                )
                with SessionLocal() as action_db:
                    action_db.add(action)
                    action_db.flush()
                    audit(action_db, "agent.action_requested", user.id, project_id, {"action_id": action.id, "tool": action.tool_name})
                    action_db.commit()
                    action_id = action.id
                yield "\n_ACTION_PENDING_||" + json.dumps({"id": action_id, "tool": call["name"], "risk_class": agent.RISK_CLASSES[call["name"]], "preview": preview}) + "\n"
        except Exception:
            yield "\n\nThe agent could not complete this request."
        finally:
            with SessionLocal() as save_db:
                save_db.add(models.Interaction(project_id=project_id, role="agent", content=response_text))
                save_db.commit()

    return StreamingResponse(events(), media_type="text/plain")


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
        raise HTTPException(status_code=409, detail="Action is no longer pending")
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
    return {"path": path, "content": Workspace(project.workspace_slug).read_file(path)}


@app.get("/projects/{project_id}/workspace/commits")
def workspace_commits(project_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    return Workspace(project.workspace_slug).history()


@app.get("/projects/{project_id}/workspace/diff")
def workspace_diff(project_id: int, old: str, new: str = "HEAD", user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    return {"diff": Workspace(project.workspace_slug).diff(old, new)}


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
    report = validate_module(module_path)
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
    validation = db.query(models.ValidationRun).filter(
        models.ValidationRun.artifact_id == artifact.id, models.ValidationRun.status == "passed"
    ).order_by(models.ValidationRun.created_at.desc()).first()
    if not validation:
        raise HTTPException(status_code=409, detail="Only a passed immutable validation artifact can be deployed")
    if not instance.deployment_config_encrypted:
        raise HTTPException(status_code=409, detail="Configure the deployment bridge or Odoo.sh repository first")
    if instance.environment == "production":
        staged = db.query(models.Deployment).filter(
            models.Deployment.artifact_id == artifact.id, models.Deployment.environment == "staging",
            models.Deployment.status == "succeeded",
        ).first()
        if not staged or project.phase not in {"uat", "ready_for_production"}:
            raise HTTPException(status_code=409, detail="The exact artifact must pass staging and UAT before production")
    deployment = models.Deployment(
        project_id=project_id, instance_id=instance.id, artifact_id=artifact.id, validation_id=validation.id,
        environment=instance.environment, requested_by_id=user.id, rollback_plan=payload.rollback_plan,
    )
    db.add(deployment); db.flush()
    audit(db, "deployment.requested", user.id, project_id, {"deployment_id": deployment.id, "artifact_digest": artifact.digest}, risk_class="E", result="pending_approval")
    db.commit(); db.refresh(deployment)
    return deployment


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
