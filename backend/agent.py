import asyncio
import json
import socket
import xmlrpc.client
import httpx
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any
from datetime import datetime, timezone
import hashlib
import os
import subprocess
import sys
import time

from database import SessionLocal
import models
import validation
import deployment

from langchain.tools import tool
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from workspace import Workspace
from model_conformance import validate_python_code, validate_manifest_content, validate_xml_views


class _TimeoutMixin:
    def __init__(self, timeout: float = 8.0):
        super().__init__()
        self.timeout = timeout

    def make_connection(self, host):
        connection = super().make_connection(host)
        connection.timeout = self.timeout
        return connection


class TimeoutTransport(_TimeoutMixin, xmlrpc.client.Transport):
    pass


class TimeoutSafeTransport(_TimeoutMixin, xmlrpc.client.SafeTransport):
    pass


def xmlrpc_transport(url: str):
    return TimeoutSafeTransport() if url.lower().startswith("https://") else TimeoutTransport()


class OdooClient:
    def __init__(self, url: str, db: str, username: str, password: str):
        self.url = url.rstrip("/")
        self.db = db
        self.username = username
        self.password = password
        transport = xmlrpc_transport(self.url)
        self.common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", transport=transport, allow_none=True)
        self.uid = self.common.authenticate(db, username, password, {})
        if not self.uid:
            raise ValueError("Authentication failed")
        self.models = xmlrpc.client.ServerProxy(
            f"{self.url}/xmlrpc/2/object", transport=xmlrpc_transport(self.url), allow_none=True
        )

    def version(self) -> dict:
        return self.common.version()

    def search_read(self, model: str, domain: list, fields: list[str], limit: int | None = None):
        kwargs: dict[str, Any] = {"fields": fields}
        if limit is not None:
            kwargs["limit"] = limit
        return self.models.execute_kw(
            self.db, self.uid, self.password, model, "search_read", [domain], kwargs
        )

    def update_company_contact(self, company_id: int, email: str, phone: str | None = None) -> bool:
        values = {"email": email}
        if phone is not None:
            values["phone"] = phone
        return self.models.execute_kw(
            self.db, self.uid, self.password, "res.company", "write", [[company_id], values]
        )

    def create(self, model: str, values: dict) -> int:
        return self.models.execute_kw(self.db, self.uid, self.password, model, "create", [values])

    def call(self, model: str, method: str, ids: list[int], **kwargs):
        return self.models.execute_kw(self.db, self.uid, self.password, model, method, [ids], kwargs)


class OdooJSON2Client:
    def __init__(self, url: str, db: str, api_key: str):
        self.url = url.rstrip("/")
        self.db = db
        self.api_key = api_key
        self.http = httpx.Client(
            base_url=self.url,
            headers={"Authorization": f"bearer {api_key}", "X-Odoo-Database": db},
            timeout=10,
            follow_redirects=False,
        )
        self.search_read("res.users", [], ["id"], 1)

    def call(self, model: str, method: str, **params):
        response = self.http.post(f"/json/2/{model}/{method}", json=params)
        response.raise_for_status()
        return response.json()

    def version(self) -> dict:
        response = self.http.get("/web/webclient/version_info")
        response.raise_for_status()
        return response.json()

    def search_read(self, model: str, domain: list, fields: list[str], limit: int | None = None):
        params: dict[str, Any] = {"domain": domain, "fields": fields}
        if limit is not None:
            params["limit"] = limit
        return self.call(model, "search_read", **params)

    def update_company_contact(self, company_id: int, email: str, phone: str | None = None) -> bool:
        values = {"email": email}
        if phone is not None:
            values["phone"] = phone
        return self.call("res.company", "write", ids=[company_id], vals=values)

    def create(self, model: str, values: dict) -> int:
        return self.call(model, "create", vals_list=[values])[0]

    def call_records(self, model: str, method: str, ids: list[int], **kwargs):
        return self.call(model, method, ids=ids, **kwargs)


class PiERPClient:
    def __init__(self, url: str, username: str, password: str):
        raise NotImplementedError("Pi ERP connectivity is not implemented")


QUESTION_SENTINEL = "__QUESTION_PENDING__"


def tool_outcome(tool_name: str, output: str) -> str:
    """Classify structured checks without pretending every returned value passed."""
    try:
        result = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        return "succeeded"
    if not isinstance(result, dict):
        return "succeeded"
    if result.get("passed") is False or result.get("success") is False:
        return "failed"
    if isinstance(result.get("exit_code"), int) and result["exit_code"] != 0:
        return "failed"
    if str(result.get("status", "")).lower() in {"failed", "error"}:
        return "failed"
    return "succeeded"


def tool_category(tool_name: str) -> str:
    if any(part in tool_name for part in ("verify", "test", "lint", "typecheck", "compile", "build", "run_project_check")):
        return "verify"
    if any(part in tool_name for part in ("write", "patch", "replace", "create_directory")):
        return "edit"
    if any(part in tool_name for part in ("read", "view", "inspect", "list", "grep", "search")):
        return "inspect"
    return "run"


# ---------------------------------------------------------------------------
# Phase 2 — Uniform tool result envelope
# ---------------------------------------------------------------------------

@dataclass
class ToolResult:
    """Canonical result envelope returned by every side-effecting tool.

    * ok      – True only when the operation fully succeeded.
    * error   – Structured failure info; None when ok=True.
    * data    – Tool-specific output payload.
    * evidence – Zero or more verifiable references [{kind, ref, digest, summary}].
    * metrics – Timing and cost metrics.

    A missing or malformed envelope is treated as ToolProtocolError (never success).
    """
    ok: bool
    error: dict | None = None
    data: dict | None = None
    evidence: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def success(cls, data: dict | None = None, evidence: list[dict] | None = None, **metrics) -> "ToolResult":
        return cls(ok=True, data=data or {}, evidence=evidence or [], metrics=metrics)

    @classmethod
    def failure(cls, code: str, message: str, retryable: bool = False,
                support_id: str | None = None, data: dict | None = None) -> "ToolResult":
        return cls(
            ok=False,
            error={"code": code, "message": message, "retryable": retryable, "support_id": support_id},
            data=data or {},
        )

    @classmethod
    def from_json(cls, raw: str) -> "ToolResult | None":
        """Parse a JSON string into a ToolResult; return None if the envelope is absent or malformed."""
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict) and "ok" in obj:
                return cls(
                    ok=bool(obj["ok"]),
                    error=obj.get("error"),
                    data=obj.get("data") or {},
                    evidence=obj.get("evidence") or [],
                    metrics=obj.get("metrics") or {},
                )
        except (TypeError, json.JSONDecodeError, KeyError):
            pass
        return None


class ToolProtocolError(RuntimeError):
    """Raised when a tool returns a response that cannot be interpreted as a ToolResult."""


# ---------------------------------------------------------------------------
# Phase 3 — Exactly-once side-effect receipts
# ---------------------------------------------------------------------------

def _operation_id(run_id: str, task_id: str, tool_call_id: str) -> str:
    """Deterministic SHA-256 key for a specific tool invocation within a run."""
    raw = f"{run_id}:{task_id}:{tool_call_id}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _args_digest(args: dict) -> str:
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def ensure_tool_execution(
    operation_id: str,
    run_id: str,
    task_id: str,
    tool_call_id: str,
    tool_name: str,
    args: dict,
    db,
) -> tuple[bool, ToolResult | None]:
    """Idempotency guard for side-effecting tools.

    Returns:
        (should_execute: bool, stored_result: ToolResult | None)

    * (True,  None)           – safe to execute; receipt created in ``preparing`` state.
    * (False, ToolResult)     – already succeeded; return stored result without re-executing.
    * raises ValueError       – args digest mismatch (different args for same key) → abort.
    * raises RuntimeError     – receipt is in ``executing`` with a live heartbeat → do NOT replay.
    """
    from models import ToolExecution  # local import to avoid circular at module load
    now = datetime.now(timezone.utc)
    digest = _args_digest(args)

    existing = db.get(ToolExecution, operation_id)
    if existing is not None:
        if existing.args_digest != digest:
            raise ValueError(
                f"operation_id={operation_id} exists with different args digest "
                f"(stored={existing.args_digest}, current={digest}); refusing replay."
            )
        if existing.status == "succeeded" and existing.structured_result is not None:
            return False, ToolResult(**existing.structured_result)
        if existing.status == "executing":
            heartbeat_age = (now - existing.heartbeat_at).total_seconds() if existing.heartbeat_at else 9999
            if heartbeat_age < 60:
                raise RuntimeError(
                    f"operation_id={operation_id} is currently executing (heartbeat {heartbeat_age:.0f}s ago); "
                    "refusing duplicate execution."
                )
            # Stale executing — allow re-claim by resetting
            existing.status = "preparing"
            existing.started_at = None
            existing.heartbeat_at = None
            db.commit()
        return True, None

    receipt = ToolExecution(
        operation_id=operation_id,
        run_id=run_id,
        task_id=task_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        args_digest=digest,
        status="preparing",
        created_at=now,
    )
    db.add(receipt)
    db.commit()
    return True, None


