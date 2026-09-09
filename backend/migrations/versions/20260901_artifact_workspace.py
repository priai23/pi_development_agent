"""keep artifacts addressable when produced in an isolated run workspace"""

from alembic import op
import sqlalchemy as sa


revision = "20260901_artifact_workspace"
down_revision = "20260901_permissions"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("artifacts")}
    if "workspace_slug" not in columns:
        op.add_column("artifacts", sa.Column("workspace_slug", sa.String(length=200), nullable=True))
        op.create_index("ix_artifacts_workspace_slug", "artifacts", ["workspace_slug"])


def downgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("artifacts")}
    if "workspace_slug" in columns:
        op.drop_index("ix_artifacts_workspace_slug", table_name="artifacts")
        op.drop_column("artifacts", "workspace_slug")
