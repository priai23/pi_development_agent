"""allow concurrent read-only runs and persist their execution intent"""

from alembic import op
import sqlalchemy as sa


revision = "20260901_run_isolation"
down_revision = "98bf13ea8b89"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("agent_runs")}
    if "intent" not in columns:
        op.add_column("agent_runs", sa.Column("intent", sa.String(length=16), nullable=False, server_default="write"))
    inspector = sa.inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("agent_runs")}
    if "uq_agent_run_active_project" in indexes:
        op.drop_index("uq_agent_run_active_project", table_name="agent_runs")
    op.create_index(
        "uq_agent_run_active_project", "agent_runs", ["project_id", "intent"], unique=True,
        postgresql_where=sa.text("status IN ('running','awaiting_question','awaiting_approval','cancelling') AND intent = 'write'"),
        sqlite_where=sa.text("status IN ('running','awaiting_question','awaiting_approval','cancelling') AND intent = 'write'"),
    )


def downgrade():
    op.drop_index("uq_agent_run_active_project", table_name="agent_runs")
    op.create_index(
        "uq_agent_run_active_project", "agent_runs", ["project_id"], unique=True,
        postgresql_where=sa.text("status IN ('running','awaiting_question','awaiting_approval','cancelling')"),
        sqlite_where=sa.text("status IN ('running','awaiting_question','awaiting_approval','cancelling')"),
    )
    op.drop_column("agent_runs", "intent")
