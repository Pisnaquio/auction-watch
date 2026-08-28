"""Add durable inventory, runs, coverage, lifecycle and delivery records."""

import sqlalchemy as sa
from alembic import op

revision = "0002_operational_domain"
down_revision = "0001_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("source_id", sa.String(256), primary_key=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("enabled IN (0, 1)", name="ck_sources_enabled"),
    )
    op.create_table(
        "auction_groups",
        sa.Column("source_id", sa.String(256), nullable=False),
        sa.Column("group_id", sa.String(256), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("closing_at", sa.DateTime(timezone=True)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("source_id", "group_id"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.source_id"]),
        sa.CheckConstraint("active IN (0, 1)", name="ck_auction_groups_active"),
    )
    op.create_table(
        "auction_lots",
        sa.Column("source_id", sa.String(256), nullable=False),
        sa.Column("auction_id", sa.String(256), nullable=False),
        sa.Column("lot_id", sa.String(256), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("price_value", sa.Numeric(20, 6)),
        sa.Column("price_currency", sa.String(3)),
        sa.Column("price_label", sa.Text(), nullable=False),
        sa.Column("closing_at", sa.DateTime(timezone=True)),
        sa.Column("lot_url", sa.Text(), nullable=False),
        sa.Column("auction_url", sa.Text(), nullable=False),
        sa.Column("image_url", sa.Text()),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("source_id", "auction_id", "lot_id"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.source_id"]),
        sa.ForeignKeyConstraint(
            ["source_id", "auction_id"], ["auction_groups.source_id", "auction_groups.group_id"]
        ),
        sa.CheckConstraint("active IN (0, 1)", name="ck_auction_lots_active"),
        sa.CheckConstraint("price_value IS NULL OR price_value >= 0", name="ck_auction_lots_price"),
    )
    op.create_table(
        "runs",
        sa.Column("run_id", sa.String(256), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'degraded', 'failed')",
            name="ck_runs_status",
        ),
    )
    op.create_table(
        "run_sources",
        sa.Column("run_id", sa.String(256), nullable=False),
        sa.Column("source_id", sa.String(256), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("discovered_count", sa.Integer(), nullable=False),
        sa.Column("processed_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("inventory_authoritative", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.PrimaryKeyConstraint("run_id", "source_id"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.ForeignKeyConstraint(["source_id"], ["sources.source_id"]),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'degraded', 'failed')",
            name="ck_run_sources_status",
        ),
        sa.CheckConstraint(
            "discovered_count >= 0 AND processed_count >= 0 AND failed_count >= 0",
            name="ck_run_sources_counts",
        ),
    )
    op.create_table(
        "coverage_receipts",
        sa.Column("run_id", sa.String(256), nullable=False),
        sa.Column("source_id", sa.String(256), nullable=False),
        sa.Column("group_id", sa.String(256), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("inventory_authoritative", sa.Boolean(), nullable=False),
        sa.Column("lot_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("run_id", "source_id", "group_id"),
        sa.ForeignKeyConstraint(
            ["run_id", "source_id"], ["run_sources.run_id", "run_sources.source_id"]
        ),
        sa.CheckConstraint(
            "status IN ('complete', 'partial', 'failed')", name="ck_coverage_status"
        ),
        sa.CheckConstraint("lot_count >= 0 AND error_count >= 0", name="ck_coverage_counts"),
    )
    op.create_table(
        "auction_snapshots",
        sa.Column("snapshot_id", sa.String(256), primary_key=True),
        sa.Column("run_id", sa.String(256), nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
    )
    op.create_table(
        "opportunities",
        sa.Column("source_id", sa.String(256), nullable=False),
        sa.Column("auction_id", sa.String(256), nullable=False),
        sa.Column("lot_id", sa.String(256), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seen_count", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True)),
        sa.Column("last_present_run_id", sa.String(256)),
        sa.Column("last_absence_run_id", sa.String(256)),
        sa.PrimaryKeyConstraint("source_id", "auction_id", "lot_id"),
        sa.ForeignKeyConstraint(
            ["source_id", "auction_id", "lot_id"],
            ["auction_lots.source_id", "auction_lots.auction_id", "auction_lots.lot_id"],
        ),
        sa.ForeignKeyConstraint(["last_present_run_id"], ["runs.run_id"]),
        sa.ForeignKeyConstraint(["last_absence_run_id"], ["runs.run_id"]),
        sa.CheckConstraint("seen_count >= 1", name="ck_opportunities_seen_count"),
        sa.CheckConstraint("active IN (0, 1)", name="ck_opportunities_active"),
    )
    op.create_table(
        "profile_matches",
        sa.Column("profile_id", sa.String(256), nullable=False),
        sa.Column("source_id", sa.String(256), nullable=False),
        sa.Column("auction_id", sa.String(256), nullable=False),
        sa.Column("lot_id", sa.String(256), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("matched_terms", sa.JSON(), nullable=False),
        sa.Column("matched_fields", sa.JSON(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("profile_id", "source_id", "auction_id", "lot_id"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"]),
        sa.ForeignKeyConstraint(
            ["source_id", "auction_id", "lot_id"],
            ["auction_lots.source_id", "auction_lots.auction_id", "auction_lots.lot_id"],
        ),
        sa.CheckConstraint("score >= 0", name="ck_profile_matches_score"),
    )
    op.create_table(
        "user_opportunity_states",
        sa.Column("profile_id", sa.String(256), nullable=False),
        sa.Column("source_id", sa.String(256), nullable=False),
        sa.Column("auction_id", sa.String(256), nullable=False),
        sa.Column("lot_id", sa.String(256), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("profile_id", "source_id", "auction_id", "lot_id"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"]),
        sa.ForeignKeyConstraint(
            ["source_id", "auction_id", "lot_id"],
            ["auction_lots.source_id", "auction_lots.auction_id", "auction_lots.lot_id"],
        ),
        sa.CheckConstraint(
            "state IN ('none', 'following', 'dismissed')", name="ck_user_opportunity_state"
        ),
        sa.CheckConstraint("version >= 1", name="ck_user_opportunity_version"),
    )
    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("dedupe_key", sa.String(512), nullable=False),
        sa.Column("channel", sa.String(64), nullable=False),
        sa.Column("profile_id", sa.String(256), nullable=False),
        sa.Column("run_id", sa.String(256)),
        sa.Column("snapshot_id", sa.String(256)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.ForeignKeyConstraint(["snapshot_id"], ["auction_snapshots.snapshot_id"]),
        sa.UniqueConstraint("dedupe_key", name="uq_notification_outbox_dedupe"),
        sa.CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'failed', 'uncertain')",
            name="ck_notification_outbox_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_notification_outbox_attempts"),
    )
    for table, columns in {
        "auction_lots": [("source_id",), ("active",), ("closing_at",)],
        "auction_groups": [("active",), ("closing_at",)],
        "opportunities": [("active",), ("last_seen_at",)],
        "profile_matches": [("profile_id",), ("source_id",)],
        "user_opportunity_states": [("profile_id",), ("state",), ("updated_at",)],
        "coverage_receipts": [("source_id",), ("status",)],
        "notification_outbox": [("profile_id",), ("status",), ("next_attempt_at",)],
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column[0]}", table, list(column))


def downgrade() -> None:
    for table in (
        "notification_outbox",
        "user_opportunity_states",
        "profile_matches",
        "opportunities",
        "auction_snapshots",
        "coverage_receipts",
        "run_sources",
        "runs",
        "auction_lots",
        "auction_groups",
        "sources",
    ):
        op.drop_table(table)
