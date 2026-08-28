"""Persist reusable risk and contextual profile rules."""

import sqlalchemy as sa
from alembic import op

revision = "0004_contextual_profile_rules"
down_revision = "0003_profile_kinds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("profiles") as batch:
        batch.add_column(sa.Column("risk_keywords", sa.JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("context_rules", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("profiles") as batch:
        batch.drop_column("context_rules")
        batch.drop_column("risk_keywords")
