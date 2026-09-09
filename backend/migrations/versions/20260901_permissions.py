"""persist resource-scoped agent permissions"""

from alembic import op
import sqlalchemy as sa


revision = "20260901_permissions"
down_revision = "20260901_run_isolation"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("permission_grants"):
        op.create_table(
            "permission_grants",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
            sa.Column("resource", sa.String(512), nullable=False),
            sa.Column("decision", sa.String(8), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("project_id", "user_id", "resource", name="uq_permission_grant_scope"),
        )
        op.create_index("ix_permission_grants_project_id", "permission_grants", ["project_id"])
        op.create_index("ix_permission_grants_user_id", "permission_grants", ["user_id"])
    if not inspector.has_table("terminal_sessions"):
        op.create_table(
        "terminal_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requested_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("cwd", sa.String(512), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("output", sa.Text(), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_terminal_sessions_project_id", "terminal_sessions", ["project_id"])
    columns = {column["name"] for column in sa.inspect(bind).get_columns("agent_runs")}
    if "workspace_slug" not in columns:
        op.add_column("agent_runs", sa.Column("workspace_slug", sa.String(200), nullable=True))
        op.create_index("ix_agent_runs_workspace_slug", "agent_runs", ["workspace_slug"])


def downgrade():
    if "workspace_slug" in {column["name"] for column in sa.inspect(op.get_bind()).get_columns("agent_runs")}:
        op.drop_index("ix_agent_runs_workspace_slug", table_name="agent_runs")
        op.drop_column("agent_runs", "workspace_slug")
    op.drop_index("ix_terminal_sessions_project_id", table_name="terminal_sessions")
    op.drop_table("terminal_sessions")
    op.drop_index("ix_permission_grants_user_id", table_name="permission_grants")
    op.drop_index("ix_permission_grants_project_id", table_name="permission_grants")
    op.drop_table("permission_grants")
