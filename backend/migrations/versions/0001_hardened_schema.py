"""create the hardened schema without deleting existing installations"""

from alembic import op
import sqlalchemy as sa

from models import Base


revision = "0001_hardened_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    # Development databases can predate Alembic while already containing data.
    # create_all is safe for a fresh database; never drop an existing install.
    if not sa.inspect(bind).has_table("users"):
        Base.metadata.create_all(bind=bind)


def downgrade():
    Base.metadata.drop_all(bind=op.get_bind())
