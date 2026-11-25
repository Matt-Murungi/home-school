"""THE list of scheduled jobs and their schedules (ADR 0002 rule 10). Add a job here and nowhere
else; the Appwrite Function only ticks every minute and knows nothing about schedules.

Schedules are anchored to Africa/Kampala midnight unless an anchor is given. Planned jobs from
ADR 0002 (auto-confirm, reminders, payout batch, tier evaluation, retention) are added by their own
tickets; the permission jobs (address-revoke, permission-reconcile, permission-audit) are M05.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..auth_otp.jobs import otp_cleanup
from ..notifications.dispatch import notification_dispatch
from ..permissions.jobs import address_revoke, permission_audit, permission_reconcile
from ..travel.jobs import travel_cache_refresh
from .handlers import heartbeat
from .model import KAMPALA, JobSpec, Schedule

JOBS: list[JobSpec] = [
    JobSpec(
        "heartbeat",
        heartbeat,
        schedule=Schedule(timedelta(minutes=5)),
        max_attempts=3,
        catch_up=timedelta(hours=1),
    ),
    # A01: purge expired OTP challenge, attempt, consume and rate-limit rows. Idempotent.
    JobSpec(
        "otp-cleanup",
        otp_cleanup,
        schedule=Schedule(timedelta(hours=1)),
        max_attempts=3,
        catch_up=timedelta(hours=3),
    ),
    # X01: keeps the travel-time zone-pair cache warm (weekly TTL), 02:00 Kampala, chunked.
    JobSpec(
        "travel-cache-refresh",
        travel_cache_refresh,
        schedule=Schedule(timedelta(days=1), anchor=datetime(2026, 1, 1, 2, tzinfo=KAMPALA)),
        max_attempts=3,
        catch_up=timedelta(days=1),
    ),
    # N01: send notifications deferred by quiet hours and retry transient failures. Idempotent.
    # Small catch_up: a missed minute is simply covered by the next run (F04 follow-up 1).
    JobSpec(
        "notification-dispatch",
        notification_dispatch,
        schedule=Schedule(timedelta(minutes=1)),
        max_attempts=3,
        catch_up=timedelta(minutes=5),
        timeout=timedelta(seconds=30),
    ),
    # M05 (#40, ADR 0012): permission jobs. All three recompute from state, so repeats are safe.
    JobSpec(
        "address-revoke",
        address_revoke,
        schedule=Schedule(timedelta(minutes=5)),
        max_attempts=5,
        # Small (F04 follow-up 1): runs recompute from state, so missed periods add nothing.
        catch_up=timedelta(minutes=10),
    ),
    JobSpec(
        "permission-reconcile",
        permission_reconcile,
        schedule=Schedule(timedelta(days=1), anchor=datetime(2026, 1, 1, 2, 0, tzinfo=KAMPALA)),
        max_attempts=5,
        catch_up=timedelta(days=1),
    ),
    JobSpec(
        "permission-audit",
        permission_audit,
        schedule=Schedule(timedelta(days=1), anchor=datetime(2026, 1, 1, 3, 0, tzinfo=KAMPALA)),
        max_attempts=3,
        catch_up=timedelta(days=1),
    ),
]
