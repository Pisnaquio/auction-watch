"""Persist optional profile category filters."""

import sqlalchemy as sa
from alembic import op

revision = "0006_profile_categories"
down_revision = "0005_run_engine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("profiles") as batch:
        batch.add_column(sa.Column("categories", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("profiles") as batch:
        batch.drop_column("categories")
