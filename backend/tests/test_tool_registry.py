"""
tests/test_tool_registry.py — Tool registry unit tests.
"""

from __future__ import annotations

import pytest

from tool_registry import (
    TOOL_REGISTRY,
    ToolRegistryEntry,
    UnregisteredToolError,
    validate_tool_call,
    get_tools_for_erp,
    safe_tools_for_erp,
)


# ---------------------------------------------------------------------------
# Registry completeness
# ---------------------------------------------------------------------------

def test_registry_not_empty():
    assert len(TOOL_REGISTRY) > 50, "Expected at least 50 tools registered"


def test_all_entries_are_tool_registry_entry():
    for name, entry in TOOL_REGISTRY.items():
        assert isinstance(entry, ToolRegistryEntry), f"{name} should be a ToolRegistryEntry"


def test_all_risk_classes_valid():
    for name, entry in TOOL_REGISTRY.items():
        assert entry.risk_class in (1, 2, 3), f"{name}: risk_class must be 1, 2 or 3"


def test_write_tools_have_risk_2_or_3():
    for name, entry in TOOL_REGISTRY.items():
        if entry.is_write:
            assert entry.risk_class >= 2, f"{name}: write tools must be risk class 2 or 3"


def test_class_3_tools_always_require_approval():
    for name, entry in TOOL_REGISTRY.items():
        if entry.risk_class == 3:
            assert entry.requires_approval, f"{name}: class-3 tools must require approval"


def test_read_only_tools_are_class_1():
    for name, entry in TOOL_REGISTRY.items():
        if not entry.is_write:
            assert entry.risk_class == 1, f"{name}: read-only tools must be class 1"


# ---------------------------------------------------------------------------
# validate_tool_call
# ---------------------------------------------------------------------------

def test_validate_known_odoo_tool():
    entry = validate_tool_call("inspect_views", erp_type="odoo")
    assert entry.name == "inspect_views"
    assert entry.risk_class == 1
    assert not entry.is_write


def test_validate_known_prierp_tool():
    entry = validate_tool_call("pri_erp_health", erp_type="pri_erp")
    assert entry.name == "pri_erp_health"


def test_validate_unknown_tool_raises():
    with pytest.raises(UnregisteredToolError) as exc_info:
        validate_tool_call("nonexistent_tool")
    assert "nonexistent_tool" in str(exc_info.value)


def test_validate_wrong_erp_type_raises():
    # inspect_views is Odoo-only, should fail for pri_erp
    with pytest.raises(UnregisteredToolError) as exc_info:
        validate_tool_call("inspect_views", erp_type="pri_erp")
    assert "pri_erp" in str(exc_info.value)


def test_validate_all_erp_tool_works_for_both():
    # write_file is registered for all ERP types
    entry_odoo = validate_tool_call("write_file", erp_type="odoo")
    entry_pri = validate_tool_call("write_file", erp_type="pri_erp")
    assert entry_odoo.name == entry_pri.name == "write_file"


# ---------------------------------------------------------------------------
# get_tools_for_erp
# ---------------------------------------------------------------------------

def test_get_tools_for_odoo_contains_odoo_tools():
    tools = get_tools_for_erp("odoo")
    names = {e.name for e in tools}
    assert "inspect_views" in names
    assert "create_partner" in names
    assert "write_file" in names


def test_get_tools_for_odoo_excludes_prierp_only():
    tools = get_tools_for_erp("odoo")
    names = {e.name for e in tools}
    assert "pri_erp_health" not in names
    assert "pri_erp_exec_terminal" not in names


def test_get_tools_for_prierp_contains_prierp_tools():
    tools = get_tools_for_erp("pri_erp")
    names = {e.name for e in tools}
    assert "pri_erp_health" in names
    assert "pri_erp_list_instances" in names
    assert "write_file" in names  # shared


def test_get_tools_for_prierp_excludes_odoo_only():
    tools = get_tools_for_erp("pri_erp")
    names = {e.name for e in tools}
    assert "inspect_views" not in names
    assert "create_partner" not in names


# ---------------------------------------------------------------------------
# safe_tools_for_erp
# ---------------------------------------------------------------------------

def test_safe_tools_all_class_1():
    safe = safe_tools_for_erp("odoo")
    for name in safe:
        entry = TOOL_REGISTRY[name]
        assert entry.risk_class == 1, f"{name} in safe set but risk_class={entry.risk_class}"


def test_safe_tools_no_write():
    safe = safe_tools_for_erp("odoo")
    for name in safe:
        entry = TOOL_REGISTRY[name]
        assert not entry.is_write, f"{name} in safe set but is_write=True"


def test_safe_tools_subset_of_all_tools():
    all_odoo = {e.name for e in get_tools_for_erp("odoo")}
    safe = safe_tools_for_erp("odoo")
    assert safe.issubset(all_odoo)


# ---------------------------------------------------------------------------
# Specific critical tools
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool_name", [
    "execute_deployment",
    "install_module_safe",
    "update_company_contact",
    "pri_erp_exec_terminal",
    "pri_erp_backup",
    "pri_erp_restore",
])
def test_critical_tools_are_class_3(tool_name):
    entry = TOOL_REGISTRY.get(tool_name)
    assert entry is not None, f"Tool '{tool_name}' must be registered"
    assert entry.risk_class == 3, f"{tool_name}: must be class-3"
    assert entry.requires_approval, f"{tool_name}: must require approval"


@pytest.mark.parametrize("tool_name", [
    "inspect_views",
    "inspect_access",
    "list_directory",
    "read_file",
    "pri_erp_health",
    "pri_erp_list_instances",
    "check_deployment_status",
])
def test_safe_tools_are_class_1(tool_name):
    entry = TOOL_REGISTRY.get(tool_name)
    assert entry is not None, f"Tool '{tool_name}' must be registered"
    assert entry.risk_class == 1, f"{tool_name}: must be class-1"
    assert not entry.requires_approval, f"{tool_name}: must not require approval"


def test_write_file_has_evidence():
    entry = TOOL_REGISTRY["write_file"]
    assert "workspace_change" in entry.evidence_kinds


def test_execute_deployment_has_evidence():
    entry = TOOL_REGISTRY["execute_deployment"]
    assert "deployment_event" in entry.evidence_kinds
    assert "artifact_digest" in entry.evidence_kinds