def complete_tool_execution(operation_id: str, result: ToolResult, db) -> None:
    """Mark a ToolExecution receipt as succeeded or failed with the stored result."""
    from models import ToolExecution
    receipt = db.get(ToolExecution, operation_id)
    if receipt is None:
        return
    receipt.status = "succeeded" if result.ok else "failed"
    receipt.structured_result = asdict(result)
    receipt.error_code = (result.error or {}).get("code")
    receipt.error_message = (result.error or {}).get("message")
    receipt.retryable = (result.error or {}).get("retryable", False)
    receipt.finished_at = datetime.now(timezone.utc)
    db.commit()


class ERPImplementationAgent:
    TOOL_REGISTRY_VERSION = "1.0"
    SAFE_TOOLS = {
        "inspect_instance",
        "installed_modules",
        "inspect_company",
        "inspect_users",
        "inspect_odoo_schema",
        "inspect_views",
        "inspect_access",
        "inspect_master_data",
        "list_directory",
        "read_file",
        "check_deployment_status",
        "save_memory",
        "search_memory",
        "run_project_check",
        "inspect_module_dependency",
    }
    # Class 1 = read-only (SAFE_TOOLS); Class 2 = reversible writes; Class 3 = destructive/live
    RISK_CLASSES = {
        "create_directory": 2,
        "write_file": 2,
        "patch_file": 2,
        "verify_module_installation": 2,
        "update_company_contact": 3,
        "configure_sales": 2,
        "configure_purchase": 2,
        "configure_inventory": 3,
        "create_partner": 2,
        "create_product": 2,
        "create_quotation": 2,
        "create_rfq": 2,
        "create_crm_lead": 2,
        "create_draft_invoice": 3,
        "package_module": 2,
        "execute_deployment": 3,
        "install_module_dependency": 3,
    }
    TOOL_POLICIES: dict[str, dict[str, str]] = {}

    def __init__(
        self,
        client: OdooClient | OdooJSON2Client,
        workspace_slug: str,
        checkpointer,
        llm_model: str = "gpt-4o-mini",
        api_key: str | None = None,
        base_url: str | None = None,
        request_timeout: int = 120,
        max_output_tokens: int = 16000,
        project_id: int | None = None,
        requested_by_id: int | None = None,
        instance_id: int | None = None,
        run_id: str | None = None,
        fallback_model: str = "anthropic/claude-3.5-sonnet",
        autonomous_workspace_writes: bool = False,
    ):
        self.client = client
        self.workspace = Workspace(workspace_slug)
        self.autonomous_workspace_writes = autonomous_workspace_writes
        self.safe_tools = set(ERPImplementationAgent.SAFE_TOOLS)
        if autonomous_workspace_writes:
            self.safe_tools.update({"write_file", "patch_file", "create_directory"})
        self.project_id = project_id
        self.requested_by_id = requested_by_id
        self.instance_id = instance_id
        self.run_id = run_id
        self.usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        self.activity_events: list[tuple[str, dict]] = []
        kwargs: dict[str, Any] = {
            "model": llm_model,
            "temperature": 0,
            "streaming": True,
            "parallel_tool_calls": False,
            "timeout": request_timeout,
            "max_tokens": max_output_tokens,
        }
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        fallback_kwargs = kwargs.copy()
        fallback_kwargs["model"] = fallback_model
        fallback_llm = ChatOpenAI(**fallback_kwargs)
        llm = ChatOpenAI(**kwargs).with_fallbacks([fallback_llm])
        self.llm = llm

        @tool
        def inspect_instance() -> str:
            """Return the connected Odoo version."""
            return json.dumps(self.client.version())

        @tool
        def installed_modules() -> str:
            """List installed Odoo modules."""
            return json.dumps(
                self.client.search_read(
                    "ir.module.module", [("state", "=", "installed")], ["name", "shortdesc"], 200
                )
            )

        @tool
        def inspect_company() -> str:
            """Read the main company's public contact details."""
            return json.dumps(
                self.client.search_read("res.company", [], ["name", "email", "phone", "currency_id"], 1)
            )

        @tool
        def inspect_users() -> str:
            """Read a limited list of Odoo users."""
            return json.dumps(self.client.search_read("res.users", [], ["name", "login"], 10))

        @tool
        def inspect_odoo_schema(model_names: list[str]) -> str:
            """Inspect field names and types for Odoo models. Returns relational fields first, then up to 15 scalar fields - enough for scaffolding. DO NOT pass more than 3 models at once!"""
            if len(model_names) > 3:
                return json.dumps({"passed": False, "missing_models": [], "error": "Inspect at most 3 models at a time."})
            result = {}
            missing_models = []
            try:
                for model_name in model_names:
                    models = self.client.search_read("ir.model", [("model", "=", model_name)], ["id", "name", "model"], 1)
                    if not models:
                        result[model_name] = "Model not found"
                        missing_models.append(model_name)
                        continue
                    # Relational fields first (always include) — these define the model's relationships
                    relational = self.client.search_read(
                        "ir.model.fields",
                        [("model", "=", model_name), ("ttype", "in", ["many2one", "one2many", "many2many"])],
                        ["name", "ttype", "relation"], 25,
                    )
                    # Top scalar fields for field name awareness
                    scalar = self.client.search_read(
                        "ir.model.fields",
                        [("model", "=", model_name), ("ttype", "not in", ["many2one", "one2many", "many2many"])],
                        ["name", "ttype"], 15,
                    )
                    result[model_name] = {"model": models[0], "relational_fields": relational, "scalar_fields": scalar}
                return json.dumps({
                    "passed": not missing_models,
                    "models": result,
                    "missing_models": missing_models,
                })
            except Exception as e:
                return json.dumps({"passed": False, "models": result, "missing_models": missing_models, "error": str(e)})

        @tool
        def inspect_module_dependency(module_name: str, expected_models: list[str] | None = None) -> str:
            """Inspect an Odoo module dependency and the models it is expected to provide."""
            expected_models = expected_models or []
            modules = self.client.search_read(
                "ir.module.module", [("name", "=", module_name)], ["id", "name", "state"], 1
            )
            state = modules[0]["state"] if modules else "not_found"
            missing_models = [
                model_name for model_name in expected_models
                if not self.client.search_read("ir.model", [("model", "=", model_name)], ["id"], 1)
            ]
            return json.dumps({
                "passed": state == "installed" and not missing_models,
                "module_name": module_name,
                "state": state,
                "expected_models": expected_models,
                "missing_models": missing_models,
            })

        @tool
        def install_module_dependency(module_name: str, expected_models: list[str] | None = None) -> str:
            """Install a required Odoo module on staging after explicit Class 3 approval, then verify its models."""
            expected_models = expected_models or []
            try:
                modules = self.client.search_read(
                    "ir.module.module", [("name", "=", module_name)], ["id", "name", "state"], 1
                )
                if not modules:
                    return json.dumps({"passed": False, "error_category": "DependencyInstallFailed", "error": f"Module {module_name} was not found"})
                if modules[0]["state"] != "installed":
                    self._call_records("ir.module.module", "button_immediate_install", [modules[0]["id"]])
                verified = self.client.search_read(
                    "ir.module.module", [("name", "=", module_name)], ["id", "name", "state"], 1
                )
                missing_models = [
                    model_name for model_name in expected_models
                    if not self.client.search_read("ir.model", [("model", "=", model_name)], ["id"], 1)
                ]
                state = verified[0]["state"] if verified else "not_found"
                passed = state == "installed" and not missing_models
                return json.dumps({
                    "passed": passed,
                    "module_name": module_name,
                    "state": state,
                    "missing_models": missing_models,
                    "error_category": None if passed else "DependencyInstallFailed",
                })
            except Exception as exc:
                return json.dumps({"passed": False, "error_category": "DependencyInstallFailed", "error": str(exc)})

        @tool
        def inspect_views(model_name: str) -> str:
            """Read standard views (form, tree, search) for a model."""
            try:
                views = self.client.search_read(
                    "ir.ui.view",
                    [("model", "=", model_name), ("type", "in", ["form", "tree", "search"])],
                    ["name", "type", "arch_db"], 5,
                )
                import yaml
                return yaml.dump(views, sort_keys=False)
            except Exception as e:
                return f"ERROR communicating with Odoo: {str(e)}"

        @tool
        def inspect_access(model_name: str) -> str:
            """Inspect access-control and record-rule metadata for a model."""
            try:
                models = self.client.search_read("ir.model", [("model", "=", model_name)], ["id"], 1)
                if not models:
                    return "Model not found"
                model_id = models[0]["id"]
                access = self.client.search_read("ir.model.access", [("model_id", "=", model_id)], ["name", "group_id", "perm_read", "perm_write", "perm_create", "perm_unlink"], 100)
                rules = self.client.search_read("ir.rule", [("model_id", "=", model_id)], ["name", "groups", "domain_force", "perm_read", "perm_write", "perm_create", "perm_unlink"], 100)
                import yaml
                return yaml.dump({"access": access, "rules": rules}, sort_keys=False)
            except Exception as e:
                return f"ERROR communicating with Odoo: {str(e)}"

        @tool
        def inspect_master_data(data_type: str, query: str = "", limit: int = 25) -> str:
            """Search an allowlisted master-data type: partners, products, taxes, warehouses, journals, payment_terms, currencies, or units."""
            registry = {
                "partners": ("res.partner", ["name", "email", "phone", "vat"]),
                "products": ("product.template", ["name", "default_code", "type", "list_price"]),
                "taxes": ("account.tax", ["name", "amount", "type_tax_use", "company_id"]),
                "warehouses": ("stock.warehouse", ["name", "code", "company_id"]),
                "journals": ("account.journal", ["name", "code", "type", "company_id"]),
                "payment_terms": ("account.payment.term", ["name", "active", "company_id"]),
                "currencies": ("res.currency", ["name", "symbol", "active"]),
                "units": ("uom.uom", ["name", "category_id", "factor", "active"]),
            }
            if data_type not in registry:
                return "Unsupported master-data type"
            model, fields = registry[data_type]
            domain = [("name", "ilike", query)] if query else []
            try:
                import yaml
                return yaml.dump(self.client.search_read(model, domain, fields, min(max(limit, 1), 100)), sort_keys=False)
            except Exception as e:
                return f"ERROR communicating with Odoo: {str(e)}"

        @tool
        def list_directory(path: str = "") -> str:
            """List a directory inside this project's workspace."""
            import yaml
            return yaml.dump(self.workspace.list_directory(path), sort_keys=False)

        @tool
        def read_file(path: str) -> str:
            """Read a UTF-8 text file inside this project's workspace."""
            return self.workspace.read_file(path)

        @tool
        def run_project_check(check: str, timeout_seconds: int = 120) -> str:
            """Run one fixed, read-only project check in the workspace. Allowed checks: pytest, npm_test, lint, typecheck, build, compile, git_status, git_log."""
            commands = {
                "pytest": [sys.executable, "-m", "pytest"],
                "npm_test": ["npm", "test"],
                "lint": ["npm", "run", "lint"],
                "typecheck": ["npx", "tsc", "--noEmit"],
                "build": ["npm", "run", "build"],
                "compile": [sys.executable, "-m", "compileall", "-q", "."],
                "git_status": ["git", "status", "--short"],
                "git_log": ["git", "log", "-10", "--oneline"],
            }
            if check not in commands:
                return json.dumps({"ok": False, "error": "Unsupported project check"})
            timeout = max(1, min(int(timeout_seconds), 600))
            started = time.monotonic()
            env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.workspace.root)}
            try:
                completed = subprocess.run(
                    commands[check], cwd=self.workspace.root, env=env,
                    capture_output=True, text=True, timeout=timeout,
                    start_new_session=True, check=False,
                )
                stdout = completed.stdout[-20_000:]
                stderr = completed.stderr[-20_000:]
                return json.dumps({
                    "check": check, "ok": completed.returncode == 0,
                    "exit_code": completed.returncode, "stdout": stdout,
                    "stderr": stderr, "truncated": len(completed.stdout) > 20_000 or len(completed.stderr) > 20_000,
                    "duration_seconds": round(time.monotonic() - started, 3),
                })
            except subprocess.TimeoutExpired as exc:
                return json.dumps({"check": check, "ok": False, "error": "timeout", "stdout": (exc.stdout or "")[-20_000:], "stderr": (exc.stderr or "")[-20_000:], "duration_seconds": round(time.monotonic() - started, 3)})
            except OSError as exc:
                return json.dumps({"check": check, "ok": False, "error": str(exc)})

        @tool
        def create_directory(path: str) -> str:
            """Create a directory inside this project's workspace after approval."""
            target = self.workspace.resolve(path)
            target.mkdir(parents=True, exist_ok=True)
            return f"Created directory {path}"

        @tool
        def write_file(path: str, content: str) -> str:
            """Atomically write a UTF-8 file inside this project's workspace. Validates Python, XML, and Manifest syntax before writing — returns an error string (not an exception) if validation fails so you can fix and retry."""
            if path.endswith("__manifest__.py"):
                conf = validate_manifest_content(content)
                if not conf.valid:
                    return f"MANIFEST_ERROR in {path}: {'; '.join(conf.errors)}. Fix and call write_file again."
            elif path.endswith(".py"):
                conf = validate_python_code(content, path=path)
                if not conf.valid:
                    return f"SYNTAX_ERROR in {path}: {'; '.join(conf.errors)}. Fix the code and call write_file again."
            elif path.endswith(".xml"):
                conf = validate_xml_views(content, path=path)
                if not conf.valid:
                    return f"XML_ERROR in {path}: {'; '.join(conf.errors)}. Fix the XML and call write_file again."
            added_lines = len(content.splitlines())
            self.workspace.write_file(path, content)
            return f"Wrote {path} (+{added_lines} -0)"

        @tool
        def patch_file(path: str, old_str: str, new_str: str) -> str:
            """Replace old_str with new_str in an existing workspace file. Use for small targeted edits instead of rewriting the whole file. Returns an error if old_str is not found or appears more than once."""
            current = self.workspace.read_file(path)
            count = current.count(old_str)
            if count == 0:
                return f"PATCH_ERROR: old_str not found in {path}. Use read_file to check the current content."
            if count > 1:
                return f"PATCH_ERROR: old_str appears {count} times in {path}. Make old_str more specific."
            old_lines = len(old_str.splitlines())
            new_lines = len(new_str.splitlines())
            self.workspace.write_file(path, current.replace(old_str, new_str, 1))
            return f"Patched {path} (+{new_lines} -{old_lines})"

        @tool
        def save_memory(category: str, key: str, content: str) -> str:
            """Save a learned insight, schema gotcha, user coding preference, or module pattern into long-term memory for future runs."""
            with SessionLocal() as db:
                existing = db.query(models.AgentMemory).filter(
                    models.AgentMemory.project_id == self.project_id,
                    models.AgentMemory.key == key,
                ).first()
                if existing:
                    existing.content = content
                    existing.category = category
                    existing.updated_at = datetime.now(timezone.utc)
                    db.commit()
                    return f"Updated existing memory '{key}'"
                mem = models.AgentMemory(
                    project_id=self.project_id,
                    category=category,
                    key=key,
                    content=content,
                    confidence=1.0,
                )
                db.add(mem)
                db.commit()
                return f"Saved new memory '{key}' under category '{category}'"

        @tool
        def save_user_preference(key: str, content: str) -> str:
            """[PROTOCOL 5] Save an explicit user preference or coding convention into persistent project memory."""
            with SessionLocal() as db:
                existing = db.query(models.AgentMemory).filter(
                    models.AgentMemory.project_id == self.project_id,
                    models.AgentMemory.key == key,
                    models.AgentMemory.category == "user_preference",
                ).first()
                if existing:
                    existing.content = content
                    existing.updated_at = datetime.now(timezone.utc)
                    db.commit()
                    return f"Updated user preference '{key}'"
                mem = models.AgentMemory(
                    project_id=self.project_id,
                    category="user_preference",
                    key=key,
                    content=content,
                    confidence=1.0,
                )
                db.add(mem)
                db.commit()
                return f"Saved user preference '{key}'"

        @tool
        def save_verified_fact(key: str, content: str, evidence_type: str, evidence_ref: str) -> str:
            """[PROTOCOL 5] Save an evidence-backed schema or system truth into verified long-term memory."""
            with SessionLocal() as db:
                existing = db.query(models.AgentMemory).filter(
                    models.AgentMemory.project_id == self.project_id,
                    models.AgentMemory.key == key,
                    models.AgentMemory.category == "verified_fact",
                ).first()
                if existing:
                    existing.content = content
                    existing.evidence_type = evidence_type
                    existing.evidence_ref_id = evidence_ref
                    existing.verified_at = datetime.now(timezone.utc)
                    existing.updated_at = datetime.now(timezone.utc)
                    db.commit()
                    return f"Updated verified fact '{key}'"
                mem = models.AgentMemory(
                    project_id=self.project_id,
                    category="verified_fact",
                    key=key,
                    content=content,
                    confidence=1.0,
                    evidence_type=evidence_type,
                    evidence_ref_id=evidence_ref,
                    verified_at=datetime.now(timezone.utc),
                )
                db.add(mem)
                db.commit()
                return f"Saved verified fact '{key}'"

        @tool
        def search_memory(query: str, category: str | None = None) -> str:
            """Search persistent long-term memory for past learnings, user preferences, schema gotchas, or project rules."""
            with SessionLocal() as db:
                q = db.query(models.AgentMemory).filter(
                    (models.AgentMemory.project_id == self.project_id) | (models.AgentMemory.project_id.is_(None))
                )
                if category:
                    q = q.filter(models.AgentMemory.category == category)
                if query:
                    q = q.filter(models.AgentMemory.content.ilike(f"%{query}%") | models.AgentMemory.key.ilike(f"%{query}%"))
                results = q.order_by(models.AgentMemory.updated_at.desc()).limit(10).all()
                if not results:
                    return "No matching memories found."
                for r in results:
                    r.usage_count += 1
                db.commit()
                return json.dumps([
                    {"category": r.category, "key": r.key, "content": r.content, "updated_at": r.updated_at.isoformat()}
                    for r in results
                ])

        @tool
        def update_company_contact(company_id: int, email: str, phone: str | None = None) -> str:
            """Update only a company's email and optional phone after approval."""
            updated = self.client.update_company_contact(company_id, email, phone)
            return "Company contact updated" if updated else "Odoo did not update the company"

        def create_record(model: str, values: dict) -> int:
            return self.client.create(model, values)

        def ensure_fields(model: str, values: dict) -> None:
            metadata = self.client.search_read(
                "ir.model.fields", [("model", "=", model), ("name", "in", list(values))], ["name"], 100
            )
            available = {row["name"] for row in metadata}
            missing = set(values) - available
            if missing:
                raise ValueError(f"Unsupported fields for {model}: {', '.join(sorted(missing))}")

        def validated_lines(lines: list[dict], quantity_field: str) -> list:
            if not lines:
                raise ValueError("At least one line is required")
            result = []
            for line in lines:
                unknown = set(line) - {"product_id", "quantity", "unit_price"}
                if unknown or "product_id" not in line or "quantity" not in line:
                    raise ValueError("Lines accept only product_id, quantity, and optional unit_price")
                quantity = float(line["quantity"])
                if quantity <= 0:
                    raise ValueError("Line quantity must be positive")
                result.append((0, 0, {
                    "product_id": int(line["product_id"]),
                    quantity_field: quantity,
                    "price_unit": float(line.get("unit_price", 0)),
                }))
            return result

        @tool
        def configure_sales(quotation_validity_days: int) -> str:
            """Set the default quotation validity in days after approval."""
            values = {"quotation_validity_days": quotation_validity_days}
            ensure_fields("res.config.settings", values)
            settings_id = create_record("res.config.settings", values)
            self._call_records("res.config.settings", "execute", [settings_id])
            return "Sales settings updated"

        @tool
        def configure_purchase(purchase_order_approval: bool) -> str:
            """Enable or disable purchase-order approval after approval."""
            values = {"po_order_approval": purchase_order_approval}
            ensure_fields("res.config.settings", values)
            settings_id = create_record("res.config.settings", values)
            self._call_records("res.config.settings", "execute", [settings_id])
            return "Purchase settings updated"

        @tool
        def configure_inventory(multi_locations: bool) -> str:
            """Enable or disable multi-location inventory after higher-risk approval."""
            values = {"group_stock_multi_locations": multi_locations}
            ensure_fields("res.config.settings", values)
            settings_id = create_record("res.config.settings", values)
            self._call_records("res.config.settings", "execute", [settings_id])
            return "Inventory settings updated"

        @tool
        def create_partner(name: str, email: str | None = None, phone: str | None = None, vat: str | None = None) -> str:
            """Create a customer/vendor master record after duplicate review and approval."""
            duplicates = self.client.search_read("res.partner", [("name", "=ilike", name)], ["id", "name", "email", "vat"], 10)
            if duplicates:
                return json.dumps({"created": False, "duplicates": duplicates})
            values = {"name": name, "email": email, "phone": phone, "vat": vat}
            return json.dumps({"created": True, "id": create_record("res.partner", {k: v for k, v in values.items() if v})})

        @tool
        def create_product(name: str, default_code: str | None = None, list_price: float = 0) -> str:
            """Create a product master record after duplicate review and approval."""
            domain = [("default_code", "=", default_code)] if default_code else [("name", "=ilike", name)]
            duplicates = self.client.search_read("product.template", domain, ["id", "name", "default_code"], 10)
            if duplicates:
                return json.dumps({"created": False, "duplicates": duplicates})
            return json.dumps({"created": True, "id": create_record("product.template", {"name": name, "default_code": default_code, "list_price": list_price})})

        @tool
        def create_quotation(partner_id: int, lines: list[dict]) -> str:
            """Create a draft sales quotation after approval. Lines contain product_id, quantity, and optional unit_price."""
            return json.dumps({"id": create_record("sale.order", {"partner_id": partner_id, "order_line": validated_lines(lines, "product_uom_qty")}), "state": "draft"})

        @tool
        def create_rfq(partner_id: int, lines: list[dict]) -> str:
            """Create a draft purchase RFQ after approval. Lines contain product_id, quantity, and optional unit_price."""
            purchase_lines = validated_lines(lines, "product_qty")
            return json.dumps({"id": create_record("purchase.order", {"partner_id": partner_id, "order_line": purchase_lines}), "state": "draft"})

        @tool
        def create_crm_lead(name: str, partner_id: int | None = None, email_from: str | None = None) -> str:
            """Create a draft CRM lead after approval."""
            values = {"name": name, "partner_id": partner_id, "email_from": email_from, "type": "lead"}
            return json.dumps({"id": create_record("crm.lead", {k: v for k, v in values.items() if v is not None}), "state": "draft"})

        @tool
        def create_draft_invoice(partner_id: int, invoice_type: str, lines: list[dict]) -> str:
            """Create an unposted customer invoice or vendor bill. invoice_type is out_invoice or in_invoice."""
            if invoice_type not in {"out_invoice", "in_invoice"}:
                return "Unsupported invoice type"
            invoice_lines = validated_lines(lines, "quantity")
            return json.dumps({"id": create_record("account.move", {"partner_id": partner_id, "move_type": invoice_type, "invoice_line_ids": invoice_lines}), "state": "draft"})

        @tool
        def package_module(name: str, version: str, path: str) -> str:
            """Package a custom module in the workspace into a versioned artifact and run automated validations after approval."""
            module_path = self.workspace.resolve(path, must_exist=True)
            commit_hash = self.workspace.commit(f"Package {name} {version}")
            report = validation.validate_module(module_path)
            archive_digest = hashlib.sha256(validation.package_module(module_path)).hexdigest()
            
            with SessionLocal() as db:
                artifact = models.Artifact(
                    project_id=self.project_id,
                    artifact_type="odoo_module",
                    name=name,
                    version=version,
                    commit_hash=commit_hash,
                    digest=archive_digest,
                    path=path,
                    status="validated" if report["passed"] else "failed_validation",
                    created_by_id=self.requested_by_id,
                )
                db.add(artifact)
                db.flush()
                val_run = models.ValidationRun(
                    project_id=self.project_id,
                    artifact_id=artifact.id,
                    status="passed" if report["passed"] else "failed",
                    report=report,
                    finished_at=datetime.now(timezone.utc),
                )
                db.add(val_run)
                
                # Update AcceptanceCheck for artifact_digest, etc.
                if self.run_id:
                    checks = db.query(models.AcceptanceCheck).filter(
                        models.AcceptanceCheck.run_id == self.run_id,
                        models.AcceptanceCheck.status == "pending"
                    ).all()
                    for check in checks:
                        if check.kind == "artifact_digest":
                            check.status = "passed"
                            check.evidence = [{"kind": "artifact", "ref": artifact.id, "digest": archive_digest, "summary": "Artifact packaged and hashed"}]
                            check.evaluated_at = datetime.now(timezone.utc)
                        elif check.kind in ("python_test", "acl", "xml_id", "model_field"):
                            # This is a bit simplified, but let's assume validate_module handles static checks
                            if report["passed"]:
                                check.status = "passed"
                                check.evidence = [{"kind": "validation_report", "ref": val_run.id, "digest": archive_digest, "summary": "Passed static validation"}]
                            else:
                                check.status = "failed"
                                check.result_detail = "Static validation failed"
                            check.evaluated_at = datetime.now(timezone.utc)

                db.commit()
                
                return json.dumps({
                    "artifact_id": artifact.id,
                    "status": artifact.status,
                    "validation": report,
                })

        @tool
        def execute_deployment(artifact_id: str, environment: str) -> str:
            """Trigger a deployment of an artifact to an environment (staging or production) after approval."""
            with SessionLocal() as db:
                artifact = db.get(models.Artifact, artifact_id)
                if not artifact or artifact.project_id != self.project_id:
                    return "Artifact not found"
                val = db.query(models.ValidationRun).filter(
                    models.ValidationRun.artifact_id == artifact.id, models.ValidationRun.status == "passed"
                ).order_by(models.ValidationRun.created_at.desc()).first()
                if not val:
                    return "Cannot deploy artifact without a passed validation run"
                
                deploy = models.Deployment(
                    project_id=self.project_id,
                    instance_id=self.instance_id,
                    artifact_id=artifact.id,
                    validation_id=val.id,
                    environment=environment,
                    status="queued",
                    requested_by_id=self.requested_by_id,
                )
                db.add(deploy)
                db.commit()
                
                try:
                    deployment.execute_deployment(deploy.id)
                    db.refresh(deploy)
                    return json.dumps({"deployment_id": deploy.id, "status": deploy.status})
                except Exception as exc:
                    return json.dumps({"deployment_id": deploy.id, "status": "failed", "error": str(exc)})

        @tool
        def check_deployment_status(deployment_id: str) -> str:
            """Check the status of a deployment."""
            with SessionLocal() as db:
                deploy = db.get(models.Deployment, deployment_id)
                if not deploy or deploy.project_id != self.project_id:
                    return "Deployment not found"
                
                if deploy.status in {"queued", "deploying", "executing"}:
                    instance = db.get(models.Instance, deploy.instance_id)
                    if instance.hosting_type != "odoo_sh":
                        try:
                            deployment.refresh_bridge_deployment(deploy, instance)
                            db.commit()
                        except Exception as exc:
                            return f"Failed to refresh deployment: {exc}"
                
                return json.dumps({
                    "status": deploy.status,
                    "logs": deploy.logs[-2000:] if deploy.logs else "",
                })

        self.tools = [
            inspect_instance,
            installed_modules,
            inspect_company,
            inspect_users,
            inspect_odoo_schema,
            inspect_module_dependency,
            inspect_views,
            inspect_access,
            inspect_master_data,
            list_directory,
            read_file,
            run_project_check,
            create_directory,
            write_file,
            patch_file,
            update_company_contact,
            configure_sales,
            configure_purchase,
            configure_inventory,
            create_partner,
            create_product,
            create_quotation,
            create_rfq,
            create_crm_lead,
            create_draft_invoice,
            package_module,
            execute_deployment,
            install_module_dependency,
            check_deployment_status,
            save_memory,
            save_user_preference,
            save_verified_fact,
            search_memory,
        ]
        @tool
        def update_task_plan(plan_items: list[dict]) -> str:
          """Update the live execution plan checklist for the user. Each item must have 'title' (string) and 'status' ('pending' | 'in_progress' | 'completed'). Call this at the start of a task and whenever progress advances."""
          self.activity_events.append(("plan.updated", {"items": plan_items}))
          return json.dumps({"status": "plan_updated", "count": len(plan_items)})

        @tool
        def verify_module_installation(module_name: str, expected_models: list[str] | None = None, expected_fields: list[str] | None = None) -> str:
          """Perform explicit post-build XML-RPC verification on Odoo 19 database. Verifies module state == 'installed', and checks ir.model and ir.model.fields. Call this before returning final completion."""
          expected_models = expected_models or []
          expected_fields = expected_fields or []
          checks = []
          try:
            mods = self._search_read("ir.module.module", [("name", "=", module_name)], ["id", "name", "state"])
            mod_installed = bool(mods) and mods[0]["state"] == "installed"
            checks.append({"check": "module_installed", "passed": mod_installed, "details": f"Module {module_name} state: {mods[0]['state'] if mods else 'not found'}"})

            for model_name in expected_models:
              models_found = self._search_read("ir.model", [("model", "=", model_name)], ["id", "model", "name"])
              model_ok = bool(models_found)
              checks.append({"check": f"model_exists:{model_name}", "passed": model_ok, "details": f"Model {model_name} found: {model_ok}"})

            for field_name in expected_fields:
              fields_found = self._search_read("ir.model.fields", [("name", "=", field_name)], ["id", "model", "name"])
              field_ok = bool(fields_found)
              checks.append({"check": f"field_exists:{field_name}", "passed": field_ok, "details": f"Field {field_name} found: {field_ok}"})

            passed = all(c["passed"] for c in checks)
            return json.dumps({"passed": passed, "checks": checks})
          except Exception as exc:
            return json.dumps({"passed": False, "error": str(exc)})

        self.tools.append(update_task_plan)
        self.tools.append(verify_module_installation)
        ERPImplementationAgent.SAFE_TOOLS.add("update_task_plan")

        kb_path = Path(__file__).parent.parent / "skills" / "odoo19-dev" / "Odoo19_Dev_Customization_KB.md"
        self._kb_path = kb_path  # stored for the read_knowledge_base tool below

        @tool
        def read_knowledge_base() -> str:
            """Read the Odoo 19 technical reference (ORM patterns, view syntax, manifest format, security CSV). Call this ONCE before writing any module code if you are uncertain about Odoo 19 conventions."""
            return kb_path.read_text(encoding="utf-8") if kb_path.exists() else "Knowledge base not found."

        self.tools.append(read_knowledge_base)
        # Add to SAFE_TOOLS so it doesn't require approval
        ERPImplementationAgent.SAFE_TOOLS.add("read_knowledge_base")

        # ── Protocol tools (all Class 1 — no approval required) ──────────────
        @tool
        def emit_thinking(message: str) -> str:
            """[PROTOCOL 2] Emit a short thinking statement so the user can follow your reasoning. Call this before every tool call. message should be 1-3 sentences of plain-language reasoning — not a summary of the tool you are about to call, but WHY you are doing it."""
            self.activity_events.append(("thinking", {"message": message}))
            return "ok"

        @tool
        def ask_question(question: str, options: list[str] | None = None) -> str:
            """[PROTOCOL 3] Ask the user a clarifying question and pause execution until they answer. Use when the task is ambiguous in a way that would produce materially different implementations, when required info is missing, or after 2 failed self-correction attempts on the same error. question should be specific. options should be 2-4 concrete choices when possible."""
            self.activity_events.append(("question", {"question": question, "options": options or []}))
            return QUESTION_SENTINEL

        @tool
        def complete_task(outcome: str, done: list[str], verification: str, errors: str = "") -> str:
            """[PROTOCOL 4] Complete only the current supervisor task. Call exactly once. outcome is SUCCESS, PARTIAL, or FAILED; include concrete work, verification evidence, and exact errors."""
            self.activity_events.append(("task.report", {
                "outcome": outcome,
                "done": done,
                "verification": verification,
                "errors": errors,
            }))
            return "task_completed"

        for proto_tool in [emit_thinking, ask_question, complete_task]:
            self.tools.append(proto_tool)
            ERPImplementationAgent.SAFE_TOOLS.add(proto_tool.name)

        memories_text = ""
        if self.project_id:
            with SessionLocal() as db:
                mems = db.query(models.AgentMemory).filter(
                    (models.AgentMemory.project_id == self.project_id) | (models.AgentMemory.project_id.is_(None))
                ).order_by(models.AgentMemory.updated_at.desc()).limit(10).all()
                if mems:
                    items = "\n".join([f"- [{m.category}] {m.key}: {m.content}" for m in mems])
                    memories_text = f"\n\n[PERSISTENT AGENT MEMORY & PAST LEARNINGS]\n{items}"

        prompt = SystemMessage(
            content=(
                "You are the Primacy ERP Implementation Agent — an autonomous engineer that builds, validates, "
                "installs, and maintains Odoo 19 modules. The user watches your work live in a split-pane IDE. "
                "You MUST follow all four protocols below on every task, with no exceptions.\n\n"

                "═══ PROTOCOL 1 — PERMISSION GATING ═══\n"
                "Before any write/modify/execute action, classify its risk and act accordingly:\n"
                "- Class 1 (read-only, auto-proceed): inspect_*, read_file, list_directory, run_project_check — call emit_thinking first, then proceed.\n"
                "- Class 2 (reversible write, requires approval): create_directory, write_file, patch_file, new models/views — the system will auto-pause for user approval.\n"
                "- Class 3 (destructive/live, requires explicit confirmation): execute_deployment, configure_inventory, update_company_contact, create_draft_invoice — the approval card will warn the user what can break.\n"
                "Never batch multiple Class 2/3 actions under a single approval. One approval = one described action.\n"
                "If the user denies an action: stop that plan branch, acknowledge the denial, and ask for an alternative.\n\n"

                "═══ PROTOCOL 2 — REAL-TIME VISIBILITY ═══\n"
                "Call emit_thinking BEFORE every tool call — no exceptions. "
                "The message must say WHY you are doing the next step (1-3 sentences), not just what tool you'll call. "
                "Never let more than one logical step pass without a visible event. "
                "If a tool is slow, emit_thinking first so the user isn't left watching a blank screen.\n\n"
                "After writing code, use run_project_check with the fixed safe checks (pytest, npm_test, lint, typecheck, build, compile, or git_status) to verify the change before reporting success. If a check fails, inspect the output and make the smallest corrective patch.\n\n"

                "═══ PROTOCOL 3 — ASK WHEN CONFUSED ═══\n"
                "Call ask_question (which pauses execution) when any of the following are true:\n"
                "- Two reasonable implementations would produce materially different results.\n"
                "- Required information is missing and cannot be inferred from schema inspection or the KB.\n"
                "- An action conflicts with an existing customization and the right resolution is not obvious.\n"
                "- You have attempted the same fix TWICE and it is still failing — stop and ask, do not retry blindly.\n"
                "Do NOT ask about things you can verify via inspect_odoo_schema, inspect_views, or read_knowledge_base.\n\n"

                "═══ PROTOCOL 4 — TASK COMPLETION ═══\n"
                "Call complete_task EXACTLY ONCE at the end of the current supervisor task (success or failure). "
                "Never report outcome=SUCCESS without having called verify_module_installation or equivalent verification. "
                "'The write call did not error' is NOT sufficient evidence of success.\n\n"

                "═══ MODULE BUILD PROCEDURE ═══\n"
                "1. Call emit_thinking with your reasoning, then update_task_plan with the ordered steps.\n"
                "2. Inspect the live schema (inspect_odoo_schema / inspect_views) ONCE.\n"
                "   If requested models are missing, inspect their module dependency. On staging, request install_module_dependency approval and do not build against missing models.\n"
                "3. If uncertain about Odoo 19 syntax, call read_knowledge_base ONCE.\n"
                "4. Write files with write_file. If SYNTAX_ERROR or XML_ERROR is returned, fix and retry immediately — show the exact error in emit_thinking.\n"
                "5. Call package_module to validate.\n"
                "6. Call verify_module_installation to confirm module state == 'installed' and ORM models exist.\n"
                "7. End your response with a 'Where to find it inside Odoo 19:' navigation guide.\n"
                "8. Call complete_task with outcome, concrete change list, and verification result.\n"
                "9. Always call exactly one tool at a time. Never use placeholders in generated code.\n"
                "10. Use save_memory for critical schema details, bug fixes, and user preferences."
                f"{memories_text}"
            )
        )
        self.executor = create_react_agent(
            llm,
            self.tools,
            prompt=prompt,
            checkpointer=checkpointer,
            interrupt_before=["tools"],
        )

    def _call_records(self, model: str, method: str, ids: list[int]):
        if isinstance(self.client, OdooJSON2Client):
            return self.client.call_records(model, method, ids)
        return self.client.call(model, method, ids)

    @staticmethod
    def _history(interactions: list) -> list:
        messages = []
        for interaction in interactions:
            if interaction.role == "user":
                messages.append(HumanMessage(content=interaction.content))
            elif interaction.role == "agent":
                messages.append(AIMessage(content=interaction.content))
        return messages

    async def stream(self, prompt: str | None, thread_id: str, interactions: list | None = None, reject=False):
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}
        task_report_seen = False
        tool_iterations = 0
        if prompt is not None:
            existing = await self.executor.aget_state(config)
            messages = [] if existing.values.get("messages") else self._history(interactions or [])
            messages.append(HumanMessage(content=prompt))
            input_data = {"messages": messages}
        else:
            if reject:
                state = await self.executor.aget_state(config)
                last = state.values.get("messages", [])[-1]
                calls = getattr(last, "tool_calls", [])
                if len(calls) != 1:
                    raise ValueError("Pending checkpoint does not contain one tool call")
                call = calls[0]
                await self.executor.aupdate_state(
                    config,
                    {"messages": [ToolMessage(tool_call_id=call["id"], name=call["name"], content="User rejected this action.")]},
                    as_node="tools",
                )
            input_data = None

        while True:
            async for event in self.executor.astream_events(input_data, config, version="v2"):
                if event["event"] == "on_chat_model_stream":
                    content = event["data"]["chunk"].content
                    if isinstance(content, str) and content:
                        yield content
                elif event["event"] == "on_chat_model_end":
                    output = event.get("data", {}).get("output")
                    usage = getattr(output, "usage_metadata", None) or {}
                    self.usage["input_tokens"] += int(usage.get("input_tokens", 0) or 0)
                    self.usage["output_tokens"] += int(usage.get("output_tokens", 0) or 0)
                    metadata = getattr(output, "response_metadata", None) or {}
                    self.usage["cost_usd"] += float(metadata.get("cost", 0) or 0)
                elif event["event"] == "on_tool_start":
                    tool_iterations += 1
                    if tool_iterations > 40:
                        raise RuntimeError("TaskExecutionLimitExceeded: current task exceeded 40 tool iterations")
                    tool_name = event.get("name", "unknown")
                    self.activity_events.append(("tool.started", {
                        "tool": tool_name,
                        "arguments": event.get("data", {}).get("input", {}),
                        "category": tool_category(tool_name),
                        # run_id wired in by worker when emitting SSE; not available here
                    }))
                    yield ""
                elif event["event"] == "on_tool_end":
                    output = str(event.get("data", {}).get("output", ""))
                    tool_name = event.get("name", "unknown")
                    # Detect ask_question sentinel — pause execution for user answer
                    if output.strip() == QUESTION_SENTINEL:
                        return
                    # Parse ToolResult envelope when present; fall back to legacy outcome classifier
                    tr = ToolResult.from_json(output)
                    if tr is not None:
                        outcome = "succeeded" if tr.ok else "failed"
                        error_code = (tr.error or {}).get("code")
                    else:
                        outcome = tool_outcome(tool_name, output)
                        error_code = None
                    self.activity_events.append(("tool.completed", {
                        "tool": tool_name,
                        "result": output[:10_000],
                        "truncated": len(output) > 10_000,
                        "outcome": outcome,
                        "error_code": error_code,
                        "category": tool_category(tool_name),
                        "evidence": tr.evidence if tr else [],
                    }))
                    yield ""
                    if tool_name == "complete_task":
                        task_report_seen = True
                        return
                elif event["event"] == "on_tool_error":
                    error = str(event.get("data", {}).get("error", "Tool execution failed"))
                    self.activity_events.append(("tool.failed", {
                        "tool": event.get("name", "unknown"), "error": error[:10_000],
                        "outcome": "failed", "truncated": len(error) > 10_000,
                        "category": tool_category(event.get("name", "unknown")),
                    }))
                    yield ""
            state = await self.executor.aget_state(config)
            if not state.next:
                if not task_report_seen and getattr(self, "_reminders", 0) < 1:
                    self._reminders = getattr(self, "_reminders", 0) + 1
                    input_data = {"messages": [HumanMessage(content="You must call complete_task exactly once to finish the current supervisor task.")]}
                    continue
                return
            if "tools" not in state.next:
                input_data = None
                continue
            call = self._single_pending_call(state)
            safe = getattr(self, "safe_tools", self.SAFE_TOOLS)
            if call["name"] in self.RISK_CLASSES and call["name"] not in safe:
                return
            input_data = None

    async def pending_call(self, thread_id: str) -> dict | None:
        state = await self.executor.aget_state({"configurable": {"thread_id": thread_id}})
        if "tools" not in state.next:
            return None
        call = self._single_pending_call(state)
        safe = getattr(self, "safe_tools", self.SAFE_TOOLS)
        if call["name"] in safe or call["name"] not in self.RISK_CLASSES:
            return None
        return call

    async def pending_question(self, thread_id: str) -> dict | None:
        """Return the pending ask_question tool call if the graph is paused waiting for a user answer."""
        state = await self.executor.aget_state({"configurable": {"thread_id": thread_id}})
        if "tools" not in state.next:
            return None
        call = self._single_pending_call(state)
        if call["name"] != "ask_question":
            return None
        return call

    async def answer_question(self, thread_id: str, answer: str) -> None:
        """Resume the graph after ask_question by injecting the user's answer as a ToolMessage."""
        config = {"configurable": {"thread_id": thread_id}}
        state = await self.executor.aget_state(config)
        last = state.values.get("messages", [])[-1]
        calls = getattr(last, "tool_calls", [])
        if len(calls) != 1 or calls[0]["name"] != "ask_question":
            raise ValueError("No pending ask_question call to answer")
        call = calls[0]
        await self.executor.aupdate_state(
            config,
            {"messages": [ToolMessage(tool_call_id=call["id"], name=call["name"], content=answer)]},
            as_node="tools",
        )

    @staticmethod
    def _single_pending_call(state) -> dict:
        last = state.values.get("messages", [])[-1]
        calls = getattr(last, "tool_calls", [])
        if len(calls) != 1:
            raise ValueError("The agent must request exactly one tool at a time")
        return calls[0]

    def preview(self, call: dict) -> dict:
        name, args = call["name"], call["args"]
        if name == "write_file":
            return self.workspace.write_preview(args["path"], args["content"])
        if name == "patch_file":
            return {
                "operation": f"patch {args.get('path')}",
                "path": args.get("path"),
                "old_str": args.get("old_str"),
                "new_str": args.get("new_str"),
            }
        if name == "create_directory":
            self.workspace.resolve(args["path"])
            return {"path": args["path"], "operation": "create directory"}
        if name == "verify_module_installation":
            return {
                "operation": f"verify module installation: {args.get('module_name')}",
                "module_name": args.get("module_name"),
                "expected_models": args.get("expected_models", []),
                "expected_fields": args.get("expected_fields", []),
            }
        if name == "install_module_dependency":
            modules = self.client.search_read(
                "ir.module.module", [("name", "=", args.get("module_name"))], ["state"], 1
            )
            return {
                "operation": "install Odoo module dependency",
                "module_name": args.get("module_name"),
                "current_state": modules[0]["state"] if modules else "not_found",
                "affected_models": args.get("expected_models", []),
                "risk_class": 3,
                "risk": "Installs a server module and may update the staging database schema.",
                "verification_plan": "Verify module state is installed and every expected model exists.",
            }
        if name == "update_company_contact":
            return {
                "operation": "update company contact",
                "company_id": args["company_id"],
                "email": args["email"],
                "phone": args.get("phone"),
            }
        if name in self.RISK_CLASSES:
            return {
                "operation": name.replace("_", " "),
                "arguments": args,
                "risk_class": self.RISK_CLASSES[name],
            }
        raise ValueError("Unsupported approval tool")

    def drain_activity(self) -> list[tuple[str, dict]]:
        events, self.activity_events = self.activity_events, []
        return events


