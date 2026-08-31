"""Transactional repository for persisted search profiles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

from sqlalchemy import delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auction_watch.core.models import ContextRule, PriceFilter, SearchProfile, SearchSchedule
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


class SystemProfileImmutableError(ProfileRepositoryError):
    """Raised when protected system identity metadata would change."""


class SystemProfileDeleteError(ProfileRepositoryError):
    """Raised when a locked system profile is deleted."""


class ProfilePersistenceError(ProfileRepositoryError):
    """Raised when SQLite rejects a non-conflict profile write."""


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
            kind=row.kind,
            locked=row.locked,
            seed_key=row.seed_key,
            seed_version=row.seed_version,
            enabled=row.enabled,
            keywords_any=row.keywords_any,
            keywords_all=row.keywords_all,
            exact_phrases=row.exact_phrases,
            exclude_keywords=row.exclude_keywords,
            categories=row.categories,
            boost_keywords=row.boost_keywords,
            risk_keywords=row.risk_keywords,
            context_rules=tuple(ContextRule(**rule) for rule in row.context_rules),
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

    def _profile_values(self, profile: SearchProfile, now: datetime) -> dict[str, object]:
        price_filter = profile.price_filter
        return {
            "name": profile.name,
            "kind": profile.kind,
            "locked": profile.locked,
            "seed_key": profile.seed_key,
            "seed_version": profile.seed_version,
            "enabled": profile.enabled,
            "keywords_any": list(profile.keywords_any),
            "keywords_all": list(profile.keywords_all),
            "exact_phrases": list(profile.exact_phrases),
            "exclude_keywords": list(profile.exclude_keywords),
            "categories": list(profile.categories),
            "boost_keywords": dict(profile.boost_keywords),
            "risk_keywords": dict(profile.risk_keywords),
            "context_rules": [rule.model_dump() for rule in profile.context_rules],
            "minimum_score": profile.minimum_score,
            "price_maximum": str(price_filter.maximum) if price_filter else None,
            "price_currency": price_filter.currency if price_filter else None,
            "price_on_unknown": price_filter.on_unknown if price_filter else None,
            "notification_mode": profile.notification_mode,
            "schedule_enabled": profile.schedule.enabled,
            "schedule_times": list(profile.schedule.times),
            "schedule_timezone": profile.schedule.timezone,
            "updated_at": now,
        }

    def _exists_after_rollback(self, profile_id: str) -> bool:
        with self._database.sessions.begin() as session:
            return session.get(ProfileRow, profile_id) is not None

    def create(self, profile: SearchProfile) -> StoredProfile:
        now = _utc_now()
        row = ProfileRow(
            id=profile.id,
            revision=1,
            created_at=now,
            updated_at=now,
            name=profile.name,
            kind=profile.kind,
            locked=profile.locked,
            seed_key=profile.seed_key,
            seed_version=profile.seed_version,
            enabled=profile.enabled,
            keywords_any=[],
            keywords_all=[],
            exact_phrases=[],
            exclude_keywords=[],
            categories=[],
            boost_keywords={},
            risk_keywords={},
            context_rules=[],
            minimum_score=0,
            notification_mode=profile.notification_mode,
            schedule_enabled=profile.schedule.enabled,
            schedule_times=[],
            schedule_timezone=profile.schedule.timezone,
        )
        for field, value in self._profile_values(profile, now).items():
            setattr(row, field, value)
        try:
            with self._database.sessions.begin() as session:
                session.add(row)
                session.flush()
                self._insert_sources(session, profile)
                session.flush()
                return self._row_to_stored(session, row)
        except IntegrityError as exc:
            if self._exists_after_rollback(profile.id):
                raise ProfileAlreadyExistsError(profile.id) from exc
            raise ProfilePersistenceError("profile create failed") from exc

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
            current = session.get(ProfileRow, profile.id)
            if current is None:
                if profile.kind == "system":
                    renamed = session.scalar(
                        select(ProfileRow).where(
                            ProfileRow.kind == "system",
                            ProfileRow.seed_key == profile.seed_key,
                        )
                    )
                    if renamed is not None:
                        raise SystemProfileImmutableError(renamed.id)
                raise ProfileNotFoundError(profile.id)
            if current.kind == "system" and (
                profile.kind != "system"
                or not profile.locked
                or profile.seed_key != current.seed_key
                or profile.id != current.id
            ):
                raise SystemProfileImmutableError(profile.id)
            if current.kind == "system":
                current_profile = self._row_to_stored(session, current).profile
                unchanged_seed = profile.model_copy(
                    update={
                        "enabled": current_profile.enabled,
                        "notification_mode": current_profile.notification_mode,
                        "schedule": current_profile.schedule,
                    }
                )
                if profile.seed_version < current_profile.seed_version or (
                    profile.seed_version == current_profile.seed_version
                    and unchanged_seed != current_profile
                ):
                    raise SystemProfileImmutableError(profile.id)
            values = self._profile_values(profile, now)
            values["revision"] = ProfileRow.revision + 1
            result = cast(
                CursorResult[tuple[object, ...]],
                session.execute(
                    update(ProfileRow)
                    .where(
                        ProfileRow.id == profile.id,
                        ProfileRow.revision == expected_revision,
                    )
                    .values(**values)
                ),
            )
            if result.rowcount != 1:
                exists = session.scalar(select(ProfileRow.id).where(ProfileRow.id == profile.id))
                if exists is None:
                    raise ProfileNotFoundError(profile.id)
                raise ProfileRevisionConflictError(profile.id)
            try:
                session.execute(
                    delete(ProfileSourceRow).where(ProfileSourceRow.profile_id == profile.id)
                )
                self._insert_sources(session, profile)
                session.flush()
            except IntegrityError as exc:
                raise ProfilePersistenceError("profile replace failed") from exc
            row = session.get(ProfileRow, profile.id)
            if row is None:
                raise ProfilePersistenceError("profile disappeared during replace")
            return self._row_to_stored(session, row)

    def delete(self, profile_id: str, expected_revision: int) -> None:
        with self._database.sessions.begin() as session:
            current = session.get(ProfileRow, profile_id)
            if current is not None and current.kind == "system":
                raise SystemProfileDeleteError(profile_id)
            result = cast(
                CursorResult[tuple[object, ...]],
                session.execute(
                    delete(ProfileRow).where(
                        ProfileRow.id == profile_id,
                        ProfileRow.revision == expected_revision,
                    )
                ),
            )
            if result.rowcount == 1:
                return
            exists = session.scalar(select(ProfileRow.id).where(ProfileRow.id == profile_id))
            if exists is None:
                raise ProfileNotFoundError(profile_id)
            raise ProfileRevisionConflictError(profile_id)

    def seed_system_profile(self, profile: SearchProfile) -> StoredProfile:
        """Create or upgrade one system seed without reactivating a pause."""

        if profile.kind != "system" or not profile.locked or not profile.seed_key:
            raise SystemProfileImmutableError(profile.id)
        existing = self.get(profile.id)
        if existing is None:
            return self.create(profile)
        if existing.profile.kind != "system" or existing.profile.seed_key != profile.seed_key:
            raise SystemProfileImmutableError(profile.id)
        if existing.profile.seed_version >= profile.seed_version:
            return existing
        upgraded = profile.model_copy(
            update={
                "enabled": existing.profile.enabled,
                "notification_mode": existing.profile.notification_mode,
                "schedule": existing.profile.schedule,
            }
        )
        return self.replace(upgraded, expected_revision=existing.revision)

    def clone(self, profile_id: str, new_id: str, name: str | None = None) -> StoredProfile:
        from auction_watch.profiles.seed import clone_as_user

        existing = self.get(profile_id)
        if existing is None:
            raise ProfileNotFoundError(profile_id)
        return self.create(clone_as_user(existing.profile, new_id, name))
