"""The purger applies the policy to one table (C03): exactly the due rows, holds, order, files,
quarantine, dry run, paging, blast radius, crash injection."""

import json
import threading
from datetime import UTC, datetime, timedelta

import pytest

from hs_core.retention.files import FakeRetentionFileStore
from hs_core.retention.periods import KAMPALA
from hs_core.retention.purger import pseudonym
from retention_helpers import (
    NOW,
    Clock,
    Crash,
    RecRepo,
    add_child,
    add_log,
    ago,
    iso,
    make_purger,
    policy_with,
    purge_table,
)


def token(repo, tid, revoked_at, user="u1"):
    repo.create("device_token", tid, {"user_id": user, "app": "parent", "platform": "android", "token_hash": f"h{tid}", "last_seen_at": revoked_at, "revoked_at": revoked_at})  # fmt: skip


def kampala_noon(d):
    return iso(datetime(d.year, d.month, d.day, 12, tzinfo=KAMPALA))


# ------------------------------------------------------------------ exactly the records due
def test_time_travel_deletes_exactly_the_tokens_due_each_day():
    """30 day rule on `revoked_at`: due when today is AFTER revoked date + 30 days (Kampala)."""
    repo = RecRepo()
    base = datetime(2027, 1, 1, 12, tzinfo=KAMPALA)
    for i in range(0, 40):
        token(repo, f"t{i:02d}", iso(base + timedelta(days=i)))
    clock = Clock()
    purger = make_purger(repo, clock=clock)
    for day in range(25, 75):
        now = datetime(2027, 1, 1, 0, 30, tzinfo=KAMPALA) + timedelta(days=day)
        purge_table(purger, "device_token", now)
        remaining = set(repo.rows("device_token"))
        due = {
            f"t{i:02d}" for i in range(40) if (now.date() > (base + timedelta(days=i + 30)).date())
        }
        assert remaining == {f"t{i:02d}" for i in range(40)} - due, now


def test_boundary_exactly_at_retention_end_is_kept_and_next_day_goes():
    repo = RecRepo()
    token(repo, "edge", kampala_noon(datetime(2026, 12, 19).date()))  # +30d = 18 Jan: today
    token(repo, "gone", kampala_noon(datetime(2026, 12, 18).date()))
    purge_table(make_purger(repo), "device_token", NOW)
    assert set(repo.rows("device_token")) == {"edge"}


def test_dec_31_2330_kampala_versus_utc_date_decides_the_day_of_expiry():
    repo = RecRepo()
    token(repo, "ny", iso(datetime(2026, 12, 31, 23, 30, tzinfo=KAMPALA)))  # 31 Dec (EAT)
    token(
        repo, "ny2", iso(datetime(2027, 1, 1, 0, 30, tzinfo=KAMPALA))
    )  # UTC date: 31 Dec; EAT: 1 Jan
    p = make_purger(repo)
    purge_table(p, "device_token", datetime(2027, 1, 30, 23, 59, tzinfo=KAMPALA))
    assert set(repo.rows("device_token")) == {"ny", "ny2"}  # both still inside their last day
    purge_table(p, "device_token", datetime(2027, 1, 31, 0, 5, tzinfo=KAMPALA))
    assert set(repo.rows("device_token")) == {"ny2"}  # ny expired (31 Dec + 30d = 30 Jan)
    purge_table(p, "device_token", datetime(2027, 1, 31, 23, 59, tzinfo=KAMPALA))
    assert set(repo.rows("device_token")) == {"ny2"}  # ny2: 1 Jan + 30d = 31 Jan, kept all day
    purge_table(p, "device_token", datetime(2027, 2, 1, 0, 1, tzinfo=KAMPALA))
    assert repo.rows("device_token") == {}