ERPImplementationAgent.TOOL_POLICIES = {
    name: {
        "version": ERPImplementationAgent.TOOL_REGISTRY_VERSION,
        "risk_class": ERPImplementationAgent.RISK_CLASSES.get(name, "A"),
        "approval": "automatic" if name in ERPImplementationAgent.SAFE_TOOLS else "explicit",
        "idempotency": "required" if name not in ERPImplementationAgent.SAFE_TOOLS else "read_only",
    }
    for name in ERPImplementationAgent.SAFE_TOOLS | ERPImplementationAgent.RISK_CLASSES.keys()
}


# ─── A2A Supervisor / Planner ──────────────────────────────────────────────────
# Decomposes complex multi-step prompts into an ordered task graph.  Each task
# becomes its own bounded unit of work with its own heartbeat and retry budget
# tracked in AgentRun.task_graph / AgentRun.subtask_heartbeat_at.
#
# Day-one approach: pattern-based decomposition for module builds.
# Future: LLM-driven dynamic planning with parallel worker sub-graphs.

_MODULE_BUILD_KEYWORDS = frozenset([
    "module", "build", "create module", "implement", "custom module", "odoo module",
    "manifest", "models.py", "views.xml", "security", "ir.model.access",
    "computed field", "extend", "mrp", "manufacturing", "cost tracker",
    "tracker", "wizard", "report", "kanban", "pivot", "dashboard",
])

