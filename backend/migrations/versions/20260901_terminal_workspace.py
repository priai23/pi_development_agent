"""pin terminal sessions to the selected run workspace"""

from alembic import op
import sqlalchemy as sa


revision = "20260901_terminal_workspace"
down_revision = "20260901_artifact_workspace"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("terminal_sessions")}
    if "run_id" not in columns:
        op.add_column(
            "terminal_sessions",
            sa.Column("run_id", sa.String(length=36), sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True),
        )
        op.create_index("ix_terminal_sessions_run_id", "terminal_sessions", ["run_id"])


def downgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("terminal_sessions")}
    if "run_id" in columns:
        op.drop_index("ix_terminal_sessions_run_id", table_name="terminal_sessions")
        op.drop_column("terminal_sessions", "run_id")
