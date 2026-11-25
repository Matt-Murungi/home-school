# Ledger service ($01, #76, SR 7.10)

Code: `services/core/src/hs_core/ledger/`. Python API only (no HTTP; admin endpoints come with later tickets). Tests: `cd services/core && uv run pytest -q tests/test_ledger_*.py` (offline).

## What it guarantees

- Every money movement is one balanced transaction: sum(debits) == sum(credits) per currency, integer UGX (plain `int`, 1..10^12 per line; floats, bools, strings, zero, negatives refused; `Money` refuses floats too).
- Rejected before anything is written: unbalanced, zero/negative/float/bool/huge amounts, unknown or retired accounts, empty or single-line, the same account twice, mixed currencies, bad keys.
- Idempotent by `idempotency_key`: same key + same content returns the original (`replayed=True`); same key + different content raises `IdempotencyConflict` (field names only) and changes nothing.
- Append-only: all ledger rows go through `AppendOnlyRepository` (create/get/list only). A mistake is fixed by a **reversal** (a new `reversal` transaction that mirrors the original, key `reversal:<original id>`, so an original is reversed once); originals are never touched.
- Balances are derived from sealed entries (`balance(code, as_of)`, `trial_balance(as_of)`); nothing stores a balance. The trial balance always nets to zero per currency, also while other posts run.
- Every post calls the audit sink (`AuditSink`, M06 #41): key `ledger:<transaction id>`, ids/counts only. Called also on replays, so an audit entry cannot be lost between "sealed" and "audited" (the sink must treat a repeated key as success).

## Write protocol (no multi-row transaction in Appwrite)

1. Deterministic ids: transaction id = hash(idempotency key), line id = hash(transaction id, line number), account id = hash(code). `create` with a known id is an atomic claim (409 = already written).
2. Lines 1..n are written first (append-only `ledger_entry`, lines sorted canonically so argument order is not "content").
3. **Seal**: the header row `ledger_transaction` (entry_count, total_amount) is written last. It is the commit record.
4. Readers (`account_totals`, `entries`, `get_transaction`) count only entries whose header exists. A trial balance first collects the sealed set, then sums entries of that set only, so it is one consistent snapshot.
5. A crash after any write leaves invisible orphan lines (or a sealed transaction when the crash is after the header). Retrying the same request finds the orphans, verifies they match (otherwise `IdempotencyConflict`), writes the missing lines (reusing the first attempt's `posted_at`), and seals. `tests/test_ledger_crash.py` injects a crash before and after every single write (2-line and 4-line transactions, reversals, crash during the retry too) and checks no half-posted transaction is ever visible and a retry converges to exactly one transaction.
6. `LedgerService.recover(rebuild=...)` scans for unsealed line groups older than the lease (15 min). If `rebuild(group)` returns the original request (the caller knows its payment/earning/payout row) it is re-posted and sealed; otherwise the group is reported `abandoned`: it stays invisible forever (rows are append-only, nothing can delete it) and a later retry of the same key still completes it. Lines do not carry the idempotency key or kind, so the ledger cannot rebuild a header alone; a schema follow-up (below) would allow that. It scans per account through the `(account_id, posted_at)` index and is meant for a periodic job (F04), not a request path.
7. The audit call comes after the seal. If it fails, `AuditFailed` is raised with the transaction already sealed; the same call again re-sends the audit.

Concurrency: 50 threads on one key make one transaction (one creator, the rest replay); 50 threads on different transactions touching one account lose no update (nothing is read-modify-written, every post only inserts rows); readers never see a half-posted transaction.

## Accounts and flows

Codes are fixed and never reused; created by `ensure_chart_of_accounts()` (idempotent; the M04 migration seeds nothing) and `ensure_teacher_payable(teacher_id)` (race-safe).

| Code | Type / normal side |
|---|---|
| `receivable:parents` | asset / debit |
| `cash:mtn_momo`, `cash:airtel_money`, `cash:card_gateway` | asset / debit |
| `teacher_payable:<teacher id>` | liability / credit |
| `revenue:platform` | revenue / credit |
| `contra_revenue:refunds_credits` | revenue / **debit** (contra revenue: the M04 enum has no contra type; live calls it `contra_revenue`, owner Q17) |
| `expense:provider_fees` | expense / debit |

Posting helpers (`ledger/flows.py`, pure, return a `TransactionRequest`, not wired to events):

| Flow | Debit | Credit | Key | Kind |
|---|---|---|---|---|
| invoice issued | receivable | platform revenue | `invoice:<invoice id>` | adjustment* |
| payment received (MoMo/Airtel/card) | cash:<provider> | receivable | `payment:<payment id>` | payment |
| session earning accrued | platform revenue | teacher payable | `earning:<earning id>` | earning |
| payout disbursed | teacher payable | cash:<provider> | `payout:<payout id>` | payout |
| refund paid | refunds+credits | cash:<provider> | `refund:<refund id>` | refund |
| credit issued | refunds+credits | receivable | `credit:<credit id>` | refund |
| provider fee | provider fees | cash:<provider> | `fee:<fee id>` | fee |
| adjustment | caller's lines + reason + admin id | | `adjustment:<id>` | adjustment |
| reversal | mirror of the original | | `reversal:<original transaction id>` | reversal |

\* the M04 `kind` enum has no `invoice`; invoices are booked as `adjustment` with `reference_type=invoice`. A negative earning (clawback) flips the sides. The accrual semantics (gross billing as revenue, earning as an expense of revenue) are my reading of SR 7.10 and need the owner/accountant's OK before launch.

**Idempotency key convention: `<prefix>:<source id>`**, one key per source business object (a payment id, an earning id, ...), source id = Appwrite row id (1-36 chars of `[A-Za-z0-9._-]`), also stored as `reference_id` on the header and every line. $02 (MoMo) and the T-tickets build the request with the helper and post it; a retry is always the same request.

Reconciliation primitives for $05/R04 (`ledger/reconcile.py`): `reconcile_payments(service, provider, payments)` (per payment: missing in ledger, unexpected in ledger, amount mismatch; reversed payments count as absent) and `reconcile_cash(service, provider, reported_balance)`. They take the provider rows or statement as plain data; real-money reconciliation needs provider statements (owner/R04).

## The `LedgerStore` boundary and the Appwrite vs Postgres question (owner Q10)

`ledger/store.py` defines `LedgerStore`; everything above it (validation, chart, helpers, balances, reconciliation, audit) is storage-agnostic. Implementations: `AppwriteLedgerStore` (the protocol above), `InMemoryLedgerStore` (reference, atomic by lock). **Moving to Postgres = write `PostgresLedgerStore` and pass `tests/test_ledger_store_contract.py`**; no caller changes. In Postgres `append` is one database transaction (unique constraint on `idempotency_key`, `CHECK`/deferred trigger that debits == credits, `REVOKE UPDATE, DELETE` on the tables, `SUM`-based `account_totals` in one query), so orphans, seals and the recovery routine disappear. Data migration is a copy of sealed transactions keyed by idempotency key.

| | Stay in Appwrite | Dedicated Postgres |
|---|---|---|
| Atomicity | emulated (header-last seal, deterministic ids); proven by fault injection, but correctness lives in our code | native transaction |
| Append-only | opt-in wrapper + AST tripwire; the server API key can still update/delete | database privileges (revoke UPDATE/DELETE) |
| Balances | O(entries of the account) per read: cursor pages of 1,000 + header lookups (20,000 entries fine; hundreds of thousands need a roll-up) | one indexed `SUM`, or materialised roll-ups |
| Balanced-by-construction | only service code | also a database constraint (defence in depth) |
| Cost / ops | nothing new | one more service to run, back up, secure, migrate and monitor; PDPO processor entry |
| Auditability | fine | stronger |

My recommendation: ship MVP on Appwrite behind this interface (volumes are small: 20k entries is far beyond the first months) and decide before automated payouts at scale; the swap is bounded by the contract test. The decision stays open (owner Q10).

## Performance limits

All reads page by cursor (limit 1,000, never offset, never trusting `total` above 5,000). `balance` reads one account's entries plus one batched header lookup per 100 transactions; `trial_balance` reads all headers then all entries. 20,000 entries are covered by `tests/test_ledger_scale.py`. The schema has no roll-up table, so there are no stored period totals; if reads get slow, add a sealed `ledger_rollup` (account, day, debits, credits) written by a job and read as base + tail. Appwrite caps query values (about 100 per `equal`) so header lookups are batched.

## Schema follow-ups (not done here; M04 is merged)

1. Add `invoice` to `ledger_transaction.kind` (and a `contra_revenue` account type if the owner prefers it to normal-side flips).
2. Add `idempotency_key` and `kind` (or a `content_hash`) to `ledger_entry`: then `recover()` could rebuild and seal orphans without the caller.
3. `ledger_entry` has no index on `posted_at` alone: orphan scans go per account.
4. The schema notes and `AppendOnlyRepository` docstring still say "nothing uses it yet / not wired": the ledger now uses it for `ledger_entry` and `ledger_transaction`. Reword in a docs-only follow-up (the M04 doc test pins that wording, so change both together).

## Settings

None. The service takes a `LedgerStore`, a clock (UTC) and an `AuditSink`; wiring into the app is for the tickets that post money.
