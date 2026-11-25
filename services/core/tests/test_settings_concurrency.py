"""H05 (#134): concurrent edits. Of any number of editors who read the same revision exactly one
wins; the history is a gap-free chain; readers never see a torn value."""

import threading
from concurrent.futures import ThreadPoolExecutor

from hs_core.app_settings.model import RevisionConflict
from settings_helpers import AUTO, REASON, ctx, history, stack, value_row


def run_all(fns):
    start = threading.Barrier(len(fns))

    def go(fn):
        start.wait()
        try:
            return ("ok", fn())
        except Exception as e:  # noqa: BLE001
            return ("err", e)

    with ThreadPoolExecutor(max_workers=len(fns)) as pool:
        return list(pool.map(go, fns))


def test_fifty_threads_editing_the_same_revision_one_wins_the_rest_get_409():
    raw, svc, _a, _c = stack()
    fns = [
        (
            lambda v=100 + i: svc.change(
                AUTO, v, reason=REASON, expected_revision=0, ctx=ctx(f"u{v}")
            )
        )
        for i in range(50)
    ]
    out = run_all(fns)
    wins = [r for k, r in out if k == "ok"]
    losses = [r for k, r in out if k == "err"]
    assert len(wins) == 1 and len(losses) == 49
    assert all(isinstance(e, RevisionConflict) and e.status == 409 for e in losses)
    h = history(raw, AUTO)
    assert len(h) == 1 and h[0]["revision"] == 1
    assert svc.get(AUTO) == wins[0].value
    assert value_row(raw, AUTO)["revision"] == 1


def test_two_instances_without_a_shared_lock_still_have_one_winner_per_revision():
    """The in-process lock is not what protects multi-instance: the history claim is."""
    raw, a, _x, clock = stack()
    _r, b, _y, _c = stack(repo=raw, clock=clock)
    fns = []
    for i in range(25):
        fns.append(
            lambda v=100 + i: a.change(
                AUTO, v, reason=REASON, expected_revision=0, ctx=ctx(f"a{v}")
            )
        )
        fns.append(
            lambda v=300 + i: b.change(
                AUTO, v, reason=REASON, expected_revision=0, ctx=ctx(f"b{v}")
            )
        )
    out = run_all(fns)
    assert sum(1 for k, _ in out if k == "ok") == 1
    assert len(history(raw, AUTO)) == 1


def test_retrying_writers_build_a_gap_free_chain_and_every_success_is_one_revision():
    raw, svc, _a, _c = stack()
    successes = []
    lock = threading.Lock()

    def worker(i):
        for attempt in range(30):
            rev = next(s for s in svc.states() if s.key == AUTO).revision
            try:
                svc.change(
                    AUTO,
                    100 + i * 31 + attempt % 7 + 1,
                    reason=REASON,
                    expected_revision=rev,
                    ctx=ctx(f"w{i}"),
                )
            except RevisionConflict:
                continue
            except Exception as e:  # noqa: BLE001 - a "no_change" collision is fine
                if getattr(e, "code", "") == "no_change":
                    continue
                raise
            with lock:
                successes.append(i)
            return

    run_all([(lambda i=i: worker(i)) for i in range(20)])
    h = history(raw, AUTO)
    assert [r["revision"] for r in h] == list(range(1, len(h) + 1))
    assert len(h) == len(successes) == 20
    last = h[-1]
    assert value_row(raw, AUTO)["revision"] == last["revision"]
    assert svc.get(AUTO) == int(last["value"])


def test_edits_to_different_settings_do_not_block_or_conflict():
    raw, svc, _a, _c = stack()
    keys = {
        "session.auto_confirm_after_hours": 30,
        "notify.rate_limit": 7,
        "notify.max_attempts": 3,
        "travel.cache_ttl_days": 5,
        "matching.max_offer_failures": 4,
    }
    out = run_all(
        [
            (lambda k=k, v=v: svc.change(k, v, reason=REASON, expected_revision=0, ctx=ctx()))
            for k, v in keys.items()
        ]
    )
    assert all(k == "ok" for k, _ in out)
    assert {k: svc.get(k) for k in keys} == keys


def test_readers_during_writes_only_ever_see_a_value_that_was_set_or_the_default():
    raw, svc, _a, _c = stack(ttl_s=0.0)
    allowed = {48} | {100 + i for i in range(40)}
    seen: set = set()
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            seen.add(svc.get(AUTO))

    t = threading.Thread(target=reader)
    t.start()
    try:
        for i in range(40):
            svc.change(AUTO, 100 + i, reason=REASON, expected_revision=i, ctx=ctx())
    finally:
        stop.set()
        t.join()
    assert seen <= allowed and svc.get(AUTO) == 139
