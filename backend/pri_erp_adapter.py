"""
pri_erp_adapter.py — Pri ERP REST adapter implementing ERPAdapter.

Pri ERP (admin.sh.prierp.com) is an Odoo hosting management platform with
a FastAPI-based REST API.  It manages Odoo *instances* (provision, backup,
module management, file editor, terminal) rather than being an Odoo instance
itself.

Authentication: POST /auth/login → session cookie + token.
API root: /api/v1/  (most endpoints) plus /instances/{id}/* legacy routes.

Write operations are disabled until an adapter contract is confirmed with the
instance admin credentials.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from erp_adapter import (
    ERPAdapter,
    AdapterAuthError,
    AdapterCapabilities,
    AdapterCapabilityError,
    AdapterConnectionError,
    AdapterError,
    AdapterRateLimitError,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT = 20.0
_MAX_RETRIES = 3
_BASE_BACKOFF = 1.0          # seconds; doubles on each retry
_RATE_LIMIT_BACKOFF = 60     # seconds to wait on 429
_API_PREFIX = "/api/v1"


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------

async def _retry_request(fn, *, retries: int = _MAX_RETRIES) -> httpx.Response:
    """Execute *fn()* (an async callable returning a Response) with exponential
    backoff on transient errors.  Raises AdapterRateLimitError on 429 and
    AdapterConnectionError on network failures after exhausting retries.
    """
    backoff = _BASE_BACKOFF
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response: httpx.Response = await fn()
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", _RATE_LIMIT_BACKOFF))
                raise AdapterRateLimitError(retry_after)
            if response.status_code in (502, 503, 504) and attempt < retries:
                await asyncio.sleep(backoff)
                backoff *= 2
                continue
            return response
        except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
            last_exc = exc
            if attempt < retries:
                await asyncio.sleep(backoff)
                backoff *= 2
            else:
                raise AdapterConnectionError(f"Network error after {retries} retries: {exc}") from exc
    raise AdapterConnectionError(f"Request failed after {retries} retries: {last_exc}")


# ---------------------------------------------------------------------------
# Pri ERP adapter
# ---------------------------------------------------------------------------

class PriERPAdapter(ERPAdapter):
    """REST adapter for the Pri ERP hosting management platform.

    Supports:
    - Instance listing and inspection (version, health, modules, addons)
    - Backup listing and triggering
    - File-tree browsing and file reading (via /editor endpoints)
    - Module installation via the addons API
    - Terminal command execution (requires write_enabled)

    Does NOT implement Odoo ORM operations (search_read, create, etc.) —
    those are handled by OdooClient/OdooJSON2Client after obtaining the
    Odoo credentials from the Pri ERP instance record.
    """

    def __init__(
        self,
        base_url: str,
        username: str | None = None,
        password: str | None = None,
        api_key: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        write_enabled: bool = False,
        instance_id: str | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._api_key = api_key
        self._timeout = timeout
        self._write_enabled = write_enabled
        self._instance_id = instance_id   # target Odoo instance on this platform
        self._token: str | None = None
        self._capabilities: AdapterCapabilities | None = None
        self._http: httpx.AsyncClient = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            follow_redirects=False,
        )

    # -- ERPAdapter.url property -------------------------------------------

    @property
    def url(self) -> str:
        return self._base_url

    @property
    def erp_type(self) -> str:
        return "pri_erp"

    # -- Internal helpers --------------------------------------------------

    def _auth_headers(self) -> dict:
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        return {}

    async def _get(self, endpoint: str, **params) -> dict:
        async def _do():
            return await self._http.get(endpoint, params=params or None, headers=self._auth_headers())
        resp = await _retry_request(_do)
        self._raise_for_status(resp, endpoint)
        return resp.json()

    async def _post(self, path: str, json: dict | None = None) -> dict:
        async def _do():
            return await self._http.post(path, json=json, headers=self._auth_headers())
        resp = await _retry_request(_do)
        self._raise_for_status(resp, path)
        return resp.json()

    def _raise_for_status(self, resp: httpx.Response, path: str) -> None:
        if resp.status_code == 401:
            raise AdapterAuthError(f"Unauthorized at {path}")
        if resp.status_code == 403:
            raise AdapterAuthError(f"Forbidden at {path}")
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text[:200])
            except Exception:
                detail = resp.text[:200]
            raise AdapterError(
                f"HTTP {resp.status_code} from {path}: {detail}",
                code=f"HTTP_{resp.status_code}",
                retryable=resp.status_code >= 500,
            )

    # -- ERPAdapter lifecycle -----------------------------------------------

    async def connect(self) -> None:
        """Authenticate and validate connectivity."""
        if self._api_key:
            # API key auth — validate by calling /auth/me or /health
            try:
                resp = await _retry_request(
                    lambda: self._http.get("/auth/me", headers=self._auth_headers())
                )
                if resp.status_code == 401:
                    raise AdapterAuthError("API key rejected by Pri ERP")
                if resp.status_code >= 400:
                    raise AdapterConnectionError(f"Pri ERP health check failed: HTTP {resp.status_code}")
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                raise AdapterConnectionError(f"Cannot reach Pri ERP at {self._base_url}: {exc}") from exc
        elif self._username and self._password:
            # Username/password auth — POST /auth/login
            try:
                resp = await _retry_request(
                    lambda: self._http.post(
                        "/auth/login",
                        json={"email": self._username, "password": self._password},
                    )
                )
                if resp.status_code == 401:
                    raise AdapterAuthError("Invalid username or password for Pri ERP")
                if resp.status_code >= 400:
                    raise AdapterAuthError(f"Login failed: HTTP {resp.status_code}")
                body = resp.json()
                self._token = body.get("access_token") or body.get("token")
                if not self._token:
                    # Try cookie-based auth (the platform may use cookies)
                    self._http = httpx.AsyncClient(
                        base_url=self._base_url,
                        timeout=self._timeout,
                        follow_redirects=False,
                        cookies=resp.cookies,
                    )
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                raise AdapterConnectionError(f"Cannot reach Pri ERP at {self._base_url}: {exc}") from exc
        else:
            raise AdapterAuthError("Either api_key or username+password is required for Pri ERP")

    async def health_check(self) -> dict:
        start = time.monotonic()
        try:
            resp = await _retry_request(
                lambda: self._http.get("/health", headers=self._auth_headers())
            )
            latency_ms = int((time.monotonic() - start) * 1000)
            ok = resp.status_code == 200
            detail = "ok" if ok else f"HTTP {resp.status_code}"
            return {"ok": ok, "latency_ms": latency_ms, "detail": detail}
        except (AdapterConnectionError, AdapterRateLimitError) as exc:
            return {"ok": False, "latency_ms": -1, "detail": str(exc)}

    def get_version(self) -> dict:
        """Synchronous version info — returns cached capabilities or defaults."""
        caps = self._capabilities
        if caps:
            return {
                "erp_type": caps.erp_type,
                "version": caps.erp_version,
                "edition": caps.erp_edition,
            }
        return {"erp_type": "pri_erp", "version": "unknown", "edition": "unknown"}

    def get_capabilities(self) -> AdapterCapabilities:
        if self._capabilities:
            return self._capabilities
        caps = AdapterCapabilities(
            erp_type="pri_erp",
            erp_version="unknown",
            erp_edition="unknown",
            can_list_modules=True,
            can_list_models=False,     # Pri ERP manages instances, not Odoo models
            can_inspect_schema=False,
            can_inspect_views=False,
            can_inspect_security=False,
            can_read_records=False,
            can_create_record=False,
            can_update_record=False,
            can_delete_record=False,
            can_run_action=self._write_enabled,
            can_install_module=self._write_enabled,
            can_upgrade_module=self._write_enabled,
            can_backup=True,
            can_restore=self._write_enabled,
            write_enabled=self._write_enabled,
        )
        self._capabilities = caps
        return caps

    # -- Discovery (read-only) ----------------------------------------------

    async def get_version_async(self) -> dict:
        """Fetch live version from the platform."""
        try:
            data = await self._get(f"{_API_PREFIX}/version")
            version = data.get("version", "unknown")
            self._capabilities = AdapterCapabilities(
                erp_type="pri_erp",
                erp_version=version,
                erp_edition="platform",
                write_enabled=self._write_enabled,
            )
            return {"erp_type": "pri_erp", "version": version, "platform": "pri_erp"}
        except AdapterError:
            return self.get_version()

    async def list_instances(self) -> list[dict]:
        """List managed Odoo instances on this platform."""
        data = await self._get("/instances")
        return data if isinstance(data, list) else data.get("items", data.get("results", []))

    async def get_instance(self, instance_id: str | None = None) -> dict:
        """Get details of a specific Odoo instance."""
        iid = instance_id or self._instance_id
        if not iid:
            raise AdapterError("instance_id is required", code="MISSING_INSTANCE_ID")
        return await self._get(f"/instances/{iid}")

    async def get_instance_dashboard(self, instance_id: str | None = None) -> dict:
        """Get dashboard metrics for an instance."""
        iid = instance_id or self._instance_id
        if not iid:
            raise AdapterError("instance_id is required", code="MISSING_INSTANCE_ID")
        return await self._get(f"/instances/{iid}/dashboard")

    def list_modules(self, query: str | None = None, limit: int = 200) -> list[dict]:
        """Not available synchronously — use list_addons_async instead."""
        raise AdapterCapabilityError("list_modules_sync", "pri_erp")

    async def list_addons_async(self, instance_id: str | None = None) -> list[dict]:
        """List installed addons on the target Odoo instance."""
        iid = instance_id or self._instance_id
        if not iid:
            raise AdapterError("instance_id is required", code="MISSING_INSTANCE_ID")
        data = await self._get(f"/instances/{iid}/addons")
        items = data if isinstance(data, list) else data.get("items", data.get("addons", []))
        return [
            {
                "name": item.get("name") or item.get("technical_name", ""),
                "display_name": item.get("display_name") or item.get("name", ""),
                "version": item.get("version", ""),
                "state": item.get("state") or item.get("status", ""),
            }
            for item in items
        ]

    def list_models(self, limit: int = 500) -> list[dict]:
        raise AdapterCapabilityError("list_models", "pri_erp")

    def inspect_schema(self, model_names: list[str]) -> dict:
        raise AdapterCapabilityError("inspect_schema", "pri_erp")

    def inspect_views(self, model: str, view_type: str = "form") -> list[dict]:
        raise AdapterCapabilityError("inspect_views", "pri_erp")

    def inspect_security(self, model: str) -> dict:
        raise AdapterCapabilityError("inspect_security", "pri_erp")

    def read_records(self, model: str, domain: list, fields: list[str], limit: int = 100) -> list[dict]:
        raise AdapterCapabilityError("read_records", "pri_erp")

    # -- File system (read) ------------------------------------------------

    async def list_files(self, instance_id: str | None = None, path: str = "/") -> list[dict]:
        """Browse instance file tree via the editor endpoint."""
        iid = instance_id or self._instance_id
        if not iid:
            raise AdapterError("instance_id is required", code="MISSING_INSTANCE_ID")
        return await self._get(f"/instances/{iid}/editor/tree", path=path)

    async def read_file(self, path: str, instance_id: str | None = None) -> str:
        """Read a file from the instance via the editor endpoint."""
        iid = instance_id or self._instance_id
        if not iid:
            raise AdapterError("instance_id is required", code="MISSING_INSTANCE_ID")
        data = await self._get(f"/instances/{iid}/editor/file", path=path)
        return data.get("content", "") if isinstance(data, dict) else str(data)

    # -- Backup (read + conditional write) ---------------------------------

    async def list_backups(self, instance_id: str | None = None) -> list[dict]:
        """List available backups for an instance."""
        iid = instance_id or self._instance_id
        if not iid:
            raise AdapterError("instance_id is required", code="MISSING_INSTANCE_ID")
        data = await self._get(f"/instances/{iid}/backups")
        return data if isinstance(data, list) else data.get("items", [])

    def backup(self) -> dict:
        """Synchronous wrapper — use backup_async instead."""
        raise AdapterError("Use backup_async() for Pri ERP", code="USE_ASYNC")

    async def backup_async(self, instance_id: str | None = None) -> dict:
        """Trigger a backup for the target instance."""
        self._require_write("backup")
        iid = instance_id or self._instance_id
        if not iid:
            raise AdapterError("instance_id is required", code="MISSING_INSTANCE_ID")
        data = await self._post(f"/system/backup")
        return {"ok": True, "backup_id": data.get("id", ""), "detail": data}

    def restore(self, backup_id: str) -> dict:
        raise AdapterError("Use restore_async() for Pri ERP", code="USE_ASYNC")

    async def restore_async(self, backup_id: str) -> dict:
        """Restore a backup."""
        self._require_write("restore")
        data = await self._post(f"/backups/{backup_id}/restore-in-place")
        return {"ok": True, "detail": data}

    # -- Module management (write) -----------------------------------------

    def install_module(self, name: str) -> dict:
        raise AdapterError("Use install_module_async() for Pri ERP", code="USE_ASYNC")

    async def install_module_async(self, module_name: str, instance_id: str | None = None) -> dict:
        """Trigger module install via instances/{id}/modules/update."""
        self._require_write("install_module")
        iid = instance_id or self._instance_id
        if not iid:
            raise AdapterError("instance_id is required", code="MISSING_INSTANCE_ID")
        data = await self._post(f"/instances/{iid}/modules/update", json={"modules": [module_name]})
        return {"ok": True, "state": "installing", "detail": data}

    def upgrade_module(self, name: str) -> dict:
        return self.install_module(name)

    async def upgrade_module_async(self, module_name: str, instance_id: str | None = None) -> dict:
        return await self.install_module_async(module_name, instance_id)

    # -- Terminal execution (write, high risk) ----------------------------

    async def exec_terminal(self, command: str, timeout: int = 60, instance_id: str | None = None) -> dict:
        """Execute a command in the instance terminal (class-3, requires approval)."""
        self._require_write("exec_terminal")
        iid = instance_id or self._instance_id
        if not iid:
            raise AdapterError("instance_id is required", code="MISSING_INSTANCE_ID")
        data = await self._post(
            f"/instances/{iid}/terminal/exec",
            json={"command": command, "timeout": timeout},
        )
        return {
            "ok": data.get("exit_code", -1) == 0,
            "exit_code": data.get("exit_code"),
            "stdout": data.get("stdout", ""),
            "stderr": data.get("stderr", ""),
        }

    # -- Stubs for unimplemented write ORM operations ---------------------

    def create_record(self, model: str, values: dict) -> int | str:
        raise AdapterCapabilityError("create_record", "pri_erp")

    def update_record(self, model: str, record_id: int | str, values: dict) -> bool:
        raise AdapterCapabilityError("update_record", "pri_erp")

    def delete_record(self, model: str, record_id: int | str) -> bool:
        raise AdapterCapabilityError("delete_record", "pri_erp")

    def run_action(self, model: str, method: str, ids: list, **kwargs) -> Any:
        raise AdapterCapabilityError("run_action", "pri_erp")

    # -- Context manager support ------------------------------------------

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

async def connect_pri_erp(
    url: str,
    username: str | None = None,
    password: str | None = None,
    api_key: str | None = None,
    write_enabled: bool = False,
    instance_id: str | None = None,
) -> PriERPAdapter:
    """Create and authenticate a PriERPAdapter.

    Raises AdapterAuthError on credential failure.
    Raises AdapterConnectionError on network failure.
    """
    adapter = PriERPAdapter(
        base_url=url,
        username=username,
        password=password,
        api_key=api_key,
        write_enabled=write_enabled,
        instance_id=instance_id,
    )
    await adapter.connect()
    return adapter
