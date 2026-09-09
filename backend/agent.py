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
import re
import subprocess
import sys
import time
from urllib.parse import urlparse

from database import SessionLocal
import models
import validation
import deployment

from langchain_core.tools import tool
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

    def call(self, model: str, method: str, ids: list[int] | None = None, **kwargs):
        args = [ids] if ids is not None else []
        return self.models.execute_kw(self.db, self.uid, self.password, model, method, args, kwargs)

    def get_views(self, model: str, view_types: list[str] | None = None) -> list[dict]:
        types = view_types or ["form", "list", "search"]
        return self.search_read("ir.ui.view", [("model", "=", model), ("type", "in", types)], ["name", "type", "arch_db", "priority"], 20)

    def get_access_rules(self, model: str) -> list[dict]:
        models_list = self.search_read("ir.model", [("model", "=", model)], ["id"], 1)
        if not models_list:
            return []
        return self.search_read("ir.model.access", [("model_id", "=", models_list[0]["id"])], ["name", "group_id", "perm_read", "perm_write", "perm_create", "perm_unlink"], 100)

    def get_record_rules(self, model: str) -> list[dict]:
        models_list = self.search_read("ir.model", [("model", "=", model)], ["id"], 1)
        if not models_list:
            return []
        return self.search_read("ir.rule", [("model_id", "=", models_list[0]["id"])], ["name", "groups", "domain_force", "perm_read", "perm_write", "perm_create", "perm_unlink"], 100)

    def install_module(self, module_name: str) -> bool:
        modules = self.search_read("ir.module.module", [("name", "=", module_name)], ["id", "state"], 1)
        if not modules:
            raise ValueError(f"Module '{module_name}' not found")
        self.call("ir.module.module", "button_immediate_install", [modules[0]["id"]])
        return True

    def upgrade_module(self, module_name: str) -> bool:
        modules = self.search_read("ir.module.module", [("name", "=", module_name)], ["id", "state"], 1)
        if not modules:
            raise ValueError(f"Module '{module_name}' not found")
        self.call("ir.module.module", "button_immediate_upgrade", [modules[0]["id"]])
        return True


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

    def get_views(self, model: str, view_types: list[str] | None = None) -> list[dict]:
        types = view_types or ["form", "list", "search"]
        return self.search_read("ir.ui.view", [("model", "=", model), ("type", "in", types)], ["name", "type", "arch_db", "priority"], 20)

    def get_access_rules(self, model: str) -> list[dict]:
        models_list = self.search_read("ir.model", [("model", "=", model)], ["id"], 1)
        if not models_list:
            return []
        return self.search_read("ir.model.access", [("model_id", "=", models_list[0]["id"])], ["name", "group_id", "perm_read", "perm_write", "perm_create", "perm_unlink"], 100)

    def get_record_rules(self, model: str) -> list[dict]:
        models_list = self.search_read("ir.model", [("model", "=", model)], ["id"], 1)
        if not models_list:
            return []
        return self.search_read("ir.rule", [("model_id", "=", models_list[0]["id"])], ["name", "groups", "domain_force", "perm_read", "perm_write", "perm_create", "perm_unlink"], 100)

    def install_module(self, module_name: str) -> bool:
        modules = self.search_read("ir.module.module", [("name", "=", module_name)], ["id", "state"], 1)
        if not modules:
            raise ValueError(f"Module '{module_name}' not found")
        self.call("ir.module.module", "button_immediate_install", ids=[modules[0]["id"]])
        return True

    def upgrade_module(self, module_name: str) -> bool:
        modules = self.search_read("ir.module.module", [("name", "=", module_name)], ["id", "state"], 1)
        if not modules:
            raise ValueError(f"Module '{module_name}' not found")
        self.call("ir.module.module", "button_immediate_upgrade", ids=[modules[0]["id"]])
        return True


from pri_erp_adapter import PriERPAdapter, connect_pri_erp


class PiERPClient(PriERPAdapter):
    """Pri ERP client adapter for instance and hosting management."""
    def __init__(self, url: str, username: str | None = None, password: str | None = None, api_key: str | None = None, **kwargs):
        super().__init__(base_url=url, api_key=api_key, username=username, password=password, **kwargs)



QUESTION_SENTINEL = "__QUESTION_PENDING__"


def tool_outcome(tool_name: str, output: str) -> str:
    """Classify structured checks without pretending every returned value passed."""
    try:
        result = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        return "succeeded"
    if not isinstance(result, dict):
        return "succeeded"
    if result.get("ok") is False or result.get("passed") is False or result.get("success") is False:
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


def normalize_module_label(value: str) -> str:
    """Normalize technical and display labels for human-friendly module lookup."""
    normalized = re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()
    return normalized.replace(" centre", " center")


def module_label_matches(row: dict, query: str) -> bool:
    """Match a user query against an Odoo technical name or display label."""
    wanted = normalize_module_label(query)
    if not wanted:
        return True
    labels = [str(row.get("name") or ""), str(row.get("shortdesc") or "")]
    searchable = normalize_module_label(" ".join(labels))
    tokens = wanted.split()
    return wanted in searchable or all(token in searchable for token in tokens)


def resolve_module_matches(rows: list[dict], query: str) -> list[dict]:
    """Prefer an exact technical-name match, then return human-label matches."""
    normalized_query = normalize_module_label(query)
    matches = [row for row in rows if module_label_matches(row, query)]
    exact = [row for row in matches if normalize_module_label(str(row.get("name") or "")) == normalized_query]
    return exact or matches


