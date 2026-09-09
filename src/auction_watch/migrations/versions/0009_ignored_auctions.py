"""Let the user name auctions that should never be scanned."""

import sqlalchemy as sa
from alembic import op

revision = "0009_ignored_auctions"
down_revision = "0008_profile_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ignored_auctions",
        sa.Column("pattern", sa.String(256), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ignored_auctions")
