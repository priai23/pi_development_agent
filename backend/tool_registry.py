"""
tool_registry.py — Typed tool registry for the Pi ERP agent.

Every agent tool must be registered here with its full contract:
  - name, description
  - input schema (JSON Schema)
  - ERP compatibility
  - risk class (1=read-only, 2=reversible write, 3=destructive/live)
  - required permissions
  - read/write flag
  - approval requirement
  - rollback capability
  - evidence kinds produced
  - idempotency behaviour

Unknown or unregistered tool calls are rejected before execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolRegistryEntry:
    name: str
    description: str
    erp_compat: frozenset[str]      # {"odoo", "pri_erp", "all"}
    risk_class: int                 # 1 | 2 | 3
    is_write: bool
    requires_approval: bool
    supports_rollback: bool
    is_idempotent: bool
    evidence_kinds: list[str] = field(default_factory=list)
    required_permissions: list[str] = field(default_factory=list)
    input_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)


class UnregisteredToolError(RuntimeError):
    def __init__(self, tool_name: str):
        super().__init__(f"Tool '{tool_name}' is not registered in TOOL_REGISTRY")
        self.tool_name = tool_name


# ---------------------------------------------------------------------------
# Registry — all tools declared here
# ---------------------------------------------------------------------------

_ODOO = frozenset({"odoo"})
_PRIERP = frozenset({"pri_erp"})
_ALL = frozenset({"odoo", "pri_erp"})

TOOL_REGISTRY: dict[str, ToolRegistryEntry] = {}


def _reg(**kwargs) -> ToolRegistryEntry:
    entry = ToolRegistryEntry(**kwargs)
    TOOL_REGISTRY[entry.name] = entry
    return entry


# ── Odoo read-only inspection ──────────────────────────────────────────────

_reg(
    name="inspect_instance",
    description="Return the connected Odoo version info.",
    erp_compat=_ODOO,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
    evidence_kinds=["version_info"],
)
_reg(
    name="installed_modules",
    description="List installed Odoo modules.",
    erp_compat=_ODOO,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
    evidence_kinds=["module_list"],
)
_reg(
    name="inspect_company",
    description="Read the main company's public contact details.",
    erp_compat=_ODOO,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
)
_reg(
    name="inspect_users",
    description="Read a limited list of Odoo users.",
    erp_compat=_ODOO,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
)
_reg(
    name="inspect_odoo_schema",
    description="Inspect field names and types for Odoo models.",
    erp_compat=_ODOO,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
    evidence_kinds=["schema_info"],
)
_reg(
    name="inspect_views",
    description="Read standard Odoo 19 views for a model.",
    erp_compat=_ODOO,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
    evidence_kinds=["view_arch"],
)
_reg(
    name="inspect_access",
    description="Inspect access-control and record-rule metadata for a model.",
    erp_compat=_ODOO,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
    evidence_kinds=["acl_info"],
)
_reg(
    name="inspect_access_rules",
    description="Detailed ACL + record rules for a model.",
    erp_compat=_ODOO,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
    evidence_kinds=["acl_info", "record_rules"],
)
_reg(
    name="inspect_computed_fields",
    description="Inspect computed, related, and selection fields for a model.",
    erp_compat=_ODOO,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
    evidence_kinds=["schema_info"],
)
_reg(
    name="inspect_module_dependency",
    description="Inspect an Odoo module and its expected models.",
    erp_compat=_ODOO,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
    evidence_kinds=["module_dep"],
)
_reg(
    name="inspect_module_deps",
    description="Return the dependency graph for a module.",
    erp_compat=_ODOO,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
    evidence_kinds=["dep_graph"],
)
_reg(
    name="inspect_master_data",
    description="Search an allowlisted master-data type.",
    erp_compat=_ODOO,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
)

# ── Odoo discovery (deep) ─────────────────────────────────────────────────

_reg(
    name="discover_module_structure",
    description="List files, deps, and inherited views for a module.",
    erp_compat=_ODOO,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
    evidence_kinds=["source_structure"],
)
_reg(
    name="discover_security_matrix",
    description="Full security matrix: ACL + record rules + groups for a model.",
    erp_compat=_ODOO,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
    evidence_kinds=["security_matrix"],
)
_reg(
    name="discover_business_flow",
    description="State machine fields, server actions, and workflows for a model.",
    erp_compat=_ODOO,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
    evidence_kinds=["business_flow"],
)
_reg(
    name="discover_source_symbols",
    description="Semantic search of the source index.",
    erp_compat=_ODOO,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
    evidence_kinds=["source_symbol"],
)

# ── Odoo design ────────────────────────────────────────────────────────────

_reg(
    name="generate_change_plan",
    description="Impact-aware implementation plan with file list.",
    erp_compat=_ODOO,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
    evidence_kinds=["change_plan"],
)
_reg(
    name="estimate_migration_impact",
    description="Fields/views affected by a module upgrade.",
    erp_compat=_ODOO,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
    evidence_kinds=["migration_impact"],
)

# ── Workspace file operations ─────────────────────────────────────────────

_reg(
    name="list_directory",
    description="List a directory inside the project workspace.",
    erp_compat=_ALL,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
)
_reg(
    name="read_file",
    description="Read a UTF-8 file from the workspace.",
    erp_compat=_ALL,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
)
_reg(
    name="create_directory",
    description="Create a directory in the workspace.",
    erp_compat=_ALL,
    risk_class=2, is_write=True, requires_approval=True,
    supports_rollback=True, is_idempotent=True,
    evidence_kinds=["workspace_change"],
)
_reg(
    name="write_file",
    description="Atomically write a file in the workspace.",
    erp_compat=_ALL,
    risk_class=2, is_write=True, requires_approval=True,
    supports_rollback=True, is_idempotent=False,
    evidence_kinds=["workspace_change"],
    required_permissions=["workspace:{path}"],
)
_reg(
    name="patch_file",
    description="Targeted string replacement in an existing workspace file.",
    erp_compat=_ALL,
    risk_class=2, is_write=True, requires_approval=True,
    supports_rollback=True, is_idempotent=False,
    evidence_kinds=["workspace_change"],
    required_permissions=["workspace:{path}"],
)

# ── Git workflow tools ────────────────────────────────────────────────────

_reg(
    name="create_worktree_branch",
    description="Create an isolated Git branch for changes.",
    erp_compat=_ALL,
    risk_class=2, is_write=True, requires_approval=False,
    supports_rollback=True, is_idempotent=False,
    evidence_kinds=["git_event"],
)
_reg(
    name="show_diff_preview",
    description="Show a unified diff of pending changes before approval.",
    erp_compat=_ALL,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
    evidence_kinds=["diff"],
)
_reg(
    name="commit_changes",
    description="Commit approved changes to the Git branch.",
    erp_compat=_ALL,
    risk_class=2, is_write=True, requires_approval=True,
    supports_rollback=True, is_idempotent=False,
    evidence_kinds=["git_event", "commit_hash"],
)
_reg(
    name="revert_changes",
    description="Revert a committed change (class-3, requires approval).",
    erp_compat=_ALL,
    risk_class=3, is_write=True, requires_approval=True,
    supports_rollback=False, is_idempotent=False,
    evidence_kinds=["git_event"],
)

# ── Odoo validation ────────────────────────────────────────────────────────

_reg(
    name="run_project_check",
    description="Run a fixed read-only project check (pytest, lint, etc.).",
    erp_compat=_ALL,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
    evidence_kinds=["check_result"],
    required_permissions=["command:{check}"],
)
_reg(
    name="run_syntax_checks",
    description="Python AST + XML + manifest validation.",
    erp_compat=_ODOO,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
    evidence_kinds=["check_result"],
)
_reg(
    name="run_view_render_check",
    description="Verify view arch renders after module install.",
    erp_compat=_ODOO,
    risk_class=2, is_write=True, requires_approval=True,
    supports_rollback=True, is_idempotent=True,
    evidence_kinds=["check_result", "view_arch"],
)
_reg(
    name="run_acl_test",
    description="Verify access matrix for a model+group.",
    erp_compat=_ODOO,
    risk_class=2, is_write=True, requires_approval=True,
    supports_rollback=True, is_idempotent=True,
    evidence_kinds=["check_result", "acl_info"],
)
_reg(
    name="run_business_scenario",
    description="Create/validate/cancel a business object in staging.",
    erp_compat=_ODOO,
    risk_class=2, is_write=True, requires_approval=True,
    supports_rollback=True, is_idempotent=False,
    evidence_kinds=["check_result", "scenario_trace"],
)
_reg(
    name="run_migration_test",
    description="Run pre_migrate + post_migrate scripts.",
    erp_compat=_ODOO,
    risk_class=2, is_write=True, requires_approval=True,
    supports_rollback=True, is_idempotent=False,
    evidence_kinds=["check_result"],
)
_reg(
    name="verify_module_installation",
    description="Verify a module is installed after deployment.",
    erp_compat=_ODOO,
    risk_class=2, is_write=True, requires_approval=True,
    supports_rollback=True, is_idempotent=True,
    evidence_kinds=["check_result", "module_state"],
)

# ── Odoo write operations ──────────────────────────────────────────────────

_reg(
    name="update_company_contact",
    description="Update company email/phone (class-3, requires approval).",
    erp_compat=_ODOO,
    risk_class=3, is_write=True, requires_approval=True,
    supports_rollback=False, is_idempotent=False,
)
_reg(
    name="configure_sales",
    description="Configure Odoo Sales settings.",
    erp_compat=_ODOO,
    risk_class=2, is_write=True, requires_approval=True,
    supports_rollback=True, is_idempotent=False,
)
_reg(
    name="configure_purchase",
    description="Configure Odoo Purchase settings.",
    erp_compat=_ODOO,
    risk_class=2, is_write=True, requires_approval=True,
    supports_rollback=True, is_idempotent=False,
)
_reg(
    name="configure_inventory",
    description="Configure Odoo Inventory settings (class-3).",
    erp_compat=_ODOO,
    risk_class=3, is_write=True, requires_approval=True,
    supports_rollback=False, is_idempotent=False,
)
_reg(
    name="create_partner",
    description="Create a res.partner record.",
    erp_compat=_ODOO,
    risk_class=2, is_write=True, requires_approval=True,
    supports_rollback=True, is_idempotent=False,
)
_reg(
    name="create_product",
    description="Create a product.template record.",
    erp_compat=_ODOO,
    risk_class=2, is_write=True, requires_approval=True,
    supports_rollback=True, is_idempotent=False,
)
_reg(
    name="create_quotation",
    description="Create a sale.order record.",
    erp_compat=_ODOO,
    risk_class=2, is_write=True, requires_approval=True,
    supports_rollback=True, is_idempotent=False,
)
_reg(
    name="create_rfq",
    description="Create a purchase.order record.",
    erp_compat=_ODOO,
    risk_class=2, is_write=True, requires_approval=True,
    supports_rollback=True, is_idempotent=False,
)
_reg(
    name="create_crm_lead",
    description="Create a crm.lead record.",
    erp_compat=_ODOO,
    risk_class=2, is_write=True, requires_approval=True,
    supports_rollback=True, is_idempotent=False,
)
_reg(
    name="create_draft_invoice",
    description="Create an account.move invoice (class-3).",
    erp_compat=_ODOO,
    risk_class=3, is_write=True, requires_approval=True,
    supports_rollback=False, is_idempotent=False,
)
_reg(
    name="install_module_safe",
    description="Install an Odoo module with pre-check (class-3).",
    erp_compat=_ODOO,
    risk_class=3, is_write=True, requires_approval=True,
    supports_rollback=False, is_idempotent=False,
    evidence_kinds=["module_state"],
)
_reg(
    name="upgrade_module_safe",
    description="Upgrade an Odoo module with migration check (class-3).",
    erp_compat=_ODOO,
    risk_class=3, is_write=True, requires_approval=True,
    supports_rollback=False, is_idempotent=False,
    evidence_kinds=["module_state"],
)
_reg(
    name="install_module_dependency",
    description="Install a required module (class-3).",
    erp_compat=_ODOO,
    risk_class=3, is_write=True, requires_approval=True,
    supports_rollback=False, is_idempotent=False,
    evidence_kinds=["module_state"],
)

# ── Deployment ────────────────────────────────────────────────────────────

_reg(
    name="package_module",
    description="Package a module into a deployable ZIP artifact.",
    erp_compat=_ODOO,
    risk_class=2, is_write=True, requires_approval=True,
    supports_rollback=True, is_idempotent=True,
    evidence_kinds=["artifact_digest"],
)
_reg(
    name="execute_deployment",
    description="Deploy an artifact to staging/production (class-3).",
    erp_compat=_ODOO,
    risk_class=3, is_write=True, requires_approval=True,
    supports_rollback=True, is_idempotent=False,
    evidence_kinds=["deployment_event", "artifact_digest"],
)
_reg(
    name="check_deployment_status",
    description="Check the status of an in-progress deployment.",
    erp_compat=_ALL,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
)

# ── Memory & inspection ────────────────────────────────────────────────────

_reg(
    name="save_memory",
    description="Save a learned insight into long-term memory.",
    erp_compat=_ALL,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
)
_reg(
    name="search_memory",
    description="Search long-term memory.",
    erp_compat=_ALL,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
)
_reg(
    name="save_user_preference",
    description="Save a user preference into persistent memory.",
    erp_compat=_ALL,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
)
_reg(
    name="save_verified_fact",
    description="Save an evidence-backed schema truth into verified memory.",
    erp_compat=_ALL,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
)
_reg(
    name="inspect_url",
    description="Inspect a page on the connected ERP host.",
    erp_compat=_ALL,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
)
_reg(
    name="browser_snapshot",
    description="Open a connected ERP page in a headless browser.",
    erp_compat=_ALL,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
)

# ── Pri ERP tools ─────────────────────────────────────────────────────────

_reg(
    name="pri_erp_health",
    description="Health check the Pri ERP platform.",
    erp_compat=_PRIERP,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
    evidence_kinds=["health_check"],
)
_reg(
    name="pri_erp_list_instances",
    description="List managed Odoo instances on the Pri ERP platform.",
    erp_compat=_PRIERP,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
    evidence_kinds=["instance_list"],
)
_reg(
    name="pri_erp_get_instance",
    description="Get details of a specific Odoo instance on Pri ERP.",
    erp_compat=_PRIERP,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
    evidence_kinds=["instance_detail"],
)
_reg(
    name="pri_erp_list_addons",
    description="List installed addons on a Pri ERP-managed Odoo instance.",
    erp_compat=_PRIERP,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
    evidence_kinds=["module_list"],
)
_reg(
    name="pri_erp_list_files",
    description="Browse the file tree of a Pri ERP-managed instance.",
    erp_compat=_PRIERP,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
)
_reg(
    name="pri_erp_read_file",
    description="Read a file from a Pri ERP-managed instance.",
    erp_compat=_PRIERP,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
)
_reg(
    name="pri_erp_list_backups",
    description="List available backups for a Pri ERP-managed instance.",
    erp_compat=_PRIERP,
    risk_class=1, is_write=False, requires_approval=False,
    supports_rollback=False, is_idempotent=True,
    evidence_kinds=["backup_list"],
)
_reg(
    name="pri_erp_backup",
    description="Trigger a backup on the Pri ERP platform (class-3).",
    erp_compat=_PRIERP,
    risk_class=3, is_write=True, requires_approval=True,
    supports_rollback=False, is_idempotent=False,
    evidence_kinds=["backup_id"],
)
_reg(
    name="pri_erp_restore",
    description="Restore a backup on the Pri ERP platform (class-3).",
    erp_compat=_PRIERP,
    risk_class=3, is_write=True, requires_approval=True,
    supports_rollback=False, is_idempotent=False,
)
_reg(
    name="pri_erp_install_module",
    description="Install a module on a Pri ERP-managed instance (class-3).",
    erp_compat=_PRIERP,
    risk_class=3, is_write=True, requires_approval=True,
    supports_rollback=False, is_idempotent=False,
    evidence_kinds=["module_state"],
)
_reg(
    name="pri_erp_exec_terminal",
    description="Execute a command in a Pri ERP instance terminal (class-3).",
    erp_compat=_PRIERP,
    risk_class=3, is_write=True, requires_approval=True,
    supports_rollback=False, is_idempotent=False,
    evidence_kinds=["terminal_output"],
)


# ---------------------------------------------------------------------------
# Validation function
# ---------------------------------------------------------------------------

def validate_tool_call(
    tool_name: str,
    erp_type: str = "odoo",
) -> ToolRegistryEntry:
    """Return the registry entry for *tool_name*, raising UnregisteredToolError
    if the tool is unknown or not compatible with *erp_type*.
    """
    entry = TOOL_REGISTRY.get(tool_name)
    if entry is None:
        raise UnregisteredToolError(tool_name)
    if erp_type not in entry.erp_compat:
        raise UnregisteredToolError(
            f"Tool '{tool_name}' is not compatible with erp_type='{erp_type}'"
        )
    return entry


def get_tools_for_erp(erp_type: str) -> list[ToolRegistryEntry]:
    """Return all registered tools compatible with *erp_type*."""
    return [e for e in TOOL_REGISTRY.values() if erp_type in e.erp_compat]


def safe_tools_for_erp(erp_type: str) -> frozenset[str]:
    """Return names of class-1 (read-only) tools for *erp_type*."""
    return frozenset(
        e.name for e in TOOL_REGISTRY.values()
        if erp_type in e.erp_compat and e.risk_class == 1
    )
