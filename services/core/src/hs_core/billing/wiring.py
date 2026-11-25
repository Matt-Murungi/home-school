"""Builds the BillingService for the app.

Real adapters for the ledger ($01), notifications (N01/N03) and sessions (S01) are
not on main yet. In `dev` (and tests) the documented fakes are used so the job runs end to end on
the in-memory repository. In staging and production NOTHING is faked: a missing adapter leaves the
service unbound with the missing names listed, and `billing-period-roll` then FAILS (raises
`BillingNotWired`, alerting via the job runner) instead of silently doing nothing or posting to a
fake ledger. Pass real adapters to `create_app(..., billing_adapters=...)` when they exist.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..repository import Repository
from ..settings import Settings
from .fakes import FakeLedger, FakeNotifier, FakeScheduler, RepositoryPlans
from .jobs import BillingNotWired  # noqa: F401  (re-exported)
from .service import BillingConfig, BillingService

ADAPTERS = ("ledger", "notifier", "scheduler")  # audit is the AuditedRepository (M06)


@dataclass
class BillingAdapters:
    ledger: object | None = None
    notifier: object | None = None
    scheduler: object | None = None
    plans: object | None = None

    def missing(self) -> list[str]:
        return [n for n in ADAPTERS if getattr(self, n) is None]


def config_from_settings(s: Settings) -> BillingConfig:
    return BillingConfig(
        renewal_lead_days=s.billing_renewal_lead_days,
        grace_days=s.billing_grace_days,
        past_due_cancel_days=s.billing_past_due_cancel_days,
        max_pause_days=s.billing_max_pause_days,
        roll_chunk_size=s.billing_roll_chunk_size,
    )


def build_billing_service(
    settings: Settings, repo: Repository, adapters: BillingAdapters | None = None
) -> tuple[BillingService | None, list[str]]:
    """Returns (service, missing adapter names). Outside dev a missing adapter -> (None, names)."""
    a = adapters or BillingAdapters()
    missing = a.missing()
    if missing and settings.hs_env != "dev":
        return None, missing
    return (
        BillingService(
            repo,
            plans=a.plans or RepositoryPlans(repo),
            ledger=a.ledger or FakeLedger(),
            notifier=a.notifier or FakeNotifier(),
            scheduler=a.scheduler or FakeScheduler(),
            config=config_from_settings(settings),
        ),
        [],
    )
