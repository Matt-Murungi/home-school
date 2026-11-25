"""A03: crash injection after every write of grant/revoke, and concurrent changes."""

from __future__ import annotations

import threading

import pytest

from hs_core.roles.service import SIGNUP, Actor, LastSuperadmin, RoleError
from roles_helpers import OPS, Boom, Env

ACTORS = {"ops": OPS(), "signup": SIGNUP}


def _writes_of(op) -> int:
    e = Env()
    u = e.user()
    op(e, u, prepare=True)
    e.tick.n = 0
    op(e, u)
    return e.tick.n


def grant(e, u, prepare=False):
    if prepare:
        return
    e.service.grant_role(ACTORS["ops"], u, "teacher", reason="r")


def revoke(e, u, prepare=False):
    if prepare:
        e.service.grant_role(ACTORS["ops"], u, "teacher")
        return
    e.service.revoke_role(ACTORS["ops"], u, "teacher", reason="r")


def staff_grant(e, u, prepare=False):
    if prepare:
        return
    e.service.grant_role(e.boss, u, "curator", reason="test reason")


def consistent(e, u, role) -> bool:
    has_label = role in e.labels(u)
    has_row = bool(e.active_rows(u, role))
    return has_label == has_row


def recover(e, hours=1):
    """Run M06's audit-recover over the raw audit rows (after the grace period)."""
    from datetime import timedelta

    from hs_core.audit import AuditService
    from hs_core.audit.recover import recover_audit

    e.clock.advance(hours * 3600)
    svc = AuditService(e.raw_repo, clock=e.clock)
    return recover_audit(
        e.raw_repo,
        svc,
        now=e.clock(),
        grace=timedelta(seconds=60),
        lookback=timedelta(days=2),
    )


@pytest.mark.parametrize("op", [grant, staff_grant], ids=["grant", "staff-grant"])
def test_crash_before_every_write_of_a_grant_then_retry_converges_and_never_duplicates(op):
    role = "teacher" if op is grant else "curator"
    e0 = Env()
    e0.boss = e0.superadmin()[1]
    u0 = e0.user()
    e0.tick.n = 0
    op(e0, u0)
    total = e0.tick.n
    assert total == 3  # audit intent, history row, label
    for k in range(1, total + 1):
        e = Env()
        e.boss = e.superadmin()[1]
        u = e.user()
        e.tick.n, e.tick.crash_at = 0, k
        with pytest.raises(Boom):
            op(e, u)
        # never label-only; a staff/mobile grant gives the user LESS than recorded on a crash
        assert role not in e.labels(u)
        if k <= 2:
            assert not e.active_rows(u, role)  # audit intent at most, no row
        else:
            assert e.active_rows(u, role)  # row without label
        e.tick.crash_at = None
        op(e, u)  # the caller retries
        assert consistent(e, u, role) and role in e.labels(u)
        assert len(e.rows(u, role)) == 1
        op(e, u)  # and a third time changes nothing
        assert len(e.rows(u, role)) == 1


@pytest.mark.parametrize("op", [grant, staff_grant], ids=["grant", "staff-grant"])
def test_m06_recovery_settles_the_intent_of_a_grant_that_never_landed(op):
    from hs_core.audit.service import meta_of

    role = "teacher" if op is grant else "curator"
    e = Env()
    e.boss = e.superadmin()[1]
    u = e.user()
    e.tick.n, e.tick.crash_at = 0, 2  # intent written, history row not
    with pytest.raises(Boom):
        op(e, u)
    assert len(e.audit.role_intents()) == 1 and not e.audit.outcomes()
    e.tick.crash_at = None
    res = recover(e)
    assert res.not_applied == 1
    (out,) = e.audit.outcomes()
    assert meta_of(out)["status"] == "not_applied"
    assert not e.active_rows(u, role) and role not in e.labels(u)


