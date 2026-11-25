"""THE list of scheduled jobs and their schedules (ADR 0002 rule 10). Add a job here and nowhere
else; the Appwrite Function only ticks every minute and knows nothing about schedules.

Schedules are anchored to Africa/Kampala midnight unless an anchor is given. Planned jobs from
ADR 0002 (auto-confirm, reminders, payout batch, tier evaluation, retention, address-access
revocation, permission-reconcile, permission-audit) are added by their own tickets.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..auth_otp.jobs import otp_cleanup
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
]
