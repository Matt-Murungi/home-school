"""H05 (#134): random edit sequences never violate a constraint, a bound or the history chain.

Seeded `random` runs (no wall clock, no network): each seed drives a few dozen operations (valid and
invalid values, stale revisions, scheduled and immediate changes, rollbacks, cancels, clock jumps,
activation runs) and checks every invariant after every step."""

import random
from datetime import timedelta

import pytest

from hs_core.app_settings import REGISTRY, InvalidValue, validate_value
from hs_core.app_settings.model import SettingsError
from hs_core.audit import ReasonRequired
from hs_core.errors import AppError
from settings_helpers import ManualClock, ctx, history, stack, value_row

KEYS = [
    "billing.grace_days", "billing.past_due_cancel_days",
    "matching.offer_expiry_minutes", "matching.substitute_offer_expiry_minutes",
    "travel.daily_element_budget", "travel.monthly_element_budget",
    "matching.weights", "notify.quiet_hours", "session.auto_confirm_after_hours",
    "session.late_cancellation_notice_hours",
]  # fmt: skip
SEEDS = range(40)
STEPS = 45


def random_value(rng, d):
    if d.kind == "map":
        if rng.random() < 0.5 and d.sum_to:
            cuts = sorted(rng.randint(0, d.sum_to) for _ in range(len(d.fields) - 1))
            parts = [b - a for a, b in zip([0, *cuts], [*cuts, d.sum_to], strict=True)]
            return {f.name: p for f, p in zip(d.fields, parts, strict=True)}
        return {f.name: rng.randint(-5, 105 if f.hi > 30 else 25) for f in d.fields}
    span = int(d.hi - d.lo)
    pick = rng.random()
    if pick < 0.1:
        return rng.choice([-1, 0, d.hi + 1, 10**12, True, "5", None, 1.5])
    return rng.randint(int(d.lo), min(int(d.hi), int(d.lo) + min(span, 60)))


def value_in(svc, state, t):
    if state.pending is not None and state.pending.effective_at <= t:
        return state.pending.value
    return state.value


def assert_invariants(raw, svc, clock):
    states = {s.key: s for s in svc.states()}
    now = clock()
    points = {now} | {s.pending.effective_at for s in states.values() if s.pending}
    points |= {p + timedelta(days=1) for p in list(points)}
    for key, s in states.items():
        d = REGISTRY.defs[key]
        assert validate_value(d, s.value) == s.value
        if s.pending is not None:
            assert validate_value(d, s.pending.value) == s.pending.value
            assert s.pending.effective_at > now
        h = history(raw, key)
        assert [r["revision"] for r in h] == list(range(1, len(h) + 1))
        row = value_row(raw, key)
        assert (row["revision"] if row else 0) == len(h)
        assert s.revision == len(h)
        # the cached reader and the history agree on the value in force now
        assert svc.get(key) == svc.value_at(key, now) == s.value
    for t in points:  # every rule holds now and at every instant a pending change lands
        for c in REGISTRY.constraints:
            vals = {k: value_in(svc, states[k], t) for k in c.keys}
            assert c.check(vals), (c.name, t, vals)
    w = svc.get("matching.weights")
    assert sum(w.values()) == 100 and all(0 <= x <= 100 for x in w.values())
    q = svc.get("notify.quiet_hours")
    assert q["start_hour"] != q["end_hour"]


@pytest.mark.parametrize("seed", SEEDS)
def test_random_edit_sequences_never_break_an_invariant(seed):
    rng = random.Random(seed)
    raw, svc, _a, clock = stack(ttl_s=rng.choice([0.0, 30.0]))
    accepted = refused = 0
    for _ in range(STEPS):
        key = rng.choice(KEYS)
        d = REGISTRY.defs[key]
        st = next(s for s in svc.states() if s.key == key)
        rev = st.revision if rng.random() < 0.85 else st.revision + rng.choice([-1, 1, 5])
        action = rng.random()
        try:
            if action < 0.6:
                eff = None
                if rng.random() < 0.3:
                    eff = clock() + timedelta(days=rng.randint(0, 5), hours=rng.randint(0, 23))
                svc.change(
                    key,
                    random_value(rng, d),
                    reason="property test run",
                    expected_revision=rev,
                    ctx=ctx(),
                    effective_at=eff,
                )
            elif action < 0.7:
                svc.rollback(key, reason="property test run", expected_revision=rev, ctx=ctx())
            elif action < 0.78:
                svc.cancel_pending(
                    key, reason="property test run", expected_revision=rev, ctx=ctx()
                )
            elif action < 0.9:
                clock.advance(hours=rng.randint(1, 60))
                svc.reconcile(ctx=ctx("system"))
            else:
                svc.reconcile(ctx=ctx("system"))
            accepted += 1
        except (AppError, InvalidValue, ReasonRequired, SettingsError):
            refused += 1
        assert_invariants(raw, svc, clock)
    assert accepted + refused == STEPS


def test_the_property_runs_actually_exercise_both_accepted_and_refused_edits():
    rng = random.Random(7)
    raw, svc, _a, clock = stack()
    outcomes = set()
    for _ in range(80):
        key = rng.choice(KEYS)
        st = next(s for s in svc.states() if s.key == key)
        try:
            svc.change(
                key,
                random_value(rng, REGISTRY.defs[key]),
                reason="property test run",
                expected_revision=st.revision,
                ctx=ctx(),
            )
            outcomes.add("ok")
        except AppError as e:
            outcomes.add(e.code)
    assert {"ok", "invalid_value", "constraint_violated"} <= outcomes
    assert isinstance(clock, ManualClock)
