"""Track when each profile's opportunities were last acknowledged."""

import sqlalchemy as sa
from alembic import op

revision = "0008_profile_reviews"
down_revision = "0007_async_runs_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profile_reviews",
        sa.Column("profile_id", sa.String(256), primary_key=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"]),
    )
    # Existing profiles start acknowledged: without this every match already in
    # storage would surface as "new" the first time the upgraded UI is opened.
    op.execute(
        "INSERT INTO profile_reviews (profile_id, reviewed_at) "
        "SELECT id, CURRENT_TIMESTAMP FROM profiles"
    )


def downgrade() -> None:
    op.drop_table("profile_reviews")
