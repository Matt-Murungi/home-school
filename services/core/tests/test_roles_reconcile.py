"""A03: `role-reconcile` finds and repairs drift between labels and role_assignment."""

from __future__ import annotations

from datetime import timedelta

from hs_core.audit.service import meta_of
from hs_core.jobs.alerts import FakeAlertSink
from hs_core.jobs.model import Continue, Done, JobContext
from hs_core.jobs.schedule import JOBS
from hs_core.roles import reconcile
from hs_core.roles.reconcile import ReconcilePolicy, reconcile_all
from roles_helpers import OPS, Env


def old(e):
    e.clock.advance(3600)  # past the in-flight grace


def row(e, uid, role, **kw):
    data = {
        "user_id": uid,
        "role": role,
        "granted_at": e.clock().isoformat(),
        "active_key": f"{uid}:{role}",
    }
    data.update(kw)
    e.raw_repo.create(
        "role_assignment", f"r{uid}{role}{len(e.raw_repo.list('role_assignment'))}"[:36], data
    )


def test_clean_state_reports_nothing():
    e = Env()
    u = e.user()
    e.service.grant_role(OPS(), u, "teacher")
    old(e)
    rep = reconcile_all(e.real_service)
    assert rep.found == [] and rep.repaired == 0


def test_active_nonstaff_row_without_label_gets_its_label():
    e = Env()
    u = e.user()
    row(e, u, "teacher")
    old(e)
    rep = reconcile_all(e.real_service)
    assert ("row_without_label", u, "teacher") in rep.found and rep.repaired == 1
    assert e.labels(u) == {"teacher"} and len(e.rows(u, "teacher")) == 1


def test_active_staff_row_without_label_is_closed_not_completed():
    e = Env()
    u = e.user()
    row(e, u, "opsadmin")
    old(e)
    # a last-superadmin style guard does not apply to opsadmin
    reconcile_all(e.real_service)
    assert e.labels(u) == set() and e.active_rows(u) == []
    assert e.rows(u, "opsadmin")[0]["revoked_at"]


def test_a_grant_in_flight_is_left_alone_within_the_grace():
    e = Env()
    u = e.user()
    row(e, u, "teacher")  # granted just now, label not yet written
    rep = reconcile_all(e.real_service)
    assert rep.found == [] and e.labels(u) == set()


def test_closed_row_with_label_still_set_loses_the_label_staff_or_not():
    e = Env()
    t, c = e.user(["teacher"]), e.user(["curator"])
    row(e, t, "teacher", active_key=None, revoked_at="2026-01-02T00:00:00+00:00")
    row(e, c, "curator", active_key=None, revoked_at="2026-01-02T00:00:00+00:00")
    old(e)
    rep = reconcile_all(e.real_service)
    assert e.labels(t) == set() and e.labels(c) == set()
    assert {k for k, _, _ in rep.found} == {"label_after_revoke"}


def test_nonstaff_label_without_any_row_gets_a_history_row():
    e = Env()
    u = e.user(["parent"])
    old(e)
    rep = reconcile_all(e.real_service)
    assert len(e.active_rows(u, "parent")) == 1 and rep.repaired == 1 and e.labels(u) == {"parent"}
    assert reconcile_all(e.real_service).found == []  # idempotent


def test_staff_label_without_a_row_is_reported_and_kept_by_default():
    e = Env()
    u = e.user(["superadmin"])
    old(e)
    rep = reconcile_all(e.real_service)
    assert rep.reported_only == 1 and ("label_without_row", u, "superadmin") in rep.found
    assert e.labels(u) == {"superadmin"} and e.active_rows(u) == []


def test_staff_label_without_a_row_is_removed_when_the_policy_says_so():
    e = Env()
    u = e.user(["curator"])
    old(e)
    reconcile_all(e.real_service, ReconcilePolicy(remove_unrecorded_staff=True))
    assert e.labels(u) == set()


def test_row_of_a_deleted_account_is_closed():
    e = Env()
    u = e.user()
    row(e, u, "teacher")
    del e.raw_users.users[u]
    old(e)
    reconcile_all(e.real_service)
    assert e.active_rows(u) == []


def test_variant_labels_in_the_directory_are_never_treated_as_roles():
    e = Env()
    e.user(["OpsAdmin", "ops_admin", "x"])
    old(e)
    assert reconcile_all(e.real_service).found == []


def test_crash_during_repair_then_rerun_converges():
    import pytest

    from roles_helpers import Boom

    e = Env()
    u = e.user(["parent"])
    old(e)
    e.tick.n, e.tick.crash_at = 0, 1
    with pytest.raises(Boom):
        reconcile_all(e.real_service)
    e.tick.crash_at = None
    reconcile_all(e.real_service)
    assert len(e.active_rows(u, "parent")) == 1 and e.labels(u) == {"parent"}


def test_repairs_are_audited_as_the_reconcile_system_actor():
    e = Env()
    u = e.user()
    row(e, u, "teacher")
    old(e)
    reconcile_all(e.real_service)
    rows = e.audit.role_intents()
    assert rows and {(r["actor_id"], r["actor_roles"]) for r in rows} == {(None, "system")}
    assert {meta_of(r)["job"] for r in rows} == {"role-reconcile"}


