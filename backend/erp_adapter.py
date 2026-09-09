"""
erp_adapter.py — Abstract ERP adapter interface shared by all connectors.

Every ERP backend (Odoo XML-RPC, Odoo JSON-2, Pri ERP REST) must implement
ERPAdapter.  The agent uses only this interface so connector internals are
invisible at the tool layer.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Capability descriptor
# ---------------------------------------------------------------------------

@dataclass
class AdapterCapabilities:
    """Declares what a connected ERP instance can do."""

    erp_type: str                          # "odoo" | "pri_erp"
    erp_version: str                       # e.g. "19.0", "16.1"
    erp_edition: str                       # "community" | "enterprise" | "unknown"

    # Read capabilities
    can_list_modules: bool = True
    can_list_models: bool = True
    can_inspect_schema: bool = True
    can_inspect_views: bool = True
    can_inspect_security: bool = True
    can_read_records: bool = True

    # Write capabilities
    can_create_record: bool = False
    can_update_record: bool = False
    can_delete_record: bool = False
    can_run_action: bool = False
    can_install_module: bool = False
    can_upgrade_module: bool = False
    can_backup: bool = False
    can_restore: bool = False

    # Connection metadata
    write_enabled: bool = False            # master kill-switch for all writes
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------

class AdapterError(RuntimeError):
    """Base for all adapter-layer errors."""
    def __init__(self, message: str, code: str = "ADAPTER_ERROR", retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class AdapterAuthError(AdapterError):
    """Authentication or authorisation failed."""
    def __init__(self, message: str = "Authentication failed"):
        super().__init__(message, code="AUTH_ERROR", retryable=False)


class AdapterConnectionError(AdapterError):
    """Network or transport error."""
    def __init__(self, message: str):
        super().__init__(message, code="CONNECTION_ERROR", retryable=True)


class AdapterCapabilityError(AdapterError):
    """The operation is not supported by this ERP backend."""
    def __init__(self, operation: str, erp_type: str):
        super().__init__(
            f"Operation '{operation}' is not supported by {erp_type}",
            code="CAPABILITY_ERROR",
            retryable=False,
        )
        self.operation = operation
        self.erp_type = erp_type


class AdapterRateLimitError(AdapterError):
    """Too many requests; caller should back off."""
    def __init__(self, retry_after: int = 60):
        super().__init__(
            f"Rate-limited; retry after {retry_after}s",
            code="RATE_LIMIT",
            retryable=True,
        )
        self.retry_after = retry_after


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------

class ERPAdapter(abc.ABC):
    """Minimal interface every ERP connector must implement.

    Read operations (list_*, inspect_*, read_records) must never mutate state.
    Write operations (create_record, update_record, delete_record, run_action,
    install_module, upgrade_module, backup, restore) must:
      1. Check ``capabilities.write_enabled`` and raise AdapterCapabilityError if False.
      2. Be idempotent where the ERP allows it.
      3. Return structured result dicts (not raw ERP payloads).
    """

    # -- lifecycle ----------------------------------------------------------

    @abc.abstractmethod
    async def connect(self) -> None:
        """Establish and validate the connection.  Raises AdapterAuthError on failure."""

    @abc.abstractmethod
    async def health_check(self) -> dict:
        """Return ``{"ok": bool, "latency_ms": int, "detail": str}``."""

    @abc.abstractmethod
    def get_version(self) -> dict:
        """Return ``{"erp_type": str, "version": str, "edition": str, ...}``."""

    @abc.abstractmethod
    def get_capabilities(self) -> AdapterCapabilities:
        """Return the authoritative capability descriptor for this connection."""

    # -- discovery (read-only) ---------------------------------------------

    @abc.abstractmethod
    def list_modules(self, query: str | None = None, limit: int = 200) -> list[dict]:
        """Return installed modules.  Each dict contains at minimum
        ``{"name": str, "display_name": str, "version": str, "state": str}``."""

    @abc.abstractmethod
    def list_models(self, limit: int = 500) -> list[dict]:
        """Return available models: ``[{"model": str, "name": str}]``."""

    @abc.abstractmethod
    def inspect_schema(self, model_names: list[str]) -> dict:
        """Return field metadata for the given models.
        Shape: ``{model_name: {"relational_fields": [...], "scalar_fields": [...]}}``.
        """

    @abc.abstractmethod
    def inspect_views(self, model: str, view_type: str = "form") -> list[dict]:
        """Return view architecture for ``model`` filtered to ``view_type``."""

    @abc.abstractmethod
    def inspect_security(self, model: str) -> dict:
        """Return ACL + record rules: ``{"access": [...], "rules": [...]}"``."""

    @abc.abstractmethod
    def read_records(
        self,
        model: str,
        domain: list,
        fields: list[str],
        limit: int = 100,
    ) -> list[dict]:
        """Search-read records.  domain uses Odoo-style tuples or REST filters."""

    # -- write operations (require write_enabled) ---------------------------

    @abc.abstractmethod
    def create_record(self, model: str, values: dict) -> int | str:
        """Create a record and return its id."""

    @abc.abstractmethod
    def update_record(self, model: str, record_id: int | str, values: dict) -> bool:
        """Update a record.  Returns True on success."""

    @abc.abstractmethod
    def delete_record(self, model: str, record_id: int | str) -> bool:
        """Delete a record.  Returns True on success."""

    @abc.abstractmethod
    def run_action(self, model: str, method: str, ids: list, **kwargs) -> Any:
        """Call an arbitrary server action / ORM method."""

    @abc.abstractmethod
    def install_module(self, name: str) -> dict:
        """Install a module.  Returns ``{"ok": bool, "state": str, "error": str|None}``."""

    @abc.abstractmethod
    def upgrade_module(self, name: str) -> dict:
        """Upgrade a module.  Returns ``{"ok": bool, "state": str, "error": str|None}``."""

    @abc.abstractmethod
    def backup(self) -> dict:
        """Trigger a database backup.  Returns ``{"ok": bool, "backup_id": str}``."""

    @abc.abstractmethod
    def restore(self, backup_id: str) -> dict:
        """Restore a backup.  Returns ``{"ok": bool}``."""

    # -- helpers -----------------------------------------------------------

    def _require_write(self, operation: str) -> None:
        """Raise AdapterCapabilityError if writes are not enabled."""
        caps = self.get_capabilities()
        if not caps.write_enabled:
            raise AdapterCapabilityError(operation, caps.erp_type)

    @property
    @abc.abstractmethod
    def url(self) -> str:
        """Base URL of the ERP instance (used for URL validation)."""
