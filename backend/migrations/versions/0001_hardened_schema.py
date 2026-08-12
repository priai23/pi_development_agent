"""reset prototype data and create hardened schema"""

from alembic import op

from models import Base


revision = "0001_hardened_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
    Base.metadata.create_all(bind=bind)


def downgrade():
    Base.metadata.drop_all(bind=op.get_bind())