def test_chunking_visits_every_user_across_pages():
    e = Env()
    users = [e.user(["parent"]) for _ in range(95)]
    old(e)
    total, cur, chunks = reconcile.DriftReport(), (0, None), 0
    while cur is not None:
        cur, rep = reconcile.reconcile_chunk(e.real_service, cur[0], cur[1], limit=10)
        total.merge(rep)
        chunks += 1
    assert total.repaired == 95 and chunks > 10
    assert all(len(e.active_rows(u, "parent")) == 1 for u in users)


# ---- the F04 job -------------------------------------------------------------------------
def ctx(cursor=None):
    return JobContext("role-reconcile", "r", "role-reconcile:p", None, {}, cursor, 0)


def test_job_is_registered_in_the_schedule_and_unbound_job_fails_loudly():
    assert "role-reconcile" in {j.name for j in JOBS}
    import pytest

    reconcile.bind(None)
    with pytest.raises(RuntimeError):
        reconcile.role_reconcile(ctx())


def test_job_runs_to_done_in_chunks_and_alerts_on_report_only_drift():
    e = Env()
    e.user(["superadmin"])
    e.user(["parent"])
    old(e)
    alerts = FakeAlertSink()
    reconcile.bind(lambda: (e.real_service, alerts, ReconcilePolicy()))
    try:
        cur, steps = None, 0
        while True:
            res = reconcile.role_reconcile(ctx(cur))
            steps += 1
            if isinstance(res, Done):
                break
            assert isinstance(res, Continue)
            cur = res.cursor
            assert steps < 50
    finally:
        reconcile.bind(None)
    assert steps == 14  # one chunk per phase at this size
    assert [a.kind for a in alerts.sent] == ["role_drift"]
    assert "reported only" in alerts.sent[0].error
    assert timedelta  # keep import used


# ---- per-row isolation (round 2): a malformed row must not stop the job --------------------
import pytest  # noqa: E402

MALFORMED = {
    "missing_user_id": {"drop": "user_id"},
    "missing_granted_at": {"drop": "granted_at"},
    "garbage_timestamp": {"granted_at": "garbage"},
    "none_timestamp": {"granted_at": None},
    "empty_timestamp": {"granted_at": ""},
    "int_timestamp": {"granted_at": 12345},
    "user_id_int": {"user_id": 5},
    "user_id_none": {"user_id": None},
    "user_id_empty": {"user_id": ""},
    "user_id_oversized": {"user_id": "u" * 10_000},
    "timestamp_oversized": {"granted_at": "x" * 100_000},
    "user_id_object": {"user_id": {"a": 1}},
}


def bad_row(e, shape, role="teacher", i=0):
    data = {
        "user_id": f"baduser{i}",
        "role": role,
        "granted_at": "2026-01-01T00:00:00+00:00",
        "active_key": f"k{i}:{role}",
    }
    spec = dict(MALFORMED[shape])
    drop = spec.pop("drop", None)
    data.update(spec)
    if drop:
        data.pop(drop)
    e.raw_repo.create("role_assignment", f"bad{shape}{i}"[:36], data)


@pytest.mark.parametrize("shape", sorted(MALFORMED))
def test_a_malformed_row_does_not_stop_the_rest_of_the_page(shape):
    e = Env()
    good = [e.user() for _ in range(3)]
    for u in good:
        row(e, u, "teacher")
    bad_row(e, shape)  # sorts among the good ones by id
    old(e)
    rep = reconcile_all(e.real_service)
    assert all(e.labels(u) == {"teacher"} for u in good), "valid rows were not repaired"
    assert rep.malformed >= 1 and rep.failed >= 1
    assert reconcile_all(e.real_service).repaired == 0  # and the next run is stable


@pytest.mark.parametrize("shape", sorted(MALFORMED))
def test_malformed_rows_do_not_block_the_cursor_in_the_label_phase_either(shape):
    e = Env()
    u = e.user(["teacher"])
    bad_row(e, shape, role="teacher", i=1)
    e.raw_repo.update("role_assignment", f"bad{shape}1"[:36], {"user_id": u})
    old(e)
    reconcile_all(e.real_service)  # must not raise
    v = e.user(["parent"])
    old(e)
    reconcile_all(e.real_service)
    assert len(e.active_rows(v, "parent")) == 1


def test_an_unknown_role_row_is_ignored_and_an_unknown_user_row_is_closed():
    e = Env()
    e.raw_repo.create(
        "role_assignment",
        "weirdrole",
        {
            "user_id": "x",
            "role": "bogus",
            "granted_at": "2026-01-01T00:00:00+00:00",
            "active_key": "x:bogus",
        },
    )
    ghost = "ghostuser"
    e.raw_repo.create(
        "role_assignment",
        "ghostrow",
        {
            "user_id": ghost,
            "role": "teacher",
            "granted_at": "2026-01-01T00:00:00+00:00",
            "active_key": f"{ghost}:teacher",
        },
    )
    old(e)
    rep = reconcile_all(e.real_service)
    assert rep.malformed == 0
    assert e.raw_repo.get("role_assignment", "ghostrow")["revoked_at"]
    assert e.raw_repo.get("role_assignment", "weirdrole")["active_key"]  # untouched


