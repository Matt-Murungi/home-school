"""BillingService: the subscription lifecycle (B03).

All state changes go through `_commit` (optimistic claim, see store.py) and the pure table in
`states.py`. A transition returns the domain events it implies; after the commit the service hands
them to the notifier (N03) and the session scheduler (S01) with deterministic event IDs, never
undoing the commit if a consumer fails (the failure is counted and logged; the claim rows are the
outbox substrate for a redelivery sweep, a follow-up).

Policies (SR 7.8, PRD SUB-3..SUB-5, PAY-1..PAY-3; owner questions in memory.md B03):
* Period anchor = Kampala date of creation; renewals on that day-of-month (periods.py).
* Pause: immediate, future sessions cancelled, the paid period is NOT extended or refunded; no
  renewal invoices while paused; resume after the paid period re-anchors on the resume date.
* Cancel: active -> at the end of the paid period (cancel_at_period_end); every other status, or
  `immediate=True` (admin), cancels now and cancels future sessions.
* Plan change: pending until the next period's invoice; no proration, no refund, no top-up.
* Grace: `mark_past_due` only after `grace_days` past the due date; the reminders and the actual
  pausing of sessions are B04's.
* A payment is applied once per provider reference; a payment that cannot be applied (cancelled
  subscription, second payment for a paid invoice, wrong amount) is flagged for refund (PAY-7).
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from ..repository import NotFound, Repository
from .errors import (
    ChildAlreadySubscribed,
    ConcurrentTransition,
    ForbiddenTransition,
    GraceNotExpired,
    InvoiceNotAllowed,
    PaymentRefReuse,
    PlanUnavailable,
    UnknownInvoice,
    UnknownSubscription,
)
from .interfaces import (
    AuditEntry,
    AuditSink,
    BillingLedger,
    DiscountPolicy,
    DomainEvent,
    Notifier,
    PlanCatalog,
    SessionScheduler,
)
from .models import Credit, Invoice, Subscription, iso, parse, ugx
from .periods import add_months, kampala_date, period, sessions_included
from .states import EventName, Status, Trigger, transition
from .store import BillingStore, rid

log = logging.getLogger("hs_core.billing")

REASON_MAX = 200
RECENT_START_DAYS = 3  # roll phase B: periods that started this recently
INTERNAL_PROVIDER = "internal"  # synthetic confirmations of zero-amount invoices
DEFAULT_PROVIDER = "mtn_momo"  # the only MVP provider (PRD PAY-1); free text in the schema
SUCCESS_STATUSES = frozenset({"succeeded", "successful", "confirmed"})


@dataclass(frozen=True)
class BillingConfig:
    renewal_lead_days: int = 5  # create the next period's invoice this many days before renewal
    grace_days: int = 3  # SR 8: payment grace period
    past_due_cancel_days: int = 14  # past_due this long -> cancelled (owner question)
    max_pause_days: int = 30  # SUB-4 "configured limit" (owner question)
    roll_chunk_size: int = 20  # one job chunk must fit the 10 s job timeout on Appwrite
    claim_lease_s: int = 30  # a claim older than this with no update is repaired
    reconcile_lookback_days: int = 7  # cancelled subscriptions checked for leftover sessions
    payment_lease_s: int = 120  # a payment "applying" older than this is resumed

    def __post_init__(self) -> None:
        for name in (
            "renewal_lead_days",
            "grace_days",
            "past_due_cancel_days",
            "max_pause_days",
            "roll_chunk_size",
            "claim_lease_s",
            "reconcile_lookback_days",
            "payment_lease_s",
        ):
            v = getattr(self, name)
            if type(v) is not int or v < (1 if name != "grace_days" else 0):
                raise ValueError(f"{name} must be a positive integer")


@dataclass
class TransitionResult:
    subscription: Subscription
    events: tuple[DomainEvent, ...] = ()
    changed: bool = True
    dispatch_failures: int = 0
    audit_failed: bool = False


@dataclass
class CreateResult:
    subscription: Subscription
    invoice: Invoice
    created: bool


@dataclass
class PaymentResult:
    status: str  # applied | duplicate | in_progress | flagged | not_confirmed
    flag: str | None = None
    events: tuple[DomainEvent, ...] = ()


@dataclass
class RollResult:
    action: str  # none | skipped | invoice_created | period_advanced | cancelled


@dataclass
class RollSummary:
    processed: int = 0
    invoices_created: int = 0
    advanced: int = 0
    cancelled: int = 0
    failed: list[str] = field(default_factory=list)
    subscription_ids: list[str] = field(default_factory=list)
    next_cursor: str | None = None
    stopped: bool = False  # cut short by `stop`; resume from next_cursor


@dataclass
class RedispatchSummary:
    processed: int = 0
    delivered: int = 0
    failed: list[str] = field(default_factory=list)
    next_cursor: str | None = None
    stopped: bool = False


@dataclass
class ReconcileSummary:
    processed: int = 0
    cancelled_sessions_for: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    next_cursor: str | None = None
    stopped: bool = False


@dataclass
class PaymentMismatch:
    billing_payment_id: str
    reason: str


@dataclass
class PaymentReconciliation:
    checked: int = 0
    mismatches: list[PaymentMismatch] = field(default_factory=list)
    next_cursor: str | None = None


class BillingService:
    def __init__(
        self,
        repo: Repository,
        *,
        plans: PlanCatalog,
        ledger: BillingLedger,
        audit: AuditSink,
        notifier: Notifier,
        scheduler: SessionScheduler,
        discounts: DiscountPolicy | None = None,
        config: BillingConfig | None = None,
    ) -> None:
        self.store = BillingStore(repo)
        self.plans, self.ledger, self.audit = plans, ledger, audit
        self.notifier, self.scheduler, self.discounts = notifier, scheduler, discounts
        self.config = config or BillingConfig()
        self.repairs = 0  # claims rolled forward by `_load` (alerted via log.error)

    # ------------------------------------------------------------------ reads
    def get(self, sub_id: str) -> Subscription:
        return self.store.get_subscription(sub_id)

    def get_invoice(self, invoice_id: str) -> Invoice:
        return self.store.get_invoice(invoice_id)

    @staticmethod
    def _now(now: datetime | None) -> datetime:
        n = now or datetime.now(UTC)
        if n.tzinfo is None:
            raise ValueError("timezone-aware datetime required")
        return n.astimezone(UTC)

    def _load(self, sub_id: str, now: datetime) -> Subscription:
        """The subscription, brought up to its latest claim (see `BillingStore.repair`)."""
        sub = self.store.get_subscription(sub_id)
        sub, repaired = self.store.repair(sub, now, self.config.claim_lease_s)
        for claim in repaired:
            self.repairs += 1
            log.error(
                "billing.repaired_claim sub=%s to_version=%s trigger=%s",
                sub.id,
                claim["to_version"],
                claim["trigger"],
            )
        return sub

    # ------------------------------------------------------- commit + publish
    @staticmethod
    def _events_for(
        sub_id: str, version: int, names: list[str], status: str, plan_id: str, at: datetime
    ) -> tuple[DomainEvent, ...]:
        """Event ids are `<subscription>:<to_version>:<index>`: stable across redeliveries, so
        consumers (N01, S01) de-duplicate on `event_id`."""
        return tuple(
            DomainEvent(
                event_id=f"{sub_id}:{version}:{i}",
                name=n,
                subscription_id=sub_id,
                version=version,
                at=at,
                data={"status": status, "plan_id": plan_id},
            )
            for i, n in enumerate(names)
        )

    def _events(
        self, sub: Subscription, names: list[str], now: datetime
    ) -> tuple[DomainEvent, ...]:
        return self._events_for(sub.id, sub.version, names, sub.status.value, sub.plan_id, now)

    def _audit_transition(self, sub, trigger, actor, reason, claim: dict | None = None) -> bool:
        try:
            self.audit.record(
                AuditEntry(
                    action=f"subscription.{trigger}",
                    actor=actor,
                    subject_type="subscription",
                    subject_id=sub.id,
                    before={"status": claim["from_status"] if claim else None},
                    after={"status": sub.status.value, "version": sub.version},
                    reason=reason,
                    idempotency_key=f"sub:{sub.id}:{sub.version}",
                )
            )
            return False
        except Exception as e:  # noqa: BLE001
            log.error("billing.audit_failed sub=%s err=%s", sub.id, type(e).__name__)
            return True

    def _dispatch(self, sub: Subscription, events, trigger: str, at: datetime) -> int:
        """Hand events to their consumers. `at` is the transition instant (cancel_future's
        from_at). Returns the number of failures; every call is idempotent by `event_id`."""
        failures = 0
        for e in events:
            try:
                if e.name == EventName.sessions_cancel_requested:
                    self.scheduler.cancel_future(
                        subscription_id=sub.id,
                        from_at=at,
                        reason="paused" if trigger == Trigger.pause else "cancelled",
                        event_id=e.event_id,
                    )
                elif e.name == EventName.sessions_regenerate_requested:
                    self.scheduler.regenerate(
                        subscription_id=sub.id,
                        period_start_at=sub.period_start_at,
                        period_end_at=sub.period_end_at,
                        event_id=e.event_id,
                    )
                else:
                    self.notifier.notify(e)
            except Exception as ex:  # noqa: BLE001
                failures += 1
                log.error(
                    "billing.dispatch_failed sub=%s event=%s err=%s",
                    sub.id,
                    e.name,
                    type(ex).__name__,
                )
        return failures

    def _complete(self, claim: dict, events, now: datetime) -> int:
        """Finish one committed transition: its state-derived effects, then its events; mark the
        claim delivered only when both succeeded (otherwise the sweep retries). Returns failures."""
        failures = 0
        try:
            sub = self.store.get_subscription(claim["subscription_id"])
            self._run_effects(sub, claim["trigger"], now)
        except Exception as ex:  # noqa: BLE001
            failures += 1
            log.error(
                "billing.effects_failed sub=%s trigger=%s err=%s",
                claim["subscription_id"],
                claim["trigger"],
                type(ex).__name__,
            )
            sub = self.store.get_subscription(claim["subscription_id"])
        failures += self._dispatch(sub, events, claim["trigger"], parse(claim["created_at"]))
        if failures == 0:
            self.store.mark_delivered(claim["$id"], now)
        return failures

    def _commit(
        self,
        sub: Subscription,
        patch: dict[str, Any],
        *,
        trigger: str,
        to_status: Status | None,
        event_names: list[str],
        now: datetime,
        actor: str,
        reason: str | None,
        dispatch: bool = True,
    ) -> TransitionResult:
        before = sub.status
        new, claim = self.store.commit(
            sub,
            patch,
            trigger=trigger,
            to_status=to_status.value if to_status else None,
            events=event_names,
            now=now,
            actor=actor,
            reason=reason,
        )
        log.info(
            "billing.commit sub=%s trigger=%s from=%s to=%s version=%s",
            new.id,
            trigger,
            before.value,
            new.status.value,
            new.version,
        )
        events = self._events(new, event_names, now)
        audit_failed = self._audit_transition(
            new, trigger, actor, reason, {"from_status": before.value}
        )
        # dispatch=False: the caller takes the events; the claim stays undelivered, so the
        # redispatch sweep still sends them (idempotent by event_id).
        failures = self._complete(claim, events, now) if dispatch else 0
        return TransitionResult(new, events, True, failures, audit_failed)

    # ------------------------------------------------------------ state moves
    def transition(
        self,
        sub_id: str,
        trigger: Trigger | str,
        *,
        now: datetime | None = None,
        actor: str = "system",
        reason: str | None = None,
        patch: dict[str, Any] | None = None,
        dispatch: bool = True,
    ) -> TransitionResult:
        """One state-machine move. Raises `ForbiddenTransition` or `ConcurrentTransition`."""
        now = self._now(now)
        sub = self._load(sub_id, now)
        t = transition(sub.status, trigger)
        p: dict[str, Any] = {"status": t.to.value}
        if t.trigger == Trigger.pause:
            p["paused_at"] = iso(now)
        elif t.trigger == Trigger.resume:
            p["paused_at"] = None
        elif t.trigger == Trigger.payment_overdue:
            p["past_due_at"] = iso(now)
        elif t.trigger in (Trigger.cancel, Trigger.grace_expired):
            p.update(cancelled_at=iso(now), cancel_at_period_end=False)
        if t.frm == Status.past_due and t.to == Status.active:
            p["past_due_at"] = None
        p.update(patch or {})
        return self._commit(
            sub,
            p,
            trigger=t.trigger.value,
            to_status=t.to,
            event_names=[e.value for e in t.events],
            now=now,
            actor=actor,
            reason=reason,
            dispatch=dispatch,
        )

    def on_teacher_assigned(
        self, sub_id, *, assignment_id: str, is_trial: bool, now=None, actor="system"
    ) -> TransitionResult:
        return self.transition(
            sub_id,
            Trigger.teacher_assigned,
            now=now,
            actor=actor,
            patch={"assignment_id": assignment_id, "is_trial": bool(is_trial)},
        )

    def pause(self, sub_id, *, now=None, actor: str, reason: str | None = None) -> TransitionResult:
        now = self._now(now)
        sub = self._load(sub_id, now)
        if sub.status == Status.paused:  # a retry after a crash: finish the effects, no new move
            self._run_effects(sub, Trigger.pause.value, now)
            return TransitionResult(sub, (), False)
        return self.transition(sub_id, Trigger.pause, now=now, actor=actor, reason=reason)

    def resume(self, sub_id, *, now=None, actor: str) -> TransitionResult:
        """paused -> active. Resuming after the paid period ended re-anchors on the resume date;
        the new period's invoice (and the voiding of stale ones) is a recoverable effect of the
        transition (`_run_effects`), and the roll job also creates a missing current invoice."""
        now = self._now(now)
        sub = self._load(sub_id, now)
        patch: dict[str, Any] = {}
        if sub.status == Status.paused and now >= sub.period_end_at:
            anchor = kampala_date(now)
            p = period(anchor, 0)
            patch = {
                "anchor_date": anchor.isoformat(),
                "period_index": 0,
                "period_start_at": iso(p.start_at),
                "period_end_at": iso(p.end_at),
            }
            if sub.pending_plan_id:
                patch.update(plan_id=sub.pending_plan_id, pending_plan_id=None)
        return self.transition(sub_id, Trigger.resume, now=now, actor=actor, patch=patch)

    def pause_limit_exceeded(self, sub: Subscription, now: datetime) -> bool:
        """Hook for B04/ops: the configured pause limit (SUB-4) has run out."""
        return (
            sub.status == Status.paused
            and sub.paused_at is not None
            and now - sub.paused_at >= timedelta(days=self.config.max_pause_days)
        )

    # --------------------------------------------------------------- cancel
    def cancel(
        self, sub_id, *, now=None, actor: str, reason: str | None = None, immediate: bool = False
    ) -> TransitionResult:
        now = self._now(now)
        sub = self._load(sub_id, now)
        if sub.status == Status.cancelled:
            # a retry after a crash or a failed ledger call: complete what the first try left
            self._run_effects(sub, Trigger.cancel.value, now)
            return TransitionResult(sub, (), False)
        if sub.status == Status.active and not immediate:
            if sub.cancel_at_period_end:
                return TransitionResult(sub, (), False)
            return self._commit(
                sub,
                {"cancel_at_period_end": True, "cancel_reason": reason and reason[:REASON_MAX]},
                trigger="cancel_at_period_end",
                to_status=None,
                event_names=[],
                now=now,
                actor=actor,
                reason=reason,
            )
        return self.transition(
            sub_id,
            Trigger.cancel,
            now=now,
            actor=actor,
            reason=reason,
            patch={"cancel_reason": reason and reason[:REASON_MAX]},
        )

    # ------------------------------------------------- recoverable effects
    def _run_effects(self, sub: Subscription, trigger: str, now: datetime) -> None:
        """The state-derived, idempotent work a transition implies besides its events. Derived
        from the CURRENT state, so it is safe to run late, twice, or from the sweep; it does
        nothing when the subscription has moved on."""
        if trigger in (Trigger.cancel.value, Trigger.grace_expired.value):
            if sub.status == Status.cancelled:
                self._heal_cancelled(sub)
        elif trigger == Trigger.pause.value:
            if sub.status == Status.paused:
                self._void_open_renewals(sub)
        elif trigger == Trigger.resume.value:
            if sub.status == Status.active:
                self._reconcile_invoices(sub, now)
        elif trigger == Trigger.payment_overdue.value and sub.status == Status.past_due:
            for inv in self.store.invoices_of(sub.id):
                if inv.status == "open" and inv.due_at <= now - timedelta(
                    days=self.config.grace_days
                ):
                    self.store.update_invoice(inv.id, {"status": "past_due"})

    def _heal_cancelled(self, sub: Subscription) -> None:
        """Void what was never paid; a paid first period of a subscription that never got a
        teacher goes on the refund queue (idempotent: ledger key per invoice)."""
        for inv in self.store.invoices_of(sub.id):
            if inv.status in ("open", "past_due"):
                self.store.update_invoice(inv.id, {"status": "void"})
            elif (
                inv.status == "paid"
                and sub.assignment_id is None
                and inv.payment_ref
                and inv.amount > 0
            ):
                self.ledger.flag_refund_due(
                    invoice_id=inv.id,
                    payment_ref=inv.payment_ref,
                    amount_ugx=inv.amount,
                    reason="cancelled_before_activation",
                    idempotency_key=f"refund:cancel:{inv.id}",
                )

    def _void_open_renewals(self, sub: Subscription) -> None:
        """A paused subscription is not billed: void the open invoice of the next period."""
        nxt = period(sub.anchor_date, sub.period_index + 1)
        inv_id = rid("inv", sub.id, nxt.start.isoformat())
        try:
            inv = self.store.get_invoice(inv_id)
        except UnknownInvoice:
            return
        if inv.status in ("open", "past_due"):
            self.store.update_invoice(inv.id, {"status": "void"})

    def _reconcile_invoices(self, sub: Subscription, now: datetime) -> None:
        """After a resume: void open invoices of an old anchor, make sure the current period has
        its invoice."""
        keep = {
            rid("inv", sub.id, period(sub.anchor_date, i).start.isoformat())
            for i in (sub.period_index, sub.period_index + 1)
        }
        for inv in self.store.invoices_of(sub.id):
            if inv.id not in keep and inv.status in ("open", "past_due"):
                self.store.update_invoice(inv.id, {"status": "void"})
        self.generate_invoice(sub.id, sub.period_index, now=now)

    def keep(self, sub_id, *, now=None, actor: str) -> TransitionResult:
        now = self._now(now)
        sub = self._load(sub_id, now)
        if not sub.cancel_at_period_end or sub.status == Status.cancelled:
            return TransitionResult(sub, (), False)
        return self._commit(
            sub,
            {"cancel_at_period_end": False, "cancel_reason": None},
            trigger="keep",
            to_status=None,
            event_names=[],
            now=now,
            actor=actor,
            reason=None,
        )

    # ---------------------------------------------------------- plan change
    def change_plan(self, sub_id, plan_id: str, *, now=None, actor: str) -> TransitionResult:
        now = self._now(now)
        plan = self.plans.get(plan_id)
        if plan is None or not plan.active:
            raise PlanUnavailable(plan_id)
        sub = self._load(sub_id, now)
        if sub.status == Status.cancelled:
            raise ForbiddenTransition(sub.status, "change_plan")
        pending = None if plan_id == sub.plan_id else plan_id
        if pending == sub.pending_plan_id:
            return TransitionResult(sub, (), False)
        return self._commit(
            sub,
            {"pending_plan_id": pending},
            trigger="plan_change",
            to_status=None,
            event_names=[],
            now=now,
            actor=actor,
            reason=f"plan:{plan_id}",
        )

    # ------------------------------------------------------------- creation
    def create_subscription(
        self, *, parent_id: str, child_id: str, plan_id: str, now=None
    ) -> CreateResult:
        """Idempotent per child: a double tap returns the live subscription (and its invoice)."""
        now = self._now(now)
        plan = self.plans.get(plan_id)
        if plan is None or not plan.active:
            raise PlanUnavailable(plan_id)
        new_id = uuid.uuid4().hex
        anchor = kampala_date(now)
        for n in range(1000):
            row, created = self.store.claim_child_slot(child_id, n, new_id, plan_id, now)
            sid = row["ref"]
            if created or sid != new_id:
                p0 = period(anchor if created else kampala_date(parse(row["created_at"])), 0)
                if not created:
                    try:
                        existing = self.store.get_subscription(sid)
                    except UnknownSubscription:
                        existing = None  # creator died before inserting: finish its job
                    if existing is not None and existing.status == Status.cancelled:
                        continue
                    if existing is not None and (
                        existing.parent_id != parent_id
                        or plan_id not in (existing.plan_id, existing.pending_plan_id)
                    ):
                        raise ChildAlreadySubscribed(child_id)
                    if row["plan_id"] != plan_id and existing is None:
                        raise ChildAlreadySubscribed(child_id)
                    if existing is not None:
                        inv = self.generate_invoice(sid, existing.period_index, now=now)
                        existing = self._confirm_free_first_invoice(existing, inv, now)
                        return CreateResult(existing, inv, False)
                sub = self.store.create_subscription(
                    Subscription(
                        id=sid,
                        child_id=child_id,
                        parent_id=parent_id,
                        plan_id=plan_id,
                        status=Status.pending_payment,
                        version=0,
                        anchor_date=p0.start,
                        period_index=0,
                        period_start_at=p0.start_at,
                        period_end_at=p0.end_at,
                        created_at=parse(row["created_at"]),
                    )
                )
                inv = self.generate_invoice(sid, 0, now=now)
                self._audit_creation(sub)
                sub = self._confirm_free_first_invoice(sub, inv, now)
                return CreateResult(sub, inv, created)
        raise ChildAlreadySubscribed(child_id)  # pragma: no cover

    def _confirm_free_first_invoice(
        self, sub: Subscription, inv: Invoice, now: datetime
    ) -> Subscription:
        """A first invoice that came to 0 UGX (full discount) has nothing to pay, so no provider
        will ever confirm it: confirm it ourselves with a synthetic zero payment (provider
        `internal`, reference `zero-<invoice>`), which moves pending_payment -> matching."""
        if sub.status == Status.pending_payment and inv.amount == 0:
            self.on_payment_confirmed(
                inv.id, f"zero-{inv.id}", now=now, amount_ugx=0, provider=INTERNAL_PROVIDER
            )
            return self.store.get_subscription(sub.id)
        return sub

    def _audit_creation(self, sub: Subscription) -> None:
        try:
            self.audit.record(
                AuditEntry(
                    action="subscription.created",
                    actor="system",
                    subject_type="subscription",
                    subject_id=sub.id,
                    before={},
                    after={"status": sub.status.value},
                    reason=None,
                    idempotency_key=f"sub:{sub.id}:created",
                )
            )
        except Exception as e:  # noqa: BLE001
            log.error("billing.audit_failed sub=%s err=%s", sub.id, type(e).__name__)

    # -------------------------------------------------------------- invoices
    def _invoices(self, sub_id: str) -> list[Invoice]:
        from appwrite.query import Query

        rows = self.store.repo.list(
            "invoice", [Query.equal("subscription_id", [sub_id]), Query.limit(200)]
        )
        return [Invoice.from_row(r) for r in rows]

    def generate_invoice(self, sub_id: str, index: int, *, now=None) -> Invoice:
        """The invoice of period `index`: created once per (subscription, period start), amounts
        in integer UGX: plan price - discount - credits (never below zero)."""
        now = self._now(now)
        sub = self._load(sub_id, now)
        if sub.status == Status.cancelled:
            raise InvoiceNotAllowed("subscription is cancelled")
        if not sub.period_index <= index <= sub.period_index + 1:
            raise InvoiceNotAllowed("only the current or the next period can be invoiced")
        p = period(sub.anchor_date, index)
        inv_id = rid("inv", sub.id, p.start.isoformat())
        try:
            existing = self.store.get_invoice(inv_id)
        except Exception:  # noqa: BLE001  (UnknownInvoice)
            existing = None
        if existing is not None:
            return self._settle_existing_invoice(sub, existing, p)
        plan_id = sub.plan_id if index <= sub.period_index else (sub.pending_plan_id or sub.plan_id)
        plan = self.plans.get(plan_id)
        if plan is None:
            raise PlanUnavailable(plan_id)
        price = ugx(plan.price_ugx, name="price_ugx")
        discount = 0
        if self.discounts is not None:
            discount = min(ugx(self.discounts.discount_for(sub, plan, p), name="discount"), price)
        included = sessions_included(plan.sessions_per_week, p.days)
        unit = price // included if included else 0
        remaining = price - discount
        taken: list[Credit] = []
        for c in self.store.unapplied_credits(sub.id):
            v = c.sessions * unit
            if not unit or v > remaining:
                continue
            # reserve the credit for THIS invoice id first (create-once claim): a credit can
            # never end up on two invoices, whatever crashes in between
            if not self.store.reserve_credit(c.id, inv_id, now):
                continue
            taken.append(c)
            remaining -= v
        credit_ugx = (price - discount) - remaining
        amount = price - discount - credit_ugx
        inv = Invoice(
            id=inv_id,
            subscription_id=sub.id,
            parent_id=sub.parent_id,
            plan_id=plan_id,
            period_index=index,
            period_start_date=p.start.isoformat(),
            period_end_date=p.end_inclusive.isoformat(),
            plan_price_ugx=price,
            discount_ugx=discount,
            credit_ugx=credit_ugx,
            amount=amount,
            due_at=max(p.start_at, now) if index == sub.period_index else p.start_at,
            created_at=now,
            credit_ids=[c.id for c in taken],
            currency=plan.currency,
            status="paid" if amount == 0 else "open",
            paid_at=now if amount == 0 else None,
        )
        inv, created = self.store.create_invoice(inv)
        self._finalise_credits(inv)
        if created:
            log.info(
                "billing.invoice_created sub=%s period=%s amount=%s",
                sub.id,
                inv.period_start_date,
                inv.amount,
            )
        return inv

    def _settle_existing_invoice(self, sub: Subscription, inv: Invoice, p) -> Invoice:
        """An invoice that already exists: finish a half-done credit application and reopen an
        unpaid void one when its subscription is billable again (after a pause)."""
        self._finalise_credits(inv)
        if (
            inv.status == "void"
            and inv.payment_ref is None
            and sub.status in (Status.active, Status.pending_payment, Status.matching)
            and inv.period_end_date == p.end_inclusive.isoformat()
            and inv.amount > 0
        ):
            inv = self.store.update_invoice(inv.id, {"status": "open"})
        return inv

    def _finalise_credits(self, inv: Invoice) -> None:
        """Post each credit of the invoice to the ledger, THEN mark it applied (both idempotent:
        a retry repeats whatever is missing)."""
        for cid in inv.credit_ids:
            c = self.store.get_credit(cid)
            unit_value = inv.plan_price_ugx // max(
                1, sessions_included(self._spw(inv.plan_id), self._days(inv)) or 1
            )
            self.ledger.record_credit_applied(
                subscription_id=inv.subscription_id,
                credit_id=cid,
                amount_ugx=c.sessions * unit_value,
                idempotency_key=f"credit:{cid}",
            )
            self.store.mark_credit_applied(cid, inv.id)

    def _spw(self, plan_id: str) -> int:
        plan = self.plans.get(plan_id)
        if plan is None:
            raise PlanUnavailable(plan_id)
        return plan.sessions_per_week

    @staticmethod
    def _days(inv: Invoice) -> int:
        from datetime import date

        return (
            date.fromisoformat(inv.period_end_date) - date.fromisoformat(inv.period_start_date)
        ).days + 1

    # --------------------------------------------------------------- credits
    def record_platform_miss(
        self, sub_id: str, session_id: str, *, reason: str, now=None
    ) -> Credit:
        """A session the platform or teacher could not deliver and chose to credit (SUB-5).
        One credit per session however often it is reported."""
        now = self._now(now)
        self.store.get_subscription(sub_id)
        c, created = self.store.create_credit(
            Credit(
                id=rid("credit", sub_id, "platform_miss", session_id),
                subscription_id=sub_id,
                kind="platform_miss",
                ref=session_id,
                sessions=1,
                reason=reason,
                created_at=now,
            )
        )
        if created:
            try:
                self.audit.record(
                    AuditEntry(
                        action="credit.recorded",
                        actor="system",
                        subject_type="subscription",
                        subject_id=sub_id,
                        before={},
                        after={"sessions": 1, "kind": c.kind},
                        reason=reason,
                        idempotency_key=f"credit:{c.id}",
                    )
                )
            except Exception as e:  # noqa: BLE001
                log.error("billing.audit_failed sub=%s err=%s", sub_id, type(e).__name__)
        return c

    # -------------------------------------------------------------- payments
    def on_payment_status(
        self,
        invoice_id: str,
        payment_ref: str,
        status: str,
        *,
        now=None,
        amount_ugx=None,
        provider: str = DEFAULT_PROVIDER,
    ) -> PaymentResult:
        """Entry for polled/callback statuses. Only a confirmed success advances anything;
        pending, unknown (MoMo OutcomeUnknown), failed and expired never change state."""
        if str(status).lower() in SUCCESS_STATUSES:
            return self.on_payment_confirmed(
                invoice_id, payment_ref, now=now, amount_ugx=amount_ugx, provider=provider
            )
        return PaymentResult("not_confirmed")

    def on_payment_confirmed(
        self,
        invoice_id: str,
        payment_ref: str,
        *,
        now=None,
        amount_ugx=None,
        provider: str = DEFAULT_PROVIDER,
    ) -> PaymentResult:
        """A payment is identified by (provider, payment_ref), like M04 `payment`."""
        now = self._now(now)
        if not isinstance(payment_ref, str) or not payment_ref.strip():
            raise ValueError("payment_ref is required")
        if not isinstance(provider, str) or not provider.strip():
            raise ValueError("provider is required")
        key = f"{provider}:{payment_ref}"
        if amount_ugx is not None:
            ugx(amount_ugx, name="amount_ugx")
        inv = self.store.get_invoice(invoice_id)
        row, created = self.store.claim_payment(provider, payment_ref, invoice_id, now)
        if not created:
            if row["invoice_id"] != invoice_id:
                raise PaymentRefReuse(key)
            if row["status"] in ("applied", "flagged"):
                return PaymentResult("duplicate", row.get("flag"))
            if (now - parse(row["created_at"])).total_seconds() < self.config.payment_lease_s:
                return PaymentResult("in_progress")
        if amount_ugx is not None and amount_ugx != inv.amount:
            return self._flag(inv, provider, payment_ref, amount_ugx, "amount_mismatch", now)
        sub = self._load(inv.subscription_id, now)
        if sub.status == Status.cancelled:
            return self._flag(
                inv, provider, payment_ref, amount_ugx, "late_payment_after_cancellation", now
            )
        if inv.status in ("void", "refunded"):
            return self._flag(inv, provider, payment_ref, amount_ugx, "invoice_void", now)
        paid, won = self.store.claim_invoice_paid(inv.id, key, now)
        if not won and paid["ref"] != key:
            return self._flag(inv, provider, payment_ref, amount_ugx, "duplicate_payment", now)
        self.store.update_invoice(
            inv.id,
            {
                "status": "paid",
                "payment_ref": payment_ref,
                "paid_at": iso(parse(paid["created_at"])),
            },
        )
        if inv.amount > 0:  # a fully discounted/credited invoice moves no money
            self.ledger.record_payment(
                invoice_id=inv.id,
                payment_ref=key,
                amount_ugx=inv.amount,
                idempotency_key=f"payment:{key}",
            )
        flag, events = self._advance_for_payment(inv, key, now)
        if flag:
            return self._flag(inv, provider, payment_ref, amount_ugx, flag, now)
        return self._finish_payment(inv, provider, payment_ref, events, now)

    def _finish_payment(
        self, inv: Invoice, provider: str, payment_ref: str, events, now
    ) -> PaymentResult:
        self.store.update_payment(provider, payment_ref, {"status": "applied"})
        try:
            self.audit.record(
                AuditEntry(
                    action="payment.applied",
                    actor="system",
                    subject_type="invoice",
                    subject_id=inv.id,
                    before={"status": "open"},
                    after={"status": "paid"},
                    reason=None,
                    idempotency_key=f"payment:{provider}:{payment_ref}",
                )
            )
        except Exception as e:  # noqa: BLE001
            log.error("billing.audit_failed invoice=%s err=%s", inv.id, type(e).__name__)
        return PaymentResult("applied", None, tuple(events))

    def _flag(
        self, inv: Invoice, provider: str, payment_ref: str, amount_ugx, reason: str, now
    ) -> PaymentResult:
        self.ledger.flag_refund_due(
            invoice_id=inv.id,
            payment_ref=f"{provider}:{payment_ref}",
            amount_ugx=inv.amount if amount_ugx is None else amount_ugx,
            reason=reason,
            idempotency_key=f"refund:{provider}:{payment_ref}",
        )
        self.store.update_payment(provider, payment_ref, {"status": "flagged", "flag": reason})
        try:
            self.audit.record(
                AuditEntry(
                    action="payment.flagged",
                    actor="system",
                    subject_type="invoice",
                    subject_id=inv.id,
                    before={},
                    after={"flag": reason},
                    reason=reason,
                    idempotency_key=f"payment:{provider}:{payment_ref}",
                )
            )
        except Exception as e:  # noqa: BLE001
            log.error("billing.audit_failed invoice=%s err=%s", inv.id, type(e).__name__)
        log.warning("billing.payment_flagged invoice=%s reason=%s", inv.id, reason)
        return PaymentResult("flagged", reason)

    def _advance_for_payment(self, inv: Invoice, payment_ref: str, now: datetime):
        """Move the subscription for a confirmed payment. Returns (flag or None, events)."""
        for _ in range(8):
            sub = self._load(inv.subscription_id, now)
            if sub.last_payment_ref == payment_ref:
                return None, ()  # a previous attempt already moved it (resume after a crash)
            if sub.status == Status.cancelled:
                return "late_payment_after_cancellation", ()
            t = transition(sub.status, Trigger.payment_confirmed)
            p = period(sub.anchor_date, inv.period_index)
            patch: dict[str, Any] = {"status": t.to.value, "last_payment_ref": payment_ref}
            if sub.first_payment_at is None:
                patch["first_payment_at"] = iso(now)
            if sub.status == Status.past_due:
                patch["past_due_at"] = None
            names = [e.value for e in t.events]
            if inv.period_index > sub.period_index and now >= p.start_at:
                patch.update(self._advance_patch(sub, inv))
                if EventName.sessions_regenerate_requested not in names:
                    names.append(EventName.sessions_regenerate_requested.value)
            if sub.status in (Status.pending_payment, Status.past_due):
                self._credit_late_payment(sub, inv, p, now)
            try:
                res = self._commit(
                    sub,
                    patch,
                    trigger=Trigger.payment_confirmed.value,
                    to_status=t.to,
                    event_names=names,
                    now=now,
                    actor="system",
                    reason=None,
                )
            except ConcurrentTransition:
                continue
            return None, res.events
        raise ConcurrentTransition(f"{inv.subscription_id}: too much contention applying payment")

    @staticmethod
    def _advance_patch(sub: Subscription, inv: Invoice) -> dict[str, Any]:
        p = period(sub.anchor_date, inv.period_index)
        return {
            "period_index": inv.period_index,
            "period_start_at": iso(p.start_at),
            "period_end_at": iso(p.end_at),
            "plan_id": inv.plan_id,
            "pending_plan_id": sub.pending_plan_id if sub.pending_plan_id != inv.plan_id else None,
        }

    def _credit_late_payment(self, sub: Subscription, inv: Invoice, p, now: datetime) -> None:
        """Payment after the period start: the start stays, the sessions that could not run
        between the start (or the day sessions paused) and the payment are credited."""
        window = p.start
        if sub.status == Status.past_due and sub.past_due_at is not None:
            window = max(window, kampala_date(sub.past_due_at))
        lost = max(0, min((kampala_date(now) - window).days, p.days))
        sessions = self._spw(inv.plan_id) * lost // 7
        if sessions <= 0:
            return
        self.store.create_credit(
            Credit(
                id=rid("credit", sub.id, "late_payment_start", inv.id),
                subscription_id=sub.id,
                kind="late_payment_start",
                ref=inv.id,
                sessions=sessions,
                reason="payment_after_period_start",
                created_at=now,
            )
        )

    # ------------------------------------------------------- past due hooks
    def _unpaid_overdue(self, sub_id: str, now: datetime, days: int) -> list[Invoice]:
        cutoff = now - timedelta(days=days)
        return [
            i
            for i in self._invoices(sub_id)
            if i.status in ("open", "past_due") and i.due_at <= cutoff
        ]

    def mark_past_due(self, sub_id: str, *, now=None, actor: str = "system") -> TransitionResult:
        """Hook for B04: active -> past_due once an invoice is `grace_days` past its due date."""
        now = self._now(now)
        sub = self._load(sub_id, now)
        if sub.status == Status.past_due:  # retry: finish marking the invoices
            self._run_effects(sub, Trigger.payment_overdue.value, now)
            return TransitionResult(sub, (), False)
        transition(sub.status, Trigger.payment_overdue)  # forbidden transitions first
        if not self._unpaid_overdue(sub_id, now, self.config.grace_days):
            raise GraceNotExpired(sub_id)
        return self.transition(sub_id, Trigger.payment_overdue, now=now, actor=actor)

    def expire_past_due(self, sub_id: str, *, now=None, actor: str = "system") -> TransitionResult:
        """Hook for B04: past_due -> cancelled after `past_due_cancel_days`."""
        now = self._now(now)
        sub = self._load(sub_id, now)
        if sub.status == Status.cancelled:  # retry: complete the voiding / refund flag
            self._run_effects(sub, Trigger.grace_expired.value, now)
            return TransitionResult(sub, (), False)
        transition(sub.status, Trigger.grace_expired)
        if sub.past_due_at is None or now < sub.past_due_at + timedelta(
            days=self.config.past_due_cancel_days
        ):
            raise GraceNotExpired(sub_id)
        return self.transition(
            sub_id, Trigger.grace_expired, now=now, actor=actor, reason="past_due_grace_expired"
        )

    # ---------------------------------------------------------- period roll
    def roll_subscription(self, sub_id: str, *, now=None) -> RollResult:
        now = self._now(now)
        sub = self._load(sub_id, now)
        if sub.status != Status.active:
            return RollResult("skipped")
        if sub.cancel_at_period_end:
            if now >= sub.period_end_at:
                self.cancel(
                    sub_id, now=now, actor="system", reason=sub.cancel_reason, immediate=True
                )
                return RollResult("cancelled")
            return RollResult("none")
        action = "none"
        # an active subscription always has an invoice for its CURRENT period (a crash after a
        # re-anchoring resume could have left none: that would be a free period)
        cur_id = rid("inv", sub.id, period(sub.anchor_date, sub.period_index).start.isoformat())
        if self._find_invoice(cur_id) is None or self._needs_settling(cur_id):
            self.generate_invoice(sub_id, sub.period_index, now=now)
            action = "invoice_created"
        nxt = period(sub.anchor_date, sub.period_index + 1)
        existing = self._find_invoice(rid("inv", sub.id, nxt.start.isoformat()))
        if existing is not None:
            # finish a half-done credit application / reopen after a pause
            existing = self.generate_invoice(sub_id, sub.period_index + 1, now=now)
            if existing.status == "paid" and now >= nxt.start_at:
                self._advance(sub, existing, now)
                return RollResult("period_advanced")
            return RollResult(action)
        if now >= nxt.start_at - timedelta(days=self.config.renewal_lead_days):
            inv = self.generate_invoice(sub_id, sub.period_index + 1, now=now)
            if inv.status == "paid" and now >= nxt.start_at:
                self._advance(self._load(sub_id, now), inv, now)
            return RollResult("invoice_created")
        return RollResult(action)

    def _find_invoice(self, inv_id: str) -> Invoice | None:
        try:
            return self.store.get_invoice(inv_id)
        except UnknownInvoice:
            return None

    def _needs_settling(self, inv_id: str) -> bool:
        inv = self._find_invoice(inv_id)
        return inv is not None and (
            inv.status == "void"
            or any(self.store.get_credit(c).applied_invoice_id is None for c in inv.credit_ids)
        )

    def _advance(self, sub: Subscription, inv: Invoice, now: datetime) -> None:
        try:
            self._commit(
                sub,
                self._advance_patch(sub, inv),
                trigger="period_advance",
                to_status=None,
                event_names=[EventName.sessions_regenerate_requested.value],
                now=now,
                actor="system",
                reason=None,
            )
        except ConcurrentTransition:
            log.info("billing.advance_conflict sub=%s", sub.id)

    def roll_due(
        self, *, now=None, cursor: str | None = None, stop: Callable[[], bool] | None = None
    ) -> RollSummary:
        """One chunk of the `billing-period-roll` job, keyset-paged by subscription id.

        Phase A: active subscriptions whose period ends within the renewal lead time (next
        invoice, advance, cancel at period end). Phase B: active subscriptions whose current
        period STARTED recently (new, advanced or re-anchored by a resume): makes sure the
        current period has its invoice. The cursor is `<phase>:<last id>`. One failing
        subscription never stops the rest."""
        now = self._now(now)
        phase, _, after = (cursor or "A:").partition(":")
        after = after or None
        chunk = self.config.roll_chunk_size
        if phase == "A":
            subs = self.store.list_due_for_roll(
                now + timedelta(days=self.config.renewal_lead_days), after, chunk
            )
        else:
            subs = self.store.list_recently_started(
                now - timedelta(days=RECENT_START_DAYS), after, chunk
            )
        out = RollSummary()
        for s in subs:
            if stop is not None and stop():
                out.next_cursor = (
                    f"{phase}:{out.subscription_ids[-1]}" if out.subscription_ids else cursor
                )
                out.stopped = True
                return out
            out.processed += 1
            out.subscription_ids.append(s.id)
            try:
                r = self.roll_subscription(s.id, now=now)
            except Exception as e:  # noqa: BLE001
                out.failed.append(s.id)
                log.error("billing.roll_failed sub=%s err=%s", s.id, type(e).__name__)
                continue
            out.invoices_created += r.action == "invoice_created"
            out.advanced += r.action == "period_advanced"
            out.cancelled += r.action == "cancelled"
        if len(subs) == chunk:
            out.next_cursor = f"{phase}:{subs[-1].id}"
        elif phase == "A":
            out.next_cursor = "B:"
        return out

    # ------------------------------------------------ sweeps (recovery, reconciliation)
    def redispatch_undelivered(
        self,
        *,
        now=None,
        older_than_s: int | None = None,
        cursor: str | None = None,
        limit: int | None = None,
        stop: Callable[[], bool] | None = None,
    ) -> RedispatchSummary:
        """Complete transitions that are not delivered: repair a claim whose writer died, redo
        the idempotent effects, resend the events (consumers de-duplicate on `event_id`), then
        mark the claim delivered. Idempotent; one failing row never stops the rest."""
        now = self._now(now)
        age = self.config.claim_lease_s if older_than_s is None else older_than_s
        rows = self.store.undelivered_claims(
            now - timedelta(seconds=age), cursor, limit or self.config.roll_chunk_size
        )
        out = RedispatchSummary()
        for claim in rows:
            if stop is not None and stop():
                out.stopped = True
                out.next_cursor = cursor if out.processed == 0 else rows[out.processed - 1]["$id"]
                return out
            out.processed += 1
            try:
                sub = self._load(
                    claim["subscription_id"], now
                )  # rolls a dead writer's claim forward
                claim = self.store.repo.get("subscription_transition", claim["$id"])
                if claim["status"] == "claimed":
                    continue  # still inside the lease of its writer
                events = self._events_for(
                    sub.id,
                    claim["to_version"],
                    json.loads(claim["events"]),
                    claim.get("to_status") or sub.status.value,
                    sub.plan_id,
                    parse(claim["created_at"]),
                )
                if self._complete(claim, events, now) == 0:
                    out.delivered += 1
                else:
                    out.failed.append(claim["$id"])
            except Exception as e:  # noqa: BLE001
                out.failed.append(claim["$id"])
                log.error(
                    "billing.redispatch_failed claim=%s err=%s", claim["$id"], type(e).__name__
                )
        if len(rows) == (limit or self.config.roll_chunk_size):
            out.next_cursor = rows[-1]["$id"]
        return out

    def reconcile_future_sessions(
        self,
        *,
        now=None,
        cursor: str | None = None,
        limit: int | None = None,
        stop: Callable[[], bool] | None = None,
    ) -> ReconcileSummary:
        """Cancelled (recently) and paused subscriptions must have no future sessions: ask the
        scheduler and cancel what is left (idempotent by a per-day event id). The cursor is
        `<phase>:<last id>` with phases C (cancelled) then P (paused)."""
        now = self._now(now)
        phase, _, after = (cursor or "C:").partition(":")
        after = after or None
        n = limit or self.config.roll_chunk_size
        if phase == "C":
            subs = self.store.list_cancelled_since(
                now - timedelta(days=self.config.reconcile_lookback_days), after, n
            )
        else:
            subs = self.store.list_paused(after, n)
        out = ReconcileSummary()
        for sub in subs:
            if stop is not None and stop():
                out.stopped = True
                out.next_cursor = f"{phase}:{sub.id}"
                return out
            out.processed += 1
            try:
                if self.scheduler.future_sessions(subscription_id=sub.id, from_at=now) > 0:
                    self.scheduler.cancel_future(
                        subscription_id=sub.id,
                        from_at=now,
                        reason="paused" if sub.status == Status.paused else "cancelled",
                        event_id=f"reconcile:{sub.id}:{kampala_date(now).isoformat()}",
                    )
                    out.cancelled_sessions_for.append(sub.id)
                    log.error(
                        "billing.reconcile_found_sessions sub=%s status=%s",
                        sub.id,
                        sub.status.value,
                    )
            except Exception as e:  # noqa: BLE001
                out.failed.append(sub.id)
                log.error("billing.reconcile_failed sub=%s err=%s", sub.id, type(e).__name__)
        if len(subs) == n:
            out.next_cursor = f"{phase}:{subs[-1].id}"
        elif phase == "C":
            out.next_cursor = "P:"
        return out

    def reconcile_payments(
        self, *, cursor: str | None = None, limit: int | None = None
    ) -> PaymentReconciliation:
        """Every applied `billing_payment` must have a matching M04 `payment` (same provider and
        reference, same invoice, succeeded, same amount); synthetic `internal` zero payments are
        exempt. Mismatches are returned and logged at error level."""
        n = limit or self.config.roll_chunk_size
        rows = self.store.list_payments(cursor, n)
        out = PaymentReconciliation()
        for r in rows:
            out.checked += 1
            if r["status"] != "applied" or r["provider"] == INTERNAL_PROVIDER:
                continue
            reason = self._payment_mismatch(r)
            if reason:
                out.mismatches.append(PaymentMismatch(r["$id"], reason))
                log.error("billing.payment_mismatch id=%s reason=%s", r["$id"], reason)
        if len(rows) == n:
            out.next_cursor = rows[-1]["$id"]
        return out

    def _payment_mismatch(self, r: dict) -> str | None:
        from appwrite.query import Query

        m04 = self.store.repo.list(
            "payment",
            [
                Query.equal("provider", [r["provider"]]),
                Query.equal("provider_reference", [r["payment_ref"]]),
                Query.limit(2),
            ],
        )
        if not m04:
            return "missing_payment_row"
        p = m04[0]
        if p.get("invoice_id") != r["invoice_id"]:
            return "invoice_differs"
        if p.get("status") != "succeeded":
            return "payment_not_succeeded"
        inv = self._find_invoice(r["invoice_id"])
        if inv is None or p.get("amount") != inv.amount:
            return "amount_differs"
        return None


__all__ = [
    "BillingConfig",
    "BillingService",
    "CreateResult",
    "PaymentResult",
    "RollResult",
    "RollSummary",
    "TransitionResult",
    "add_months",
    "NotFound",
]
