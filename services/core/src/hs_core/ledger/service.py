"""`LedgerService`: the one place every money movement is posted (SR 7.10, #76).

Validation is storage-agnostic and happens before anything is written; the `LedgerStore` makes
the write atomic in effect and idempotent. Balances are DERIVED from sealed entries, never stored.
Logs carry ids and counts only (no names, phones or amounts together; F08 scrubber still applies).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

from . import chart
from .audit import AuditEvent, AuditSink, NullAuditSink
from .errors import (
    AuditFailed,
    DuplicateLine,
    InactiveAccount,
    InvalidTransaction,
    MixedCurrencies,
    TooFewLines,
    UnbalancedTransaction,
    UnknownAccount,
    UnknownTransaction,
)
from .model import (
    ID_CHARS,
    KEY,
    MAX_LINES,
    Account,
    AccountTotals,
    Entry,
    Kind,
    LineRequest,
    OrphanGroup,
    PostedTransaction,
    PreparedLine,
    PreparedTransaction,
    Side,
    TransactionRequest,
    iso,
    transaction_id_for,
)
from .money import Money, check_amount, check_currency
from .store import LedgerStore

log = logging.getLogger("hs_core.ledger")

DEFAULT_LEASE_SECONDS = 15 * 60  # an unsealed post younger than this may still be in flight


@dataclass(frozen=True, slots=True)
class TrialRow:
    account_code: str
    account_id: str
    currency: str
    debits: int
    credits: int
    balance: int  # on the account's normal side


@dataclass(frozen=True, slots=True)
class TrialBalance:
    as_of: str | None
    rows: tuple[TrialRow, ...]

    @property
    def net(self) -> dict[str, int]:
        """Per currency: sum(debits) - sum(credits) over all accounts. Always 0."""
        out: dict[str, int] = {}
        for r in self.rows:
            out[r.currency] = out.get(r.currency, 0) + r.debits - r.credits
        return out

    @property
    def is_balanced(self) -> bool:
        return all(v == 0 for v in self.net.values())


@dataclass(slots=True)
class RecoveryReport:
    completed: list[str] = field(default_factory=list)  # sealed by re-posting a rebuilt request
    abandoned: list[str] = field(default_factory=list)  # stale, not rebuildable: invisible forever


def _assert_balanced(lines: list[tuple[Account, Side, int]]) -> None:
    debits = sum(a for _, s, a in lines if s is Side.DEBIT)
    credits = sum(a for _, s, a in lines if s is Side.CREDIT)
    if debits != credits:
        raise UnbalancedTransaction("sum of debits must equal sum of credits")


class LedgerService:
    def __init__(
        self,
        store: LedgerStore,
        *,
        clock: Callable[[], datetime] | None = None,
        audit: AuditSink | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> None:
        self.store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._audit = audit or NullAuditSink()
        self.lease_seconds = lease_seconds

    # ---- accounts -------------------------------------------------------------------------
    def ensure_chart_of_accounts(self) -> dict[str, Account]:
        stamp = iso(self._clock())
        out = {a.code: self.store.create_account(a) for a in chart.fixed_accounts(stamp)}
        for a in out.values():
            self._record_account_audit(a)
        return out

    def ensure_teacher_payable(self, teacher_id: str) -> Account:
        """Race-safe: the id derives from the code, so concurrent callers claim one row."""
        acct = self.store.create_account(chart.teacher_payable(teacher_id, iso(self._clock())))
        self._record_account_audit(acct)
        return acct

    def _record_account_audit(self, a: Account) -> None:
        """One summary event per account (M06 #41), deterministic key and content (no stamps), so
        every ensure_* call is idempotent; a failure raises AuditFailed and repeating re-sends."""
        event = AuditEvent(
            key=f"ledger-account:{a.code}",
            action="ledger.account_create",
            entity_type="ledger_account",
            entity_id=a.id,
            actor_id=None,
            detail={
                "code": a.code,
                "type": a.type.value,
                "normal_side": a.normal_side.value,
                "currency": a.currency,
                "teacher_id": a.teacher_id,
            },
        )
        try:
            self._audit.record(event)
        except Exception as exc:
            log.warning("ledger.audit_failed", extra={"account": a.code})
            raise AuditFailed(f"account {a.code} exists but audit failed") from exc

    def account(self, code: str) -> Account:
        found = self.store.get_account(code) if isinstance(code, str) else None
        if found is None:
            raise UnknownAccount(f"no ledger account with code {code!r}")
        return found

    # ---- posting --------------------------------------------------------------------------
    def _prepare(self, req: TransactionRequest) -> PreparedTransaction:
        if not isinstance(req, TransactionRequest):
            raise InvalidTransaction("post() takes a TransactionRequest")
        key = req.idempotency_key
        if not isinstance(key, str) or not KEY.fullmatch(key) or len(key) > 200:
            raise InvalidTransaction("idempotency key must look like <kind>:<source id>")
        try:
            kind = Kind(req.kind)
        except ValueError:
            raise InvalidTransaction("unknown transaction kind") from None
        currency = check_currency(req.currency)
        for name, limit in (
            ("reference_type", 60),
            ("reference_id", 36),
            ("description", 500),
            ("reversal_reason", 500),
            ("created_by_id", 36),
        ):
            v = getattr(req, name)
            if v is not None and (not isinstance(v, str) or not v or len(v) > limit):
                raise InvalidTransaction(f"{name} must be a non-empty string up to {limit} chars")
        for name in ("reference_id", "created_by_id", "reverses_transaction_id"):
            v = getattr(req, name)
            if v is not None and not (isinstance(v, str) and ID_CHARS.fullmatch(v)):
                raise InvalidTransaction(f"{name} must be 1-36 characters of [A-Za-z0-9._-]")
        lines = tuple(req.lines) if isinstance(req.lines, (list, tuple)) else None
        if lines is None or len(lines) < 2:
            raise TooFewLines("a transaction needs at least two lines")
        if len(lines) > MAX_LINES:
            raise InvalidTransaction(f"a transaction has at most {MAX_LINES} lines")

        resolved: list[tuple[Account, Side, int]] = []
        seen: set[str] = set()
        cache: dict[str, Account] = {}
        for ln in lines:
            if not isinstance(ln, LineRequest):
                raise InvalidTransaction("lines must be LineRequest values")
            try:
                side = Side(ln.side)
            except ValueError:
                raise InvalidTransaction("line side must be debit or credit") from None
            amount = check_amount(ln.amount, what="line amount")
            code = ln.account_code
            acct = cache.get(code) if isinstance(code, str) else None
            if acct is None:
                acct = self.account(code)
                cache[code] = acct
            if not acct.active:
                raise InactiveAccount(f"account {acct.code} is retired")
            if acct.currency != currency:
                raise MixedCurrencies("line account currency differs from the transaction currency")
            if acct.id in seen:
                raise DuplicateLine(f"account {acct.code} appears more than once")
            seen.add(acct.id)
            resolved.append((acct, side, amount))
        _assert_balanced(resolved)
        self._check_reversal_fields(req, kind, key)
        if kind is Kind.REVERSAL:
            self._check_mirrors(req.reverses_transaction_id, resolved)

        canonical = sorted(resolved, key=lambda t: (t[0].id, t[1].value))
        prepared_lines = tuple(
            PreparedLine(line=i, account_id=a.id, side=s, amount=n)
            for i, (a, s, n) in enumerate(canonical, start=1)
        )
        return PreparedTransaction(
            id=transaction_id_for(key),
            idempotency_key=key,
            kind=kind,
            lines=prepared_lines,
            currency=currency,
            posted_at=iso(self._clock()),
            reference_type=req.reference_type,
            reference_id=req.reference_id,
            description=req.description,
            reverses_transaction_id=req.reverses_transaction_id,
            reversal_reason=req.reversal_reason,
            created_by_id=req.created_by_id,
        )

    @staticmethod
    def _check_reversal_fields(req: TransactionRequest, kind: Kind, key: str) -> None:
        if kind is Kind.REVERSAL:
            if not req.reverses_transaction_id or not req.reversal_reason:
                raise InvalidTransaction("a reversal names the original and a reason")
            if key != f"reversal:{req.reverses_transaction_id}":
                raise InvalidTransaction("a reversal's key is reversal:<original id> (one each)")
        elif req.reverses_transaction_id or req.reversal_reason:
            raise InvalidTransaction("only a reversal may carry reverses_transaction_id/reason")

    def _check_mirrors(
        self, original_id: str | None, lines: list[tuple[Account, Side, int]]
    ) -> None:
        original = self.store.get_transaction(original_id or "")
        if original is None:
            raise UnknownTransaction(f"no sealed transaction {original_id}")
        if original.kind is Kind.REVERSAL:
            raise InvalidTransaction("a reversal cannot itself be reversed; post a new transaction")
        want = {(e.account_id, e.side.opposite, e.amount) for e in original.lines}
        got = {(a.id, s, n) for a, s, n in lines}
        if want != got or len(original.lines) != len(lines):
            raise InvalidTransaction("a reversal must mirror the original's lines exactly")

    def post(self, request: TransactionRequest) -> PostedTransaction:
        """Validate, post atomically in effect, audit. Idempotent by `idempotency_key`; the same
        key with different content raises `IdempotencyConflict`. Safe to retry after any failure:
        a failed call has either posted nothing visible or can be completed by calling again."""
        prepared = self._prepare(request)
        posted = self.store.append(prepared)
        self._record_audit(posted)
        log.info(
            "ledger.post",
            extra={
                "transaction_id": posted.id,
                "kind": posted.kind.value,
                "entry_count": posted.entry_count,
                "replayed": posted.replayed,
            },
        )
        return posted

    def _record_audit(self, posted: PostedTransaction) -> None:
        event = AuditEvent(
            key=f"ledger:{posted.id}",
            action="ledger.post",
            entity_type="ledger_transaction",
            entity_id=posted.id,
            actor_id=posted.created_by_id,
            detail={
                "kind": posted.kind.value,
                "entry_count": posted.entry_count,
                "total_amount": posted.total_amount,
                "currency": posted.currency,
                "reference_type": posted.reference_type,
                "reference_id": posted.reference_id,
                "reverses_transaction_id": posted.reverses_transaction_id,
            },
        )
        try:
            self._audit.record(event)
        except Exception as exc:  # sealed already: say so, the retry re-sends the audit
            log.warning("ledger.audit_failed", extra={"transaction_id": posted.id})
            raise AuditFailed(f"transaction {posted.id} sealed but audit failed") from exc

    def reverse(
        self, transaction_id: str, *, reason: str, created_by_id: str | None = None
    ) -> PostedTransaction:
        """Post the mirror image of a sealed transaction as a NEW reversal; the original is never
        touched. One reversal per original (key `reversal:<id>`); repeating returns it."""
        original = self.store.get_transaction(transaction_id)
        if original is None:
            raise UnknownTransaction(f"no sealed transaction {transaction_id}")
        if original.kind is Kind.REVERSAL:
            raise InvalidTransaction("a reversal cannot itself be reversed; post a new transaction")
        codes = {a.id: a.code for a in self.store.list_accounts()}
        from .flows import reversal_of

        return self.post(reversal_of(original, codes, reason=reason, created_by_id=created_by_id))

    # ---- reads ----------------------------------------------------------------------------
    def get_transaction(self, transaction_id: str) -> PostedTransaction | None:
        return self.store.get_transaction(transaction_id)

    def get_by_key(self, idempotency_key: str) -> PostedTransaction | None:
        return self.store.get_by_key(idempotency_key)

    @staticmethod
    def _net(account: Account, t: AccountTotals) -> int:
        return t.debits - t.credits if account.normal_side is Side.DEBIT else t.credits - t.debits

    def balance(self, account_code: str, as_of: datetime | None = None) -> Money:
        """Balance on the account's normal side, derived from sealed entries (<= as_of)."""
        acct = self.account(account_code)
        totals = self.store.account_totals(as_of, acct.id).get(acct.id, AccountTotals())
        return Money(self._net(acct, totals), acct.currency)

    def statement(self, account_code: str, as_of: datetime | None = None) -> Iterator[Entry]:
        return self.store.entries(self.account(account_code).id, as_of)

    def trial_balance(self, as_of: datetime | None = None) -> TrialBalance:
        accounts = {a.id: a for a in self.store.list_accounts()}
        totals = self.store.account_totals(as_of)
        rows = []
        for aid, t in sorted(totals.items()):
            a = accounts.get(aid)
            code = a.code if a else f"unknown:{aid}"
            currency = a.currency if a else "UGX"
            bal = self._net(a, t) if a else t.debits - t.credits
            rows.append(TrialRow(code, aid, currency, t.debits, t.credits, bal))
        return TrialBalance(iso(as_of) if as_of else None, tuple(rows))

    # ---- recovery -------------------------------------------------------------------------
    def recover(
        self,
        *,
        rebuild: Callable[[OrphanGroup], TransactionRequest | None] | None = None,
        now: datetime | None = None,
        since: datetime | None = None,
    ) -> RecoveryReport:
        """Find unsealed line groups (a post that crashed before sealing). Younger than the lease
        they are in flight and left alone. Older: if `rebuild` returns the original request
        (the caller knows its source: payment, earning, payout row) it is re-posted, which
        completes it idempotently; otherwise the group is reported `abandoned`. Abandoned groups
        are NEVER visible in balances; they stay inert (rows are append-only) and a later retry of
        the same key still completes them."""
        from datetime import timedelta

        now = now or self._clock()
        report = RecoveryReport()
        for g in self.store.find_orphans(now - timedelta(seconds=self.lease_seconds), since):
            request = rebuild(g) if rebuild else None
            if (
                request is not None
                and transaction_id_for(request.idempotency_key) == g.transaction_id
            ):
                self.post(request)
                report.completed.append(g.transaction_id)
            else:
                report.abandoned.append(g.transaction_id)
                log.warning("ledger.orphan_abandoned", extra={"transaction_id": g.transaction_id})
        return report
