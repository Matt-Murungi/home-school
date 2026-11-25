"""N01 (#126): property tests over random event streams and concurrency."""

import random
import threading
from datetime import UTC, datetime, timedelta

import pytest

from hs_core.notifications import catalog
from hs_core.notifications.service import Recipient
from hs_core.notifications.templates import sample_values
from notify_helpers import LESSON, Rig


def vars_for(ev):
    return sample_values(ev.variables)


@pytest.mark.parametrize("seed", range(6))
def test_random_streams_never_break_the_invariants(seed):
    rng = random.Random(seed)
    start = datetime(2027, 3, 1, 0, 0, tzinfo=UTC) + timedelta(minutes=rng.randrange(1440))
    rig = Rig(start)
    users = {}
    for i in range(6):
        role = rng.choice(["parent", "teacher", "admin"])
        rec = Recipient(f"u{i}", role)
        rig.user(rec)
        users[rec.user_id] = rec
        if rng.random() < 0.5:
            rig.prefs.set_marketing_muted(rec.user_id, True)
        if rng.random() < 0.3:
            rig.prefs.set_muted_channels(rec.user_id, {rng.choice(["push", "email", "in_app"])})
    events = list(catalog.EVENTS.values())
    for step in range(120):
        rig.clock.advance(minutes=rng.randrange(1, 90))
        ev = rng.choice(events)
        cands = [u for u in users.values() if u.role == ev.role]
        if not cands:
            continue
        who = rng.choice(cands)
        rig.svc.send(ev.key, who, vars_for(ev), f"s{seed}-{step % 40}")  # repeats keys on purpose
        if rng.random() < 0.3:
            rig.svc.dispatch_due()
    rig.clock.advance(hours=30)
    rig.svc.dispatch_due(page=500)

    for row in rig.repo.list("notification"):
        ev = catalog.EVENTS.get(row["template_key"])
        # 1. never a marketing notification to a muted user
        if row["category"] == "marketing" and row["status"] in ("sent", "delivered"):
            p = rig.prefs.get(row["user_id"])
            assert not p.marketing_muted and row["channel"] not in p.muted_channels
        # 2. never deferred (scheduled after creation) when critical; nothing here fails/retries
        if ev and ev.priority == "critical":
            assert row["scheduled_at"] <= row["created_at"], "critical notification was deferred"
        # 3. a safeguarding row is never blocked by preferences
        if row["category"] == "safeguarding":
            assert row["last_error"] != "suppressed:marketing_muted"
    # 4. exactly once per provider idempotency key
    assert rig.push.deliveries == len(rig.push.accepted_keys)
    assert all(n == 1 for n in rig.push.accepted_keys.values())
    assert rig.push.deliveries > 0  # the stream really sent something


def test_exactly_once_per_idempotency_key_under_50_threads():
    rig = Rig()
    rig.user(Recipient("u1", "parent"))
    barrier = threading.Barrier(50)
    results = []

    def go():
        barrier.wait()
        results.append(
            rig.svc.send(
                "session_cancelled_parent",
                Recipient("u1", "parent"),
                {"lesson_time": LESSON, "reason_label": "x"},
                "one-key",
            )
        )

    ts = [threading.Thread(target=go) for _ in range(50)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(results) == 50
    assert sum(1 for r in results if not r.duplicate) == 1
    assert rig.push.deliveries == 1 and len(rig.email.sent_unique) == 1
    assert len(rig.repo.list("notification")) == 3  # push, email, in_app


def test_50_threads_many_keys_each_sent_once():
    rig = Rig()
    rig.user(Recipient("u1", "parent"))
    keys = [f"key-{i % 10}" for i in range(50)]
    ts = [
        threading.Thread(
            target=rig.svc.send,
            args=(
                "session_log_published_parent",
                Recipient("u1", "parent"),
                {"lesson_time": LESSON},
                k,
            ),
        )
        for k in keys
    ]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert rig.push.deliveries == 5  # rate limit caps one template at 5; keys are exactly-once
    assert len({r["dedupe_key"] for r in rig.repo.list("notification")}) == len(
        rig.repo.list("notification")
    )
