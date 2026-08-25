"""SQLAlchemy persistence models for the first durable schema."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base metadata used by Alembic autogeneration and inspection."""


class ProfileRow(Base):
    __tablename__ = "profiles"
    __table_args__ = (
        CheckConstraint("enabled IN (0, 1)", name="ck_profiles_enabled"),
        CheckConstraint("minimum_score >= 0", name="ck_profiles_minimum_score"),
        CheckConstraint("revision > 0", name="ck_profiles_revision"),
        CheckConstraint(
            "notification_mode IN ('disabled', 'matches', 'matches_or_failure')",
            name="ck_profiles_notification_mode",
        ),
        CheckConstraint(
            "(price_maximum IS NULL AND price_currency IS NULL AND price_on_unknown IS NULL)"
            " OR (price_maximum IS NOT NULL AND price_currency IS NOT NULL"
            " AND price_on_unknown IN ('include', 'exclude'))",
            name="ck_profiles_price_pair",
        ),
        CheckConstraint("schedule_enabled IN (0, 1)", name="ck_profiles_schedule_enabled"),
    )

    id: Mapped[str] = mapped_column(String(256), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
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
        String(256),
        ForeignKey("profiles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
