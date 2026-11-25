"""Property/fuzz test: random valid transactions, random crashes, retries, replays, conflicting
re-posts and reversals. After EVERY operation and at the end:

* the trial balance is zero,
* each account's balance equals an independently computed sum of its sealed entries (read from the
  raw rows: entries whose header exists),
* an idempotent retry never double counts (one header per key, entries == lines),
* after every key was retried to completion, balances equal the model's expectation."""

from __future__ import annotations

import random

import pytest

from hs_core.ledger.chart import FIXED_CHART, teacher_payable_code
from hs_core.ledger.errors import IdempotencyConflict
from hs_core.ledger.model import Kind, Side, TransactionRequest, credit, debit
from hs_core.ledger.testing import CrashInjected
from ledger_helpers import Env

TEACHERS = ["tA", "tB", "tC"]
SEEDS = range(120)


def oracle(env) -> dict[str, tuple[int, int]]:
    """account id -> (debits, credits) from raw rows: only entries whose header exists."""
    sealed = set(env.raw._t.get("ledger_transaction", {}))
    out: dict[str, list[int]] = {}
    for row in env.raw._t.get("ledger_entry", {}).values():
        if row["transaction_id"] in sealed:
            t = out.setdefault(row["account_id"], [0, 0])
            t[0 if row["side"] == "debit" else 1] += row["amount"]
    return {k: (v[0], v[1]) for k, v in out.items()}


def check(env) -> None:
    tb = env.svc.trial_balance()
    assert tb.is_balanced, tb.net
    want = oracle(env)
    got = {r.account_id: (r.debits, r.credits) for r in tb.rows}
    assert got == want
    for code in [*FIXED_CHART, *(teacher_payable_code(t) for t in TEACHERS)]:
        acct = env.svc.account(code)
        d, c = want.get(acct.id, (0, 0))
        expect = d - c if acct.normal_side is Side.DEBIT else c - d
        assert env.svc.balance(code).amount == expect


def random_request(rng: random.Random, n: int, codes: list[str]) -> TransactionRequest:
    k = rng.choice([2, 2, 2, 3, 4, 5])
    accounts = rng.sample(codes, k)
    # split a total into debits and credits independently, so any balanced shape is covered
    total = rng.randint(1, 5_000_000)

    def split(parts: int) -> list[int]:
        if parts == 1:
            return [total]
        cuts = sorted(rng.sample(range(1, total), parts - 1)) if total > parts else None
        if cuts is None:
            return [total] + [0] * (parts - 1)
        return [b - a for a, b in zip([0, *cuts], [*cuts, total], strict=True)]

    nd = rng.randint(1, k - 1)
    dr, cr = split(nd), split(k - nd)
    if 0 in dr or 0 in cr:
        dr, cr, accounts = [total], [total], accounts[:2]
    lines = tuple(
        [debit(a, v) for a, v in zip(accounts[:nd], dr, strict=False)]
        + [credit(a, v) for a, v in zip(accounts[nd:], cr, strict=False)]
    )
    return TransactionRequest(
        f"adjustment:f{n}",
        Kind.ADJUSTMENT,
        lines,
        reference_type="adjustment",
        reference_id=f"f{n}",
        description="fuzz",
        created_by_id="admin",
    )


@pytest.mark.parametrize("seed", SEEDS)
def test_random_operations_and_crashes_keep_every_invariant(seed):
    rng = random.Random(seed)
    env = Env("appwrite")
    for t in TEACHERS:
        env.svc.ensure_teacher_payable(t)
    codes = [*FIXED_CHART, *(teacher_payable_code(t) for t in TEACHERS)]
    requests: dict[str, TransactionRequest] = {}
    reversed_ids: dict[str, str] = {}
    reverse_attempted: set[str] = set()
    n = 0
    for _ in range(40):
        action = rng.choice(["new", "new", "retry", "retry", "conflict", "reverse"])
        if action == "new" or not requests:
            n += 1
            req = random_request(rng, n, codes)
            requests[req.idempotency_key] = req
            key = req.idempotency_key
        else:
            key = rng.choice(list(requests))
            req = requests[key]
        if action == "conflict" and env.svc.get_by_key(key) is None:
            continue  # a conflict needs a sealed original (an unsealed key is simply completed)
        crash = rng.randint(1, 8) if rng.random() < 0.5 and action != "conflict" else None
        env.faulty.arm(crash, rng.choice(["before", "after"]))
        try:
            if action == "conflict":
                other = TransactionRequest(
                    req.idempotency_key,
                    req.kind,
                    req.lines,
                    reference_type="adjustment",
                    reference_id=req.reference_id,
                    description="DIFFERENT",
                    created_by_id="admin",
                )
                with pytest.raises(IdempotencyConflict):
                    env.svc.post(other)
            elif action == "reverse":
                posted = env.svc.get_by_key(key)
                if posted is not None:
                    reverse_attempted.add(key)
                    rev = env.svc.reverse(posted.id, reason="fuzz")
                    reversed_ids[key] = rev.id
            else:
                env.svc.post(req)
        except CrashInjected:
            if rng.random() < 0.5:
                env.restart()
        env.faulty.arm(None)
        check(env)

    # converge: retry every key to completion; then each is posted exactly once
    for req in requests.values():
        env.svc.post(req)
    for key in reverse_attempted:  # a crashed reversal is completed by repeating it
        reversed_ids[key] = env.svc.reverse(env.svc.get_by_key(key).id, reason="fuzz").id
    check(env)
    headers = env.raw._t.get("ledger_transaction", {})
    entries = env.raw._t.get("ledger_entry", {})
    assert len(headers) == len(requests) + len(set(reversed_ids.values()))
    per_tx: dict[str, int] = {}
    for row in entries.values():
        per_tx[row["transaction_id"]] = per_tx.get(row["transaction_id"], 0) + 1
    assert all(per_tx[h] == headers[h]["entry_count"] for h in headers)  # no strays, no dupes
    # model expectation (reversals cancel their originals exactly)
    want: dict[str, int] = {}
    for key, req in requests.items():
        if key in reversed_ids:
            continue
        for ln in req.lines:
            a = env.svc.account(ln.account_code)
            sign = 1 if ln.side == a.normal_side else -1
            want[ln.account_code] = want.get(ln.account_code, 0) + sign * ln.amount
    for code in codes:
        assert env.svc.balance(code).amount == want.get(code, 0)
