"""Create durable profile storage.

Revision ID: 0001_profiles
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_profiles"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profiles",
        sa.Column("id", sa.String(length=256), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("keywords_any", sa.JSON(), nullable=False),
        sa.Column("keywords_all", sa.JSON(), nullable=False),
        sa.Column("exact_phrases", sa.JSON(), nullable=False),
        sa.Column("exclude_keywords", sa.JSON(), nullable=False),
        sa.Column("boost_keywords", sa.JSON(), nullable=False),
        sa.Column("minimum_score", sa.Integer(), nullable=False),
        sa.Column("price_maximum", sa.Text(), nullable=True),
        sa.Column("price_currency", sa.String(length=3), nullable=True),
        sa.Column("price_on_unknown", sa.String(length=7), nullable=True),
        sa.Column("notification_mode", sa.String(length=32), nullable=False),
        sa.Column("schedule_enabled", sa.Boolean(), nullable=False),
        sa.Column("schedule_times", sa.JSON(), nullable=False),
        sa.Column("schedule_timezone", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("enabled IN (0, 1)", name="ck_profiles_enabled"),
        sa.CheckConstraint("minimum_score >= 0", name="ck_profiles_minimum_score"),
        sa.CheckConstraint("revision > 0", name="ck_profiles_revision"),
        sa.CheckConstraint(
            "notification_mode IN ('disabled', 'matches', 'matches_or_failure')",
            name="ck_profiles_notification_mode",
        ),
        sa.CheckConstraint(
            "(price_maximum IS NULL AND price_currency IS NULL AND price_on_unknown IS NULL)"
            " OR (price_maximum IS NOT NULL AND price_currency IS NOT NULL"
            " AND price_on_unknown IN ('include', 'exclude'))",
            name="ck_profiles_price_pair",
        ),
        sa.CheckConstraint("schedule_enabled IN (0, 1)", name="ck_profiles_schedule_enabled"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "profile_sources",
        sa.Column("profile_id", sa.String(length=256), nullable=False),
        sa.Column("source_id", sa.String(length=256), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_profile_sources_position"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("profile_id", "source_id"),
        sa.UniqueConstraint("profile_id", "position", name="uq_profile_sources_position"),
    )


def downgrade() -> None:
    op.drop_table("profile_sources")
    op.drop_table("profiles")
