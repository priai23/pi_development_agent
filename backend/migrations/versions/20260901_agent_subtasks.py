"""persist child-agent executions for supervisor task graph nodes"""

from alembic import op
import sqlalchemy as sa


revision = "20260901_agent_subtasks"
down_revision = "20260901_terminal_workspace"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("agent_subtasks"):
        op.create_table(
            "agent_subtasks",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("parent_run_id", sa.String(length=36), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("task_id", sa.String(length=64), nullable=False),
            sa.Column("title", sa.String(length=300), nullable=False),
            sa.Column("role", sa.String(length=64), nullable=False, server_default="implementation_specialist"),
            sa.Column("thread_id", sa.String(length=200), nullable=False, unique=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
            sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
            sa.Column("result", sa.JSON(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("parent_run_id", "task_id", name="uq_agent_subtask_parent_task"),
        )
        op.create_index("ix_agent_subtasks_parent_run_id", "agent_subtasks", ["parent_run_id"])
        op.create_index("ix_agent_subtasks_project_id", "agent_subtasks", ["project_id"])
        op.create_index("ix_agent_subtasks_status", "agent_subtasks", ["status"])


def downgrade():
    if sa.inspect(op.get_bind()).has_table("agent_subtasks"):
        op.drop_index("ix_agent_subtasks_status", table_name="agent_subtasks")
        op.drop_index("ix_agent_subtasks_project_id", table_name="agent_subtasks")
        op.drop_index("ix_agent_subtasks_parent_run_id", table_name="agent_subtasks")
        op.drop_table("agent_subtasks")
