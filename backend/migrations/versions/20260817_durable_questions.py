"""durable questions and awaiting-question run state

Revision ID: 20260817_durable_questions
Revises: 30f6ebcb7008
"""
from alembic import op
import sqlalchemy as sa


revision = "20260817_durable_questions"
down_revision = "30f6ebcb7008"
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table("agent_questions"):
        return
    op.drop_index("uq_agent_run_active_project", table_name="agent_runs")
    op.create_index(
        "uq_agent_run_active_project", "agent_runs", ["project_id"], unique=True,
        postgresql_where=sa.text("status IN ('running','awaiting_question','awaiting_approval','cancelling')"),
    )
    op.create_table(
        "agent_questions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_id", sa.String(length=36), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("requested_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_questions_run_id", "agent_questions", ["run_id"], unique=True)
    op.create_index("ix_agent_questions_project_id", "agent_questions", ["project_id"])
    op.create_index("ix_agent_questions_status", "agent_questions", ["status"])


def downgrade():
    op.drop_index("uq_agent_run_active_project", table_name="agent_runs")
    op.create_index(
        "uq_agent_run_active_project", "agent_runs", ["project_id"], unique=True,
        postgresql_where=sa.text("status IN ('running','awaiting_approval','cancelling')"),
    )
    op.drop_index("ix_agent_questions_status", table_name="agent_questions")
    op.drop_index("ix_agent_questions_project_id", table_name="agent_questions")
    op.drop_index("ix_agent_questions_run_id", table_name="agent_questions")
    op.drop_table("agent_questions")
