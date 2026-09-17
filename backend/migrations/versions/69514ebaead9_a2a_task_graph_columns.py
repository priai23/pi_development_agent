"""a2a_task_graph_columns

Revision ID: 69514ebaead9
Revises: 0003_deployment_delivery
Create Date: 2026-08-14 11:42:30.759682
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '69514ebaead9'
down_revision = '0003_deployment_delivery'
branch_labels = None
depends_on = None


def upgrade():
    # LangGraph owns its checkpoint tables; application migrations must not drop them.
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("agent_runs")}
    additions = {
        "task_graph": sa.Column("task_graph", sa.JSON(), nullable=True),
        "active_task_id": sa.Column("active_task_id", sa.String(length=64), nullable=True),
        "subtask_heartbeat_at": sa.Column("subtask_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        "task_retries": sa.Column("task_retries", sa.JSON(), nullable=True),
    }
    for name, column in additions.items():
        if name not in columns:
            op.add_column("agent_runs", column)


def downgrade():
    for column_name in ('task_retries', 'subtask_heartbeat_at', 'active_task_id', 'task_graph'):
        if column_name in {column['name'] for column in sa.inspect(op.get_bind()).get_columns('agent_runs')}:
            op.drop_column('agent_runs', column_name)