def test_leap_year_and_month_end_periods_delete_on_the_calendar_not_in_30_day_steps():
    repo = RecRepo()
    pol = policy_with(device_token="1y")
    repo.create("device_token", "leap", {"user_id": "u", "app": "parent", "platform": "ios", "token_hash": "x", "revoked_at": iso(datetime(2024, 2, 29, 12, tzinfo=KAMPALA))})  # fmt: skip
    p = make_purger(repo, policy=pol)
    purge_table(p, "device_token", datetime(2025, 3, 1, 23, 0, tzinfo=KAMPALA))
    assert "leap" in repo.rows("device_token")  # 29 Feb + 1y = 1 Mar 2025 (kept through that day)
    purge_table(p, "device_token", datetime(2025, 3, 2, 0, 1, tzinfo=KAMPALA))
    assert "leap" not in repo.rows("device_token")


def test_unreadable_or_missing_clock_values_are_never_purged():
    repo = RecRepo()
    repo.create(
        "device_token",
        "nullclock",
        {"user_id": "u", "app": "parent", "platform": "ios", "token_hash": "x"},
    )
    repo.create("device_token", "bad", {"user_id": "u", "app": "parent", "platform": "ios", "token_hash": "y", "revoked_at": "not-a-date"})  # fmt: skip
    purge_table(make_purger(repo), "device_token", NOW)
    assert set(repo.rows("device_token")) == {"nullclock", "bad"}


def test_idempotent_second_run_changes_nothing():
    repo = RecRepo()
    for i in range(5):
        token(repo, f"t{i}", ago(100))
    p = make_purger(repo)
    first = purge_table(p, "device_token")
    writes = repo.writes
    second = purge_table(p, "device_token")
    assert first["deleted"] == 5 and second["deleted"] == 0 and repo.writes == writes


def test_dry_run_is_the_default_and_writes_nothing():
    repo = RecRepo()
    for i in range(5):
        token(repo, f"t{i}", ago(100))
    stats = purge_table(make_purger(repo, enforce=False), "device_token")
    assert stats["would_delete"] == 5 and stats["deleted"] == 0
    assert len(repo.rows("device_token")) == 5 and repo.writes == 5  # only the 5 seeding creates


def test_purge_config_default_is_dry_run():
    from hs_core.retention.purger import PurgeConfig

    assert PurgeConfig().enforce is False


# ------------------------------------------------------------------ holds
def test_explicit_hold_on_the_subject_blocks_and_lifting_it_allows():
    repo = RecRepo()
    token(repo, "t1", ago(100), user="u-held")
    token(repo, "t2", ago(100), user="u-free")
    repo.create("retention_hold", "h1", {"subject_kind": "user", "subject_id": "u-held", "reason": "legal_claim", "placed_by_id": "staff", "placed_at": ago(5)})  # fmt: skip
    p = make_purger(repo)
    st = purge_table(p, "device_token")
    assert set(repo.rows("device_token")) == {"t1"} and st["held"] == 1
    repo.update("retention_hold", "h1", {"revoked_at": ago(1)})
    purge_table(p, "device_token")
    assert repo.rows("device_token") == {}


def test_hold_on_a_single_row_blocks_only_that_row():
    repo = RecRepo()
    token(repo, "a", ago(100))
    token(repo, "b", ago(100))
    repo.create("retention_hold", "h", {"subject_kind": "row", "subject_id": "device_token:a", "reason": "other", "placed_by_id": "s", "placed_at": ago(2)})  # fmt: skip
    purge_table(make_purger(repo), "device_token")
    assert set(repo.rows("device_token")) == {"a"}


def test_open_safeguarding_case_holds_the_childs_data_without_any_hold_row():
    repo = RecRepo()
    add_child(repo, "c1")
    add_log(repo, "l1", child="c1")
    add_log(repo, "l2", child="c2")
    repo.create("safeguarding_case", "case1", {"child_id": "c1", "raised_by_id": "u", "status": "investigating", "description": "x", "category": "other", "raised_at": ago(9)})  # fmt: skip
    purge_table(make_purger(repo), "session_log")
    assert set(repo.rows("session_log")) == {"l1"}


