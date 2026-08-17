"""pipeline_foundation — SourceSnapshot, SourceSymbol, RunSpecification,
AcceptanceCheck, ToolExecution tables; extend AgentRun, Artifact, AgentMemory.

Revision ID: 20260817_pipeline_foundation
Revises: 30f6ebcb7008
Create Date: 2026-08-17
"""
from alembic import op
import sqlalchemy as sa

revision = "20260817_pipeline_foundation"
down_revision = ("30f6ebcb7008", "20260817_workspace_base_revision")
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Helper: idempotent column addition
# ---------------------------------------------------------------------------

def _add_column_if_missing(table: str, column: sa.Column) -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns(table)}
    if column.name not in cols:
        op.add_column(table, column)


def _table_exists(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------

def upgrade() -> None:
    # ------------------------------------------------------------------
    # source_snapshots
    # ------------------------------------------------------------------
    if not _table_exists("source_snapshots"):
        op.create_table(
            "source_snapshots",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("instance_id", sa.Integer, sa.ForeignKey("instances.id", ondelete="CASCADE"), nullable=False),
            sa.Column("odoo_version", sa.String(16), nullable=False),
            sa.Column("odoo_edition", sa.String(16), nullable=False),
            sa.Column("db_uuid", sa.String(64), nullable=True),
            sa.Column("server_serial", sa.String(64), nullable=True),
            sa.Column("installed_modules", sa.JSON, nullable=False, server_default="{}"),
            sa.Column("addon_digests", sa.JSON, nullable=False, server_default="{}"),
            sa.Column("fingerprint", sa.String(64), nullable=False),
            sa.Column("runner_identity", sa.String(128), nullable=True),
            sa.Column("status", sa.String(24), nullable=False, server_default="pending_index"),
            sa.Column("symbol_count", sa.Integer, nullable=False, server_default="0"),
            sa.Column("index_error", sa.Text, nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_source_snapshots_instance_id", "source_snapshots", ["instance_id"])
        op.create_index("ix_source_snapshots_fingerprint", "source_snapshots", ["fingerprint"])

    # ------------------------------------------------------------------
    # source_symbols
    # ------------------------------------------------------------------
    if not _table_exists("source_symbols"):
        op.create_table(
            "source_symbols",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("snapshot_id", sa.String(36), sa.ForeignKey("source_snapshots.id", ondelete="CASCADE"), nullable=False),
            sa.Column("module", sa.String(128), nullable=False),
            sa.Column("model", sa.String(128), nullable=True),
            sa.Column("kind", sa.String(32), nullable=False),
            sa.Column("name", sa.String(256), nullable=False),
            sa.Column("path", sa.String(512), nullable=True),
            sa.Column("line_start", sa.Integer, nullable=True),
            sa.Column("line_end", sa.Integer, nullable=True),
            sa.Column("digest", sa.String(64), nullable=True),
            sa.Column("payload", sa.JSON, nullable=False, server_default="{}"),
        )
        op.create_index("ix_source_symbols_snapshot_kind", "source_symbols", ["snapshot_id", "kind"])
        op.create_index("ix_source_symbols_module_model", "source_symbols", ["module", "model"])
        op.create_index("ix_source_symbols_name", "source_symbols", ["name"])

    # ------------------------------------------------------------------
    # run_specifications
    # ------------------------------------------------------------------
    if not _table_exists("run_specifications"):
        op.create_table(
            "run_specifications",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("snapshot_id", sa.String(36), sa.ForeignKey("source_snapshots.id", ondelete="SET NULL"), nullable=True),
            sa.Column("requirements", sa.JSON, nullable=False, server_default="[]"),
            sa.Column("changes", sa.JSON, nullable=False, server_default="[]"),
            sa.Column("acceptance_check_ids", sa.JSON, nullable=False, server_default="[]"),
            sa.Column("digest", sa.String(64), nullable=False),
            sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("run_id", name="uq_run_specifications_run_id"),
        )
        op.create_index("ix_run_specifications_run_id", "run_specifications", ["run_id"])
        op.create_index("ix_run_specifications_project_id", "run_specifications", ["project_id"])

    # ------------------------------------------------------------------
    # acceptance_checks
    # ------------------------------------------------------------------
    if not _table_exists("acceptance_checks"):
        op.create_table(
            "acceptance_checks",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("task_id", sa.String(64), nullable=True),
            sa.Column("kind", sa.String(32), nullable=False),
            sa.Column("spec_target", sa.JSON, nullable=False, server_default="{}"),
            sa.Column("required", sa.Boolean, nullable=False, server_default="1"),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("evidence", sa.JSON, nullable=False, server_default="[]"),
            sa.Column("result_detail", sa.Text, nullable=True),
            sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_acceptance_checks_run_kind", "acceptance_checks", ["run_id", "kind"])

    # ------------------------------------------------------------------
    # tool_executions
    # ------------------------------------------------------------------
    if not _table_exists("tool_executions"):
        op.create_table(
            "tool_executions",
            sa.Column("operation_id", sa.String(64), primary_key=True),
            sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("task_id", sa.String(64), nullable=True),
            sa.Column("tool_call_id", sa.String(128), nullable=False),
            sa.Column("tool_name", sa.String(128), nullable=False),
            sa.Column("args_digest", sa.String(64), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="preparing"),
            sa.Column("structured_result", sa.JSON, nullable=True),
            sa.Column("retryable", sa.Boolean, nullable=False, server_default="0"),
            sa.Column("error_code", sa.String(64), nullable=True),
            sa.Column("error_message", sa.Text, nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_tool_executions_run_id", "tool_executions", ["run_id"])

    # ------------------------------------------------------------------
    # agent_runs — pipeline grounding columns
    # ------------------------------------------------------------------
    _add_column_if_missing("agent_runs", sa.Column("source_snapshot_id", sa.String(36), nullable=True))
    _add_column_if_missing("agent_runs", sa.Column("specification_id", sa.Integer, nullable=True))
    _add_column_if_missing("agent_runs", sa.Column("stage", sa.String(32), nullable=False, server_default="queued"))
    _add_column_if_missing("agent_runs", sa.Column("last_progress_at", sa.DateTime(timezone=True), nullable=True))
    _add_column_if_missing("agent_runs", sa.Column("current_operation", sa.String(128), nullable=True))
    _add_column_if_missing("agent_runs", sa.Column("operation_deadline_at", sa.DateTime(timezone=True), nullable=True))

    # ------------------------------------------------------------------
    # artifacts — build traceability columns
    # ------------------------------------------------------------------
    _add_column_if_missing("artifacts", sa.Column("spec_digest", sa.String(64), nullable=True))
    _add_column_if_missing("artifacts", sa.Column("source_snapshot_digest", sa.String(64), nullable=True))
    _add_column_if_missing("artifacts", sa.Column("build_fingerprint", sa.JSON, nullable=True))

    # ------------------------------------------------------------------
    # agent_memories — evidence backing columns
    # ------------------------------------------------------------------
    _add_column_if_missing("agent_memories", sa.Column("evidence_type", sa.String(32), nullable=True))
    _add_column_if_missing("agent_memories", sa.Column("evidence_ref_id", sa.String(64), nullable=True))
    _add_column_if_missing("agent_memories", sa.Column("snapshot_scope_id", sa.String(36), nullable=True))
    _add_column_if_missing("agent_memories", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    _add_column_if_missing("agent_memories", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------

def downgrade() -> None:
    # Drop new tables (reverse order of FK dependencies)
    for tbl in ("tool_executions", "acceptance_checks", "run_specifications", "source_symbols", "source_snapshots"):
        if _table_exists(tbl):
            op.drop_table(tbl)

    # Drop added columns — wrapped in try/except for partial rollback safety
    for col in ("source_snapshot_id", "specification_id", "stage", "last_progress_at", "current_operation", "operation_deadline_at"):
        try:
            op.drop_column("agent_runs", col)
        except Exception:
            pass

    for col in ("spec_digest", "source_snapshot_digest", "build_fingerprint"):
        try:
            op.drop_column("artifacts", col)
        except Exception:
            pass

    for col in ("evidence_type", "evidence_ref_id", "snapshot_scope_id", "verified_at", "expires_at"):
        try:
            op.drop_column("agent_memories", col)
        except Exception:
            pass