def test_m06_recovery_leaves_the_intent_of_a_grant_that_landed_alone():
    e = Env()
    u = e.user()
    e.tick.n, e.tick.crash_at = 0, 3  # intent and row written, label not
    with pytest.raises(Boom):
        grant(e, u)
    res = recover(e)
    assert res.not_applied == 0 and not e.audit.outcomes()
    assert e.active_rows(u, "teacher")


def test_a_replay_of_the_same_request_writes_one_audit_row_and_one_role_row():
    from hs_core.audit.context import AuditContext

    def ctx():
        return AuditContext.for_principal("ops1", ["opsadmin"], request_id="same-request")

    e = Env()
    u = e.user()
    e.tick.n, e.tick.crash_at = 0, 2  # crash after the intent
    with pytest.raises(Boom):
        e.real_service.grant_role(OPS(), u, "teacher", ctx=ctx())
    e.tick.crash_at = None
    e.real_service.grant_role(OPS(), u, "teacher", ctx=ctx())  # same request key again
    assert len(e.audit.role_intents()) == 1, "the replay must reuse the intent row"
    assert len(e.rows(u, "teacher")) == 1 and e.labels(u) == {"teacher"}
    e.real_service.grant_role(OPS(), u, "teacher", ctx=ctx())  # and once more: a no-op
    assert len(e.audit.role_intents()) == 1


def test_crash_before_every_write_of_a_revoke_then_retry_converges():
    e0 = Env()
    u0 = e0.user()
    revoke(e0, u0, prepare=True)
    e0.tick.n = 0
    revoke(e0, u0)
    total = e0.tick.n
    assert total == 3  # audit intent, close the row, remove the label
    for k in range(1, total + 1):
        e = Env()
        u = e.user()
        revoke(e, u, prepare=True)
        e.tick.n, e.tick.crash_at = 0, k
        with pytest.raises(Boom):
            revoke(e, u)
        if k <= 2:
            assert e.active_rows(u, "teacher") and "teacher" in e.labels(u)  # nothing happened
        else:
            assert not e.active_rows(u, "teacher") and "teacher" in e.labels(u)  # over-grant window
        e.tick.crash_at = None
        revoke(e, u)
        assert not e.active_rows(u, "teacher") and "teacher" not in e.labels(u)
        assert len(e.rows(u, "teacher")) == 1  # history kept, closed once


def test_the_over_grant_window_of_a_half_finished_revoke_is_closed_by_a_retry_even_with_no_row():
    e = Env()
    u = e.user()
    revoke(e, u, prepare=True)
    e.tick.n, e.tick.crash_at = 0, 3
    with pytest.raises(Boom):
        revoke(e, u)
    # row closed, label still there: a second revoke call still removes the label
    e.tick.crash_at = None
    res = e.service.revoke_role(OPS(), u, "teacher")
    assert res.changed and "teacher" not in e.labels(u)


def test_audit_rows_of_a_crashed_then_retried_grant_are_one_per_attempt_not_more():
    e = Env()
    u = e.user()
    e.tick.n, e.tick.crash_at = 0, 3
    with pytest.raises(Boom):
        grant(e, u)
    e.tick.crash_at = None
    grant(e, u)  # repairs the label: its own audited change
    ops = [meta_of_op(r) for r in e.audit.role_intents()]
    assert sorted(ops) == ["create", "repair"]


def meta_of_op(row):
    from hs_core.audit.service import meta_of

    return meta_of(row)["op"]


# ---- concurrency ---------------------------------------------------------------------------
def _run(threads):
    errors: list[BaseException] = []

    def wrap(fn):
        def run():
            try:
                fn()
            except RoleError:
                pass
            except BaseException as ex:  # noqa: BLE001
                errors.append(ex)

        return run

    ts = [threading.Thread(target=wrap(fn)) for fn in threads]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors, errors