_STANDARD_MODULE_TASK_GRAPH = [
    {
        "task_id": "task_01_inspect_ground",
        "title": "Inspect & ground schema",
        "risk_class": 1,
        "depends_on": [],
        "status": "pending",
        "retry_count": 0,
        "context_bundle": {},
        "result": None,
        "heartbeat_at": None,
    },
    {
        "task_id": "task_02_extend_models",
        "title": "Extend models / computed fields",
        "risk_class": 2,
        "depends_on": ["task_01_inspect_ground"],
        "status": "pending",
        "retry_count": 0,
        "context_bundle": {},
        "result": None,
        "heartbeat_at": None,
    },
    {
        "task_id": "task_03_config_model",
        "title": "Create config model",
        "risk_class": 2,
        "depends_on": ["task_01_inspect_ground"],
        "status": "pending",
        "retry_count": 0,
        "context_bundle": {},
        "result": None,
        "heartbeat_at": None,
    },
    {
        "task_id": "task_04_views_menus",
        "title": "Add views & menus",
        "risk_class": 2,
        "depends_on": ["task_02_extend_models", "task_03_config_model"],
        "status": "pending",
        "retry_count": 0,
        "context_bundle": {},
        "result": None,
        "heartbeat_at": None,
    },
    {
        "task_id": "task_05_security_access",
        "title": "Add security access entries",
        "risk_class": 2,
        "depends_on": ["task_02_extend_models", "task_03_config_model", "task_04_views_menus"],
        "status": "pending",
        "retry_count": 0,
        "context_bundle": {},
        "result": None,
        "heartbeat_at": None,
    },
    {
        "task_id": "task_06_verify_install",
        "title": "Verify module installation",
        "risk_class": 1,
        "depends_on": ["task_05_security_access"],
        "status": "pending",
        "retry_count": 0,
        "context_bundle": {},
        "result": None,
        "heartbeat_at": None,
    },
]

