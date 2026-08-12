"""add runs, lifecycle, connection operations, and deployment records"""

import sqlalchemy as sa
from alembic import op

from models import Base


revision = "0002_erp_platform"
down_revision = "0001_hardened_schema"
branch_labels = None
depends_on = None


def upgrade():
    # A fresh 0001 install creates the current metadata. Existing hardened
    # installations do not have agent_runs and need this incremental upgrade.
    if sa.inspect(op.get_bind()).has_table("agent_runs"):
        return
    op.add_column("users", sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("organizations", sa.Column("monthly_budget_usd", sa.Float(), nullable=True))
    op.add_column("organizations", sa.Column("budget_warning_percent", sa.Integer(), nullable=False, server_default="80"))
    op.add_column("organization_memberships", sa.Column("is_approver", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("user_sessions", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.add_column("projects", sa.Column("phase", sa.String(32), nullable=False, server_default="discovery"))
    op.add_column("projects", sa.Column("monthly_budget_usd", sa.Float(), nullable=True))

    for column in (
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("environment", sa.String(16), nullable=False, server_default="staging"),
        sa.Column("hosting_type", sa.String(16), nullable=False, server_default="on_premise"),
        sa.Column("auth_method", sa.String(16), nullable=False, server_default="xmlrpc"),
        sa.Column("status", sa.String(16), nullable=False, server_default="connected"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version_info", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("capabilities", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("bridge_status", sa.String(24), nullable=False, server_default="not_configured"),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
    ):
        op.add_column("instances", column)
    op.alter_column("instances", "username", existing_type=sa.String(), nullable=True)
    op.alter_column("instances", "password_encrypted", existing_type=sa.Text(), nullable=True)

    for column in (
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
    ):
        op.add_column("pending_actions", column)
    op.create_unique_constraint("uq_pending_action_idempotency", "pending_actions", ["idempotency_key"])

    for column in (
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("risk_class", sa.String(1), nullable=True),
        sa.Column("result", sa.String(24), nullable=True),
        sa.Column("support_id", sa.String(36), nullable=True),
    ):
        op.add_column("audit_events", column)
    op.create_foreign_key("fk_audit_organization", "audit_events", "organizations", ["organization_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_audit_events_organization_id", "audit_events", ["organization_id"])
    op.create_index("ix_audit_events_support_id", "audit_events", ["support_id"])

    Base.metadata.create_all(bind=op.get_bind())
    op.add_column("pending_actions", sa.Column("run_id", sa.String(36), nullable=True))
    op.create_foreign_key("fk_pending_action_run", "pending_actions", "agent_runs", ["run_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_pending_actions_run_id", "pending_actions", ["run_id"])


def downgrade():
    raise RuntimeError("This data-preserving platform migration is not automatically reversible")
