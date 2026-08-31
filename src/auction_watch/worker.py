"""Recoverable single-process workers for runs and notification delivery."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Event

from auction_watch.async_ops import NotificationRepository, RunQueueRepository
from auction_watch.notifications.sender import NotificationMessage, NotificationSender
from auction_watch.notifications.service import NotificationPlanner
from auction_watch.persistence.operational_repository import OperationalRepository
from auction_watch.persistence.repository import ProfileRepository
from auction_watch.runner import AuctionRunEngine, RunOutcome

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


class RunWorker:
    """Claim at most one job per call; the database is the mutual exclusion boundary."""

    def __init__(
        self,
        engine: AuctionRunEngine,
        profiles: ProfileRepository,
        operational: OperationalRepository,
        queue: RunQueueRepository,
        planner: NotificationPlanner | None = None,
        *,
        now: Callable[[], datetime] = _now,
        max_attempts: int = 3,
    ) -> None:
        self.engine = engine
        self.profiles = profiles
        self.operational = operational
        self.queue = queue
        self.planner = planner
        self.now = now
        self.max_attempts = max_attempts
        self.queue.recover_interrupted(now=self.now())

    def run_once(self) -> RunOutcome | None:
        job = self.queue.claim_next(now=self.now())
        if job is None:
            return None
        previous_snapshot = self.operational.latest_snapshot()
        try:
            outcome = self.engine.run(
                job.profile_id,
                request_id=job.run_id,
                trigger=job.trigger,
            )
            self.queue.finish(
                job.run_id,
                status=outcome.status,
                finished_at=self.now(),
                error="; ".join(outcome.errors) or None,
            )
            if self.planner is not None:
                profile = self.profiles.get(job.profile_id)
                snapshot = self.operational.snapshot_for_run(job.run_id)
                if profile is not None:
                    self.planner.plan(profile, outcome, snapshot, previous_snapshot)
            logger.info(
                "auction_run_worker_finished",
                extra={"run_id": job.run_id, "status": outcome.status},
            )
            return outcome
        except Exception as exc:
            error = f"{type(exc).__name__}"
            if job.attempt < self.max_attempts:
                self.queue.requeue(job.run_id, error=error, now=self.now())
            else:
                self.queue.finish(job.run_id, status="failed", finished_at=self.now(), error=error)
                run = self.operational.get_run(job.run_id)
                if run is not None:
                    self.operational.update_run(
                        run.model_copy(
                            update={
                                "status": "failed",
                                "finished_at": self.now(),
                                "error": error,
                            }
                        )
                    )
            logger.error(
                "auction_run_worker_failed",
                extra={"run_id": job.run_id, "error": error},
            )
            return None


class NotificationDeliveryWorker:
    """Deliver one claimed outbox row and schedule bounded retries on failure."""

    def __init__(
        self,
        repository: NotificationRepository,
        sender: NotificationSender,
        *,
        now: Callable[[], datetime] = _now,
        max_attempts: int = 3,
        base_delay: timedelta = timedelta(seconds=5),
    ) -> None:
        self.repository = repository
        self.sender = sender
        self.now = now
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.repository.recover_inflight(now=self.now())

    def run_once(self) -> bool:
        item = self.repository.claim_due(now=self.now(), max_attempts=self.max_attempts)
        if item is None:
            return False
        try:
            payload = item.payload
            self.sender.send(
                NotificationMessage(
                    subject=str(payload.get("subject", "Auction Watch")),
                    body=str(payload.get("body", "Auction Watch notification")),
                )
            )
        except Exception as exc:
            self.repository.mark_failed(
                item.dedupe_key,
                type(exc).__name__,
                now=self.now(),
                max_attempts=self.max_attempts,
                base_delay=self.base_delay,
            )
            logger.error(
                "auction_notification_failed",
                extra={"dedupe_key": item.dedupe_key, "error": type(exc).__name__},
            )
        else:
            self.repository.mark_sent(item.dedupe_key, now=self.now())
            logger.info(
                "auction_notification_sent",
                extra={"dedupe_key": item.dedupe_key, "channel": item.channel},
            )
        return True


class AuctionWatchWorker:
    """Run both queues serially in one process, with a stoppable background loop."""

    def __init__(
        self,
        run_worker: RunWorker,
        delivery_worker: NotificationDeliveryWorker,
        *,
        schedule_once: Callable[[], object] | None = None,
    ) -> None:
        self.run_worker = run_worker
        self.delivery_worker = delivery_worker
        self.schedule_once = schedule_once

    def run_once(self) -> RunOutcome | None:
        if self.schedule_once is not None:
            try:
                self.schedule_once()
            except Exception as exc:
                logger.error(
                    "auction_scheduler_failed",
                    extra={"error": type(exc).__name__},
                )
        result = self.run_worker.run_once()
        self.delivery_worker.run_once()
        return result

    def run_forever(self, stop: Event, *, poll_seconds: float = 0.5) -> None:
        while not stop.is_set():
            self.run_once()
            stop.wait(poll_seconds)


__all__ = ["AuctionWatchWorker", "NotificationDeliveryWorker", "RunWorker"]
