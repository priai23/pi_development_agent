"""persist each agent run's workspace baseline

Revision ID: 20260817_workspace_base_revision
Revises: 20260817_durable_questions
"""

from alembic import op
import sqlalchemy as sa


revision = "20260817_workspace_base_revision"
down_revision = "20260817_durable_questions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("agent_runs")}
    if "workspace_base_revision" not in columns:
        op.add_column("agent_runs", sa.Column("workspace_base_revision", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "workspace_base_revision")
