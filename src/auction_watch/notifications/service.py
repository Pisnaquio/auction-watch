"""Plan notifications from durable run snapshots, never from transport payloads."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from auction_watch.async_ops import NotificationRepository
from auction_watch.notifications.rendering import render_matches_notification
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

    def __init__(
        self,
        repository: NotificationRepository,
        *,
        enabled: bool,
        timezone: str = "UTC",
    ) -> None:
        self.repository = repository
        self.enabled = enabled
        self.timezone = timezone

    def plan(
        self,
        profile: StoredProfile,
        outcome: RunOutcome,
        snapshot: Any,
        previous_snapshot: Any,
        dismissed_keys: frozenset[str] = frozenset(),
    ) -> NotificationOutboxRecord | None:
        mode = profile.profile.notification_mode
        if mode == "disabled" or not self.enabled:
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
            # The copies carry why each lot is here, so the mail can say "nuevo"
            # or "con cambios" instead of leaving the reader to guess.
            for candidate in current_matches:
                seen = previous_by_key.get(candidate.get("opportunity_key"))
                if seen is None:
                    changed_matches.append({**candidate, "change": "new"})
                elif candidate.get("score") != seen.get("score") or candidate.get(
                    "matched_terms"
                ) != seen.get("matched_terms"):
                    changed_matches.append({**candidate, "change": "changed"})
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
            if dismissed_keys:
                changed_matches = [
                    item
                    for item in changed_matches
                    if item.get("opportunity_key") not in dismissed_keys
                ]
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
            }
            subject = f"Auction Watch: falló {profile.profile.name}"
            notification_type = "failure"
        elif not changed_matches:
            return None
        else:
            rendered = render_matches_notification(
                profile_name=profile.profile.name,
                matches=changed_matches,
                generated_at=now,
                timezone=self.timezone,
            )
            payload = {
                "reason": "new_or_changed_matches",
                "count": len(changed_matches),
                "body_html": rendered.html,
                "opportunities": [
                    {
                        "opportunity_key": item.get("opportunity_key"),
                        "score": item.get("score"),
                        "change": item.get("change", "new"),
                        "title": (item.get("lot") or {}).get("title"),
                        "url": (item.get("lot") or {}).get("lot_url"),
                        "source_id": (item.get("lot") or {}).get("source_id"),
                        "price_label": (item.get("lot") or {}).get("price_label"),
                        "closing_at": (item.get("lot") or {}).get("closing_at"),
                    }
                    for item in changed_matches
                ],
            }
            body = rendered.text
            subject = rendered.subject
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
