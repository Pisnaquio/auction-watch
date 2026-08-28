"""SQLAlchemy models for the complete durable Auction Watch domain."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ProfileRow(Base):
    __tablename__ = "profiles"
    __table_args__ = (
        CheckConstraint("enabled IN (0, 1)", name="ck_profiles_enabled"),
        CheckConstraint("kind IN ('system', 'user')", name="ck_profiles_kind"),
        CheckConstraint("locked IN (0, 1)", name="ck_profiles_locked"),
        CheckConstraint("seed_version >= 0", name="ck_profiles_seed_version"),
        CheckConstraint("minimum_score >= 0", name="ck_profiles_minimum_score"),
        CheckConstraint("revision > 0", name="ck_profiles_revision"),
        CheckConstraint(
            "notification_mode IN ('disabled', 'matches', 'matches_or_failure')",
            name="ck_profiles_notification_mode",
        ),
        CheckConstraint(
            "(price_maximum IS NULL AND price_currency IS NULL "
            "AND price_on_unknown IS NULL) OR "
            "(price_maximum IS NOT NULL AND price_currency IS NOT NULL "
            "AND price_on_unknown IN ('include', 'exclude'))",
            name="ck_profiles_price_pair",
        ),
        CheckConstraint("schedule_enabled IN (0, 1)", name="ck_profiles_schedule_enabled"),
    )
    id: Mapped[str] = mapped_column(String(256), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    seed_key: Mapped[str | None] = mapped_column(String(256))
    seed_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    keywords_any: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    keywords_all: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    exact_phrases: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    exclude_keywords: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    boost_keywords: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False)
    minimum_score: Mapped[int] = mapped_column(Integer, nullable=False)
    price_maximum: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    price_on_unknown: Mapped[str | None] = mapped_column(String(7), nullable=True)
    notification_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    schedule_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    schedule_times: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    schedule_timezone: Mapped[str] = mapped_column(String(128), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProfileSourceRow(Base):
    __tablename__ = "profile_sources"
    __table_args__ = (
        UniqueConstraint("profile_id", "position", name="uq_profile_sources_position"),
        CheckConstraint("position >= 0", name="ck_profile_sources_position"),
    )
    profile_id: Mapped[str] = mapped_column(
        String(256), ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class SourceRow(Base):
    __tablename__ = "sources"
    __table_args__ = (CheckConstraint("enabled IN (0, 1)", name="ck_sources_enabled"),)
    source_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuctionGroupRow(Base):
    __tablename__ = "auction_groups"
    __table_args__ = (CheckConstraint("active IN (0, 1)", name="ck_auction_groups_active"),)
    source_id: Mapped[str] = mapped_column(
        String(256), ForeignKey("sources.source_id"), primary_key=True
    )
    group_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False, default="")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    closing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuctionLotRow(Base):
    __tablename__ = "auction_lots"
    __table_args__ = (
        CheckConstraint("active IN (0, 1)", name="ck_auction_lots_active"),
        CheckConstraint("price_value IS NULL OR price_value >= 0", name="ck_auction_lots_price"),
        ForeignKeyConstraint(
            ["source_id", "auction_id"],
            ["auction_groups.source_id", "auction_groups.group_id"],
        ),
    )
    source_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    auction_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    lot_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[str] = mapped_column(Text, nullable=False, default="")
    price_value: Mapped[str | None] = mapped_column(Numeric(20, 6), nullable=True)
    price_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    price_label: Mapped[str] = mapped_column(Text, nullable=False, default="")
    closing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lot_url: Mapped[str] = mapped_column(Text, nullable=False)
    auction_url: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RunRow(Base):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'degraded', 'failed')",
            name="ck_runs_status",
        ),
    )
    run_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class RunSourceRow(Base):
    __tablename__ = "run_sources"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'degraded', 'failed')",
            name="ck_run_sources_status",
        ),
        CheckConstraint(
            "discovered_count >= 0 AND processed_count >= 0 AND failed_count >= 0",
            name="ck_run_sources_counts",
        ),
        CheckConstraint("inventory_authoritative IN (0, 1)", name="ck_run_sources_authority"),
    )
    run_id: Mapped[str] = mapped_column(String(256), ForeignKey("runs.run_id"), primary_key=True)
    source_id: Mapped[str] = mapped_column(
        String(256), ForeignKey("sources.source_id"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    discovered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inventory_authoritative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class CoverageReceiptRow(Base):
    __tablename__ = "coverage_receipts"
    __table_args__ = (
        CheckConstraint("status IN ('complete', 'partial', 'failed')", name="ck_coverage_status"),
        CheckConstraint("lot_count >= 0 AND error_count >= 0", name="ck_coverage_counts"),
        CheckConstraint("inventory_authoritative IN (0, 1)", name="ck_coverage_authority"),
    )
    run_id: Mapped[str] = mapped_column(String(256), ForeignKey("runs.run_id"), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    group_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    inventory_authoritative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    lot_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuctionSnapshotRow(Base):
    __tablename__ = "auction_snapshots"
    snapshot_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(256), ForeignKey("runs.run_id"), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    payload_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OpportunityRow(Base):
    __tablename__ = "opportunities"
    __table_args__ = (
        CheckConstraint("seen_count >= 1", name="ck_opportunities_seen_count"),
        CheckConstraint("active IN (0, 1)", name="ck_opportunities_active"),
    )
    source_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    auction_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    lot_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    seen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_present_run_id: Mapped[str | None] = mapped_column(String(256), ForeignKey("runs.run_id"))
    last_absence_run_id: Mapped[str | None] = mapped_column(String(256), ForeignKey("runs.run_id"))


class ProfileMatchRow(Base):
    __tablename__ = "profile_matches"
    profile_id: Mapped[str] = mapped_column(
        String(256), ForeignKey("profiles.id"), primary_key=True
    )
    source_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    auction_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    lot_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    matched_terms: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    matched_fields: Mapped[dict[str, list[str]]] = mapped_column(JSON, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UserOpportunityStateRow(Base):
    __tablename__ = "user_opportunity_states"
    __table_args__ = (
        CheckConstraint(
            "state IN ('none', 'following', 'dismissed')", name="ck_user_opportunity_state"
        ),
        CheckConstraint("version >= 1", name="ck_user_opportunity_version"),
    )
    profile_id: Mapped[str] = mapped_column(
        String(256), ForeignKey("profiles.id"), primary_key=True
    )
    source_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    auction_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    lot_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NotificationOutboxRow(Base):
    __tablename__ = "notification_outbox"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_notification_outbox_dedupe"),
        CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'failed', 'uncertain')",
            name="ck_notification_outbox_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_notification_outbox_attempts"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dedupe_key: Mapped[str] = mapped_column(String(512), nullable=False)
    channel: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_id: Mapped[str] = mapped_column(String(256), ForeignKey("profiles.id"), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(256), ForeignKey("runs.run_id"))
    snapshot_id: Mapped[str | None] = mapped_column(
        String(256), ForeignKey("auction_snapshots.snapshot_id")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
