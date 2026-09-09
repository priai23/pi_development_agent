"""
permissions.py — Five-layer permission engine for the Pi ERP agent.

Enforcement chain (all must pass):
  1. Workspace grant  (workspace:<path>)
  2. Command grant    (command:<name>)
  3. ERP resource     (erp:<tool_name>)
  4. User/project scope
  5. Approval state   (PendingAction.status)

The engine returns a PermissionDecision rather than raising an exception so
callers can decide whether to return 403, queue an approval, or cache a deny.
"""

from __future__ import annotations

import functools
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public helpers (kept for backward compat)
# ---------------------------------------------------------------------------

def resource_for(tool_name: str, arguments: dict) -> str:
    path = str(arguments.get("path") or "").strip()
    if path and tool_name in {"write_file", "patch_file", "create_directory"}:
        return f"workspace:{path.lstrip('./')}"
    if tool_name == "run_project_check":
        return f"command:{arguments.get('check', '')}"
    if tool_name == "terminal_command":
        return f"command:{arguments.get('command', '')}"
    return f"erp:{tool_name}"


def resource_matches(pattern: str, resource: str) -> bool:
    return fnmatchcase(resource, pattern)


def active_grant(db, project_id: int, user_id: int, resource: str):
    """Return the most specific non-expired grant, with deny taking precedence.

    This function is kept for backward compatibility.  New code should use
    PermissionEngine.check() instead.
    """
    from models import PermissionGrant

    now = datetime.now(timezone.utc)
    candidates = db.query(PermissionGrant).filter(
        ((PermissionGrant.project_id == project_id) | (PermissionGrant.project_id.is_(None))),
        ((PermissionGrant.user_id == user_id) | (PermissionGrant.user_id.is_(None))),
    ).all()
    candidates = [
        item for item in candidates
        if resource_matches(item.resource, resource)
        and (item.expires_at is None or item.expires_at > now)
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            item.decision == "deny",
            item.project_id is not None,
            item.user_id is not None,
        ),
        reverse=True,
    )
    return candidates[0]


# ---------------------------------------------------------------------------
# PermissionDecision
# ---------------------------------------------------------------------------

@dataclass
class PermissionDecision:
    allowed: bool
    reason: str
    requires_approval: bool = False
    cached: bool = False
    grant_id: str | None = None


# ---------------------------------------------------------------------------
# LRU cache for permission lookups
# ---------------------------------------------------------------------------

class _PermissionCache:
    """Simple thread-safe TTL cache keyed by (project_id, user_id, resource)."""

    _TTL = 60  # seconds

    def __init__(self):
        self._store: dict[tuple, tuple[PermissionDecision, float]] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple) -> PermissionDecision | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            decision, expires_at = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                return None
            d = PermissionDecision(
                allowed=decision.allowed,
                reason=decision.reason,
                requires_approval=decision.requires_approval,
                cached=True,
                grant_id=decision.grant_id,
            )
            return d

    def set(self, key: tuple, decision: PermissionDecision) -> None:
        with self._lock:
            self._store[key] = (decision, time.monotonic() + self._TTL)

    def invalidate(self, project_id: int | None = None, user_id: int | None = None) -> None:
        """Remove all cached entries for the given scope."""
        with self._lock:
            keys = [
                k for k in self._store
                if (project_id is None or k[0] == project_id)
                and (user_id is None or k[1] == user_id)
            ]
            for k in keys:
                del self._store[k]

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


# Module-level cache shared by all PermissionEngine instances
_cache = _PermissionCache()


def invalidate_permission_cache(project_id: int | None = None, user_id: int | None = None) -> None:
    """Call this whenever a PermissionGrant is created, updated, or deleted."""
    _cache.invalidate(project_id=project_id, user_id=user_id)


# ---------------------------------------------------------------------------
# PermissionEngine
# ---------------------------------------------------------------------------