def test_50_threads_granting_the_same_role_make_one_active_row_and_one_label():
    e = Env()
    u = e.user()
    _run([lambda: e.service.grant_role(OPS(), u, "teacher") for _ in range(50)])
    assert len(e.rows(u, "teacher")) == 1 and len(e.active_rows(u, "teacher")) == 1
    assert e.labels(u) == {"teacher"}


def _flip(e, u, i):
    if i % 2 == 0:
        return lambda: e.service.grant_role(OPS(), u, "teacher")
    return lambda: e.service.revoke_role(OPS(), u, "teacher")


def test_50_threads_mixing_grant_and_revoke_end_consistent_in_every_run():
    for _ in range(5):
        e = Env()
        u = e.user()
        _run([_flip(e, u, i) for i in range(50)])
        assert len(e.active_rows(u, "teacher")) <= 1
        assert consistent(e, u, "teacher")


def test_50_threads_over_different_users_and_roles_do_not_interfere():
    e = Env()
    users = [e.user() for _ in range(10)]
    ops = []
    for i in range(50):
        u = users[i % 10]
        role = ("teacher", "parent", "guardian")[i % 3]
        ops.append(lambda u=u, role=role: e.service.grant_role(OPS(), u, role))
    _run(ops)
    for u in users:
        for r in ("teacher", "parent", "guardian"):
            assert len(e.active_rows(u, r)) <= 1
            assert consistent(e, u, r)


def _go(e, barrier, actor, target):
    def f():
        barrier.wait()
        e.service.revoke_role(actor, target, "superadmin", reason="test reason")

    return f


def test_two_superadmins_revoking_each_other_at_once_leave_at_least_one():
    for _ in range(20):
        e = Env()
        a, actor_a = e.superadmin()
        b, actor_b = e.superadmin()
        barrier = threading.Barrier(2)
        _run([_go(e, barrier, actor_a, b), _go(e, barrier, actor_b, a)])
        remaining = [
            x for x in (a, b) if "superadmin" in e.labels(x) and e.active_rows(x, "superadmin")
        ]
        assert remaining, "the platform lost its last super admin"


def test_two_signup_grants_of_the_same_role_race_to_one_row():
    e = Env()
    u = e.user()
    _run([lambda: e.service.grant_role(SIGNUP, u, "parent") for _ in range(12)])
    assert len(e.rows(u, "parent")) == 1 and e.labels(u) == {"parent"}


def test_actor_type_is_a_plain_value():
    assert Actor.user("a", ["teacher"]).roles == {"teacher"}
    assert LastSuperadmin.code == "last_superadmin"


def test_with_label_writes_slowed_down_mixed_grants_and_revokes_still_end_consistent():
    # The per-user lock is what keeps row and label in step inside one process.
    for _ in range(3):
        e = Env()
        e.users.jitter = 0.002
        u = e.user()
        _run([_flip(e, u, i) for i in range(30)])
        assert len(e.active_rows(u, "teacher")) <= 1
        assert consistent(e, u, "teacher")


def test_a_stale_read_that_races_another_process_claims_the_next_generation_safely():
    from hs_core.roles.service import TABLE

    e = Env()
    u = e.user()
    e.service.grant_role(OPS(), u, "teacher")  # the other process's grant
    real = e.repo.inner.list
    calls = {"n": 0}

    def stale(table, queries=None):
        if table == TABLE:
            calls["n"] += 1
            if calls["n"] == 1:
                return []  # this process read before the other one wrote
        return real(table, queries)

    e.repo.inner.list = stale
    res = e.service.grant_role(OPS(), u, "teacher")
    assert not res.changed  # the other process's grant stands; nothing was done twice
    assert len(e.rows(u, "teacher")) == 1 and e.labels(u) == {"teacher"}


