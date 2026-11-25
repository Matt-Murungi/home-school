# Billing operations (B03, #71)

Code: `services/core/src/hs_core/billing/`. Schema: `appwrite/schema/billing.json` plus additive columns on `subscription` and `invoice`, migration `0011_billing_schema.py`.

## How a transition is made durable

1. `store.commit` creates the claim row `subscription_transition(subscription, version+1)` (a second writer gets 409 = `ConcurrentTransition`), updates the subscription, marks the claim `applied`.
2. `service._complete` then runs the transition's **effects** (state-derived and idempotent: void unpaid invoices and flag a refund for a paid first period on cancel; void the open renewal on pause; reconcile invoices and create the current period's invoice on resume; mark overdue invoices on `payment_overdue`) and hands the **events** to the notifier and the session scheduler. Only when both succeeded is the claim marked `delivered` (`dispatched_at`).
3. Anything that fails or dies in between leaves the claim `claimed` or `applied`. Recovery paths, all idempotent: the same operation retried (cancel/pause/expire on an already-moved subscription re-run the effects), `store.repair` on every load (rolls a dead writer's claim forward after the 30 s lease and self-heals a row a stale repairer set back), the **`billing-redispatch`** job (every 15 minutes: repairs, redoes effects, resends events, marks delivered), and the **`billing-period-roll`** job (also creates a missing current-period invoice and finishes half-applied credits).
4. A permanent failure is visible: `billing.dispatch_failed`, `billing.effects_failed`, `billing.repaired_claim` (a claim had to be rolled forward: alert on it) and `billing.reconcile_found_sessions` are logged at error level.

Test fixture: `tests/billing_crash.py` + `tests/test_billing_crash.py` crash every lifecycle operation after EVERY write (repository writes and adapter calls), run the recovery above and check the invariants.

## Consumer contract (N01/N03, S01)

* Event ids are `<subscription_id>:<to_version>:<index>` (stable across redelivery) and `reconcile:<subscription_id>:<date>`. Consumers MUST de-duplicate on `event_id`; billing re-sends until the transition is delivered, so at-least-once is the contract.
* S01's calls are keyed by assignment (`cancel_future(assignment_id, from_date, reason)`, `regenerate(assignment_id, from_date)`). The adapter maps subscription -> assignment through `subscription.assignment_id` (set by `on_teacher_assigned`) and `from_at` -> Kampala date; a subscription with no assignment is a no-op. `future_sessions(subscription_id, from_at)` returns the count of future scheduled sessions, used by the daily **`billing-reconcile`** job to cancel sessions that survive on cancelled (last `reconcile_lookback_days`, default 7) and paused subscriptions.

## Money rules worth knowing

* A credit is reserved for one invoice first (`billing_claim` kind `credit_applied`, create-once), the invoice is created, then each credit is posted to the ledger and only then marked applied.
* A full discount makes the first invoice 0 UGX and `paid`; the service confirms it itself with a synthetic payment (provider `internal`, reference `zero-<invoice>`, no ledger posting), moving `pending_payment` to `matching`. The discount hook is not implemented yet; the rule is tested with a fake.
* A payment is identified by `(provider, payment_ref)` like M04 `payment`. `reconcile_payments` compares every applied `billing_payment` with the M04 `payment` row (same provider and reference, same invoice, status `succeeded`, same amount); `internal` zero payments are exempt. No job calls it yet: $01/#78 should run it.
* A confirmed payment for a void invoice (paused, cancelled, or an old anchor after a re-anchoring resume) is flagged `invoice_void` or `late_payment_after_cancellation` for the refund queue and never advances the subscription.
* `cancel_reason` and transition reasons are cut to 200 characters; the transition row records `reason_truncated`.

## Follow-ups for M06 (audit) and M05 (permissions)

These are not on main yet. When M06's `AuditedRepository` refuses unaudited writes to audited tables:

* Writes that need an audit context: `billing/store.py` (`create_subscription`, `commit`, `repair`, `create_invoice`, `update_invoice`, `mark_credit_applied`, `create_credit`, `claim_payment`/`update_payment`, `claim_child_slot`, `claim_invoice_paid`, `reserve_credit`, `mark_delivered`) and the service methods that call them: `BillingService.cancel`, `_heal_cancelled`, `_void_open_renewals`, `_reconcile_invoices`, `generate_invoice`, `on_payment_confirmed`, `redispatch_undelivered`.
* The billing jobs (`billing-period-roll`, `billing-redispatch`, `billing-reconcile`) must run under M06's `job_scope` (system actor); any future API route calling the service needs `Depends(audit_context)`.
* Billing's own `AuditSink` entries (`subscription.<trigger>`, `payment.applied`, `payment.flagged`, `credit.recorded`) would duplicate M06's intent rows: decide which survives and replace `AuditSink`, do not double-audit.
* M05: `subscription_transition`, `billing_credit`, `billing_payment` and `billing_claim` are service-only (no client read or write); `subscription` and `invoice` keep their M02/M04 decisions.

## Ledger ($01) mapping: why the real adapter is not wired yet

`hs_core.ledger` is on main (flows `invoice_issued`, `payment_received`, `credit_issued`, `refund_paid`; ids must match `[A-Za-z0-9._-]{1,36}`, provider is the ledger's `Provider` enum). `BillingLedger` maps as: `record_payment` -> `flows.payment_received(<36-char id derived from provider:reference>, amount, provider, invoice_id=...)`; `record_credit_applied` -> `flows.credit_issued(credit_id, amount)`. Two gaps keep it fail-closed outside dev: (1) billing never posts the invoice itself, but a payment credits receivables, so `BillingLedger` needs `record_invoice_issued(invoice_id, gross_ugx = plan price - discount)` called idempotently when an invoice is created or settled (the net payment plus credit postings then net receivables to zero); (2) `flag_refund_due` has no ledger or schema equivalent: PAY-7 needs an admin refund/credit queue (a table or an `adjustment` workflow) before a real adapter can exist. Both are follow-ups for the ledger-wiring/admin-refund tickets.

## Notifications (N01) mapping: why the real adapter is not wired yet

`hs_core.notifications.NotificationService.send(event_key, recipient, variables, idempotency_key, ...)` is catalogue-driven (for example `payment_received_parent`, `payment_due_parent`). Billing's `Notifier.notify(DomainEvent)` carries domain events (`first_payment_confirmed`, `subscription_paused`, ...). The adapter must map each event to a catalogue event (some have none yet: add them to the catalogue), resolve the recipient (the subscription's `parent_id`) and the variables (amount, due date), and pass `idempotency_key = event.event_id` (N01 de-duplicates on it). Owner: N01/N03 wiring ticket.

## Wiring

`create_app(billing_adapters=BillingAdapters(ledger, audit, notifier, scheduler))`. In dev the documented fakes are used; in staging and production a missing adapter leaves the service unbound and all three billing jobs raise `BillingNotWired`. Real adapters are owned by $01 (ledger), M06 (audit), N01/N03 (notifier) and S01 (scheduler).
