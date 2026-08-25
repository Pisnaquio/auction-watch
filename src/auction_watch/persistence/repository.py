"""Transactional repository for persisted search profiles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auction_watch.core.models import PriceFilter, SearchProfile, SearchSchedule
from auction_watch.persistence.database import Database
from auction_watch.persistence.models import ProfileRow, ProfileSourceRow


class ProfileRepositoryError(RuntimeError):
    """Base class for profile persistence failures."""


class ProfileAlreadyExistsError(ProfileRepositoryError):
    """Raised when creating a profile with an existing ID."""


class ProfileNotFoundError(ProfileRepositoryError):
    """Raised when an expected profile does not exist."""


class ProfileRevisionConflictError(ProfileRepositoryError):
    """Raised when an optimistic revision does not match the stored revision."""


@dataclass(frozen=True)
class StoredProfile:
    profile: SearchProfile
    revision: int
    created_at: datetime
    updated_at: datetime


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class ProfileRepository:
    """Persist and reconstruct valid Pydantic profiles transactionally."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def _insert_sources(self, session: Session, profile: SearchProfile) -> None:
        session.add_all(
            ProfileSourceRow(profile_id=profile.id, source_id=source_id, position=position)
            for position, source_id in enumerate(profile.source_ids)
        )

    def _row_to_stored(self, session: Session, row: ProfileRow) -> StoredProfile:
        sources = session.scalars(
            select(ProfileSourceRow)
            .where(ProfileSourceRow.profile_id == row.id)
            .order_by(ProfileSourceRow.position)
        ).all()
        price_filter = None
        if row.price_maximum is not None:
            price_filter = PriceFilter(
                maximum=Decimal(row.price_maximum),
                currency=row.price_currency,
                on_unknown=row.price_on_unknown,
            )
        profile = SearchProfile(
            id=row.id,
            name=row.name,
            enabled=row.enabled,
            keywords_any=row.keywords_any,
            keywords_all=row.keywords_all,
            exact_phrases=row.exact_phrases,
            exclude_keywords=row.exclude_keywords,
            boost_keywords=row.boost_keywords,
            source_ids=[source.source_id for source in sources],
            minimum_score=row.minimum_score,
            price_filter=price_filter,
            notification_mode=row.notification_mode,
            schedule=SearchSchedule(
                enabled=row.schedule_enabled,
                times=row.schedule_times,
                timezone=row.schedule_timezone,
            ),
        )
        return StoredProfile(
            profile=profile,
            revision=row.revision,
            created_at=_as_utc(row.created_at),
            updated_at=_as_utc(row.updated_at),
        )

    def _apply_profile(self, row: ProfileRow, profile: SearchProfile, now: datetime) -> None:
        price_filter = profile.price_filter
        row.id = profile.id
        row.name = profile.name
        row.enabled = profile.enabled
        row.keywords_any = list(profile.keywords_any)
        row.keywords_all = list(profile.keywords_all)
        row.exact_phrases = list(profile.exact_phrases)
        row.exclude_keywords = list(profile.exclude_keywords)
        row.boost_keywords = dict(profile.boost_keywords)
        row.minimum_score = profile.minimum_score
        row.price_maximum = str(price_filter.maximum) if price_filter else None
        row.price_currency = price_filter.currency if price_filter else None
        row.price_on_unknown = price_filter.on_unknown if price_filter else None
        row.notification_mode = profile.notification_mode
        row.schedule_enabled = profile.schedule.enabled
        row.schedule_times = list(profile.schedule.times)
        row.schedule_timezone = profile.schedule.timezone
        row.updated_at = now

    def create(self, profile: SearchProfile) -> StoredProfile:
        now = _utc_now()
        row = ProfileRow(
            id=profile.id,
            revision=1,
            created_at=now,
            updated_at=now,
            name=profile.name,
            enabled=profile.enabled,
            keywords_any=[],
            keywords_all=[],
            exact_phrases=[],
            exclude_keywords=[],
            boost_keywords={},
            minimum_score=0,
            notification_mode=profile.notification_mode,
            schedule_enabled=profile.schedule.enabled,
            schedule_times=[],
            schedule_timezone=profile.schedule.timezone,
        )
        self._apply_profile(row, profile, now)
        try:
            with self._database.sessions.begin() as session:
                if session.get(ProfileRow, profile.id) is not None:
                    raise ProfileAlreadyExistsError(profile.id)
                session.add(row)
                session.flush()
                self._insert_sources(session, profile)
                session.flush()
                return self._row_to_stored(session, row)
        except IntegrityError as exc:
            raise ProfileAlreadyExistsError(profile.id) from exc

    def get(self, profile_id: str) -> StoredProfile | None:
        with self._database.sessions.begin() as session:
            row = session.get(ProfileRow, profile_id)
            return None if row is None else self._row_to_stored(session, row)

    def list(self) -> list[StoredProfile]:
        with self._database.sessions.begin() as session:
            rows = session.scalars(select(ProfileRow).order_by(ProfileRow.id)).all()
            return [self._row_to_stored(session, row) for row in rows]

    def replace(self, profile: SearchProfile, expected_revision: int) -> StoredProfile:
        now = _utc_now()
        with self._database.sessions.begin() as session:
            row = session.get(ProfileRow, profile.id)
            if row is None:
                raise ProfileNotFoundError(profile.id)
            if row.revision != expected_revision:
                raise ProfileRevisionConflictError(profile.id)
            row.revision += 1
            self._apply_profile(row, profile, now)
            session.execute(
                delete(ProfileSourceRow).where(ProfileSourceRow.profile_id == profile.id)
            )
            session.flush()
            self._insert_sources(session, profile)
            session.flush()
            return self._row_to_stored(session, row)

    def delete(self, profile_id: str, expected_revision: int) -> None:
        with self._database.sessions.begin() as session:
            row = session.get(ProfileRow, profile_id)
            if row is None:
                raise ProfileNotFoundError(profile_id)
            if row.revision != expected_revision:
                raise ProfileRevisionConflictError(profile_id)
            session.delete(row)