def test_closed_case_is_not_a_hold_but_a_hold_row_on_the_case_still_is():
    repo = RecRepo()
    add_log(repo, "l1", child="c1")
    repo.create("safeguarding_case", "case1", {"child_id": "c1", "raised_by_id": "u", "status": "closed", "description": "x", "category": "other", "raised_at": ago(900), "closed_at": ago(800)})  # fmt: skip
    p = make_purger(repo)
    purge_table(p, "session_log")
    assert repo.rows("session_log") == {}
    add_log(repo, "l2", child="c1")
    repo.create("retention_hold", "h", {"subject_kind": "child", "subject_id": "c1", "reason": "safeguarding", "placed_by_id": "s", "placed_at": ago(1)})  # fmt: skip
    purge_table(p, "session_log")
    assert set(repo.rows("session_log")) == {"l2"}


def test_a_hold_is_inherited_by_child_rows_of_a_held_parent():
    repo = RecRepo()
    add_log(repo, "l1", child="c1")
    repo.create("session_log_topic", "lt1", {"session_log_id": "l1", "topic_id": "t", "mastery": "taught", "log_topic_key": "k"})  # fmt: skip
    repo.create("retention_hold", "h", {"subject_kind": "row", "subject_id": "session_log:l1", "reason": "legal_claim", "placed_by_id": "s", "placed_at": ago(1)})  # fmt: skip
    purge_table(make_purger(repo), "session_log")
    assert "l1" in repo.rows("session_log") and "lt1" in repo.rows("session_log_topic")


def test_a_hold_placed_after_the_page_was_read_is_seen_by_the_final_recheck():
    repo = RecRepo()
    token(repo, "t1", ago(100), user="u9")

    def place_hold(op, table, row_id):
        pass

    from hs_core.retention.holds import HoldChecker

    p = make_purger(repo)
    real = HoldChecker.held_ids
    calls = {"n": 0}

    def held_then_hold(self, rule, rows):
        out = real(self, rule, rows)
        calls["n"] += 1
        if calls["n"] == 1:  # between the page check and the delete a hold appears
            repo.create("retention_hold", "late", {"subject_kind": "user", "subject_id": "u9", "reason": "other", "placed_by_id": "s", "placed_at": ago(0)})  # fmt: skip
        return out

    HoldChecker.held_ids = held_then_hold
    try:
        purge_table(p, "device_token")
    finally:
        HoldChecker.held_ids = real
    assert set(repo.rows("device_token")) == {"t1"}


def test_hold_lookup_failure_deletes_nothing_fail_closed():
    repo = RecRepo()
    token(repo, "t1", ago(100))
    p = make_purger(repo)
    real = repo.list

    def boom(table, queries=None):
        if table == "retention_hold":
            raise RuntimeError("down")
        return real(table, queries)

    repo.list = boom
    from hs_core.retention.holds import HoldLookupFailed

    with pytest.raises(HoldLookupFailed):
        purge_table(p, "device_token")
    assert set(repo.rows("device_token")) == {"t1"}


# ------------------------------------------------------------------ order, restrict, cascade
def test_children_are_deleted_before_their_parent_and_never_after():
    repo = RecRepo()
    add_log(repo, "l1")
    for i in range(3):
        repo.create("session_log_topic", f"lt{i}", {"session_log_id": "l1", "topic_id": f"t{i}", "mastery": "taught", "log_topic_key": f"k{i}"})  # fmt: skip
        repo.create("session_log_struggle", f"ls{i}", {"session_log_id": "l1", "struggle_tag_id": f"s{i}", "log_struggle_key": f"q{i}"})  # fmt: skip
    purge_table(make_purger(repo), "session_log")
    order = repo.deletes()
    parent_pos = order.index(("session_log", "l1"))
    assert parent_pos == len(order) - 1
    assert {t for t, _ in order[:parent_pos]} == {"session_log_topic", "session_log_struggle"}
    assert repo.rows("session_log_topic") == {}


