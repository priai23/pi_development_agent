"""
tests/test_permission_engine.py — PermissionEngine unit tests.

Tests the 5-layer enforcement chain, LRU cache, grant evaluation,
always-allowed set, always-require-approval set, and ERP capability checks.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from permissions import (
    PermissionEngine,
    PermissionDecision,
    check_permission,
    invalidate_permission_cache,
    _cache,
    resource_for,
    resource_matches,
)


@pytest.fixture(autouse=True)
def clear_permission_cache():
    """Clear the permission cache before every test to avoid cross-test pollution."""
    _cache.clear()
    yield
    _cache.clear()


def utcnow():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# resource_for() and resource_matches()
# ---------------------------------------------------------------------------

def test_resource_for_write_file():
    res = resource_for("write_file", {"path": "my_module/models/sale.py"})
    assert res == "workspace:my_module/models/sale.py"


def test_resource_for_run_check():
    res = resource_for("run_project_check", {"check": "pytest"})
    assert res == "command:pytest"


def test_resource_for_erp_tool():
    res = resource_for("create_partner", {"name": "Test"})
    assert res == "erp:create_partner"


def test_resource_matches_glob():
    assert resource_matches("workspace:my_module/*", "workspace:my_module/models/sale.py")
    assert resource_matches("workspace:*", "workspace:anything/here")
    assert not resource_matches("workspace:other_module/*", "workspace:my_module/models/sale.py")


def test_resource_matches_exact():
    assert resource_matches("erp:create_partner", "erp:create_partner")
    assert not resource_matches("erp:create_product", "erp:create_partner")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_grant(resource, decision="allow", project_id=1, user_id=1, expires_at=None):
    g = MagicMock()
    g.resource = resource
    g.decision = decision
    g.project_id = project_id
    g.user_id = user_id
    g.expires_at = expires_at
    g.id = f"{resource}-{decision}"
    return g


def _db_with_grants(*grants):
    """Return a mock DB session that returns the given grants."""
    db = MagicMock()
    query_mock = MagicMock()
    query_mock.filter.return_value.all.return_value = list(grants)
    db.query.return_value = query_mock
    return db


# ---------------------------------------------------------------------------
# Always-allowed tools
# ---------------------------------------------------------------------------

def test_always_allowed_tool():
    engine = PermissionEngine()
    db = _db_with_grants()  # No grants needed
    decision = engine.check(
        db=db, project_id=1, user_id=1,
        tool_name="inspect_views", args={"model_name": "sale.order"},
    )
    assert decision.allowed is True
    assert decision.reason == "safe_tool"
    # Should not have hit the DB at all
    db.query.assert_not_called()


def test_always_allowed_pri_erp_tools():
    engine = PermissionEngine()
    db = _db_with_grants()
    for tool in ("pri_erp_health", "pri_erp_list_instances", "pri_erp_list_backups"):
        decision = engine.check(db=db, project_id=1, user_id=1, tool_name=tool, args={})
        assert decision.allowed is True, f"Expected {tool} to be always allowed"


# ---------------------------------------------------------------------------
# Always-require-approval tools
# ---------------------------------------------------------------------------

def test_always_require_approval_tools():
    engine = PermissionEngine()
    db = _db_with_grants()
    for tool in ("execute_deployment", "pri_erp_exec_terminal", "pri_erp_backup", "terminal_command"):
        decision = engine.check(db=db, project_id=1, user_id=1, tool_name=tool, args={})
        assert decision.allowed is False, f"Expected {tool} to be denied pending approval"
        assert decision.requires_approval is True, f"Expected {tool} to require approval"


def test_always_require_approval_approved():
    engine = PermissionEngine()
    db = _db_with_grants()
    decision = engine.check(
        db=db, project_id=1, user_id=1,
        tool_name="execute_deployment",
        args={},
        approval_state="approved",
    )
    assert decision.allowed is True
    assert decision.requires_approval is False
    assert decision.reason == "approved_by_human"


def test_always_require_approval_rejected():
    engine = PermissionEngine()
    db = _db_with_grants()
    decision = engine.check(
        db=db, project_id=1, user_id=1,
        tool_name="execute_deployment",
        args={},
        approval_state="rejected",
    )
    assert decision.allowed is False
    assert decision.requires_approval is False
    assert decision.reason == "rejected_by_human"


# ---------------------------------------------------------------------------
# Explicit grant evaluation
# ---------------------------------------------------------------------------

def test_allow_grant_permits():
    engine = PermissionEngine()
    grant = _make_grant("workspace:my_module/*", "allow")
    db = _db_with_grants(grant)
    decision = engine.check(
        db=db, project_id=1, user_id=1,
        tool_name="write_file",
        args={"path": "my_module/models/sale.py"},
    )
    assert decision.allowed is True
    assert "allow" in decision.reason


def test_deny_grant_blocks():
    engine = PermissionEngine()
    deny_grant = _make_grant("workspace:restricted/*", "deny")
    db = _db_with_grants(deny_grant)
    decision = engine.check(
        db=db, project_id=1, user_id=1,
        tool_name="write_file",
        args={"path": "restricted/models/private.py"},
    )
    assert decision.allowed is False
    assert "deny" in decision.reason


def test_deny_takes_precedence_over_allow():
    engine = PermissionEngine()
    deny_grant = _make_grant("workspace:my_module/*", "deny", project_id=1, user_id=None)
    allow_grant = _make_grant("workspace:my_module/*", "allow", project_id=1, user_id=1)
    db = _db_with_grants(deny_grant, allow_grant)
    decision = engine.check(
        db=db, project_id=1, user_id=1,
        tool_name="write_file",
        args={"path": "my_module/models/sale.py"},
    )
    assert decision.allowed is False


def test_ask_grant_requires_approval():
    engine = PermissionEngine()
    ask_grant = _make_grant("workspace:my_module/*", "ask")
    db = _db_with_grants(ask_grant)
    decision = engine.check(
        db=db, project_id=1, user_id=1,
        tool_name="write_file",
        args={"path": "my_module/models/sale.py"},
    )
    assert decision.allowed is False
    assert decision.requires_approval is True


# ---------------------------------------------------------------------------
# No grants → write ops require approval
# ---------------------------------------------------------------------------

def test_no_workspace_grant_requires_approval():
    engine = PermissionEngine()
    db = _db_with_grants()  # No grants
    decision = engine.check(
        db=db, project_id=1, user_id=1,
        tool_name="write_file",
        args={"path": "my_module/models/sale.py"},
    )
    assert decision.allowed is False
    assert decision.requires_approval is True


# ---------------------------------------------------------------------------
# ERP capability check
# ---------------------------------------------------------------------------

def test_erp_capability_blocks_odoo_orm_on_pri_erp():
    engine = PermissionEngine()
    db = _db_with_grants()
    decision = engine.check(
        db=db, project_id=1, user_id=1,
        tool_name="create_partner",
        args={},
        erp_capabilities={"erp_type": "pri_erp"},
    )
    assert decision.allowed is False
    assert "erp_capability_missing" in decision.reason


def test_erp_capability_allows_odoo_orm_on_odoo():
    engine = PermissionEngine()
    db = _db_with_grants()
    # create_partner is not in ALWAYS_REQUIRE_APPROVAL, but erp_capabilities allows it
    decision = engine.check(
        db=db, project_id=1, user_id=1,
        tool_name="create_partner",
        args={},
        erp_capabilities={"erp_type": "odoo"},
    )
    # With no grants and no approval requirement in ALWAYS_REQUIRE_APPROVAL
    # it falls through to default_allow
    assert decision.allowed is True


# ---------------------------------------------------------------------------
# Expired grant handling
# ---------------------------------------------------------------------------

def test_expired_grant_is_ignored():
    engine = PermissionEngine()
    from datetime import timedelta
    expired_grant = _make_grant(
        "workspace:my_module/*", "allow",
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db = _db_with_grants(expired_grant)
    decision = engine.check(
        db=db, project_id=1, user_id=1,
        tool_name="write_file",
        args={"path": "my_module/models/sale.py"},
    )
    # Expired grant should be ignored → no workspace grant → require approval
    assert decision.allowed is False
    assert decision.requires_approval is True


# ---------------------------------------------------------------------------
# Permission cache
# ---------------------------------------------------------------------------

def test_cache_serves_second_call():
    _cache.clear()
    engine = PermissionEngine()
    call_count = 0

    original_check = engine._check_grants

    def counting_check(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return PermissionDecision(allowed=True, reason="explicit_allow:test")

    engine._check_grants = counting_check

    db = MagicMock()
    engine.check(db=db, project_id=99, user_id=99, tool_name="write_file", args={"path": "test/x.py"})
    engine.check(db=db, project_id=99, user_id=99, tool_name="write_file", args={"path": "test/x.py"})

    assert call_count == 1, "Second call should have been served from cache"


def test_cache_invalidation():
    _cache.clear()
    engine = PermissionEngine()
    call_count = 0

    def counting_check(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return PermissionDecision(allowed=True, reason="explicit_allow:test")

    engine._check_grants = counting_check

    db = MagicMock()
    engine.check(db=db, project_id=88, user_id=88, tool_name="write_file", args={"path": "test/y.py"})
    invalidate_permission_cache(project_id=88)
    engine.check(db=db, project_id=88, user_id=88, tool_name="write_file", args={"path": "test/y.py"})

    assert call_count == 2, "Cache invalidation should force re-evaluation"


# ---------------------------------------------------------------------------
# Module-level convenience wrapper
# ---------------------------------------------------------------------------

def test_check_permission_convenience():
    db = _db_with_grants()
    decision = check_permission(
        db=db, project_id=1, user_id=1,
        tool_name="inspect_views",
        args={"model_name": "sale.order"},
    )
    assert decision.allowed is True