def test_malformed_rows_log_the_row_id_only_and_raise_an_alert(caplog):
    e = Env()
    e.raw_repo.create(
        "role_assignment",
        "row-with-bad-time",
        {
            "user_id": "baduser0",
            "role": "teacher",
            "granted_at": "SECRET-VALUE",
            "active_key": "k:teacher",
        },
    )
    old(e)
    alerts = FakeAlertSink()
    reconcile.bind(lambda: (e.real_service, alerts, ReconcilePolicy()))
    try:
        cur = None
        for _ in range(30):
            res = reconcile.role_reconcile(ctx(cur))
            if isinstance(res, Done):
                break
            cur = res.cursor
    finally:
        reconcile.bind(None)
    assert "role_malformed_row" in [a.kind for a in alerts.sent]
    text = " ".join(r.getMessage() + str(r.__dict__) for r in caplog.records)
    assert "SECRET-VALUE" not in text and "baduser0" not in text
    assert "row-with-bad-time" in text  # the row id is logged


def test_outages_still_abort_the_chunk_so_the_job_retries():
    from hs_core.roles.directory import DirectoryUnavailable

    e = Env()
    u = e.user()
    row(e, u, "teacher")
    old(e)
    e.raw_users.available = False
    with pytest.raises(DirectoryUnavailable):
        reconcile.reconcile_chunk(e.real_service, 2, None)  # phase 2 = teacher rows


def test_10000_rows_with_500_malformed_complete_and_repair_everything_else():
    e = Env()
    # 9,480 healthy rows whose label is present; 20 that need a repair; 500 malformed.
    shapes = sorted(MALFORMED)
    needs = []
    from hs_core.roles.directory import AuthUser

    for i in range(9_480):
        uid = f"ok{i:05d}"
        e.raw_users.users[uid] = AuthUser(uid, "p", ["teacher"], True, None)
        e.raw_repo.create(
            "role_assignment",
            f"okrow{i:05d}",
            {
                "user_id": uid,
                "role": "teacher",
                "granted_at": "2026-01-01T00:00:00+00:00",
                "active_key": f"{uid}:teacher",
            },
        )
    for i in range(20):
        uid = f"fix{i:03d}"
        e.raw_users.users[uid] = AuthUser(uid, "p", [], True, None)
        e.raw_repo.create(
            "role_assignment",
            f"fixrow{i:03d}",
            {
                "user_id": uid,
                "role": "teacher",
                "granted_at": "2026-01-01T00:00:00+00:00",
                "active_key": f"{uid}:teacher",
            },
        )
        needs.append(uid)
    for i in range(500):
        bad_row(e, shapes[i % len(shapes)], i=i + 100)
    old(e)
    # The in-memory repository copies the whole table per query; a real Appwrite query is
    # indexed. Index the per-user lookup so the test measures the job, not the fake.
    index: dict = {}
    for r in e.raw_repo.list("role_assignment"):
        if isinstance(r.get("user_id"), str) and isinstance(r.get("role"), str):
            index.setdefault((r["user_id"], r["role"]), []).append(r)
    e.real_service._rows = lambda uid, role: list(index.get((uid, role), []))
    total, cur, chunks = reconcile.DriftReport(), (0, None), 0
    while cur is not None:
        cur, rep = reconcile.reconcile_chunk(e.real_service, cur[0], cur[1])
        total.merge(rep)
        chunks += 1
        assert chunks < 2000
    assert total.malformed == 500
    assert total.repaired == 20
    assert all("teacher" in e.labels(u) for u in needs)


def test_the_cursor_advances_past_malformed_rows_with_small_pages():
    e = Env()
    users = [e.user() for _ in range(6)]
    for i, u in enumerate(users):
        row(e, u, "teacher")
        bad_row(e, "garbage_timestamp", i=i)
    old(e)
    total, cur, chunks = reconcile.DriftReport(), (0, None), 0
    while cur is not None:
        cur, rep = reconcile.reconcile_chunk(e.real_service, cur[0], cur[1], limit=3)
        total.merge(rep)
        chunks += 1
        assert chunks < 60, "the cursor is stuck on a malformed row"
    assert total.malformed == 6
    assert all(e.labels(u) == {"teacher"} for u in users)


def test_a_history_row_with_a_wrong_typed_timestamp_does_not_break_changes_for_that_user():
    e = Env()
    u = e.user()
    e.raw_repo.create(
        "role_assignment",
        "oddrow",
        {
            "user_id": u,
            "role": "teacher",
            "granted_at": 12345,
            "active_key": None,
            "revoked_at": "2026-01-01T00:00:00+00:00",
        },
    )
    e.service.grant_role(OPS(), u, "teacher")
    e.service.revoke_role(OPS(), u, "teacher")
    assert e.labels(u) == set()