def test_a_parent_with_live_restrict_children_is_blocked_not_deleted():
    repo = RecRepo()
    add_child(repo, "c1", deleted_at=ago(2000))
    add_log(repo, "l1", child="c1", submitted_at=ago(10))  # recent log still within its period
    st = purge_table(make_purger(repo), "child")
    assert "c1" in repo.rows("child") and st["blocked"] == 1
    assert st["blocked_reasons"] == {"restrict:session_log": 1}


def test_a_child_is_purged_after_its_logs_and_not_while_a_subscription_is_live():
    repo = RecRepo()
    add_child(repo, "c1", deleted_at=ago(2000))
    repo.create("subscription", "s1", {"child_id": "c1", "parent_id": "p1", "plan_id": "pl", "status": "active"})  # fmt: skip
    p = make_purger(repo)
    purge_table(p, "child")
    assert "c1" in repo.rows("child")  # live subscription: never due
    repo.update("subscription", "s1", {"status": "cancelled", "cancelled_at": ago(1500)})
    st = purge_table(p, "child")
    assert repo.rows("child") == {} and st["deleted"] == 1
    # the subscription row stays (tax), its child reference becomes a pseudonym
    assert repo.rows("subscription")["s1"]["child_id"] == pseudonym("c1")


def test_child_clock_is_the_later_of_soft_delete_and_last_cancellation():
    repo = RecRepo()
    add_child(repo, "c1", deleted_at=ago(2000))
    repo.create("subscription", "s1", {"child_id": "c1", "parent_id": "p1", "plan_id": "pl", "status": "cancelled", "cancelled_at": ago(100)})  # fmt: skip
    purge_table(make_purger(repo), "child")
    assert "c1" in repo.rows("child")  # cancelled 100 days ago: 3 years not over


# ------------------------------------------------------------------ money: kept, minimised
def payment(repo, pid, parent="par1", requested=None, **kw):
    repo.create("payment", pid, {"invoice_id": "i1", "parent_id": parent, "provider": "momo", "provider_reference": f"ref{pid}", "amount": 50000, "currency": "UGX", "status": "succeeded", "idempotency_key": f"k{pid}", "raw_callback": '{"msisdn":"256700000000"}', "failure_reason": "n/a", "requested_at": requested or ago(3000), **kw})  # fmt: skip


def test_payments_past_the_tax_period_are_kept_with_personal_fields_minimised():
    repo = RecRepo()
    payment(repo, "old", requested=ago(3000))
    payment(repo, "new", requested=ago(400))
    st = purge_table(make_purger(repo), "payment")
    rows = repo.rows("payment")
    assert set(rows) == {"old", "new"}, "a payment is never deleted"
    assert st["anonymised"] == 1
    old = rows["old"]
    assert old["parent_id"] == pseudonym("par1") and old["raw_callback"] is None
    assert old["failure_reason"] is None
    assert (old["amount"], old["currency"], old["provider_reference"], old["status"]) == (50000, "UGX", "refold", "succeeded")  # fmt: skip
    assert rows["new"]["parent_id"] == "par1" and rows["new"]["raw_callback"]


def test_anonymising_twice_is_a_noop_and_the_marker_prefilter_skips_finished_rows():
    repo = RecRepo()
    for i in range(10):
        payment(repo, f"p{i}")
    p = make_purger(repo)
    purge_table(p, "payment")
    writes = repo.writes
    st = purge_table(p, "payment")
    assert repo.writes == writes and st["scanned"] == 0  # finished rows are not even listed


