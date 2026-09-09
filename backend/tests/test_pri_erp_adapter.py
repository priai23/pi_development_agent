"""
tests/test_pri_erp_adapter.py — Pri ERP adapter tests.

Uses respx to mock HTTP responses without touching the real network.
"""

from __future__ import annotations

import pytest
import httpx
import respx

from erp_adapter import AdapterAuthError, AdapterConnectionError, AdapterCapabilityError
from pri_erp_adapter import PriERPAdapter, connect_pri_erp


@pytest.fixture
def adapter():
    return PriERPAdapter(
        base_url="https://erp.test",
        api_key="test-key",
        write_enabled=False,
    )


@pytest.fixture
def write_adapter():
    return PriERPAdapter(
        base_url="https://erp.test",
        api_key="test-key",
        write_enabled=True,
        instance_id="inst-1",
    )


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

def test_capabilities_read_only(adapter):
    caps = adapter.get_capabilities()
    assert caps.erp_type == "pri_erp"
    assert caps.can_list_modules is True
    assert caps.can_list_models is False
    assert caps.write_enabled is False
    assert caps.can_backup is True
    assert caps.can_install_module is False


def test_capabilities_write_enabled(write_adapter):
    caps = write_adapter.get_capabilities()
    assert caps.write_enabled is True
    assert caps.can_install_module is True
    assert caps.can_run_action is True


# ---------------------------------------------------------------------------
# _require_write guard
# ---------------------------------------------------------------------------

def test_write_guard_blocks_on_readonly(adapter):
    with pytest.raises(AdapterCapabilityError) as exc_info:
        adapter._require_write("backup")
    assert "pri_erp" in str(exc_info.value)


def test_unsupported_orm_ops(adapter):
    with pytest.raises(AdapterCapabilityError):
        adapter.list_models()
    with pytest.raises(AdapterCapabilityError):
        adapter.inspect_schema(["sale.order"])
    with pytest.raises(AdapterCapabilityError):
        adapter.create_record("res.partner", {"name": "Test"})


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_connect_api_key_success():
    respx.get("https://erp.test/auth/me").mock(
        return_value=httpx.Response(200, json={"email": "admin@test.com"})
    )
    adapter = PriERPAdapter(base_url="https://erp.test", api_key="valid-key")
    await adapter.connect()  # Should not raise
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_connect_api_key_rejected():
    respx.get("https://erp.test/auth/me").mock(
        return_value=httpx.Response(401, json={"detail": "Invalid token"})
    )
    adapter = PriERPAdapter(base_url="https://erp.test", api_key="bad-key")
    with pytest.raises(AdapterAuthError):
        await adapter.connect()
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_connect_password_success():
    respx.post("https://erp.test/auth/login").mock(
        return_value=httpx.Response(200, json={"access_token": "jwt-token"})
    )
    adapter = PriERPAdapter(
        base_url="https://erp.test",
        username="admin@test.com",
        password="secret",
    )
    await adapter.connect()
    assert adapter._token == "jwt-token"
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_connect_password_rejected():
    respx.post("https://erp.test/auth/login").mock(
        return_value=httpx.Response(401, json={"detail": "Bad credentials"})
    )
    adapter = PriERPAdapter(
        base_url="https://erp.test",
        username="admin@test.com",
        password="wrong",
    )
    with pytest.raises(AdapterAuthError):
        await adapter.connect()
    await adapter.aclose()


@pytest.mark.asyncio
async def test_connect_no_credentials_raises():
    adapter = PriERPAdapter(base_url="https://erp.test")
    with pytest.raises(AdapterAuthError) as exc_info:
        await adapter.connect()
    assert "required" in str(exc_info.value).lower()
    await adapter.aclose()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_health_check_ok():
    respx.get("https://erp.test/health").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )
    adapter = PriERPAdapter(base_url="https://erp.test", api_key="key")
    result = await adapter.health_check()
    assert result["ok"] is True
    assert result["latency_ms"] >= 0
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_health_check_down():
    respx.get("https://erp.test/health").mock(
        return_value=httpx.Response(503, json={"status": "degraded"})
    )
    adapter = PriERPAdapter(base_url="https://erp.test", api_key="key")
    result = await adapter.health_check()
    assert result["ok"] is False
    await adapter.aclose()


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_get_version_async():
    respx.get("https://erp.test/api/v1/version").mock(
        return_value=httpx.Response(200, json={"version": "2.5.1"})
    )
    adapter = PriERPAdapter(base_url="https://erp.test", api_key="key")
    ver = await adapter.get_version_async()
    assert ver["erp_type"] == "pri_erp"
    assert ver["version"] == "2.5.1"
    await adapter.aclose()