def test_the_race_safety_net_restores_a_superadmin_when_the_guard_read_was_stale():
    e = Env()
    uid, boss = e.superadmin()
    other = e.user()
    e.service._is_last_superadmin = lambda *a, **k: False  # both revokers saw "someone else"
    with pytest.raises(LastSuperadmin):
        e.service.revoke_role(
            Actor.user(other, ["superadmin"]), uid, "superadmin", reason="test reason"
        )
    assert "superadmin" in e.labels(uid) and e.active_rows(uid, "superadmin")


def test_last_superadmin_refusal_happens_before_anything_is_written_or_audited():
    e = Env()
    uid, _ = e.superadmin()
    other = e.user()
    before = len(e.raw_repo.list("role_assignment"))
    with pytest.raises(LastSuperadmin):
        e.service.revoke_role(
            Actor.user(other, ["superadmin"]), uid, "superadmin", reason="test reason"
        )
    assert len(e.raw_repo.list("role_assignment")) == before
    assert [r["action"] for r in e.audit.rows()] == ["role.revoke_refused"]


def test_a_revoke_waits_for_a_grant_in_flight_on_the_same_user():
    import threading
    import time

    e = Env()
    u = e.user()
    entered, release = threading.Event(), threading.Event()
    real = e.raw_users.add_label

    def slow_add(uid, label):
        entered.set()
        release.wait(5)
        return real(uid, label)

    e.raw_users.add_label = slow_add
    a = threading.Thread(target=lambda: e.service.grant_role(OPS(), u, "teacher"))
    a.start()
    assert entered.wait(5)  # the grant has written its row and is writing the label
    b = threading.Thread(target=lambda: e.service.revoke_role(OPS(), u, "teacher"))
    b.start()
    time.sleep(0.1)
    assert e.active_rows(u, "teacher"), "the revoke must not run inside the grant"
    release.set()
    a.join(5)
    b.join(5)
    assert not e.active_rows(u, "teacher") and "teacher" not in e.labels(u)


# ---- staff revoke: access goes FIRST (round 2) ----------------------------------------------
def staff_revoke(e, u, prepare=False):
    if prepare:
        e.service.grant_role(e.boss, u, "opsadmin", reason="test reason")
        return
    e.service.revoke_role(e.boss, u, "opsadmin", reason="left the team")


def test_crash_before_every_write_of_a_staff_revoke_never_leaves_label_and_closed_row():
    e0 = Env()
    e0.boss = e0.superadmin()[1]
    u0 = e0.user()
    staff_revoke(e0, u0, prepare=True)
    e0.tick.n = 0
    staff_revoke(e0, u0)
    total = e0.tick.n
    assert total == 4  # audit intent, label removed, sessions ended, history row closed
    for k in range(1, total + 1):
        e = Env()
        e.boss = e.superadmin()[1]
        u = e.user()
        staff_revoke(e, u, prepare=True)
        e.tick.n, e.tick.crash_at = 0, k
        with pytest.raises(Boom):
            staff_revoke(e, u)
        label = "opsadmin" in e.labels(u)
        row_open = bool(e.active_rows(u, "opsadmin"))
        assert not (label and not row_open), f"crash {k}: label kept after the row was closed"
        if k >= 3:
            assert not label, "access must already be gone"
        e.tick.crash_at = None
        staff_revoke(e, u)  # the caller repeats
        assert not e.active_rows(u, "opsadmin") and "opsadmin" not in e.labels(u)
        assert len(e.rows(u, "opsadmin")) == 1


def test_reconcile_closes_the_staff_row_a_crashed_revoke_left_behind():
    from hs_core.roles.reconcile import reconcile_all

    e = Env()
    e.boss = e.superadmin()[1]
    u = e.user()
    staff_revoke(e, u, prepare=True)
    e.tick.n, e.tick.crash_at = 0, 4  # label gone, sessions ended, row still open
    with pytest.raises(Boom):
        staff_revoke(e, u)
    e.tick.crash_at = None
    e.clock.advance(3600)
    reconcile_all(e.service)
    assert not e.active_rows(u, "opsadmin") and "opsadmin" not in e.labels(u)