def test_same_parent_keeps_one_pseudonym_across_rows_and_it_hides_the_id():
    repo = RecRepo()
    payment(repo, "a", parent="par1")
    payment(repo, "b", parent="par1")
    payment(repo, "c", parent="par2")
    purge_table(make_purger(repo), "payment")
    r = repo.rows("payment")
    assert r["a"]["parent_id"] == r["b"]["parent_id"] != r["c"]["parent_id"]
    assert "par1" not in r["a"]["parent_id"] and len(r["a"]["parent_id"]) <= 36


def test_ledger_tables_are_never_purged_even_with_every_switch_on_and_an_ancient_clock():
    repo = RecRepo()
    repo.create("ledger_entry", "e1", {"transaction_id": "t", "line": 1, "account_id": "a", "side": "debit", "amount": 5, "currency": "UGX", "posted_at": "2001-01-01T00:00:00+00:00"})  # fmt: skip
    p = make_purger(repo, allow_append_only=True)
    for t in ("ledger_entry", "ledger_transaction", "ledger_account", "audit_log"):
        assert p.skip_reason(p.policy[t]) is not None
    assert "e1" in repo.rows("ledger_entry")


# ------------------------------------------------------------------ files
def teacher_doc(repo, did, fid, deleted_at):
    repo.create("teacher_document", did, {"teacher_id": "t1", "type": "id", "file_id": fid, "verification_status": "verified", "deleted_at": deleted_at})  # fmt: skip


def test_files_are_deleted_before_their_row_and_missing_files_are_fine():
    repo = RecRepo()
    files = FakeRetentionFileStore()
    files.put("teacher-docs", "f1")
    teacher_doc(repo, "d1", "f1", ago(4000))
    teacher_doc(repo, "d2", "already-gone", ago(4000))
    order = []
    repo.on_write = lambda op, t, i: order.append(("row", op, i)) if op == "delete" else None
    files.fail_on = lambda b, f: order.append(("file", f))
    st = purge_table(make_purger(repo, files=files), "teacher_document")
    assert repo.rows("teacher_document") == {} and not files.exists("teacher-docs", "f1")
    assert order.index(("file", "f1")) < order.index(("row", "delete", "d1"))
    assert st["files"] == 1  # only the file that existed is counted


def test_a_file_store_outage_leaves_the_row_for_the_next_run():
    repo = RecRepo()
    files = FakeRetentionFileStore()
    files.put("teacher-docs", "f1")
    teacher_doc(repo, "d1", "f1", ago(4000))

    def down(b, f):
        raise OSError("storage down")

    files.fail_on = down
    p = make_purger(repo, files=files)
    st = purge_table(p, "teacher_document")
    assert (
        "d1" in repo.rows("teacher_document")
        and st["errors"] == 1
        and files.exists("teacher-docs", "f1")
    )
    files.fail_on = None
    purge_table(p, "teacher_document")
    assert repo.rows("teacher_document") == {} and not files.exists("teacher-docs", "f1")


def test_orphan_file_sweeper_deletes_only_unreferenced_files_older_than_the_grace():
    from hs_core.retention.files import sweep_orphans
    from hs_core.retention.policy import POLICY

    repo = RecRepo()
    files = FakeRetentionFileStore()
    files.put("teacher-docs", "kept")
    files.put("teacher-docs", "orphan")
    files.put("teacher-docs", "fresh", created_at=NOW - timedelta(hours=1))
    teacher_doc(repo, "d1", "kept", None)
    dry = sweep_orphans(repo, files, POLICY, NOW, enforce=False)
    assert dry == {"scanned": 3, "orphans": 1, "deleted": 0} and files.exists(
        "teacher-docs", "orphan"
    )
    out = sweep_orphans(repo, files, POLICY, NOW, enforce=True)
    assert out["deleted"] == 1
    assert [f.id for f in files.list_files("teacher-docs")] == ["fresh", "kept"]