# ---------------------------------------------------------------------------
# Instance listing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_list_instances():
    respx.get("https://erp.test/instances").mock(
        return_value=httpx.Response(200, json=[
            {"id": "inst-1", "name": "Production", "status": "active"},
            {"id": "inst-2", "name": "Staging", "status": "active"},
        ])
    )
    adapter = PriERPAdapter(base_url="https://erp.test", api_key="key")
    instances = await adapter.list_instances()
    assert len(instances) == 2
    assert instances[0]["id"] == "inst-1"
    await adapter.aclose()


# ---------------------------------------------------------------------------
# Addon listing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_list_addons():
    respx.get("https://erp.test/instances/inst-1/addons").mock(
        return_value=httpx.Response(200, json=[
            {"name": "sale", "state": "installed", "version": "19.0"},
            {"name": "purchase", "state": "installed", "version": "19.0"},
        ])
    )
    adapter = PriERPAdapter(base_url="https://erp.test", api_key="key", instance_id="inst-1")
    addons = await adapter.list_addons_async()
    assert len(addons) == 2
    assert addons[0]["name"] == "sale"
    await adapter.aclose()


# ---------------------------------------------------------------------------
# Backup (write guard)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backup_requires_write_enabled():
    adapter = PriERPAdapter(base_url="https://erp.test", api_key="key", instance_id="inst-1")
    with pytest.raises(AdapterCapabilityError):
        await adapter.backup_async()
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_backup_with_write_enabled():
    respx.post("https://erp.test/system/backup").mock(
        return_value=httpx.Response(200, json={"id": "bk-abc123"})
    )
    adapter = PriERPAdapter(
        base_url="https://erp.test", api_key="key",
        write_enabled=True, instance_id="inst-1",
    )
    result = await adapter.backup_async()
    assert result["ok"] is True
    assert result["backup_id"] == "bk-abc123"
    await adapter.aclose()


# ---------------------------------------------------------------------------
# Terminal exec (class-3)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exec_terminal_blocked_without_write():
    adapter = PriERPAdapter(base_url="https://erp.test", api_key="key", instance_id="inst-1")
    with pytest.raises(AdapterCapabilityError):
        await adapter.exec_terminal("ls /")
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_exec_terminal_with_write_enabled():
    respx.post("https://erp.test/instances/inst-1/terminal/exec").mock(
        return_value=httpx.Response(200, json={"exit_code": 0, "stdout": "file1\nfile2", "stderr": ""})
    )
    adapter = PriERPAdapter(
        base_url="https://erp.test", api_key="key",
        write_enabled=True, instance_id="inst-1",
    )
    result = await adapter.exec_terminal("ls /")
    assert result["ok"] is True
    assert "file1" in result["stdout"]
    await adapter.aclose()


# ---------------------------------------------------------------------------
# connect_pri_erp factory
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_connect_pri_erp_factory():
    respx.get("https://erp.test/auth/me").mock(
        return_value=httpx.Response(200, json={"email": "admin@test.com"})
    )
    adapter = await connect_pri_erp(url="https://erp.test", api_key="valid-key")
    assert adapter is not None
    assert adapter._base_url == "https://erp.test"
    await adapter.aclose()


# ---------------------------------------------------------------------------
# 401 → AdapterAuthError propagation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_get_raises_auth_error_on_401():
    respx.get("https://erp.test/auth/me").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    respx.get("https://erp.test/instances").mock(
        return_value=httpx.Response(401, json={"detail": "Unauthorized"})
    )
    adapter = PriERPAdapter(base_url="https://erp.test", api_key="key")
    await adapter.connect()
    with pytest.raises(AdapterAuthError):
        await adapter.list_instances()
    await adapter.aclose()