def validate_inspection_url(url: str, connected_url: str):
    """Allow read-only URL inspection only on the configured ERP host."""
    target = urlparse((url or "").strip())
    connected = urlparse((connected_url or "").strip())
    if target.scheme not in {"http", "https"} or not target.hostname:
        raise ValueError("Inspection URL must be an absolute HTTP(S) URL")
    if target.hostname.casefold() != (connected.hostname or "").casefold():
        raise ValueError("Inspection URL must use the connected ERP host")
    if target.scheme != connected.scheme:
        raise ValueError("Inspection URL must use the connected ERP scheme")
    try:
        target_port = target.port
        connected_port = connected.port
    except ValueError as exc:
        raise ValueError("Inspection URL has an invalid port") from exc
    if target_port != connected_port:
        raise ValueError("Inspection URL must use the connected ERP port")
    return target


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
    TOOL_REGISTRY_VERSION = "2.0"
    from tool_registry import TOOL_REGISTRY

    SAFE_TOOLS = {
        name for name, entry in TOOL_REGISTRY.items()
        if not entry.requires_approval and not entry.is_write
    }
    RISK_CLASSES = {
        name: entry.risk_class for name, entry in TOOL_REGISTRY.items()
    }
    TOOL_POLICIES: dict[str, dict[str, str]] = {}

    def __init__(
        self,
        client: Any,
        workspace_slug: str,
        checkpointer,
        llm_model: str = "gpt-4o-mini",
        api_key: str | None = None,
        base_url: str | None = None,
        request_timeout: int = 120,
        max_output_tokens: int = 2048,
        project_id: int | None = None,
        requested_by_id: int | None = None,
        instance_id: int | None = None,
        run_id: str | None = None,
        fallback_model: str = "anthropic/claude-3.5-sonnet",
        autonomous_workspace_writes: bool = False,
        read_only: bool = False,
    ):
        self.client = client
        self.workspace = Workspace(workspace_slug, create=not read_only)
        self.autonomous_workspace_writes = autonomous_workspace_writes
        self.read_only = read_only
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
        def installed_modules(module_query: str | None = None) -> str:
            """List installed Odoo modules, optionally filtered by technical or human-facing name."""
            rows = self.client.search_read(
                "ir.module.module", [("state", "=", "installed")], ["name", "shortdesc"], 500
            )
            matches = rows if not module_query else [row for row in rows if module_label_matches(row, module_query)]
            result = [
                {**row, "technical_name": row.get("name"), "display_name": row.get("shortdesc") or row.get("name")}
                for row in matches
            ]
            if module_query:
                return json.dumps({"query": module_query, "count": len(result), "matches": result})
            return json.dumps(result)

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
            """Inspect an Odoo module by technical name or human-facing display name and its expected models."""
            expected_models = expected_models or []
            modules = self.client.search_read(
                "ir.module.module", [], ["id", "name", "shortdesc", "state"], 500
            )
            matches = [row for row in modules if module_label_matches(row, module_name)]
            resolved_matches = resolve_module_matches(modules, module_name)
            if len(matches) > 1 and len(resolved_matches) > 1:
                return json.dumps({
                    "passed": False,
                    "module_name": module_name,
                    "state": "ambiguous",
                    "candidates": [
                        {"technical_name": row.get("name"), "display_name": row.get("shortdesc") or row.get("name"), "state": row.get("state")}
                        for row in matches
                    ],
                    "expected_models": expected_models,
                    "missing_models": [],
                })
            module = resolved_matches[:1]
            resolved_name = module[0].get("name") if module else module_name
            state = module[0].get("state") if module else "not_found"
            missing_models = [
                model_name for model_name in expected_models
                if not self.client.search_read("ir.model", [("model", "=", model_name)], ["id"], 1)
            ]
            return json.dumps({
                "passed": state == "installed" and not missing_models,
                "module_name": module_name,
                "resolved_technical_name": resolved_name,
                "display_name": (module[0].get("shortdesc") if module else None),
                "state": state,
                "expected_models": expected_models,
                "missing_models": missing_models,
            })

        @tool
        def install_module_dependency(module_name: str, expected_models: list[str] | None = None) -> str:
            """Install a required Odoo module by technical or unique human-facing name on staging after explicit Class 3 approval."""
            expected_models = expected_models or []
            try:
                modules = self.client.search_read(
                    "ir.module.module", [], ["id", "name", "shortdesc", "state"], 500
                )
                matches = [row for row in modules if module_label_matches(row, module_name)]
                resolved_matches = resolve_module_matches(modules, module_name)
                if len(matches) > 1 and len(resolved_matches) > 1:
                    return json.dumps({
                        "passed": False,
                        "error_category": "AmbiguousModuleName",
                        "error": f"More than one module matches '{module_name}'; use a technical name.",
                        "candidates": [row.get("name") for row in matches],
                    })
                module = resolved_matches[:1]
                if not module:
                    return json.dumps({"passed": False, "error_category": "DependencyInstallFailed", "error": f"Module {module_name} was not found"})
                resolved_name = module[0]["name"]
                if module[0]["state"] != "installed":
                    self._call_records("ir.module.module", "button_immediate_install", [module[0]["id"]])
                verified = self.client.search_read(
                    "ir.module.module", [("name", "=", resolved_name)], ["id", "name", "state"], 1
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
                    "resolved_technical_name": resolved_name,
                    "state": state,
                    "missing_models": missing_models,
                    "error_category": None if passed else "DependencyInstallFailed",
                })
            except Exception as exc:
                return json.dumps({"passed": False, "error_category": "DependencyInstallFailed", "error": str(exc)})

        @tool
        def inspect_views(model_name: str) -> str:
            """Read standard Odoo 19 views (form, list, search) for a model."""
            try:
                views = self.client.search_read(
                    "ir.ui.view",
                    [("model", "=", model_name), ("type", "in", ["form", "list", "search"])],
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
        def inspect_url(url: str) -> str:
            """Inspect a page on the connected ERP host without following redirects or sending ERP credentials."""
            try:
                validate_inspection_url(url, self.client.url)
                response = httpx.get(
                    url,
                    timeout=15,
                    follow_redirects=False,
                    headers={"User-Agent": "Primacy-ERP-Agent/inspection"},
                )
                title_match = re.search(r"<title[^>]*>(.*?)</title>", response.text, re.IGNORECASE | re.DOTALL)
                title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else None
                text = re.sub(r"<[^>]+>", " ", response.text)
                text = re.sub(r"\s+", " ", text).strip()
                return json.dumps({
                    "url": str(response.url),
                    "status_code": response.status_code,
                    "content_type": response.headers.get("content-type", ""),
                    "title": title,
                    "text": text[:10_000],
                    "truncated": len(text) > 10_000,
                })
            except (ValueError, httpx.HTTPError) as exc:
                return json.dumps({"ok": False, "error": str(exc)})

        @tool
        def browser_snapshot(url: str) -> str:
            """Open a connected ERP page in a clean headless browser and report visible UI state."""
            try:
                validate_inspection_url(url, self.client.url)
                playwright_entry = Path(__file__).parent.parent / "frontend" / "node_modules" / "@playwright" / "test"
                if not playwright_entry.exists():
                    return json.dumps({"ok": False, "error": "Browser runtime is not installed; install frontend Playwright dependencies first."})
                script = r"""
const { chromium } = require(process.env.PRIMARY_PLAYWRIGHT);
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ javaScriptEnabled: true });
  const response = await page.goto(process.env.PRIMARY_URL, { waitUntil: 'domcontentloaded', timeout: 15000 });
  const result = {
    ok: true,
    url: page.url(),
    status_code: response ? response.status() : null,
    title: await page.title(),
    text: (await page.locator('body').innerText()).slice(0, 10000),
    links: await page.locator('a').evaluateAll(items => items.slice(0, 50).map(item => ({ text: (item.innerText || '').trim(), href: item.href }))),
    buttons: await page.locator('button').evaluateAll(items => items.slice(0, 50).map(item => (item.innerText || '').trim()).filter(Boolean)),
    forms: await page.locator('form').count()
  };
  process.stdout.write(JSON.stringify(result));
  await browser.close();
})().catch(error => { process.stdout.write(JSON.stringify({ ok: false, error: String(error) })); process.exitCode = 1; });
"""
                environment = {"PATH": os.environ.get("PATH", ""), "PRIMARY_PLAYWRIGHT": str(playwright_entry), "PRIMARY_URL": url}
                completed = subprocess.run(
                    ["node", "-e", script],
                    cwd=Path(__file__).parent.parent,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                if completed.stdout.strip():
                    return completed.stdout.strip()
                return json.dumps({"ok": False, "error": completed.stderr[-2000:] or "Browser inspection failed"})
            except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
                return json.dumps({"ok": False, "error": str(exc)})

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
            installed = self.client.search_read("ir.module.module", [("name", "=", name)], ["state"], 1)
            report = validation.validate_module_full(
                module_path,
                name,
                is_upgrade=bool(installed and installed[0].get("state") == "installed"),
            )
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
                    workspace_slug=self.workspace.root.name,
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
                        models.AcceptanceCheck.status != "passed"
                    ).all()
                    for check in checks:
                        if check.kind == "artifact_digest" and report["static"]["passed"]:
                            check.status = "passed"
                            check.evidence = [{"kind": "artifact", "ref": artifact.id, "digest": archive_digest, "summary": "Artifact packaged and hashed"}]
                            check.evaluated_at = datetime.now(timezone.utc)
                        elif check.task_id == "validate_and_verify":
                            runtime_ok = report["runtime"].get("ok") is True
                            tests_ok = int(report["runtime"].get("test_count", 0)) > 0
                            check.status = "passed" if runtime_ok and (check.kind != "business_scenario" or tests_ok) else "failed"
                            check.result_detail = "Disposable Odoo install/upgrade and tests passed" if check.status == "passed" else (report["runtime"].get("error") or "Runtime evidence did not satisfy this check")
                            check.evidence = [{"kind": "runtime_validation", "ref": val_run.id, "digest": archive_digest, "summary": check.result_detail}]
                            check.evaluated_at = datetime.now(timezone.utc)

                db.commit()
                
                return json.dumps({
                    "artifact_id": artifact.id,
                    "status": artifact.status,
                    "validation": report,
                })

        @tool
        def execute_deployment(artifact_id: str, environment: str) -> str:
            """Request deployment of a validated artifact. Execution remains subject to deployment approval."""
            with SessionLocal() as db:
                artifact = db.get(models.Artifact, artifact_id)
                if not artifact or artifact.project_id != self.project_id:
                    return "Artifact not found"
                if environment not in {"staging", "production"}:
                    return "Unsupported deployment environment"
                instance = db.get(models.Instance, self.instance_id)
                if not instance or instance.project_id != self.project_id or instance.environment != environment:
                    return "Deployment environment does not match the connected project instance"
                project = db.get(models.Project, self.project_id)
                try:
                    deploy = deployment.request_deployment(
                        db, project, instance, artifact, self.requested_by_id,
                        rollback_plan="Agent-prepared rollback to the previously verified artifact",
                    )
                except ValueError as exc:
                    return str(exc)
                db.commit()
                return json.dumps({"deployment_id": deploy.id, "status": deploy.status})

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

        # ── Odoo inspection & safety tools ────────────────────────────────────
        @tool
        def inspect_access_rules(model_name: str) -> str:
            """Inspect access rules (ir.model.access) for an Odoo model."""
            try:
                if hasattr(self.client, "get_access_rules"):
                    rules = self.client.get_access_rules(model_name)
                else:
                    models_list = self._search_read("ir.model", [("model", "=", model_name)], ["id"], 1)
                    if not models_list:
                        return f"Model '{model_name}' not found"
                    rules = self._search_read("ir.model.access", [("model_id", "=", models_list[0]["id"])], ["name", "group_id", "perm_read", "perm_write", "perm_create", "perm_unlink"], 100)
                import yaml
                return yaml.dump(rules, sort_keys=False)
            except Exception as e:
                return f"ERROR inspecting access rules: {e}"

        @tool
        def inspect_computed_fields(model_name: str) -> str:
            """Inspect computed and stored fields for an Odoo model."""
            try:
                fields = self._search_read("ir.model.fields", [("model", "=", model_name), ("compute", "!=", False)], ["name", "field_description", "ttype", "compute", "depends", "store", "readonly"], 100)
                import yaml
                return yaml.dump(fields, sort_keys=False)
            except Exception as e:
                return f"ERROR inspecting computed fields: {e}"

        @tool
        def inspect_module_deps(module_name: str) -> str:
            """Inspect dependencies of an installed Odoo module."""
            try:
                mods = self._search_read("ir.module.module", [("name", "=", module_name)], ["id", "name", "state"], 1)
                if not mods:
                    return f"Module '{module_name}' not found"
                deps = self._search_read("ir.module.module.dependency", [("module_id", "=", mods[0]["id"])], ["name", "depend_id"], 100)
                import yaml
                return yaml.dump(deps, sort_keys=False)
            except Exception as e:
                return f"ERROR inspecting module dependencies: {e}"

        @tool
        def install_module_safe(module_name: str) -> str:
            """Safely install an Odoo module with verification."""
            try:
                if hasattr(self.client, "install_module"):
                    self.client.install_module(module_name)
                    return json.dumps({"ok": True, "module": module_name, "action": "installed"})
                return json.dumps({"ok": False, "error": "Client does not support install_module"})
            except Exception as e:
                return json.dumps({"ok": False, "error": str(e)})

        @tool
        def upgrade_module_safe(module_name: str) -> str:
            """Safely upgrade an Odoo module with verification."""
            try:
                if hasattr(self.client, "upgrade_module"):
                    self.client.upgrade_module(module_name)
                    return json.dumps({"ok": True, "module": module_name, "action": "upgraded"})
                return json.dumps({"ok": False, "error": "Client does not support upgrade_module"})
            except Exception as e:
                return json.dumps({"ok": False, "error": str(e)})

        # ── Discovery & Intelligence tools ────────────────────────────────────
        @tool
        def discover_module_structure(module_path: str = "") -> str:
            """Discover addon module layout: manifest, models, views, controllers, static assets."""
            try:
                target = self.workspace.resolve(module_path)
                if not target.is_dir():
                    return f"Path '{module_path}' is not a directory"
                structure = {
                    "has_manifest": (target / "__manifest__.py").exists(),
                    "has_init": (target / "__init__.py").exists(),
                    "models": [f.name for f in (target / "models").glob("*.py")] if (target / "models").is_dir() else [],
                    "views": [f.name for f in (target / "views").glob("*.xml")] if (target / "views").is_dir() else [],
                    "security": [f.name for f in (target / "security").iterdir()] if (target / "security").is_dir() else [],
                    "controllers": [f.name for f in (target / "controllers").glob("*.py")] if (target / "controllers").is_dir() else [],
                    "static": [str(f.relative_to(target)) for f in (target / "static").rglob("*") if f.is_file()][:20] if (target / "static").is_dir() else [],
                }
                return json.dumps(structure, indent=2)
            except Exception as e:
                return f"Error discovering module structure: {e}"

        @tool
        def discover_security_matrix(module_path: str = "") -> str:
            """Parse ir.model.access.csv and security groups in workspace into a structured matrix."""
            try:
                target = self.workspace.resolve(module_path)
                csv_path = target / "security" / "ir.model.access.csv" if (target / "security").is_dir() else target / "ir.model.access.csv"
                if not csv_path.exists():
                    return "No ir.model.access.csv found"
                import csv as pycsv
                rows = list(pycsv.DictReader(csv_path.open(encoding="utf-8")))
                return json.dumps(rows, indent=2)
            except Exception as e:
                return f"Error reading security matrix: {e}"

        @tool
        def discover_business_flow(flow_name: str) -> str:
            """Return standard Odoo business flow models, states, and transition methods."""
            flows = {
                "sales": {
                    "model": "sale.order",
                    "states": ["draft", "sent", "sale", "cancel"],
                    "methods": ["action_quotation_send", "action_confirm", "action_cancel", "action_draft"],
                    "lines_model": "sale.order.line",
                },
                "purchase": {
                    "model": "purchase.order",
                    "states": ["draft", "sent", "to approve", "purchase", "done", "cancel"],
                    "methods": ["button_confirm", "button_cancel", "button_done"],
                    "lines_model": "purchase.order.line",
                },
                "invoicing": {
                    "model": "account.move",
                    "states": ["draft", "posted", "cancel"],
                    "methods": ["action_post", "button_cancel", "button_draft"],
                    "lines_model": "account.move.line",
                },
                "inventory": {
                    "model": "stock.picking",
                    "states": ["draft", "waiting", "confirmed", "assigned", "done", "cancel"],
                    "methods": ["action_confirm", "action_assign", "button_validate", "action_cancel"],
                    "lines_model": "stock.move",
                },
                "crm": {
                    "model": "crm.lead",
                    "types": ["lead", "opportunity"],
                    "methods": ["action_set_won_rainbowman", "action_set_lost"],
                },
            }
            flow_key = flow_name.lower().strip()
            result = flows.get(flow_key) or {k: v for k, v in flows.items() if flow_key in k}
            return json.dumps(result or f"Unknown business flow '{flow_name}'. Available: {list(flows.keys())}", indent=2)

        @tool
        def discover_source_symbols(query: str, kind: str | None = None) -> str:
            """Search indexed source symbols across the project."""
            try:
                from source_indexer import search_symbols
                if self.project_id:
                    with SessionLocal() as db:
                        run = db.get(models.AgentRun, self.run_id) if self.run_id else None
                        snapshot_id = run.source_snapshot_id if run else None
                        if snapshot_id:
                            results = search_symbols(db, snapshot_id, query, kind)
                            return json.dumps(results, indent=2)
                matches = self.workspace.search(query, limit=20)
                return json.dumps(matches, indent=2)
            except Exception as e:
                return f"Error discovering symbols: {e}"

        @tool
        def generate_change_plan(description: str, files_involved: list[str] | None = None) -> str:
            """Generate a structured plan for implementing proposed changes."""
            plan = {
                "description": description,
                "files_involved": files_involved or [],
                "phases": [
                    {"phase": 1, "name": "Model & Schema Definition", "status": "planned"},
                    {"phase": 2, "name": "Security & ACL Configuration", "status": "planned"},
                    {"phase": 3, "name": "View & UI Templates", "status": "planned"},
                    {"phase": 4, "name": "Business Logic & Constraints", "status": "planned"},
                    {"phase": 5, "name": "Automated Conformance Checks", "status": "planned"},
                ],
                "recommended_verification": ["run_syntax_checks", "run_view_render_check", "run_acl_test"],
            }
            return json.dumps(plan, indent=2)

        @tool
        def estimate_migration_impact(target_version: str = "19.0", module_name: str = "") -> str:
            """Scan workspace module files for deprecated Odoo patterns and estimate migration impact."""
            try:
                target_dir = self.workspace.resolve(module_name)
                files = list(target_dir.rglob("*.py")) + list(target_dir.rglob("*.xml"))
                issues = []
                for f in files:
                    txt = f.read_text(encoding="utf-8", errors="replace")
                    rel = str(f.relative_to(target_dir))
                    if "<tree" in txt or "</tree>" in txt:
                        issues.append({"file": rel, "issue": "Legacy <tree> tag; replace with <list>"})
                    if "attrs=" in txt:
                        issues.append({"file": rel, "issue": "Deprecated attrs= attribute; use pythonic expressions"})
                    if "def name_get(" in txt:
                        issues.append({"file": rel, "issue": "Deprecated name_get(); replace with _compute_display_name()"})
                return json.dumps({
                    "target_version": target_version,
                    "scanned_files": len(files),
                    "deprecated_pattern_count": len(issues),
                    "issues": issues[:30],
                    "migration_difficulty": "LOW" if len(issues) < 5 else "MEDIUM" if len(issues) < 20 else "HIGH",
                }, indent=2)
            except Exception as e:
                return f"Error estimating migration impact: {e}"

        # ── Validation tools ──────────────────────────────────────────────────
        @tool
        def run_syntax_checks(module_name: str = "") -> str:
            """Run static syntax, XML and manifest conformance checks on an addon."""
            try:
                target = self.workspace.resolve(module_name)
                res = validation.validate_module(target)
                return json.dumps(res, indent=2)
            except Exception as e:
                return json.dumps({"passed": False, "error": str(e)})

        @tool
        def run_view_render_check(model_name: str, view_id: str | None = None) -> str:
            """Validate Odoo 19 XML view syntax for a model."""
            try:
                xml_files = list(self.workspace.root.rglob("*.xml"))
                matching_xml = ""
                for xf in xml_files:
                    c = xf.read_text(encoding="utf-8", errors="replace")
                    if model_name in c:
                        matching_xml += "\n" + c
                if not matching_xml:
                    return json.dumps({"passed": True, "note": f"No local XML view files found mentioning model '{model_name}'"})
                res = validation.validate_view_render(matching_xml, model_name)
                return json.dumps(res, indent=2)
            except Exception as e:
                return json.dumps({"passed": False, "error": str(e)})

        @tool
        def run_acl_test(model_name: str, group_xml_id: str | None = None, operation: str = "read") -> str:
            """Check that ACL rules for model_name exist and grant permissions."""
            try:
                csv_files = list(self.workspace.root.rglob("ir.model.access.csv"))
                if not csv_files:
                    return json.dumps({"passed": False, "error": "No ir.model.access.csv found in workspace"})
                csv_text = csv_files[0].read_text(encoding="utf-8", errors="replace")
                res = validation.validate_acl(csv_text, [model_name])
                return json.dumps(res, indent=2)
            except Exception as e:
                return json.dumps({"passed": False, "error": str(e)})

        @tool
        def run_business_scenario(scenario_name: str, params: dict | None = None) -> str:
            """Simulate and validate a business scenario flow."""
            try:
                res = validation.validate_business_scenario(scenario_name, params or {})
                return json.dumps(res, indent=2)
            except Exception as e:
                return json.dumps({"passed": False, "error": str(e)})

        @tool
        def run_migration_test(module_name: str = "") -> str:
            """Run migration check on module files in workspace."""
            try:
                target = self.workspace.resolve(module_name)
                res = validation.validate_module(target)
                return json.dumps({"ok": res.get("passed", False), "report": res}, indent=2)
            except Exception as e:
                return json.dumps({"ok": False, "error": str(e)})

        # ── Workspace git tools ───────────────────────────────────────────────
        @tool
        def create_worktree_branch(branch_name: str) -> str:
            """Create and checkout a new branch in the workspace git repository."""
            try:
                return self.workspace.create_branch(branch_name)
            except Exception as e:
                return f"Error creating branch: {e}"

        @tool
        def show_diff_preview(target: str = "HEAD") -> str:
            """Show unified diff preview between target and working tree."""
            try:
                res = self.workspace.diff_preview(target)
                return json.dumps(res, indent=2)
            except Exception as e:
                return f"Error generating diff preview: {e}"

        @tool
        def commit_changes(message: str) -> str:
            """Stage all changes and create a git commit."""
            try:
                rev = self.workspace.commit(message)
                return f"Committed: {rev}"
            except Exception as e:
                return f"Error committing changes: {e}"

        @tool
        def revert_changes(revision: str = "HEAD") -> str:
            """Revert a git revision in the workspace."""
            try:
                return self.workspace.revert_commit(revision)
            except Exception as e:
                return f"Error reverting changes: {e}"

        # ── Pri ERP management tools ─────────────────────────────────────────
        @tool
        def pri_erp_health() -> str:
            """Check Pri ERP platform connectivity and health."""
            try:
                if hasattr(self.client, "health"):
                    res = asyncio.run(self.client.health()) if asyncio.iscoroutinefunction(self.client.health) else self.client.health()
                    return json.dumps(res, indent=2)
                return "Current ERP connection is not a Pri ERP instance"
            except Exception as e:
                return f"Pri ERP health check failed: {e}"

        @tool
        def pri_erp_list_instances() -> str:
            """List managed Odoo instances on the Pri ERP platform."""
            try:
                if hasattr(self.client, "list_instances"):
                    res = asyncio.run(self.client.list_instances()) if asyncio.iscoroutinefunction(self.client.list_instances) else self.client.list_instances()
                    return json.dumps(res, indent=2)
                return "Current ERP connection is not a Pri ERP instance"
            except Exception as e:
                return f"Pri ERP list instances failed: {e}"

        @tool
        def pri_erp_get_instance(instance_id: int | str) -> str:
            """Get details of a specific instance on the Pri ERP platform."""
            try:
                if hasattr(self.client, "get_instance"):
                    res = asyncio.run(self.client.get_instance(instance_id)) if asyncio.iscoroutinefunction(self.client.get_instance) else self.client.get_instance(instance_id)
                    return json.dumps(res, indent=2)
                return "Current ERP connection is not a Pri ERP instance"
            except Exception as e:
                return f"Pri ERP get instance failed: {e}"

        @tool
        def pri_erp_list_addons(instance_id: int | str | None = None) -> str:
            """List custom addons for an instance on the Pri ERP platform."""
            try:
                if hasattr(self.client, "list_addons"):
                    res = asyncio.run(self.client.list_addons(instance_id)) if asyncio.iscoroutinefunction(self.client.list_addons) else self.client.list_addons(instance_id)
                    return json.dumps(res, indent=2)
                return "Current ERP connection is not a Pri ERP instance"
            except Exception as e:
                return f"Pri ERP list addons failed: {e}"

        @tool
        def pri_erp_list_files(path: str = "") -> str:
            """List files in the managed instance filesystem via Pri ERP."""
            try:
                if hasattr(self.client, "list_files"):
                    res = asyncio.run(self.client.list_files(path)) if asyncio.iscoroutinefunction(self.client.list_files) else self.client.list_files(path)
                    return json.dumps(res, indent=2)
                return "Current ERP connection is not a Pri ERP instance"
            except Exception as e:
                return f"Pri ERP list files failed: {e}"

        @tool
        def pri_erp_read_file(path: str) -> str:
            """Read a file from the managed instance filesystem via Pri ERP."""
            try:
                if hasattr(self.client, "read_file"):
                    return asyncio.run(self.client.read_file(path)) if asyncio.iscoroutinefunction(self.client.read_file) else self.client.read_file(path)
                return "Current ERP connection is not a Pri ERP instance"
            except Exception as e:
                return f"Pri ERP read file failed: {e}"

        @tool
        def pri_erp_write_file(path: str, content: str) -> str:
            """Write a file to the managed instance filesystem via Pri ERP."""
            try:
                if hasattr(self.client, "write_file"):
                    res = asyncio.run(self.client.write_file(path, content)) if asyncio.iscoroutinefunction(self.client.write_file) else self.client.write_file(path, content)
                    return json.dumps(res, indent=2)
                return "Current ERP connection is not a Pri ERP instance"
            except Exception as e:
                return f"Pri ERP write file failed: {e}"

        @tool
        def pri_erp_exec_terminal(command: str) -> str:
            """Execute a shell command inside the managed instance terminal via Pri ERP."""
            try:
                if hasattr(self.client, "exec_terminal"):
                    res = asyncio.run(self.client.exec_terminal(command)) if asyncio.iscoroutinefunction(self.client.exec_terminal) else self.client.exec_terminal(command)
                    return json.dumps(res, indent=2)
                return "Current ERP connection is not a Pri ERP instance"
            except Exception as e:
                return f"Pri ERP terminal execution failed: {e}"

        @tool
        def pri_erp_backup(name: str | None = None) -> str:
            """Create a backup of the managed instance on Pri ERP."""
            try:
                if hasattr(self.client, "create_backup"):
                    res = asyncio.run(self.client.create_backup(name)) if asyncio.iscoroutinefunction(self.client.create_backup) else self.client.create_backup(name)
                    return json.dumps(res, indent=2)
                return "Current ERP connection is not a Pri ERP instance"
            except Exception as e:
                return f"Pri ERP backup failed: {e}"

        @tool
        def pri_erp_list_backups() -> str:
            """List available backups for the instance on Pri ERP."""
            try:
                if hasattr(self.client, "list_backups"):
                    res = asyncio.run(self.client.list_backups()) if asyncio.iscoroutinefunction(self.client.list_backups) else self.client.list_backups()
                    return json.dumps(res, indent=2)
                return "Current ERP connection is not a Pri ERP instance"
            except Exception as e:
                return f"Pri ERP list backups failed: {e}"

        @tool
        def pri_erp_restore(backup_id: str) -> str:
            """Restore an instance from backup on Pri ERP."""
            try:
                if hasattr(self.client, "restore_backup"):
                    res = asyncio.run(self.client.restore_backup(backup_id)) if asyncio.iscoroutinefunction(self.client.restore_backup) else self.client.restore_backup(backup_id)
                    return json.dumps(res, indent=2)
                return "Current ERP connection is not a Pri ERP instance"
            except Exception as e:
                return f"Pri ERP restore failed: {e}"

        self.tools = [
            inspect_instance,
            installed_modules,
            inspect_company,
            inspect_users,
            inspect_odoo_schema,
            inspect_module_dependency,
            inspect_views,
            inspect_access,
            inspect_access_rules,
            inspect_computed_fields,
            inspect_module_deps,
            inspect_master_data,
            list_directory,
            read_file,
            inspect_url,
            browser_snapshot,
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
            install_module_safe,
            upgrade_module_safe,
            check_deployment_status,
            save_memory,
            save_user_preference,
            save_verified_fact,
            search_memory,
            discover_module_structure,
            discover_security_matrix,
            discover_business_flow,
            discover_source_symbols,
            generate_change_plan,
            estimate_migration_impact,
            run_syntax_checks,
            run_view_render_check,
            run_acl_test,
            run_business_scenario,
            run_migration_test,
            create_worktree_branch,
            show_diff_preview,
            commit_changes,
            revert_changes,
            pri_erp_health,
            pri_erp_list_instances,
            pri_erp_get_instance,
            pri_erp_list_addons,
            pri_erp_list_files,
            pri_erp_read_file,
            pri_erp_write_file,
            pri_erp_exec_terminal,
            pri_erp_backup,
            pri_erp_list_backups,
            pri_erp_restore,
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
            all_modules = self._search_read("ir.module.module", [], ["id", "name", "shortdesc", "state"], 500)
            matches = [row for row in all_modules if module_label_matches(row, module_name)]
            mods = resolve_module_matches(all_modules, module_name)
            if len(matches) > 1 and len(mods) > 1:
              return json.dumps({
                "passed": False,
                "error_category": "AmbiguousModuleName",
                "checks": [{"check": "module_installed", "passed": False, "details": f"More than one module matches {module_name}"}],
                "candidates": [row.get("name") for row in matches],
              })
            selected = mods[0] if mods else None
            mod_installed = bool(selected) and selected["state"] == "installed"
            checks.append({"check": "module_installed", "passed": mod_installed, "details": f"Module {module_name} ({selected.get('name') if selected else 'not found'}) state: {selected['state'] if selected else 'not found'}"})

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

        if self.read_only:
            allowed_read_tools = {
                "inspect_instance", "installed_modules", "inspect_company", "inspect_users",
                "inspect_odoo_schema", "inspect_module_dependency", "inspect_views", "inspect_access",
                "inspect_master_data", "list_directory", "read_file",
                "inspect_url", "browser_snapshot",
                "search_memory", "read_knowledge_base", "emit_thinking", "ask_question", "complete_task",
            }
            self.tools = [registered_tool for registered_tool in self.tools if registered_tool.name in allowed_read_tools]

        instance_context_text = ""
        memories_text = ""
        if self.project_id:
            with SessionLocal() as db:
                inst = None
                if self.instance_id:
                    inst = db.get(models.Instance, self.instance_id)
                if not inst:
                    inst = db.query(models.Instance).filter(models.Instance.project_id == self.project_id).first()
                if inst:
                    ver_info = inst.version_info or {}
                    ver_str = ver_info.get("server_version", "19.0")
                    edition_str = ver_info.get("server_edition", "community")
                    platform_name = "PRI ERP" if inst.erp_type == "pri_erp" else "Odoo"
                    instance_context_text = (
                        f"\n\n[CONNECTED LIVE ERP CONTEXT]\n"
                        f"- Platform: {platform_name}\n"
                        f"- Server URL: {inst.url}\n"
                        f"- Active Database: {inst.db_name}\n"
                        f"- Server Version: {ver_str} ({edition_str})\n"
                        f"- Environment: {inst.environment}\n"
                        f"- Auth Method: {inst.auth_method}\n"
                    )
                mems = db.query(models.AgentMemory).filter(
                    (models.AgentMemory.project_id == self.project_id) | (models.AgentMemory.project_id.is_(None))
                ).order_by(models.AgentMemory.updated_at.desc()).limit(10).all()
                if mems:
                    items = "\n".join([f"- [{m.category}] {m.key}: {m.content}" for m in mems])
                    memories_text = f"\n\n[PERSISTENT AGENT MEMORY & PAST LEARNINGS]\n{items}"

        prompt = SystemMessage(
            content=(
                "You are the Primacy ERP Implementation Agent — an autonomous engineer and pair-programming assistant "
                "that inspects, plans, builds, validates, installs, and maintains Odoo 19 modules. "
                "The user watches your work live in a split-pane IDE.\n\n"
                f"{instance_context_text}\n"

                "═══ ARCHITECTURE & FOUNDATIONS (GOOGLE ANTIGRAVITY SDK & AGENT STACK) ═══\n"
                "You are built directly upon the Google Antigravity (AGY) Agent architecture and standards:\n"
                "1. SUPERVISOR-WORKER DAG ORCHESTRATION: High-level SupervisorPlanner decomposes complex requests into an "
                "   ordered directed acyclic graph (DAG) of discrete tasks with explicit dependencies (depends_on), acceptance "
                "   criteria, and isolated subtask execution threads with per-task retry budgets (max_retries).\n"
                "2. DURABLE ASYNCHRONOUS OUTBOX WORKER: Operates via a resilient PostgreSQL transactional outbox pattern "
                "   (AgentRun, AgentEvent, AgentAuditLog, AgentSubtask) with LangGraph state checkpoints, monotonic heartbeat tracking "
                "   (heartbeat_at), and crash-recovery watchdogs ensuring no lost execution states across server restarts.\n"
                "3. GROUNDING LAW (erp-agent-grounding): The LLM decides intent, tools obtain real data, the LLM explains results. "
                "   Never hallucinate ERP records, customer names, or non-existent fields. Read-only queries never invoke write pipelines. "
                "   3-bucket intent classification: In-scope (execute tool), Ambiguous (ask/clarify), Out-of-scope (explain boundaries).\n"
                "4. PERMISSION GATING & HUMAN-IN-THE-LOOP: Three strict risk tiers:\n"
                "   - Class 1 (Read-only / Safe): inspect_*, installed_modules, read_file, list_directory, run_project_check — auto-proceed.\n"
                "   - Class 2 (Reversible Workspace Writes): create_directory, write_file, patch_file — auto-paused in the IDE for user approval.\n"
                "   - Class 3 (Destructive / Live Production Actions): package_module, execute_deployment, install_module_dependency, configure_inventory — explicit high-risk confirmation.\n"
                "5. REAL-TIME OBSERVABILITY & SPLIT-PANE IDE: Call emit_thinking before every tool call to explain WHY you are acting. "
                "   Streams real-time event logs, file diffs, and token/cost metrics live into the developer workspace.\n"
                "6. DUAL ERP CONNECTORS: Supports dynamic multi-strategy discovery, standard XML-RPC (/xmlrpc/2/object) and "
                "   modern Odoo 19 JSON-2 API (/web/dataset/call_kw) with zero hardcoded credentials.\n"
                "7. ODOO 19 TECHNICAL MASTERY: Full native support for OWL 3 components, <list> views (replacing deprecated <tree>), "
                "   <form> views, ir.model.access.csv security, and Python models.\n\n"

                "═══ PROTOCOL 0 — CONVERSATIONAL GROUNDING & INTENT UNDERSTANDING ═══\n"
                "Determine the user's intent before invoking any tools or initiating code changes:\n"
                "1. RESPONSE STYLE (ANTIGRAVITY IDE / CODEX STANDARDS):\n"
                "   - Small, precise, and high-signal: Never output walls of text, unsolicited tutorials, or conversational filler.\n"
                "   - Proper formatting: Use clean, concise GitHub markdown (bullet points, bold headers, inline code tags).\n"
                "   - NO ASCII art diagrams, box drawings, or decorative flowcharts. Use compact numbered lists for lifecycles or steps.\n"
                "   - Code snippets: Only include code if strictly necessary, keeping snippets concise (<=10 lines) and directly targeted.\n"
                "   - Actionable conclusion: End with 1-2 brief, concrete next steps or options.\n"
                "2. TECHNICAL, ARCHITECTURAL, & INFORMATIONAL INQUIRIES:\n"
                "   If the user asks how this agent was built, inquires about Antigravity SDK, Odoo 19 concepts, instance status, or technical advice:\n"
                "   - Answer directly and concisely in 2-3 short bulleted sections.\n"
                "   - If outlining a workflow or lifecycle, use a simple numbered list with 1-line descriptions per stage.\n"
                "   - If discussing a potential module, give a compact 3-4 bullet plan and ask if they want to proceed.\n"
                "   - NEVER call write_file, patch_file, create_directory, package_module, or execute_deployment on informational inquiries!\n"
                "   - Call complete_task with outcome=SUCCESS once your answer is provided.\n"
                "3. GREETINGS & BROAD DISCUSSIONS:\n"
                "   If the user sends a greeting (e.g. 'hi', 'hello'), asks what you can do, or starts a conversation:\n"
                "   - Greet concisely (1-2 sentences) confirming connected ERP instance and database.\n"
                "   - List 3 quick bulleted actions you can run (inspect schema, scaffold module, safe deploy).\n"
                "   - Call complete_task with outcome=SUCCESS without modifying files.\n"
                "4. PLAN BEFORE BUILDING (NEVER CREATE RANDOMLY):\n"
                "   When the user asks to build, customize, or implement anything:\n"
                "   - Do NOT immediately generate code or write files randomly.\n"
                "   - FIRST inspect live schema/installed modules (inspect_odoo_schema or installed_modules) to ground in the actual environment.\n"
                "   - Propose a concise implementation plan (models, fields, views, security).\n"
                "   - If ambiguous, ask 1 concise clarifying question before generating code.\n"
                "5. GROUNDING LAW:\n"
                "   Never invent or hallucinate ERP records, customer names, or non-existent fields. Ground all factual assertions in tool responses.\n\n"

                "═══ PROTOCOL 1 — PERMISSION GATING ═══\n"
                "Before any write/modify/execute action, classify its risk and act accordingly:\n"
                "- Class 1 (read-only, auto-proceed): inspect_*, read_file, list_directory, run_project_check — call emit_thinking first, then proceed.\n"
                "- Class 2 (reversible write, requires approval): create_directory, write_file, patch_file, new models/views — the system will auto-pause for user approval.\n"
                "- Class 3 (destructive/live, requires explicit confirmation): execute_deployment, configure_inventory, update_company_contact, create_draft_invoice — the approval card will warn the user what can break.\n"
                "Never batch multiple Class 2/3 actions under a single approval. One approval = one described action.\n"
                "If the user denies an action: stop that plan branch, acknowledge the denial, and ask for an alternative.\n\n"

                "═══ PROTOCOL 2 — REAL-TIME VISIBILITY ═══\n"
                "Call emit_thinking BEFORE every tool call — no exceptions. "
                "The message must say WHY you are doing the next step (1-2 sentences), not just what tool you'll call. "
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
                "   For module discovery, installed_modules accepts a human-facing name such as 'Training Centre' as module_query and returns both display and technical names. Do not guess snake_case aliases. If a requested model is missing, inspect its module dependency by display or technical name. On staging, request install_module_dependency approval and do not build against missing models.\n"
                "3. If uncertain about Odoo 19 syntax, call read_knowledge_base ONCE.\n"
                "4. Write files with write_file. If SYNTAX_ERROR or XML_ERROR is returned, fix and retry immediately — show the exact error in emit_thinking.\n"
                "5. Call package_module to validate.\n"
                "6. Call verify_module_installation to confirm module state == 'installed' and ORM models exist.\n"
                "7. End your response with a 'Where to find it inside Odoo 19:' navigation guide.\n"
                "8. Call complete_task with outcome, concrete change list, and verification result.\n"
                "9. Always call exactly one tool at a time. Never use placeholders in generated code.\n"
                "10. Use save_memory for critical schema details, bug fixes, and user preferences."
                f"{memories_text}"
                + (
                    "\n\nREAD-ONLY / INFORMATIONAL REQUEST OVERRIDE: This is a read-only or informational request. "
                    "Use only read-only inspection tools (inspect_*, installed_modules, read_file, list_directory, "
                    "inspect_url, browser_snapshot, read_knowledge_base, or complete_task). "
                    "Never call create_directory, write_file, patch_file, package_module, execute_deployment, "
                    "install_module_dependency, or any other write/deployment tool. "
                    "Answer directly, concisely, and precisely in Antigravity IDE / Codex style: compact markdown, "
                    "no ASCII art diagrams, no verbose preambles, and 1-2 actionable next steps. "
                    "If an inspection of the connected ERP was requested, return an evidence-based summary of what was found. "
                    "Call complete_task exactly once."
                    if read_only else ""
                )
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

    def _search_read(self, model: str, domain: list, fields: list[str], limit: int | None = None):
        return self.client.search_read(model, domain, fields, limit)

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
                messages = state.values.get("messages", [])
                last = messages[-1] if messages else None
                calls = getattr(last, "tool_calls", []) if last else []
                if not calls:
                    raise ValueError("Pending checkpoint does not contain a tool call")
                tool_messages = [
                    ToolMessage(tool_call_id=c["id"], name=c.get("name", "tool"), content="User rejected this action.")
                    for c in calls
                ]
                await self.executor.aupdate_state(
                    config,
                    {"messages": tool_messages},
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
                    tool_name = event.get("name", "unknown")
                    self.activity_events.append(("tool.started", {
                        "tool": tool_name,
                        "arguments": event.get("data", {}).get("input", {}),
                        "category": tool_category(tool_name),
                    }))
                    yield ""
                elif event["event"] == "on_tool_end":
                    output = str(event.get("data", {}).get("output", ""))
                    tool_name = event.get("name", "unknown")
                    if output.strip() == QUESTION_SENTINEL:
                        return
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
                    if tool_name == "complete_task":
                        task_report_seen = True
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

            # Sanitize multi-tool calls to avoid dangling unfulfilled tool calls in state
            messages = state.values.get("messages", [])
            if messages and getattr(messages[-1], "tool_calls", []):
                last = messages[-1]
                calls = getattr(last, "tool_calls", [])
                if len(calls) > 1:
                    pruned_msg = AIMessage(
                        content=last.content,
                        tool_calls=[calls[0]],
                        id=getattr(last, "id", None),
                    )
                    await self.executor.aupdate_state(
                        config,
                        {"messages": [pruned_msg]},
                    )

            call = self._single_pending_call(state)
            if not call:
                input_data = None
                continue
            safe = getattr(self, "safe_tools", self.SAFE_TOOLS)
            if call["name"] in self.RISK_CLASSES and call["name"] not in safe:
                return
            input_data = None

    async def pending_call(self, thread_id: str) -> dict | None:
        state = await self.executor.aget_state({"configurable": {"thread_id": thread_id}})
        if "tools" not in state.next:
            return None
        call = self._single_pending_call(state)
        if not call:
            return None
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
        if not call or call["name"] != "ask_question":
            return None
        return call

    async def answer_question(self, thread_id: str, answer: str) -> None:
        """Resume the graph after ask_question by injecting the user's answer."""
        config = {"configurable": {"thread_id": thread_id}}
        state = await self.executor.aget_state(config)
        messages = state.values.get("messages", [])
        last = messages[-1] if messages else None
        calls = getattr(last, "tool_calls", []) if last else []
        if calls:
            tool_messages = []
            for c in calls:
                if c.get("name") == "ask_question":
                    tool_messages.append(ToolMessage(tool_call_id=c["id"], name=c["name"], content=answer))
                else:
                    tool_messages.append(ToolMessage(tool_call_id=c["id"], name=c.get("name", "unknown"), content="Clarification provided by user."))
            await self.executor.aupdate_state(
                config,
                {"messages": tool_messages},
                as_node="tools",
            )
        else:
            await self.executor.aupdate_state(
                config,
                {"messages": [HumanMessage(content=answer)]},
            )

    @staticmethod
    def _single_pending_call(state) -> dict | None:
        messages = state.values.get("messages", [])
        if not messages:
            return None
        last = messages[-1]
        calls = getattr(last, "tool_calls", [])
        if not calls:
            return None
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
                "ir.module.module", [], ["id", "name", "shortdesc", "state"], 500
            )
            requested_name = str(args.get("module_name") or "")
            matches = [row for row in modules if module_label_matches(row, requested_name)]
            resolved_matches = resolve_module_matches(modules, requested_name)
            selected = resolved_matches[0] if len(matches) == 1 or len(resolved_matches) == 1 else None
            return {
                "operation": "install Odoo module dependency",
                "module_name": requested_name,
                "resolved_technical_name": selected.get("name") if selected else None,
                "display_name": selected.get("shortdesc") if selected else None,
                "current_state": selected.get("state") if selected else ("ambiguous" if matches else "not_found"),
                "candidates": [row.get("name") for row in matches] if not selected else [],
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

_READ_ONLY_KEYWORDS = frozenset([
    "inspect", "find", "list", "show", "check", "search", "look up", "what is", "which",
])
_WRITE_KEYWORDS = frozenset([
    "build", "create", "add", "implement", "write", "modify", "update", "change", "deploy",
    "install", "configure", "scaffold", "generate", "remove", "delete", "fix",
])
_NEGATED_ACTIONS = re.compile(r"\b(?:do\s+not|don't|dont|never|avoid|without)\b[^.!?\n;]*", re.IGNORECASE)
_NO_CHANGE_PHRASES = re.compile(r"\b(?:create|make|write|modify|change)\s+no\s+(?:new\s+)?(?:file|files|directory|directories|changes?)\b|\bno\s+(?:file|workspace|ERP)\s+changes?\b", re.IGNORECASE)

_CONVERSATIONAL_TASK_GRAPH = [{
    "task_id": "conversational_dialogue",
    "title": "Respond to user inquiry and provide technical/instance guidance",
    "risk_class": 1,
    "depends_on": [],
    "status": "pending",
    "retry_count": 0,
    "max_retries": 1,
    "context_bundle": {},
    "result": None,
    "heartbeat_at": None,
    "acceptance_criteria": (
        "Answer the user's inquiry directly, accurately, and conversationally based on "
        "Google Antigravity agent architecture, Odoo 19 capabilities, and live connected ERP instance context. "
        "Explain concepts clearly, answer technical questions, propose structured plans if discussing workflows, "
        "and do not modify any files or execute write actions. Call complete_task with outcome=SUCCESS once answered."
    ),
}]

_INSPECTION_TASK_GRAPH = [{
    "task_id": "inspect_request",
    "title": "Inspect the connected database and report findings",
    "risk_class": 1,
    "depends_on": [],
    "status": "pending",
    "retry_count": 0,
    "max_retries": 1,
    "context_bundle": {},
    "result": None,
    "heartbeat_at": None,
    "acceptance_criteria": "Use read-only inspection tools and return concrete database evidence. Do not modify the workspace or ERP.",
}]

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
                "task_id": "generate_tests",
                "title": "Generate Odoo tests for the required business scenario",
                "depends_on": ["implement_models", "implement_views", "implement_security"],
                "status": "pending",
                "max_retries": 2,
                "retry_count": 0,
                "acceptance_criteria": "Odoo tests cover the core scenario and security-sensitive behavior.",
            },
            {
                "task_id": "validate_and_verify",
                "title": "Package module and verify installation on Odoo 19",
                "depends_on": ["generate_tests"],
                "status": "pending",
                "max_retries": 2,
                "retry_count": 0,
                "acceptance_criteria": "Module passes static validation and verify_module_installation confirms clean installation.",
            },
        ]
        return tasks

    @staticmethod
    def is_continuation_request(prompt: str) -> bool:
        """Return True if prompt contains anaphoric cues or is a follow-up command
        relying on conversation context (e.g. 'do it', 'proceed', 'build that', 'create that model')."""
        lower = (prompt or "").lower().strip()
        cleaned = re.sub(r"[^a-zA-Z0-9\s]", " ", lower).strip()
        tokens = cleaned.split()
        if not tokens:
            return False
        
        # Direct approval/continuation phrases
        direct_cues = {
            "do it", "do that", "proceed", "go ahead", "yes please", "yes go ahead",
            "yes do it", "build it", "build that", "create it", "create that",
            "implement it", "implement that", "make it", "make that", "make those changes",
            "start building", "start it", "let s do it", "lets do it", "sounds good proceed",
            "option 1", "option 2", "option 3", "first option", "second option",
            "add that", "add it", "create that model", "create those models",
            "proceed with that", "proceed with option 1", "proceed with option 2",
        }
        if cleaned in direct_cues:
            return True

        if len(tokens) <= 6:
            continuation_words = {"it", "that", "this", "those", "them", "proceed", "ahead", "discussed", "above", "mentioned"}
            action_words = {"do", "build", "create", "make", "implement", "add", "update", "change", "yes", "proceed", "start"}
            if any(t in action_words for t in tokens) and any(t in continuation_words for t in tokens):
                return True

        return False

    @staticmethod
    def is_module_build(prompt: str, conversation_history: list | None = None) -> bool:
        """Return True if the prompt describes a complex multi-step module build,
        or is a continuation command referencing a module build from previous messages."""
        lower = prompt.lower().strip()
        # Questions asking for explanations, architecture, or conceptual understanding are not build commands
        question_starters = (
            "explain", "describe", "what is", "what are", "how does", "how do", "how is",
            "why does", "why is", "why do", "tell me about", "can you explain", "could you explain",
            "how was", "what docs", "how to understand",
        )
        if any(lower.startswith(qs) for qs in question_starters) and not any(
            f" {act} " in f" {lower} " for act in ["build a", "create a", "scaffold a", "develop a", "implement a"]
        ):
            return False

        hits = sum(1 for kw in _MODULE_BUILD_KEYWORDS if kw in lower)
        if hits >= 2:  # require at least 2 module-build signals
            return True

        # Check if this is a continuation command referencing a prior module build discussion
        if SupervisorPlanner.is_continuation_request(prompt) and conversation_history:
            for item in reversed(conversation_history[-6:]):
                content = getattr(item, "content", "") or ""
                if sum(1 for kw in _MODULE_BUILD_KEYWORDS if kw in content.lower()) >= 2:
                    return True
                if any(phrase in content.lower() for phrase in ["module", "custom module", "create model", "models.model", "__manifest__"]):
                    return True

        return False

    @staticmethod
    def is_conversational_request(prompt: str) -> bool:
        """Return True if the prompt is a greeting, broad question, architecture inquiry, or general conversational message."""
        cleaned = re.sub(r"[^a-zA-Z0-9\s]", " ", (prompt or "").casefold()).strip()
        tokens = cleaned.split()
        if not tokens:
            return True
        greetings = {"hi", "hello", "hey", "greetings", "good morning", "good afternoon", "good evening", "howdy", "sup", "yo"}
        if cleaned in greetings or (len(tokens) <= 3 and any(t in greetings for t in tokens)):
            return True

        # Check for Antigravity, architecture, docs, and system design inquiries
        antigravity_patterns = [
            r"\b(antigravity|agy)\b",
            r"\bhow\s+(was|is|did\s+we)\s+(this|the)\s+(agent\s+)?built\b",
            r"\bwhat\s+docs\b",
            r"\b(system\s+)?architecture\b",
            r"\bhow\s+does\s+(this\s+agent|antigravity)\s+work\b",
            r"\b(tell\s+me\s+about\s+)?(antigravity|agy)\s+docs\b",
        ]
        if any(re.search(pat, cleaned) for pat in antigravity_patterns):
            return True

        # Check for broad conversational / conceptual questions & explanations
        general_patterns = [
            r"^(what|who)\s+are\s+you\b",
            r"^what\s+can\s+you\s+do\b",
            r"^how\s+(does\s+this|do\s+you)\s+work\b",
            r"^help(\s+me)?\b",
            r"^tell\s+me\s+about\s+(yourself|this\s+project|this\s+agent|my\s+instance|the\s+database|antigravity)\b",
            r"^(can\s+you\s+help|i\s+need\s+help|where\s+do\s+we\s+start)\b",
            r"^(can\s+you\s+explain|explain\s+how|explain\s+to\s+me|explain\b)\b",
            r"^(describe\b|tell\s+me\s+how\b)\b",
            r"^why\s+(do\s+we|is\s+it|are\s+we)\b",
            r"^how\s+do\s+(we|i)\s+start\b",
        ]
        return any(re.search(pat, cleaned) for pat in general_patterns)

    @staticmethod
    def is_read_only_request(prompt: str) -> bool:
        """Return True for requests that ask only for inspection/reporting."""
        # Safety clauses such as "Do not create or modify files" must not turn
        # an inspection request into a write request. Remove only those
        # negated clauses before looking for requested actions.
        lower = _NO_CHANGE_PHRASES.sub(" ", prompt.casefold())
        lower = _NEGATED_ACTIONS.sub(" ", lower)
        if any(re.search(rf"\b{re.escape(keyword)}\b", lower) for keyword in _WRITE_KEYWORDS):
            return False
        return any(re.search(rf"\b{re.escape(keyword)}\b", lower) for keyword in _READ_ONLY_KEYWORDS)

    @staticmethod
    async def decompose(prompt: str, llm: ChatOpenAI | None = None, conversation_history: list | None = None) -> list[dict]:
        """Return the appropriate task graph for the given prompt.
        If a planner LLM is provided, generates a dynamic graph.
        Otherwise, falls back to the standard template.
        """
        import copy
        import schemas
        from pydantic import BaseModel

        if SupervisorPlanner.is_conversational_request(prompt) and not SupervisorPlanner.is_continuation_request(prompt):
            return copy.deepcopy(_CONVERSATIONAL_TASK_GRAPH)

        if SupervisorPlanner.is_read_only_request(prompt) and not SupervisorPlanner.is_continuation_request(prompt):
            return copy.deepcopy(_INSPECTION_TASK_GRAPH)

        if not SupervisorPlanner.is_module_build(prompt, conversation_history):
            return copy.deepcopy(_SIMPLE_TASK_GRAPH)

        if llm:
            try:
                system_prompt = (
                    "You are the Supervisor Planner for an Odoo 19 module building agent.\n"
                    "Your job is to break down the user's prompt into an ordered sequence of tasks.\n"
                    "If conversation context is provided, resolve references like 'it', 'that', 'proceed', or specific models/features discussed.\n"
                    "Return a JSON array of tasks. Ensure you include dependencies (`depends_on`) so tasks run in the correct order.\n"
                    "Set `max_retries` based on the difficulty of the task (usually 2, maybe 3 for complex tasks).\n"
                    "A standard module build covers: inspection, model changes, views, security, and verification."
                )
                
                context_str = ""
                if conversation_history:
                    history_items = []
                    for item in conversation_history[-6:]:
                        role = getattr(item, "role", "user")
                        content = getattr(item, "content", str(item))
                        if content.strip():
                            history_items.append(f"{role.upper()}: {content[:500]}")
                    if history_items:
                        context_str = "\n[Prior Conversation Context]\n" + "\n".join(history_items) + "\n\n"

                messages = [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=f"{context_str}Plan tasks for this request: {prompt}")
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