# ------------------------------------------------------------------ quarantine (soft first, hard later)
def test_soft_tables_are_quarantined_first_and_hard_deleted_after_the_window():
    repo = RecRepo()
    add_log(repo, "l1", session_key="natural-key")
    p = make_purger(repo, quarantine_days=14)
    st = purge_table(p, "session_log")
    row = repo.rows("session_log")["l1"]
    assert st["quarantined"] == 1 and row["deleted_at"] and row["session_key"] is None  # M02 rule
    st2 = purge_table(p, "session_log")
    assert st2["waiting"] == 1 and "l1" in repo.rows("session_log")
    later = NOW + timedelta(days=13, hours=23)
    purge_table(p, "session_log", later)
    assert "l1" in repo.rows("session_log")
    purge_table(p, "session_log", NOW + timedelta(days=14, hours=1))
    assert repo.rows("session_log") == {}


def test_dry_run_does_not_quarantine_either():
    repo = RecRepo()
    add_log(repo, "l1")
    st = purge_table(make_purger(repo, enforce=False, quarantine_days=14), "session_log")
    assert st["would_quarantine"] == 1 and "deleted_at" not in repo.rows("session_log")["l1"]


# ------------------------------------------------------------------ paging
def test_paging_uses_cursors_with_at_most_100_rows_and_never_offsets():
    repo = RecRepo()
    for i in range(450):
        token(repo, f"t{i:04d}", ago(100))
    purge_table(make_purger(repo), "device_token")
    lists = [q for op, t, q in repo.calls if op == "list" and t == "device_token"]
    assert lists
    for q in lists:
        methods = [json.loads(x)["method"] for x in q]
        assert "offset" not in methods and "page" not in methods
        limit = [json.loads(x)["values"][0] for x in q if json.loads(x)["method"] == "limit"]
        assert limit and max(limit) <= 100
    assert repo.rows("device_token") == {}


def test_a_table_of_20000_rows_is_walked_by_cursor_and_only_due_rows_go():
    repo = RecRepo()
    for i in range(20_000):
        token(repo, f"t{i:05d}", ago(100) if i % 10 == 0 else ago(5))
    st = purge_table(make_purger(repo), "device_token")
    assert st["deleted"] == 2000 and len(repo.rows("device_token")) == 18_000
    assert st["scanned"] <= 2100  # the prefilter, not a full scan


def test_held_rows_never_stall_the_cursor():
    repo = RecRepo()
    for i in range(150):
        token(repo, f"a{i:03d}", ago(100), user="held")
    for i in range(60):
        token(repo, f"z{i:03d}", ago(100), user="free")
    repo.create("retention_hold", "h", {"subject_kind": "user", "subject_id": "held", "reason": "other", "placed_by_id": "s", "placed_at": ago(1)})  # fmt: skip
    st = purge_table(make_purger(repo), "device_token")
    assert len(repo.rows("device_token")) == 150 and st["held"] == 150 and st["deleted"] == 60


def test_cursor_row_deleted_by_someone_else_restarts_the_table_safely():
    repo = RecRepo()
    for i in range(10):
        token(repo, f"a{i:03d}", ago(100), user="held")  # stay: they are the resume cursor
    for i in range(250):
        token(repo, f"t{i:03d}", ago(100))
    repo.create("retention_hold", "h", {"subject_kind": "user", "subject_id": "held", "reason": "other", "placed_by_id": "s", "placed_at": ago(1)})  # fmt: skip
    p = make_purger(repo)
    from hs_core.retention.purger import new_stats

    st = new_stats()
    chunk = p.purge_chunk("device_token", NOW, None, st, max_writes=30)
    assert not chunk.done and chunk.after == "a009"
    repo.delete("device_token", chunk.after)  # a concurrent actor removes the cursor row
    chunk = p.purge_chunk("device_token", NOW, chunk.after, st, max_writes=10_000)
    assert chunk.done and set(repo.rows("device_token")) == {f"a{i:03d}" for i in range(9)}


