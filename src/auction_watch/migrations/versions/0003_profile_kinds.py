"""Add system/user profile identity and versioned seed metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0003_profile_kinds"
down_revision = "0002_operational_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("profiles", recreate="always") as batch:
        batch.add_column(sa.Column("kind", sa.String(16), nullable=False, server_default="user"))
        batch.add_column(
            sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("seed_key", sa.String(256), nullable=True))
        batch.add_column(
            sa.Column("seed_version", sa.Integer(), nullable=False, server_default="0")
        )
        batch.create_check_constraint("ck_profiles_kind", "kind IN ('system', 'user')")
        batch.create_check_constraint("ck_profiles_locked", "locked IN (0, 1)")
        batch.create_check_constraint("ck_profiles_seed_version", "seed_version >= 0")


def downgrade() -> None:
    with op.batch_alter_table("profiles", recreate="always") as batch:
        batch.drop_constraint("ck_profiles_seed_version", type_="check")
        batch.drop_constraint("ck_profiles_locked", type_="check")
        batch.drop_constraint("ck_profiles_kind", type_="check")
        batch.drop_column("seed_version")
        batch.drop_column("seed_key")
        batch.drop_column("locked")
        batch.drop_column("kind")
