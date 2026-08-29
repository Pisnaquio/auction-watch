"""Plan notifications from durable run snapshots, never from transport payloads."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from auction_watch.async_ops import NotificationRepository
from auction_watch.persistence.contracts import NotificationOutboxRecord
from auction_watch.persistence.repository import StoredProfile
from auction_watch.runner import RunOutcome


def _sanitize_error(value: str) -> str:
    return re.sub(r"https?://\S+", "<url>", " ".join(value.split()))[:300]


def _matches(payload: object, profile_id: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    entries = payload.get("profiles")
    if not isinstance(entries, list):
        return []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("profile_id") == profile_id:
            values = entry.get("matches")
            if isinstance(values, list):
                return [value for value in values if isinstance(value, dict)]
            return []
    return []


class NotificationPlanner:
    """Turn a completed run into at most one logical outbox item."""

    def __init__(self, repository: NotificationRepository, *, recipient: str | None) -> None:
        self.repository = repository
        self.recipient = recipient

    def plan(
        self,
        profile: StoredProfile,
        outcome: RunOutcome,
        snapshot: Any,
        previous_snapshot: Any,
    ) -> NotificationOutboxRecord | None:
        mode = profile.profile.notification_mode
        if mode == "disabled" or not self.recipient:
            return None
        now = datetime.now(UTC)
        notification_type = "failure"
        current_matches: list[dict[str, Any]] = []
        changed_matches: list[dict[str, Any]] = []
        if snapshot is not None:
            current_matches = _matches(snapshot.payload_json, profile.profile.id)
            previous = _matches(
                previous_snapshot.payload_json if previous_snapshot is not None else {},
                profile.profile.id,
            )
            previous_by_key = {item.get("opportunity_key"): item for item in previous}
            changed_matches = [
                item
                for item in current_matches
                if item.get("opportunity_key") not in previous_by_key
                or item.get("score") != previous_by_key[item.get("opportunity_key")].get("score")
                or item.get("matched_terms")
                != previous_by_key[item.get("opportunity_key")].get("matched_terms")
            ]
            if getattr(snapshot, "status", "completed") == "completed":
                current_keys = {item.get("opportunity_key") for item in current_matches}
                changed_matches.extend(
                    {
                        "opportunity_key": item.get("opportunity_key"),
                        "score": item.get("score"),
                        "matched_terms": item.get("matched_terms"),
                        "lot": item.get("lot") or {},
                        "change": "removed",
                    }
                    for item in previous
                    if item.get("opportunity_key") not in current_keys
                )
            notification_type = "matches"
        if outcome.status == "failed":
            if mode != "matches_or_failure":
                return None
            body = f"La corrida {outcome.run_id} de {profile.profile.name} falló."
            if outcome.errors:
                body += f" Error: {_sanitize_error(outcome.errors[0])}"
            payload: dict[str, object] = {
                "reason": "run_failed",
                "error": _sanitize_error(body),
                "recipient": self.recipient,
            }
            subject = f"Auction Watch: falló {profile.profile.name}"
            notification_type = "failure"
        elif not changed_matches:
            return None
        else:
            payload = {
                "reason": "new_or_changed_matches",
                "count": len(changed_matches),
                "recipient": self.recipient,
                "opportunities": [
                    {
                        "opportunity_key": item.get("opportunity_key"),
                        "score": item.get("score"),
                        "title": (item.get("lot") or {}).get("title"),
                        "url": (item.get("lot") or {}).get("lot_url"),
                    }
                    for item in changed_matches
                ],
            }
            body = (
                f"{len(changed_matches)} oportunidad(es) nueva(s) o modificada(s) "
                f"en {profile.profile.name}."
            )
            subject = f"Auction Watch: novedades en {profile.profile.name}"
        item = NotificationOutboxRecord(
            dedupe_key=f"run:{outcome.run_id}:profile:{profile.profile.id}:channel:smtp",
            channel="smtp",
            profile_id=profile.profile.id,
            run_id=outcome.run_id,
            snapshot_id=snapshot.snapshot_id if snapshot is not None else None,
            notification_type=notification_type,
            payload={"subject": subject, "body": body, **payload},
            created_at=now,
            updated_at=now,
        )
        record, _ = self.repository.enqueue(item)
        return record


__all__ = ["NotificationPlanner"]