def test_chunks_resume_from_the_cursor_and_respect_the_write_budget():
    repo = RecRepo()
    for i in range(95):
        token(repo, f"t{i:03d}", ago(100))
    p = make_purger(repo)
    from hs_core.retention.purger import new_stats

    st, after, chunks = new_stats(), None, 0
    while True:
        c = p.purge_chunk("device_token", NOW, after, st, max_writes=20)
        chunks += 1
        assert c.writes <= 20
        if c.done:
            break
        after = c.after
    assert chunks >= 5 and st["deleted"] == 95


def test_stop_flag_ends_a_chunk_early_and_loses_nothing():
    repo = RecRepo()
    for i in range(50):
        token(repo, f"t{i:02d}", ago(100))
    p = make_purger(repo)
    from hs_core.retention.purger import new_stats

    st = new_stats()
    c = p.purge_chunk("device_token", NOW, None, st, 1000, stop=lambda: True)
    assert not c.done and c.writes == 0 and len(repo.rows("device_token")) == 50


# ------------------------------------------------------------------ blast radius and caps
def test_blast_radius_refuses_a_table_when_too_much_is_due():
    repo = RecRepo()
    for i in range(100):
        token(repo, f"t{i:03d}", ago(100) if i < 50 else ago(2))
    p = make_purger(repo, blast_override=False, max_delete_pct=10)
    reason = p.blast_check(p.policy["device_token"], NOW)
    assert reason and reason.startswith("blast_limit:50/100")
    assert make_purger(repo, blast_override=True).blast_check(p.policy["device_token"], NOW) is None


def test_blast_radius_allows_a_small_share_and_small_tables():
    repo = RecRepo()
    for i in range(100):
        token(repo, f"t{i:03d}", ago(100) if i < 10 else ago(2))
    p = make_purger(repo, blast_override=False, max_delete_pct=10)
    assert p.blast_check(p.policy["device_token"], NOW) is None  # exactly 10%: allowed
    repo2 = RecRepo()
    for i in range(5):
        token(repo2, f"t{i}", ago(100))  # 100% but a tiny table
    assert (
        make_purger(repo2, blast_override=False).blast_check(p.policy["device_token"], NOW) is None
    )


def test_per_table_cap_stops_a_runaway_run():
    repo = RecRepo()
    for i in range(300):
        token(repo, f"t{i:03d}", ago(100))
    st = purge_table(make_purger(repo, per_table_cap=120), "device_token")
    assert st["deleted"] == 120 and st["capped"] and len(repo.rows("device_token")) == 180


# ------------------------------------------------------------------ crash injection
def build_world(repo):
    for i in range(3):
        add_child(repo, f"c{i}", deleted_at=ago(2000))
        add_log(repo, f"l{i}", child="x")
        repo.create("session_log_topic", f"lt{i}", {"session_log_id": f"l{i}", "topic_id": "t", "mastery": "taught", "log_topic_key": f"k{i}"})  # fmt: skip
    teacher_doc(repo, "d1", "f1", ago(4000))
    payment(repo, "p1")


def snapshot(repo):
    return {t: {i: json.dumps(r, sort_keys=True) for i, r in repo.rows(t).items()} for t in ("session_log", "session_log_topic", "teacher_document", "payment", "child")}  # fmt: skip


def run_all(purger):
    for t in ("teacher_document", "session_log", "child", "payment"):
        purge_table(purger, t)


def test_crash_before_every_write_then_resume_reaches_the_same_state_with_no_orphans():
    clean = RecRepo()
    build_world(clean)
    cf = FakeRetentionFileStore()
    cf.put("teacher-docs", "f1")
    seed = clean.writes
    run_all(make_purger(clean, files=cf))
    expected = snapshot(clean)
    total = clean.writes - seed
    assert total >= 8
    for k in range(1, total + 1):
        repo = RecRepo()
        build_world(repo)
        files = FakeRetentionFileStore()
        files.put("teacher-docs", "f1")
        repo.crash_at = repo.writes + k
        with pytest.raises(Crash):
            run_all(make_purger(repo, files=files))
        # the crashed state never has a deleted parent with surviving children
        logs, topics = repo.rows("session_log"), repo.rows("session_log_topic")
        assert all(t["session_log_id"] in logs for t in topics.values()), f"orphan after crash {k}"
        repo.crash_at = None
        run_all(make_purger(repo, files=files))
        assert snapshot(repo) == expected, f"crash before write {k}"
        assert not files.exists("teacher-docs", "f1")


