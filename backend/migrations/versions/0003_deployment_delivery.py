"""add encrypted deployment configuration and artifact delivery tokens"""

import sqlalchemy as sa
from alembic import op


revision = "0003_deployment_delivery"
down_revision = "0002_erp_platform"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("instances")}
    if "deployment_config_encrypted" in columns:
        return
    op.add_column("instances", sa.Column("deployment_config_encrypted", sa.Text(), nullable=True))
    op.add_column("deployments", sa.Column("external_job_id", sa.String(128), nullable=True))
    op.create_index("ix_deployments_external_job_id", "deployments", ["external_job_id"])
    op.create_table(
        "discovery_findings",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(64), nullable=False), sa.Column("title", sa.String(), nullable=False), sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False), sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_discovery_findings_project_id", "discovery_findings", ["project_id"])
    op.create_table(
        "technical_specifications",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requirement_id", sa.Integer(), sa.ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False), sa.Column("content", sa.Text(), nullable=False),
        sa.Column("risk_assessment", sa.Text(), nullable=False), sa.Column("status", sa.String(24), nullable=False),
        sa.Column("approved_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_technical_specifications_project_id", "technical_specifications", ["project_id"])
    op.create_table(
        "uat_evidence",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("artifact_id", sa.String(36), sa.ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False), sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("accepted_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_uat_evidence_project_id", "uat_evidence", ["project_id"])


def downgrade():
    raise RuntimeError("Deployment delivery records are intentionally preserved")
