"""Behaviour checks shared by the retention tests and the mutation tests (C03).

Each check builds its own tiny world, runs the real code and returns True when the property holds.
`test_retention_mutations.py` breaks the code on purpose and asserts the matching check turns False,
so a check that cannot fail would itself be caught."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta

from hs_core.jobs.alerts import FakeAlertSink
from hs_core.jobs.model import Done, JobContext
from hs_core.retention.files import FakeRetentionFileStore
from hs_core.retention.periods import KAMPALA
from hs_core.retention.purger import PurgeConfig
from hs_core.retention.run import Deps, RetentionJob
from retention_helpers import NOW, Clock, RecRepo, add_log, ago, iso, make_purger, purge_table


def tok(repo, tid, revoked_at, user="u1"):
    repo.create("device_token", tid, {"user_id": user, "app": "parent", "platform": "ios", "token_hash": tid, "revoked_at": revoked_at})  # fmt: skip


def boundary_is_kept_and_next_day_goes() -> bool:
    repo = RecRepo()
    tok(repo, "edge", iso(datetime(2026, 12, 19, 12, tzinfo=KAMPALA)))
    tok(repo, "gone", iso(datetime(2026, 12, 18, 12, tzinfo=KAMPALA)))
    purge_table(make_purger(repo), "device_token", NOW)
    return set(repo.rows("device_token")) == {"edge"}


def kampala_date_not_utc_date() -> bool:
    """00:30 EAT on 19 Dec is still 18 Dec in UTC: 30 days on, 18 Jan is its LAST day (EAT)."""
    repo = RecRepo()
    tok(repo, "ny", iso(datetime(2026, 12, 19, 0, 30, tzinfo=KAMPALA)))
    purge_table(make_purger(repo), "device_token", NOW)  # 18 Jan 12:00 EAT
    return set(repo.rows("device_token")) == {"ny"}


def nothing_purged_inside_retention() -> bool:
    """Rows inside the prefilter band (29-30 days old) are read but must NOT be deleted."""
    repo = RecRepo()
    for i, age in enumerate((30, 29, 28, 5, 3)):
        tok(repo, f"t{i}", ago(age))
    purge_table(make_purger(repo), "device_token", NOW)
    return len(repo.rows("device_token")) == 5


def held_rows_survive() -> bool:
    repo = RecRepo()
    tok(repo, "a", ago(100), user="held")
    tok(repo, "b", ago(100), user="free")
    repo.create("retention_hold", "h", {"subject_kind": "user", "subject_id": "held", "reason": "other", "placed_by_id": "s", "placed_at": ago(1)})  # fmt: skip
    purge_table(make_purger(repo), "device_token")
    return set(repo.rows("device_token")) == {"a"}


def children_deleted_before_parent() -> bool:
    repo = RecRepo()
    add_log(repo, "l1")
    for i in range(2):
        repo.create("session_log_topic", f"lt{i}", {"session_log_id": "l1", "topic_id": "t", "mastery": "taught", "log_topic_key": f"k{i}"})  # fmt: skip
    purge_table(make_purger(repo), "session_log")
    order = repo.deletes()
    return bool(order) and order[-1] == ("session_log", "l1") and len(order) == 3


def no_offset_paging() -> bool:
    """150 held rows stay (so the cursor IS needed) and 100 due rows sit behind them."""
    repo = RecRepo()
    for i in range(150):
        tok(repo, f"a{i:03d}", ago(100), user="held")
    for i in range(100):
        tok(repo, f"z{i:03d}", ago(100))
    repo.create("retention_hold", "h", {"subject_kind": "user", "subject_id": "held", "reason": "other", "placed_by_id": "s", "placed_at": ago(1)})  # fmt: skip
    purge_table(make_purger(repo), "device_token")
    for op, table, q in repo.calls:
        if op == "list" and table == "device_token":
            if any(json.loads(x)["method"] in ("offset", "page") for x in q):
                return False
    return len(repo.rows("device_token")) == 150


def dry_run_writes_nothing() -> bool:
    repo = RecRepo()
    for i in range(3):
        tok(repo, f"t{i}", ago(100))
    repo.create("payment", "p", {"invoice_id": "i", "parent_id": "par", "provider": "m", "provider_reference": "r", "amount": 1, "currency": "UGX", "status": "ok", "idempotency_key": "k", "raw_callback": "x", "requested_at": ago(3000)})  # fmt: skip
    before = repo.writes
    purge_table(make_purger(repo, enforce=False), "device_token")
    purge_table(make_purger(repo, enforce=False), "payment")
    return repo.writes == before and len(repo.rows("device_token")) == 3


def default_config_is_dry_run() -> bool:
    return PurgeConfig().enforce is False


def files_deleted_before_row() -> bool:
    repo = RecRepo()
    files = FakeRetentionFileStore()
    files.put("teacher-docs", "f1")
    repo.create("teacher_document", "d1", {"teacher_id": "t", "type": "id", "file_id": "f1", "verification_status": "ok", "deleted_at": ago(4000)})  # fmt: skip
    order = []
    repo.on_write = lambda op, t, i: order.append("row") if op == "delete" else None
    files.fail_on = lambda b, f: order.append("file")
    purge_table(make_purger(repo, files=files), "teacher_document")
    return order == ["file", "row"]


def restrict_blocks_parent() -> bool:
    repo = RecRepo()
    repo.create("child", "c1", {"parent_id": "p", "name": "n", "date_of_birth": "2018-01-01", "deleted_at": ago(3000)})  # fmt: skip
    add_log(repo, "l1", child="c1", submitted_at=ago(5))
    purge_table(make_purger(repo), "child")
    return "c1" in repo.rows("child")


def money_is_never_deleted_and_is_minimised() -> bool:
    repo = RecRepo()
    repo.create("payment", "p", {"invoice_id": "i", "parent_id": "par", "provider": "m", "provider_reference": "r", "amount": 7, "currency": "UGX", "status": "ok", "idempotency_key": "k", "raw_callback": "msisdn", "requested_at": ago(3000)})  # fmt: skip
    purge_table(make_purger(repo), "payment")
    row = repo.rows("payment").get("p")
    return (
        bool(row)
        and row["raw_callback"] is None
        and row["amount"] == 7
        and row["parent_id"] != "par"
    )


def ledger_never_purged() -> bool:
    """Even if someone gave the ledger a period and flipped never_purge, with every switch on."""
    from dataclasses import replace

    from hs_core.retention.periods import Period
    from hs_core.retention.policy import POLICY, Action

    repo = RecRepo()
    repo.create("ledger_entry", "e", {"transaction_id": "t", "line": 1, "account_id": "a", "side": "debit", "amount": 5, "currency": "UGX", "posted_at": "2001-01-01T00:00:00+00:00"})  # fmt: skip
    pol = dict(POLICY)
    pol["ledger_entry"] = replace(pol["ledger_entry"], never_purge=False, action=Action.HARD_DELETE, period=Period(years=7), clock="posted_at")  # fmt: skip
    p = make_purger(repo, policy=pol, allow_append_only=True)
    try:
        purge_table(p, "ledger_entry")
    except Exception:  # noqa: BLE001
        pass
    return "e" in repo.rows("ledger_entry")


def append_only_needs_the_switch_and_the_floor() -> bool:
    def world(acted):
        r = RecRepo()
        r.create("safeguarding_case", "c", {"child_id": "ch", "raised_by_id": "u", "status": "closed", "description": "x", "category": "o", "raised_at": ago(15000), "closed_at": ago(15000)})  # fmt: skip
        r.create(
            "safeguarding_action",
            "a",
            {"case_id": "c", "action": "note", "by_id": "u", "acted_at": acted},
        )
        return r

    old, young = ago(15000), ago(100)
    r1 = world(old)
    purge_table(make_purger(r1, allow_append_only=False), "safeguarding_case")  # switch off
    off_ok = "a" in r1.rows("safeguarding_action") and "c" in r1.rows("safeguarding_case")
    r2 = world(young)
    purge_table(
        make_purger(r2, allow_append_only=True), "safeguarding_case"
    )  # action under the floor
    floor_ok = "a" in r2.rows("safeguarding_action") and "c" in r2.rows("safeguarding_case")
    return off_ok and floor_ok


def blast_radius_refuses() -> bool:
    repo = RecRepo()
    for i in range(100):
        tok(repo, f"t{i:03d}", ago(100) if i < 60 else ago(2))
    p = make_purger(repo, blast_override=False, max_delete_pct=10)
    return bool(p.blast_check(p.policy["device_token"], NOW))


def job_world():
    repo = RecRepo()
    for i in range(4):
        tok(repo, f"t{i}", ago(100))
    return repo


def make_job(repo, clock, enforce=True, lease_s=600):
    alerts = FakeAlertSink()
    deps = Deps(repo=repo, files=FakeRetentionFileStore(), config=PurgeConfig(enforce=enforce, quarantine_days=0, blast_override=True), clock=clock, alert=alerts, lease_s=lease_s)  # fmt: skip
    return RetentionJob(deps), alerts


def ctx(run_id="r"):
    return JobContext("retention-purge", run_id, f"d:{run_id}", NOW, {}, None, 0, None)


def guard_refuses_a_clock_before_the_last_run() -> bool:
    repo, clock = job_world(), Clock()
    job, _ = make_job(repo, clock)
    cursor = None
    while True:  # a first, normal run
        res = job(JobContext("retention-purge", "a", "d:a", NOW, {}, cursor, 0, None))
        if isinstance(res, Done):
            break
        cursor = res.cursor
    clock.now = NOW - timedelta(days=3)  # the clock jumped back
    for i in range(2):
        tok(repo, f"n{i}", ago(100))
    job2, _ = make_job(repo, clock)
    res = job2(ctx("b"))
    return (
        isinstance(res, Done)
        and res.detail.startswith("refused")
        and "n0" in repo.rows("device_token")
    )


def guard_refuses_absurd_years() -> bool:
    for year in (2001, 2200):
        repo = job_world()
        job, _ = make_job(repo, Clock(datetime(year, 1, 1, tzinfo=UTC)))
        res = job(ctx())
        if not (isinstance(res, Done) and res.detail.startswith("refused")) or len(repo.rows("device_token")) != 4:  # fmt: skip
            return False
    return True


def one_of_fifty_runs_wins() -> bool:
    import hs_core.retention.run as run_mod

    old = run_mod.ROWS_PER_CHUNK
    run_mod.ROWS_PER_CHUNK = 1
    try:
        repo = job_world()
        clock = Clock()
        results, lock = [], threading.Lock()

        def go(i):
            job, _ = make_job(repo, clock)
            res = job(ctx(f"r{i}"))
            with lock:
                results.append(res)

        ts = [threading.Thread(target=go, args=(i,)) for i in range(50)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        return sum(1 for r in results if not (isinstance(r, Done) and "lease" in r.detail)) == 1
    finally:
        run_mod.ROWS_PER_CHUNK = old


def quarantine_before_hard_delete() -> bool:
    repo = RecRepo()
    add_log(repo, "l1", session_key="nat")
    p = make_purger(repo, quarantine_days=14)
    purge_table(p, "session_log")
    row = repo.rows("session_log").get("l1")
    return bool(row) and bool(row.get("deleted_at")) and row.get("session_key") is None


def concurrent_purge_counts_once() -> bool:
    """Run B deletes every row after run A read its page and before A writes: A counts nothing."""
    repo = RecRepo()
    for i in range(30):
        tok(repo, f"t{i:03d}", ago(100))
    a, b = make_purger(repo), make_purger(repo)
    real = a.holds.held_ids
    result = {}

    def race(rule, rows):
        out = real(rule, rows)
        if "b" not in result:
            result["b"] = purge_table(b, "device_token")["deleted"]
        return out

    a.holds.held_ids = race
    got = purge_table(a, "device_token")["deleted"]
    return result["b"] == 30 and got == 0 and repo.rows("device_token") == {}


ALL = {
    name: fn
    for name, fn in sorted(globals().items())
    if callable(fn) and getattr(fn, "__module__", "") == __name__ and fn.__code__.co_argcount == 0
    and name not in ("job_world", "ctx", "tok", "make_job")
}  # fmt: skip
