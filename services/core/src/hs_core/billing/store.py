"""Persistence over the `Repository` interface.

Appwrite has no conditional update and no transactions (ADR 0002/0012), so every guarantee here
comes from creating a row with a deterministic ID: the second create is a `Conflict` (409).

* Optimistic transitions: to move subscription S from version v to v+1 a writer first CREATES the
  claim row `claim(S, v+1)` (table `subscription_transition`, append-only history), then updates
  the subscription, then marks the claim applied. Of two writers that read version v exactly one
  creates the claim; the other gets `Conflict` -> `ConcurrentTransition`. A writer that dies
  between the claim and the update leaves a claim with `status=claimed`; `repair()` rolls it
  forward once it is older than the lease. Residual risk: `repair()` itself is a read-then-write
  without a conditional update (a repairer that stalls past a later transition could overwrite
  it); the lease makes that window very narrow and the roll job logs every repair.
* One invoice per (subscription, period start): the invoice ID is derived from them.
* One application per provider payment reference, one "paid" claim per invoice.

Tables (appwrite/schema/billing.json, migration 0011): `subscription_transition`, `billing_credit`,
`billing_payment`, `billing_claim`; additive optional columns on `subscription` and `invoice`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from appwrite.query import Query

from ..repository import Conflict, NotFound, Repository
from .errors import ConcurrentTransition, UnknownInvoice, UnknownSubscription
from .models import Credit, Invoice, Subscription, iso, parse

SUBSCRIPTION = "subscription"
INVOICE = "invoice"
CLAIM = "subscription_transition"
CREDIT = "billing_credit"
PAYMENT = "billing_payment"
BILLING_CLAIM = "billing_claim"  # kind invoice_paid | child_live | credit_applied
REASON_MAX = 200


def rid(*parts: object) -> str:
    """Deterministic row ID (Appwrite IDs: 36 chars max, [A-Za-z0-9_])."""
    h = hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()
    return h[:32]


class BillingStore:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo

    # ---- subscriptions
    def get_subscription(self, sub_id: str) -> Subscription:
        try:
            return Subscription.from_row(self.repo.get(SUBSCRIPTION, sub_id))
        except NotFound:
            raise UnknownSubscription(sub_id) from None

    def create_subscription(self, sub: Subscription) -> Subscription:
        """Idempotent for a retried creation (same ID)."""
        try:
            return Subscription.from_row(self.repo.create(SUBSCRIPTION, sub.id, sub.to_row()))
        except Conflict:
            return self.get_subscription(sub.id)

    def child_subscriptions(self, child_id: str) -> list[Subscription]:
        rows = self.repo.list(SUBSCRIPTION, [Query.equal("child_id", [child_id]), Query.limit(100)])
        return [Subscription.from_row(r) for r in rows if not r.get("deleted_at")]

    def claim_child_slot(self, child_id: str, n: int, sub_id: str, plan_id: str, now: datetime):
        """Create the n-th "live subscription" claim of a child; returns (row, created).
        `ref` is the subscription id."""
        row_id = rid("live", child_id, n)
        data = {
            "kind": "child_live",
            "subject_id": child_id,
            "seq": n,
            "ref": sub_id,
            "plan_id": plan_id,
            "claim_key": f"child_live:{child_id}:{n}",
            "created_at": iso(now),
        }
        try:
            return self.repo.create(BILLING_CLAIM, row_id, data), True
        except Conflict:
            return self.repo.get(BILLING_CLAIM, row_id), False

    def list_due_for_roll(self, horizon: datetime, after_id: str | None, limit: int):
        q = [
            Query.equal("status", ["active"]),
            Query.less_than_equal("period_end_at", iso(horizon)),
            Query.order_asc("$id"),
            Query.limit(limit),
        ]
        if after_id:
            q.insert(2, Query.greater_than("$id", after_id))
        return [Subscription.from_row(r) for r in self.repo.list(SUBSCRIPTION, q)]

    def list_recently_started(self, since: datetime, after_id: str | None, limit: int):
        q = [
            Query.equal("status", ["active"]),
            Query.greater_than_equal("period_start_at", iso(since)),
            Query.order_asc("$id"),
            Query.limit(limit),
        ]
        if after_id:
            q.insert(2, Query.greater_than("$id", after_id))
        return [Subscription.from_row(r) for r in self.repo.list(SUBSCRIPTION, q)]

    def list_active_ids_after(self, after_id: str | None, limit: int) -> list[str]:
        q = [Query.equal("status", ["active"]), Query.order_asc("$id"), Query.limit(limit)]
        if after_id:
            q.insert(1, Query.greater_than("$id", after_id))
        return [r["$id"] for r in self.repo.list(SUBSCRIPTION, q)]

    # ---- optimistic commit
    def commit(
        self,
        sub: Subscription,
        patch: dict[str, Any],
        *,
        trigger: str,
        to_status: str | None,
        events: list[str],
        now: datetime,
        actor: str,
        reason: str | None,
    ) -> tuple[Subscription, dict]:
        """Move `sub` from its version to version+1 or raise `ConcurrentTransition`. Returns the
        new subscription and the claim row (status applied, not yet delivered)."""
        nv = sub.version + 1
        truncated = bool(reason) and len(reason) > REASON_MAX
        claim = {
            "subscription_id": sub.id,
            "from_version": sub.version,
            "to_version": nv,
            "trigger": trigger,
            "from_status": sub.status.value,
            "to_status": to_status,
            "patch": json.dumps(patch, sort_keys=True),
            "events": json.dumps(events),
            "actor": actor[:64],
            "reason": reason[:REASON_MAX] if reason else None,
            "reason_truncated": truncated,
            "status": "claimed",
            "created_at": iso(now),
        }
        cid = rid("claim", sub.id, nv)
        try:
            self.repo.create(CLAIM, cid, claim)
        except Conflict:
            raise ConcurrentTransition(f"{sub.id} moved past version {sub.version}") from None
        row = self.repo.update(SUBSCRIPTION, sub.id, {**patch, "version": nv})
        claim_row = self.repo.update(CLAIM, cid, {"status": "applied"})
        return Subscription.from_row(row), claim_row

    def repair(
        self, sub: Subscription, now: datetime, lease_s: int
    ) -> tuple[Subscription, list[dict]]:
        """Bring the subscription row up to its latest claim, in order.

        * a `claimed` claim older than the lease: its writer died before updating the row, roll
          it forward;
        * an `applied` claim ahead of the row: a stale repairer overwrote a later transition (the
          version went backwards): replay the patches in order (self-heal).
        Before every write the subscription is re-read and the write happens only if it is still
        at the claim's `from_version`, so a repairer that stalled cannot clobber a newer state
        (the remaining window is the read-then-write gap: Appwrite has no conditional update).
        Returns (subscription, repaired claim rows)."""
        repaired: list[dict] = []
        try:  # the row is already at this claim's version but its writer died before saying so
            cur = self.repo.get(CLAIM, rid("claim", sub.id, sub.version))
            if (
                cur["status"] == "claimed"
                and (now - parse(cur["created_at"])).total_seconds() >= lease_s
            ):
                repaired.append(self.repo.update(CLAIM, cur["$id"], {"status": "applied"}))
        except NotFound:
            pass
        while True:
            try:
                claim = self.repo.get(CLAIM, rid("claim", sub.id, sub.version + 1))
            except NotFound:
                break
            if (
                claim["status"] == "claimed"
                and (now - parse(claim["created_at"])).total_seconds() < lease_s
            ):
                break  # its writer may still be running
            fresh = self.get_subscription(sub.id)
            if fresh.version != sub.version:
                sub = fresh
                continue
            row = self.repo.update(
                SUBSCRIPTION, sub.id, {**json.loads(claim["patch"]), "version": claim["to_version"]}
            )
            if claim["status"] == "claimed":
                claim = self.repo.update(CLAIM, claim["$id"], {"status": "applied"})
            repaired.append(claim)
            sub = Subscription.from_row(row)
        return sub, repaired

    def mark_delivered(self, claim_id: str, now: datetime) -> None:
        self.repo.update(CLAIM, claim_id, {"status": "delivered", "dispatched_at": iso(now)})

    def undelivered_claims(self, cutoff: datetime, after_id: str | None, limit: int) -> list[dict]:
        """Claims whose effects/events are not confirmed delivered (applied, or claimed and old)."""
        q = [
            Query.equal("status", ["claimed", "applied"]),
            Query.less_than_equal("created_at", iso(cutoff)),
            Query.order_asc("$id"),
            Query.limit(limit),
        ]
        if after_id:
            q.insert(2, Query.greater_than("$id", after_id))
        return self.repo.list(CLAIM, q)

    def list_cancelled_since(self, since: datetime, after_id: str | None, limit: int):
        q = [
            Query.equal("status", ["cancelled"]),
            Query.greater_than_equal("cancelled_at", iso(since)),
            Query.order_asc("$id"),
            Query.limit(limit),
        ]
        if after_id:
            q.insert(2, Query.greater_than("$id", after_id))
        return [Subscription.from_row(r) for r in self.repo.list(SUBSCRIPTION, q)]

    def list_paused(self, after_id: str | None, limit: int):
        q = [Query.equal("status", ["paused"]), Query.order_asc("$id"), Query.limit(limit)]
        if after_id:
            q.insert(1, Query.greater_than("$id", after_id))
        return [Subscription.from_row(r) for r in self.repo.list(SUBSCRIPTION, q)]

    def invoices_of(self, sub_id: str) -> list[Invoice]:
        rows = self.repo.list(INVOICE, [Query.equal("subscription_id", [sub_id]), Query.limit(200)])
        return [Invoice.from_row(r) for r in rows]

    def claims(self, sub_id: str) -> list[dict]:
        rows = self.repo.list(CLAIM, [Query.equal("subscription_id", [sub_id]), Query.limit(500)])
        return sorted(rows, key=lambda r: r["to_version"])

    # ---- invoices
    def get_invoice(self, invoice_id: str) -> Invoice:
        try:
            return Invoice.from_row(self.repo.get(INVOICE, invoice_id))
        except NotFound:
            raise UnknownInvoice(invoice_id) from None

    def create_invoice(self, inv: Invoice) -> tuple[Invoice, bool]:
        try:
            return Invoice.from_row(self.repo.create(INVOICE, inv.id, inv.to_row())), True
        except Conflict:
            return self.get_invoice(inv.id), False

    def update_invoice(self, invoice_id: str, patch: dict[str, Any]) -> Invoice:
        return Invoice.from_row(self.repo.update(INVOICE, invoice_id, patch))

    def claim_invoice_paid(self, invoice_id: str, payment_ref: str, now: datetime):
        """The payment that wins the invoice; `ref` is its provider reference."""
        data = {
            "kind": "invoice_paid",
            "subject_id": invoice_id,
            "ref": payment_ref,
            "claim_key": f"invoice_paid:{invoice_id}",
            "created_at": iso(now),
        }
        row_id = rid("paid", invoice_id)
        try:
            return self.repo.create(BILLING_CLAIM, row_id, data), True
        except Conflict:
            return self.repo.get(BILLING_CLAIM, row_id), False

    # ---- credits
    def create_credit(self, c: Credit) -> tuple[Credit, bool]:
        try:
            return Credit.from_row(self.repo.create(CREDIT, c.id, c.to_row())), True
        except Conflict:
            return Credit.from_row(self.repo.get(CREDIT, c.id)), False

    def unapplied_credits(self, sub_id: str) -> list[Credit]:
        rows = self.repo.list(CREDIT, [Query.equal("subscription_id", [sub_id]), Query.limit(500)])
        cs = [Credit.from_row(r) for r in rows if not r.get("applied_invoice_id")]
        return sorted(cs, key=lambda c: (c.created_at, c.id))

    def get_credit(self, credit_id: str) -> Credit:
        return Credit.from_row(self.repo.get(CREDIT, credit_id))

    def mark_credit_applied(self, credit_id: str, invoice_id: str) -> None:
        cur = self.repo.get(CREDIT, credit_id).get("applied_invoice_id")
        if cur is None:
            self.repo.update(CREDIT, credit_id, {"applied_invoice_id": invoice_id})

    def reserve_credit(self, credit_id: str, invoice_id: str, now: datetime) -> bool:
        """Claim a credit for ONE invoice (create-once). True if this invoice holds the claim
        (also on a retry of the same invoice); False if another invoice already reserved it."""
        row_id = rid("creditclaim", credit_id)
        data = {
            "kind": "credit_applied",
            "subject_id": credit_id,
            "ref": invoice_id,
            "claim_key": f"credit_applied:{credit_id}",
            "created_at": iso(now),
        }
        try:
            self.repo.create(BILLING_CLAIM, row_id, data)
            return True
        except Conflict:
            return self.repo.get(BILLING_CLAIM, row_id)["ref"] == invoice_id

    # ---- payments
    def claim_payment(self, provider: str, payment_ref: str, invoice_id: str, now: datetime):
        data = {
            "provider": provider,
            "payment_ref": payment_ref,
            "invoice_id": invoice_id,
            "status": "applying",
            "flag": None,
            "created_at": iso(now),
        }
        row_id = rid("pay", provider, payment_ref)
        try:
            return self.repo.create(PAYMENT, row_id, data), True
        except Conflict:
            return self.repo.get(PAYMENT, row_id), False

    def update_payment(self, provider: str, payment_ref: str, patch: dict[str, Any]) -> None:
        self.repo.update(PAYMENT, rid("pay", provider, payment_ref), patch)

    def list_payments(self, after_id: str | None, limit: int) -> list[dict]:
        q = [Query.order_asc("$id"), Query.limit(limit)]
        if after_id:
            q.insert(0, Query.greater_than("$id", after_id))
        return self.repo.list(PAYMENT, q)