def test_crash_during_anonymise_resumes_without_double_pseudonymising():
    repo = RecRepo()
    for i in range(6):
        payment(repo, f"p{i}")
    repo.crash_at = repo.writes + 3
    with pytest.raises(Crash):
        purge_table(make_purger(repo), "payment")
    repo.crash_at = None
    purge_table(make_purger(repo), "payment")
    assert {r["parent_id"] for r in repo.rows("payment").values()} == {pseudonym("par1")}


# ------------------------------------------------------------------ concurrency
def test_fifty_threads_purging_the_same_table_delete_every_row_once_without_errors():
    repo = RecRepo()
    for i in range(400):
        token(repo, f"t{i:03d}", ago(100))
    errors, results = [], []

    def work():
        try:
            results.append(purge_table(make_purger(repo), "device_token"))
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=work) for _ in range(50)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors and repo.rows("device_token") == {}
    # racing deletes of one row are tolerated (NotFound), and each row is COUNTED once overall
    assert sum(r["deleted"] for r in results) == 400


# ------------------------------------------------------------------ misc
def test_utc_isoformat_clock_values_with_other_offsets_are_read_correctly():
    repo = RecRepo()
    repo.create("device_token", "t", {"user_id": "u", "app": "parent", "platform": "ios", "token_hash": "x", "revoked_at": "2026-12-01T00:30:00+03:00"})  # fmt: skip
    purge_table(make_purger(repo), "device_token", NOW)
    assert repo.rows("device_token") == {}


def test_a_failing_row_is_counted_and_the_rest_of_the_table_still_runs():
    repo = RecRepo()
    token(repo, "bad", ago(100))
    token(repo, "ok", ago(100))
    from hs_core.repository import RepositoryUnavailable

    def flaky(op, table, row_id):
        if op == "delete" and row_id == "bad":
            raise RepositoryUnavailable("503")

    repo.on_write = flaky
    st = purge_table(make_purger(repo), "device_token")
    assert set(repo.rows("device_token")) == {"bad"} and st["errors"] == 1 and st["deleted"] == 1


def test_naive_now_is_refused():
    repo = RecRepo()
    token(repo, "t", ago(100))
    with pytest.raises(ValueError):
        purge_table(make_purger(repo), "device_token", datetime(2027, 1, 18, 9, 0))
    assert UTC


def test_purging_a_teacher_minimises_the_earnings_that_stay_in_one_update():
    """The referencing row loses the link AND its other personal fields together: a half-minimised
    row (link gone, note kept) could never be found again by a later run."""
    repo = RecRepo()
    repo.create(
        "teacher_profile", "t1", {"user_id": "u", "status": "deactivated", "deleted_at": ago(4000)}
    )
    repo.create("earning", "e1", {"teacher_id": "t1", "session_id": "s", "type": "session", "amount": 5, "currency": "UGX", "status": "paid", "dedupe_key": "d", "created_at": ago(30), "note": "free text"})  # fmt: skip
    purge_table(make_purger(repo), "teacher_profile")
    e = repo.rows("earning")["e1"]
    assert repo.rows("teacher_profile") == {}
    assert e["teacher_id"] == pseudonym("t1") and e["note"] is None and e["amount"] == 5
    assert [c for c in repo.calls if c[0] == "update" and c[1] == "earning"] == [
        ("update", "earning", "e1")
    ]
