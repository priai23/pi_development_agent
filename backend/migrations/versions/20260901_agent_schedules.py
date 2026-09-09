"""persist recurring agent prompts"""

from alembic import op
import sqlalchemy as sa


revision = "20260901_agent_schedules"
down_revision = "20260901_agent_subtasks"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if sa.inspect(bind).has_table("agent_schedules"):
        return
    op.create_table(
        "agent_schedules",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requested_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False, server_default="3600"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_id", sa.String(length=36), sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_schedules_project_id", "agent_schedules", ["project_id"])
    op.create_index("ix_agent_schedules_enabled", "agent_schedules", ["enabled"])
    op.create_index("ix_agent_schedules_next_run_at", "agent_schedules", ["next_run_at"])


def downgrade():
    if sa.inspect(op.get_bind()).has_table("agent_schedules"):
        op.drop_index("ix_agent_schedules_next_run_at", table_name="agent_schedules")
        op.drop_index("ix_agent_schedules_enabled", table_name="agent_schedules")
        op.drop_index("ix_agent_schedules_project_id", table_name="agent_schedules")
        op.drop_table("agent_schedules")