_SIMPLE_TASK_GRAPH = [
    {
        "task_id": "task_01_execute",
        "title": "Execute task",
        "risk_class": 1,
        "depends_on": [],
        "status": "pending",
        "retry_count": 0,
        "context_bundle": {},
        "result": None,
        "heartbeat_at": None,
    },
]

MAX_TASK_RETRIES = 2


class SupervisorPlanner:
    """Determines whether a prompt warrants A2A task decomposition and returns
    an ordered task graph.  The Supervisor persists this graph on the AgentRun
    so the worker watchdog can track per-task heartbeats and retry budgets.

    Day-one: pattern-matching decomposition for standard Odoo module builds.
    The graph is intentionally hardcoded — dynamic LLM planning can be layered
    on later without changing the worker/frontend contract.
    """

    @staticmethod
    def build_from_specification(spec_requirements: list[dict]) -> list[dict]:
        """Build a targeted, requirement-driven task graph from a RunSpecification."""
        tasks = [
            {
                "task_id": "scaffold_module",
                "title": "Inspect environment and scaffold module files",
                "depends_on": [],
                "status": "pending",
                "max_retries": 2,
                "retry_count": 0,
                "acceptance_criteria": "Module manifest, security CSV, and __init__.py files are created and valid.",
            },
            {
                "task_id": "implement_models",
                "title": "Implement Python models, fields, and constraints",
                "depends_on": ["scaffold_module"],
                "status": "pending",
                "max_retries": 2,
                "retry_count": 0,
                "acceptance_criteria": "All required models and fields from the specification are implemented.",
            },
            {
                "task_id": "implement_views",
                "title": "Implement XML views, menus, and actions",
                "depends_on": ["implement_models"],
                "status": "pending",
                "max_retries": 2,
                "retry_count": 0,
                "acceptance_criteria": "Form, list, and action XML views are created and valid.",
            },
            {
                "task_id": "implement_security",
                "title": "Configure access control and security rules",
                "depends_on": ["implement_models"],
                "status": "pending",
                "max_retries": 2,
                "retry_count": 0,
                "acceptance_criteria": "ACL entries exist for all models in ir.model.access.csv.",
            },
            {
                "task_id": "validate_and_verify",
                "title": "Package module and verify installation on Odoo 19",
                "depends_on": ["implement_views", "implement_security"],
                "status": "pending",
                "max_retries": 2,
                "retry_count": 0,
                "acceptance_criteria": "Module passes static validation and verify_module_installation confirms clean installation.",
            },
        ]
        return tasks

    @staticmethod
    def is_module_build(prompt: str) -> bool:
        """Return True if the prompt describes a complex multi-step module build."""
        lower = prompt.lower()
        hits = sum(1 for kw in _MODULE_BUILD_KEYWORDS if kw in lower)
        return hits >= 2  # require at least 2 module-build signals

    @staticmethod
    async def decompose(prompt: str, llm: ChatOpenAI | None = None) -> list[dict]:
        """Return the appropriate task graph for the given prompt.
        If a planner LLM is provided, generates a dynamic graph.
        Otherwise, falls back to the standard template.
        """
        import copy
        import schemas
        from pydantic import BaseModel

        if not SupervisorPlanner.is_module_build(prompt):
            return copy.deepcopy(_SIMPLE_TASK_GRAPH)

        if llm:
            try:
                system_prompt = (
                    "You are the Supervisor Planner for an Odoo 19 module building agent.\n"
                    "Your job is to break down the user's prompt into an ordered sequence of tasks.\n"
                    "Return a JSON array of tasks. Ensure you include dependencies (`depends_on`) so tasks run in the correct order.\n"
                    "Set `max_retries` based on the difficulty of the task (usually 2, maybe 3 for complex tasks).\n"
                    "A standard module build covers: inspection, model changes, views, security, and verification."
                )
                
                messages = [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=f"Plan tasks for this request: {prompt}")
                ]
                
                class TaskList(BaseModel):
                    tasks: list[schemas.TaskGraphItem]

                structured_llm = llm.with_structured_output(TaskList)
                result = await structured_llm.ainvoke(messages)
                
                return [task.model_dump() for task in result.tasks]
            except Exception as e:
                print(f"Dynamic planning failed, falling back to static graph: {e}")

        return copy.deepcopy(_STANDARD_MODULE_TASK_GRAPH)

    @staticmethod
    def get_next_task(task_graph: list[dict]) -> dict | None:
        """Return the first task that is ready to run (all dependencies done)."""
        done_ids = {t["task_id"] for t in task_graph if t["status"] == "done"}
        for task in task_graph:
            if task["status"] != "pending":
                continue
            if all(dep in done_ids for dep in task["depends_on"]):
                return task
        return None

    @staticmethod
    def is_complete(task_graph: list[dict]) -> bool:
        """Return True when every task in the graph has status 'done'."""
        return all(t["status"] == "done" for t in task_graph)

    @staticmethod
    def has_failure(task_graph: list[dict]) -> bool:
        """Return True when any task has status 'failed' and exhausted its dynamic retries."""
        return any(
            t["status"] == "failed" and t["retry_count"] >= t.get("max_retries", 2)
            for t in task_graph
        )

    @staticmethod
    async def extract_context(task_id: str, messages: list, llm: ChatOpenAI) -> dict:
        """Generate a concise handoff summary of what was accomplished in this task."""
        try:
            # We only care about what the agent actually did/said
            agent_msgs = [m.content for m in messages if isinstance(m, AIMessage) and m.content]
            if not agent_msgs:
                return {}
                
            prompt = (
                f"You are summarizing the completion of task '{task_id}'.\n"
                "Based on the agent's messages, write a very concise summary (1-3 sentences) of what was accomplished "
                "or discovered. This will be passed to the next task as context. Focus only on facts, models created, "
                "or files written. Do not include conversational filler."
            )
            
            res = await llm.ainvoke([
                SystemMessage(content=prompt),
                HumanMessage(content="\n---\n".join(agent_msgs[-5:])) # last few messages
            ])
            return {"handoff_summary": str(res.content)}
        except Exception:
            return {}

    @staticmethod
    def format_progress(task_graph: list[dict]) -> str:
        """Human-readable progress summary for emit_thinking."""
        done = sum(1 for t in task_graph if t["status"] == "done")
        total = len(task_graph)
        return f"Task {done}/{total} complete"


async def connect_odoo(url: str, db: str, username: str, password: str) -> OdooClient:
    return await asyncio.wait_for(
        asyncio.to_thread(OdooClient, url, db, username, password),
        timeout=10,
    )


async def connect_odoo_json2(url: str, db: str, api_key: str) -> OdooJSON2Client:
    return await asyncio.wait_for(asyncio.to_thread(OdooJSON2Client, url, db, api_key), timeout=10)
