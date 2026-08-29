"""Versioned HTTP boundary for independent Auction Watch profiles."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, NoReturn, cast

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from auction_watch.async_ops import NotificationRepository, RunQueueRepository
from auction_watch.core.identity import decode_opportunity_key, encode_opportunity_key
from auction_watch.core.models import SearchProfile
from auction_watch.persistence.contracts import UserOpportunityState
from auction_watch.persistence.operational_repository import (
    OperationalPersistenceError,
    OperationalRepository,
    RunLeaseBusyError,
    UserStateRevisionConflict,
)
from auction_watch.persistence.repository import (
    ProfileAlreadyExistsError,
    ProfileNotFoundError,
    ProfilePersistenceError,
    ProfileRepository,
    ProfileRevisionConflictError,
    StoredProfile,
    SystemProfileDeleteError,
    SystemProfileImmutableError,
)
from auction_watch.runner import RunOutcome


class ProfileCreateRequest(BaseModel):
    """Create one editable profile from the shared domain contract."""

    model_config = ConfigDict(extra="forbid")

    profile: SearchProfile

    @model_validator(mode="before")
    @classmethod
    def accept_profile_object_or_wrapper(cls, value: object) -> object:
        if isinstance(value, dict) and "profile" not in value:
            payload = dict(value)
            expected_revision = payload.pop("expected_revision", None)
            wrapped: dict[str, object] = {"profile": payload}
            if expected_revision is not None:
                wrapped["expected_revision"] = expected_revision
            return wrapped
        return value


class ProfileUpdateRequest(ProfileCreateRequest):
    expected_revision: int = Field(ge=1)


class ProfileCloneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_id: str
    name: str | None = None


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str
    request_id: str | None = None

    @field_validator("request_id")
    @classmethod
    def validate_request_id(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("request_id must not be empty")
        return value


class OpportunityStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opportunity_key: str
    state: Literal["following", "dismissed", "none", "follow", "discard", "restore"]
    expected_version: int | None = Field(default=None, ge=1)

    @field_validator("opportunity_key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        decode_opportunity_key(value)
        return value


def _repositories(request: Request) -> tuple[ProfileRepository, OperationalRepository]:
    profiles = getattr(request.app.state, "profile_repository", None)
    operational = getattr(request.app.state, "operational_repository", None)
    if profiles is None or operational is None:
        raise HTTPException(status_code=503, detail="database is not ready")
    return profiles, operational


def _queue(request: Request) -> RunQueueRepository:
    queue = getattr(request.app.state, "run_queue", None)
    if queue is None:
        raise HTTPException(status_code=503, detail="run queue is not ready")
    return cast(RunQueueRepository, queue)


def _notifications(request: Request) -> NotificationRepository:
    notifications = getattr(request.app.state, "notifications", None)
    if notifications is None:
        raise HTTPException(status_code=503, detail="notification outbox is not ready")
    return cast(NotificationRepository, notifications)


def _profile_view(stored: StoredProfile) -> dict[str, object]:
    return {
        "profile": stored.profile.model_dump(mode="json"),
        "revision": stored.revision,
        "created_at": stored.created_at.isoformat(),
        "updated_at": stored.updated_at.isoformat(),
        "protected": stored.profile.kind == "system",
    }


def _validate_registered_sources(profile: SearchProfile) -> None:
    from auction_watch.sources import DEFAULT_SOURCE_REGISTRY

    known = {spec.source_id for spec in DEFAULT_SOURCE_REGISTRY.specs()}
    unknown = sorted(set(profile.source_ids) - known)
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"unknown source_id: {', '.join(unknown)}"
        )


def _raise_profile_error(exc: Exception) -> NoReturn:
    if isinstance(exc, ProfileNotFoundError):
        raise HTTPException(status_code=404, detail="profile not found") from exc
    if isinstance(exc, ProfileAlreadyExistsError):
        raise HTTPException(status_code=409, detail="profile already exists") from exc
    if isinstance(exc, ProfileRevisionConflictError):
        raise HTTPException(status_code=409, detail="profile revision is stale") from exc
    if isinstance(exc, (SystemProfileImmutableError, SystemProfileDeleteError)):
        raise HTTPException(status_code=403, detail="protected profile cannot be changed") from exc
    if isinstance(exc, (RunLeaseBusyError, UserStateRevisionConflict)):
        raise HTTPException(status_code=409, detail="resource is busy or stale") from exc
    if isinstance(exc, (ProfilePersistenceError, OperationalPersistenceError)):
        raise HTTPException(status_code=503, detail="persistence operation failed") from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=422, detail="invalid profile or run request") from exc
    raise exc


def _run_view(outcome: RunOutcome, operational: OperationalRepository) -> dict[str, object]:
    run = operational.get_run(outcome.run_id)
    if run is None:
        return {
            "run_id": outcome.run_id,
            "status": outcome.status,
            "snapshot_id": outcome.snapshot_id,
            "content_hash": outcome.content_hash,
            "errors": list(outcome.errors),
        }
    snapshot = operational.snapshot_for_run(run.run_id)
    return {
        "run_id": run.run_id,
        "status": run.status,
        "trigger": run.trigger,
        "selected_sources": list(run.selected_sources),
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "error": run.error,
        "snapshot_id": snapshot.snapshot_id if snapshot else outcome.snapshot_id,
        "content_hash": snapshot.content_hash if snapshot else outcome.content_hash,
        "errors": list(outcome.errors),
        "snapshot": _snapshot_view(snapshot) if snapshot else None,
    }


def _queue_view(
    item: Any, operational: OperationalRepository
) -> dict[str, object]:
    run = operational.get_run(item.run_id)
    snapshot = operational.snapshot_for_run(item.run_id)
    return {
        "run_id": item.run_id,
        "idempotency_key": item.idempotency_key,
        "profile_id": item.profile_id,
        "trigger": item.trigger,
        "status": item.status,
        "attempt": item.attempt,
        "enqueued_at": item.enqueued_at.isoformat(),
        "started_at": item.started_at.isoformat() if item.started_at else None,
        "finished_at": item.finished_at.isoformat() if item.finished_at else None,
        "error": item.error or (run.error if run else None),
        "selected_sources": list(run.selected_sources) if run else [],
        "snapshot_id": snapshot.snapshot_id if snapshot else None,
        "content_hash": snapshot.content_hash if snapshot else None,
        "snapshot": _snapshot_view(snapshot) if snapshot else None,
    }


def _snapshot_view(row: Any) -> dict[str, object]:
    return {
        "snapshot_id": row.snapshot_id,
        "run_id": row.run_id,
        "content_hash": row.content_hash,
        "status": row.status,
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "payload": row.payload_json,
    }


def _canonical_snapshot_view(
    row: Any, operational: OperationalRepository, profile_id: str
) -> dict[str, object]:
    """Expose a persisted snapshot with current durable user decisions overlaid."""

    view = _snapshot_view(row)
    payload = dict(cast(dict[str, object], view["payload"]))
    payload["user_states"] = [
        {
            **state.model_dump(mode="json"),
            "opportunity_key": encode_opportunity_key(
                state.source_id, state.auction_id, state.lot_id
            ),
        }
        for state in operational.user_states((profile_id,))
    ]
    view["payload"] = payload
    return view


router = APIRouter(prefix="/api/v1", tags=["auction-watch"])


@router.get("/sources")
def list_sources() -> list[dict[str, str]]:
    from auction_watch.sources import DEFAULT_SOURCE_REGISTRY

    return [
        {"source_id": spec.source_id, "label": spec.label}
        for spec in DEFAULT_SOURCE_REGISTRY.specs()
    ]


@router.get("/profiles")
def list_profiles(request: Request) -> list[dict[str, object]]:
    profiles, _ = _repositories(request)
    return [_profile_view(item) for item in profiles.list()]


@router.get("/profiles/{profile_id}/runs")
def list_profile_runs(request: Request, profile_id: str) -> list[dict[str, object]]:
    profiles, operational = _repositories(request)
    if profiles.get(profile_id) is None:
        raise HTTPException(status_code=404, detail="profile not found")
    return [_queue_view(item, operational) for item in _queue(request).recent(profile_id)]


@router.get("/profiles/{profile_id}/notifications")
def list_profile_notifications(request: Request, profile_id: str) -> list[dict[str, object]]:
    profiles, _ = _repositories(request)
    if profiles.get(profile_id) is None:
        raise HTTPException(status_code=404, detail="profile not found")
    return [item.model_dump(mode="json") for item in _notifications(request).recent(profile_id)]


@router.post("/profiles", status_code=status.HTTP_201_CREATED)
def create_profile(request: Request, body: ProfileCreateRequest) -> dict[str, object]:
    profiles, _ = _repositories(request)
    if body.profile.kind != "user":
        raise HTTPException(status_code=422, detail="only editable user profiles can be created")
    _validate_registered_sources(body.profile)
    try:
        return _profile_view(profiles.create(body.profile))
    except Exception as exc:
        _raise_profile_error(exc)


@router.get("/profiles/{profile_id}")
def get_profile(request: Request, profile_id: str) -> dict[str, object]:
    profiles, _ = _repositories(request)
    stored = profiles.get(profile_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="profile not found")
    return _profile_view(stored)


@router.patch("/profiles/{profile_id}")
def update_profile(
    request: Request, profile_id: str, body: ProfileUpdateRequest
) -> dict[str, object]:
    profiles, _ = _repositories(request)
    if body.profile.id != profile_id:
        raise HTTPException(status_code=422, detail="profile id cannot change")
    if body.profile.kind != "user":
        raise HTTPException(status_code=403, detail="protected profile cannot be edited")
    _validate_registered_sources(body.profile)
    try:
        return _profile_view(
            profiles.replace(body.profile, expected_revision=body.expected_revision)
        )
    except Exception as exc:
        _raise_profile_error(exc)


def _set_enabled(request: Request, profile_id: str, enabled: bool) -> dict[str, object]:
    profiles, _ = _repositories(request)
    stored = profiles.get(profile_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="profile not found")
    updated = stored.profile.model_copy(update={"enabled": enabled})
    try:
        return _profile_view(profiles.replace(updated, expected_revision=stored.revision))
    except Exception as exc:
        _raise_profile_error(exc)


@router.post("/profiles/{profile_id}/pause")
def pause_profile(request: Request, profile_id: str) -> dict[str, object]:
    return _set_enabled(request, profile_id, False)


@router.post("/profiles/{profile_id}/resume")
def resume_profile(request: Request, profile_id: str) -> dict[str, object]:
    return _set_enabled(request, profile_id, True)


@router.post("/profiles/{profile_id}/clone", status_code=status.HTTP_201_CREATED)
def clone_profile(
    request: Request, profile_id: str, body: ProfileCloneRequest
) -> dict[str, object]:
    profiles, _ = _repositories(request)
    try:
        return _profile_view(profiles.clone(profile_id, body.new_id, body.name))
    except Exception as exc:
        _raise_profile_error(exc)


@router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile(request: Request, profile_id: str, expected_revision: int) -> None:
    profiles, _ = _repositories(request)
    try:
        profiles.delete(profile_id, expected_revision)
    except Exception as exc:
        _raise_profile_error(exc)


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
def create_run(
    request: Request,
    body: RunCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    profiles, operational = _repositories(request)
    queue = _queue(request)
    stored = profiles.get(body.profile_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="profile not found")
    if not stored.profile.enabled:
        raise HTTPException(status_code=409, detail="profile is paused")
    key = idempotency_key or body.request_id
    if key is None or not key.strip():
        raise HTTPException(status_code=400, detail="Idempotency-Key is required")
    if len(key) > 256:
        raise HTTPException(status_code=400, detail="Idempotency-Key is too long")
    existing_queue = queue.get_by_key(key)
    if existing_queue is not None:
        if existing_queue.profile_id != stored.profile.id:
            raise HTTPException(
                status_code=409, detail="Idempotency-Key belongs to another profile"
            )
        return _queue_view(existing_queue, operational)
    existing = operational.get_run(key)
    if existing is not None:
        bound_profiles = operational.run_profile_ids(key)
        if bound_profiles and body.profile_id not in bound_profiles:
            raise HTTPException(
                status_code=409, detail="Idempotency-Key belongs to another profile"
            )
        if existing.status in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="run is already in progress")
        return _run_view(
            RunOutcome(
                run_id=existing.run_id,
                status=existing.status,
                snapshot_id=None,
                content_hash=None,
                errors=(existing.error,) if existing.error else (),
            ),
            operational,
        )
    try:
        queued, _ = queue.enqueue(
            idempotency_key=key,
            profile_id=stored.profile.id,
            trigger="manual",
            revision=stored.revision,
        )
        return _queue_view(queued, operational)
    except Exception as exc:
        _raise_profile_error(exc)


@router.get("/runs/{run_id}")
def get_run(request: Request, run_id: str) -> dict[str, object]:
    _, operational = _repositories(request)
    queued = _queue(request).get(run_id)
    if queued is not None:
        return _queue_view(queued, operational)
    run = operational.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    snapshot = operational.snapshot_for_run(run_id)
    return _run_view(
        RunOutcome(
            run_id=run_id,
            status=run.status,
            snapshot_id=snapshot.snapshot_id if snapshot else None,
            content_hash=snapshot.content_hash if snapshot else None,
            errors=(run.error,) if run.error else (),
        ),
        operational,
    )


@router.get("/profiles/{profile_id}/snapshot")
def get_profile_snapshot(request: Request, profile_id: str) -> dict[str, object]:
    profiles, operational = _repositories(request)
    if profiles.get(profile_id) is None:
        raise HTTPException(status_code=404, detail="profile not found")
    row = operational.latest_snapshot()
    if row is None:
        raise HTTPException(status_code=404, detail="snapshot not found")
    payload = row.payload_json
    profile_entries = payload.get("profiles") if isinstance(payload, dict) else None
    if not isinstance(profile_entries, list) or not any(
        isinstance(item, dict) and item.get("profile_id") == profile_id for item in profile_entries
    ):
        raise HTTPException(status_code=404, detail="snapshot not found for profile")
    return _canonical_snapshot_view(row, operational, profile_id)


@router.post("/profiles/{profile_id}/opportunities/state")
def set_opportunity_state(
    request: Request, profile_id: str, body: OpportunityStateRequest
) -> dict[str, object]:
    profiles, operational = _repositories(request)
    if profiles.get(profile_id) is None:
        raise HTTPException(status_code=404, detail="profile not found")
    source_id, auction_id, lot_id = decode_opportunity_key(body.opportunity_key)
    if not operational.lot_exists(source_id, auction_id, lot_id):
        raise HTTPException(status_code=404, detail="opportunity not found in reconciled storage")
    normalized_state = {
        "follow": "following",
        "discard": "dismissed",
        "restore": "none",
    }.get(body.state, body.state)
    now = datetime.now(UTC)
    state = UserOpportunityState(
        profile_id=profile_id,
        source_id=source_id,
        auction_id=auction_id,
        lot_id=lot_id,
        state=normalized_state,
        created_at=now,
        updated_at=now,
    )
    try:
        result = operational.set_user_state(state, expected_version=body.expected_version)
    except Exception as exc:
        _raise_profile_error(exc)
    return result.model_dump(mode="json")


__all__ = ["router"]