class PermissionEngine:
    """Evaluate the five-layer permission chain for a single tool call.

    Usage::

        engine = PermissionEngine()
        decision = engine.check(
            db=db,
            project_id=project.id,
            user_id=user.id,
            tool_name="write_file",
            args={"path": "my_module/models/sale.py"},
            erp_capabilities=instance.capabilities,
            approval_state=None,   # not yet approved
        )
        if not decision.allowed:
            raise PermissionDenied(decision.reason)
        if decision.requires_approval:
            create_pending_action(...)
    """

    # Tools that are always allowed regardless of grants (read-only safe tools)
    _ALWAYS_ALLOWED: frozenset[str] = frozenset({
        "inspect_instance",
        "installed_modules",
        "inspect_company",
        "inspect_users",
        "inspect_odoo_schema",
        "inspect_views",
        "inspect_access",
        "inspect_access_rules",
        "inspect_computed_fields",
        "inspect_master_data",
        "list_directory",
        "read_file",
        "inspect_url",
        "browser_snapshot",
        "check_deployment_status",
        "save_memory",
        "search_memory",
        "save_user_preference",
        "save_verified_fact",
        "run_project_check",
        "inspect_module_dependency",
        "inspect_module_deps",
        "discover_module_structure",
        "discover_security_matrix",
        "discover_business_flow",
        "discover_source_symbols",
        "generate_change_plan",
        "estimate_migration_impact",
        "pri_erp_health",
        "pri_erp_list_instances",
        "pri_erp_get_instance",
        "pri_erp_list_addons",
        "pri_erp_list_files",
        "pri_erp_read_file",
        "pri_erp_list_backups",
    })

    # Tools that always require human approval (class-3)
    _ALWAYS_REQUIRE_APPROVAL: frozenset[str] = frozenset({
        "update_company_contact",
        "configure_inventory",
        "create_draft_invoice",
        "execute_deployment",
        "install_module_dependency",
        "install_module_safe",
        "terminal_command",
        "pri_erp_exec_terminal",
        "pri_erp_install_module",
        "pri_erp_backup",
        "pri_erp_restore",
        "revert_changes",
    })

    def check(
        self,
        db: Any,
        project_id: int,
        user_id: int,
        tool_name: str,
        args: dict,
        erp_capabilities: dict | None = None,
        approval_state: str | None = None,
    ) -> PermissionDecision:
        """Run all five permission layers and return a PermissionDecision.

        Parameters
        ----------
        db:
            SQLAlchemy session.
        project_id, user_id:
            Scope identifiers.
        tool_name:
            The agent tool being called.
        args:
            Tool arguments (used to build the workspace/command resource string).
        erp_capabilities:
            ``Instance.capabilities`` dict — used for ERP resource check.
        approval_state:
            ``None | "approved" | "rejected"`` — current PendingAction state.
        """
        # Layer 0: always-allowed safe tools
        if tool_name in self._ALWAYS_ALLOWED:
            return PermissionDecision(allowed=True, reason="safe_tool")

        # Cache lookup
        resource = resource_for(tool_name, args)
        cache_key = (project_id, user_id, resource, approval_state)
        cached = _cache.get(cache_key)
        if cached:
            return cached

        # Layer 1-4: database grant check
        decision = self._check_grants(db, project_id, user_id, tool_name, resource, erp_capabilities)

        # Layer 5: approval state
        if decision.requires_approval:
            if approval_state == "approved":
                decision = PermissionDecision(
                    allowed=True,
                    reason="approved_by_human",
                    requires_approval=False,
                    grant_id=decision.grant_id,
                )
            elif approval_state == "rejected":
                decision = PermissionDecision(
                    allowed=False,
                    reason="rejected_by_human",
                    requires_approval=False,
                    grant_id=decision.grant_id,
                )
            # else: still pending — leave requires_approval=True, allowed=False

        # Cache successful (non-pending) decisions
        if not decision.requires_approval:
            _cache.set(cache_key, decision)

        self._audit(project_id, user_id, tool_name, resource, decision)
        return decision

    def _check_grants(
        self,
        db: Any,
        project_id: int,
        user_id: int,
        tool_name: str,
        resource: str,
        erp_capabilities: dict | None,
    ) -> PermissionDecision:
        from models import PermissionGrant

        now = datetime.now(timezone.utc)

        # Fetch all non-expired grants in scope
        candidates = db.query(PermissionGrant).filter(
            ((PermissionGrant.project_id == project_id) | (PermissionGrant.project_id.is_(None))),
            ((PermissionGrant.user_id == user_id) | (PermissionGrant.user_id.is_(None))),
        ).all()

        active = [
            g for g in candidates
            if (g.expires_at is None or g.expires_at > now)
        ]

        # Layer 1: workspace grant (workspace:path)
        if resource.startswith("workspace:"):
            ws_grants = [g for g in active if resource_matches(g.resource, resource)]
            result = self._evaluate_grants(ws_grants, resource)
            if result is not None:
                return result
            # No workspace grant → require approval for write ops
            if tool_name in {"write_file", "patch_file", "create_directory"}:
                return PermissionDecision(
                    allowed=False, reason="no_workspace_grant", requires_approval=True
                )

        # Layer 2: command grant (command:*)
        if resource.startswith("command:"):
            cmd_grants = [g for g in active if resource_matches(g.resource, resource)]
            result = self._evaluate_grants(cmd_grants, resource)
            if result is not None:
                return result

        # Layer 3: ERP resource grant (erp:tool_name)
        erp_grants = [g for g in active if resource_matches(g.resource, f"erp:{tool_name}")]
        erp_result = self._evaluate_grants(erp_grants, f"erp:{tool_name}")
        if erp_result is not None and not erp_result.allowed:
            return erp_result  # explicit deny wins

        # Layer 4: ERP capability check
        if erp_capabilities:
            cap_ok = self._check_erp_capability(tool_name, erp_capabilities)
            if not cap_ok:
                return PermissionDecision(
                    allowed=False,
                    reason=f"erp_capability_missing:{tool_name}",
                )

        # Always-require-approval set
        if tool_name in self._ALWAYS_REQUIRE_APPROVAL:
            return PermissionDecision(
                allowed=False,
                reason="requires_human_approval",
                requires_approval=True,
            )

        # Default: allow (deny-by-default is enforced by ALWAYS_REQUIRE_APPROVAL + explicit grants)
        return PermissionDecision(allowed=True, reason="default_allow")

    @staticmethod
    def _evaluate_grants(grants: list, resource: str) -> PermissionDecision | None:
        """Return a PermissionDecision from matching grants, or None if no grants match."""
        if not grants:
            return None
        # Sort: deny > project-scoped > user-scoped > global
        grants.sort(
            key=lambda g: (
                g.decision == "deny",
                g.project_id is not None,
                g.user_id is not None,
            ),
            reverse=True,
        )
        top = grants[0]
        if top.decision == "deny":
            return PermissionDecision(
                allowed=False, reason=f"explicit_deny:{resource}", grant_id=top.id
            )
        if top.decision == "allow":
            return PermissionDecision(
                allowed=True, reason=f"explicit_allow:{resource}", grant_id=top.id
            )
        # "ask" decision
        return PermissionDecision(
            allowed=False,
            reason=f"ask_required:{resource}",
            requires_approval=True,
            grant_id=top.id,
        )

    @staticmethod
    def _check_erp_capability(tool_name: str, capabilities: dict) -> bool:
        """Cross-check tool against the Instance.capabilities dict."""
        erp_type = capabilities.get("erp_type", "odoo")
        if erp_type == "pri_erp":
            # Pri ERP cannot do ORM operations
            orm_tools = {
                "inspect_odoo_schema", "inspect_views", "inspect_access",
                "inspect_access_rules", "create_partner", "create_product",
                "create_quotation", "create_rfq", "create_crm_lead",
                "create_draft_invoice", "configure_sales", "configure_purchase",
                "configure_inventory", "update_company_contact",
            }
            if tool_name in orm_tools:
                return False
        return True

    @staticmethod
    def _audit(project_id: int, user_id: int, tool_name: str, resource: str, decision: PermissionDecision) -> None:
        if decision.allowed:
            logger.debug(
                "PERM_ALLOW project=%s user=%s tool=%s reason=%s",
                project_id, user_id, tool_name, decision.reason,
            )
        else:
            logger.info(
                "PERM_DENY project=%s user=%s tool=%s resource=%s reason=%s requires_approval=%s",
                project_id, user_id, tool_name, resource, decision.reason, decision.requires_approval,
            )


# Module-level singleton for convenience
_engine = PermissionEngine()


def check_permission(
    db: Any,
    project_id: int,
    user_id: int,
    tool_name: str,
    args: dict,
    erp_capabilities: dict | None = None,
    approval_state: str | None = None,
) -> PermissionDecision:
    """Module-level convenience wrapper around PermissionEngine.check()."""
    return _engine.check(
        db=db,
        project_id=project_id,
        user_id=user_id,
        tool_name=tool_name,
        args=args,
        erp_capabilities=erp_capabilities,
        approval_state=approval_state,
    )