def test_a_failing_label_call_on_a_staff_revoke_leaves_the_row_open_and_access_unchanged():
    from hs_core.roles.directory import DirectoryUnavailable
    from hs_core.roles.service import IncompleteChange

    e = Env()
    e.boss = e.superadmin()[1]
    u = e.user()
    staff_revoke(e, u, prepare=True)

    def boom(*a, **k):
        raise DirectoryUnavailable

    e.raw_users.remove_label = boom
    with pytest.raises(IncompleteChange):
        staff_revoke(e, u)
    assert e.active_rows(u, "opsadmin") and "opsadmin" in e.labels(u)  # nothing half-done
    del e.raw_users.remove_label
    staff_revoke(e, u)
    assert not e.active_rows(u, "opsadmin") and "opsadmin" not in e.labels(u)


def test_staff_revoke_ends_the_users_sessions_but_a_mobile_revoke_does_not():
    e = Env()
    e.boss = e.superadmin()[1]
    s, t = e.user(), e.user()
    staff_revoke(e, s, prepare=True)
    e.service.grant_role(OPS(), t, "teacher")
    e.service.revoke_role(OPS(), t, "teacher")
    assert e.raw_users.sessions_ended == []
    staff_revoke(e, s)
    assert e.raw_users.sessions_ended == [s]


def test_a_failing_session_end_does_not_undo_the_revoke_the_label_is_already_gone():
    from hs_core.roles.directory import DirectoryUnavailable

    e = Env()
    e.boss = e.superadmin()[1]
    u = e.user()
    staff_revoke(e, u, prepare=True)

    def boom(*a, **k):
        raise DirectoryUnavailable

    e.raw_users.end_sessions = boom
    staff_revoke(e, u)
    assert not e.active_rows(u, "opsadmin") and "opsadmin" not in e.labels(u)


def test_a_nonstaff_revoke_still_closes_the_row_first():
    e = Env()
    u = e.user()
    e.service.grant_role(OPS(), u, "teacher")
    e.tick.n, e.tick.crash_at = 0, 3
    with pytest.raises(Boom):
        e.service.revoke_role(OPS(), u, "teacher")
    assert not e.active_rows(u, "teacher") and "teacher" in e.labels(u)  # row-first: documented


@pytest.mark.parametrize("k", [2, 3, 4])
def test_m06_recovery_after_a_crashed_staff_revoke_matches_what_really_happened(k):
    from hs_core.audit.service import meta_of

    e = Env()
    e.boss = e.superadmin()[1]
    u = e.user()
    staff_revoke(e, u, prepare=True)
    e.tick.n, e.tick.crash_at = 0, k  # 2 = label removal, 3 = session end, 4 = history row
    with pytest.raises(Boom):
        staff_revoke(e, u)
    res = recover(e)
    # the history row never changed: the revoke is settled as not applied (and the label is
    # already gone for k >= 3, which role-reconcile / a repeat of the call completes)
    assert res.not_applied == 1
    assert [meta_of(r)["status"] for r in e.audit.outcomes()] == ["not_applied"]
    assert e.active_rows(u, "opsadmin")
    staff_revoke(e, u)
    assert not e.active_rows(u, "opsadmin") and "opsadmin" not in e.labels(u)
    # one intent per attempt (the crashed one and the repeat), nothing more
    assert len([r for r in e.audit.role_intents() if r["action"] == "role.revoke_staff"]) == 2


def test_a_staff_revoke_is_one_audit_row_for_label_sessions_and_row_together():
    e = Env()
    e.boss = e.superadmin()[1]
    u = e.user()
    staff_revoke(e, u, prepare=True)
    before = len(e.audit.rows())
    staff_revoke(e, u)
    new = e.audit.rows()[before:]
    assert [r["action"] for r in new] == ["role.revoke_staff"]
    assert new[0]["reason"] == "left the team"
