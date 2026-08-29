"""Pure scheduler boundary: decide due profiles and enqueue idempotently."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from auction_watch.async_ops import RunQueueRepository
from auction_watch.persistence.repository import ProfileRepository, StoredProfile
from auction_watch.runner import due_profiles


def _slot_stamp(profile: StoredProfile, now: datetime) -> str:
    local_now = now.astimezone(ZoneInfo(profile.profile.schedule.timezone))
    elapsed = [
        local_now.replace(
            hour=int(raw_time[:2]), minute=int(raw_time[3:]), second=0, microsecond=0
        )
        for raw_time in profile.profile.schedule.times
        if local_now.replace(
            hour=int(raw_time[:2]), minute=int(raw_time[3:]), second=0, microsecond=0
        )
        <= local_now
    ]
    slot = max(elapsed) if elapsed else local_now
    return slot.astimezone(UTC).strftime("%Y%m%d%H%M")


def enqueue_due_profiles(
    profiles: ProfileRepository,
    queue: RunQueueRepository,
    *,
    now: datetime,
) -> tuple[str, ...]:
    """Enqueue the current elapsed slot for each enabled, uncovered profile."""

    stored = profiles.list()
    due = due_profiles(stored, queue.last_successful_by_profile(), now)
    by_id = {item.profile.id: item for item in stored}
    enqueued: list[str] = []
    for profile_id in due:
        item: StoredProfile = by_id[profile_id]
        queue.enqueue(
            idempotency_key=f"scheduled:{profile_id}:{_slot_stamp(item, now)}",
            profile_id=profile_id,
            trigger="scheduled",
            revision=item.revision,
            now=now,
        )
        enqueued.append(profile_id)
    return tuple(enqueued)


__all__ = ["enqueue_due_profiles"]
