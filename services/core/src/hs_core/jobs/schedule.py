"""THE list of scheduled jobs and their schedules (ADR 0002 rule 10). Add a job here and nowhere
else; the Appwrite Function only ticks every minute and knows nothing about schedules.

Schedules are anchored to Africa/Kampala midnight unless an anchor is given. Planned jobs from
ADR 0002 (auto-confirm, reminders, payout batch, tier evaluation, retention, address-access
revocation, permission-reconcile, permission-audit) are added by their own tickets.
"""

from __future__ import annotations

from datetime import timedelta

from .handlers import heartbeat
from .model import JobSpec, Schedule

JOBS: list[JobSpec] = [
    JobSpec(
        "heartbeat",
        heartbeat,
        schedule=Schedule(timedelta(minutes=5)),
        max_attempts=3,
        catch_up=timedelta(hours=1),
    ),
]
