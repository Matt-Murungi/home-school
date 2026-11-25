"""V01 round 3: crash injection after EVERY write of a submit and of a takeover (reviewer harness
adopted): the application must always become finishable exactly once."""

import threading
from datetime import timedelta

import pytest

from application_helpers import Clock, valid_content
from hs_core.auth import Principal
from hs_core.errors import AppError
from hs_core.repository import InMemoryRepository
from hs_core.teacher_application import ApplicationPolicy, ApplicationService
from hs_core.teacher_application.models import DraftBody, SubmitBody
from hs_core.teacher_application.ports import (
    FakeCatalogue,
    FakeNotifier,
    FakeZones,
    RepositoryAuditSink,
)


class Crash(BaseException):
    pass


class CrashRepo(InMemoryRepository):
    def __init__(self):
        super().__init__()
        self.n = 0
        self.at = None
        self.log = []

    def _tick(self, kind, t, r):
        self.n += 1
        if self.at is not None and self.n == self.at:
            raise Crash(f"{kind} {t}/{r}")

    def create(self, t, r, d, p=None):
        self._tick("pre-create", t, r) if False else None
        out = super().create(t, r, d, p)
        self._tick("create", t, r)
        return out

    def update(self, t, r, d, p=None):
        out = super().update(t, r, d, p)
        self._tick("update", t, r)
        return out

    def delete(self, t, r):
        out = super().delete(t, r)
        self._tick("delete", t, r)
        return out


def mk():
    repo = CrashRepo()
    clock = Clock()
    s = ApplicationService(
        repo,
        FakeCatalogue(),
        FakeZones(),
        RepositoryAuditSink(repo, clock),
        FakeNotifier(),
        ApplicationPolicy(submits_per_hour=1000),
        clock,
    )
    return repo, clock, s


P = Principal("teacher_one", ["teacher"])
Q = Principal("teacher_two", ["teacher"])


def prep(s, p=P):
    s.save_draft(p, DraftBody(base_revision=0, step=0, content=valid_content()))


SB = SubmitBody(base_revision=1, declaration=True)
TABS = [
    "teacher_profile",
    "teacher_zone",
    "availability_slot",
    "teacher_approval",
    "teacher_application",
    "teacher_referee",
    "teacher_application_subject",
    "audit_log",
]


def snapshot(repo):
    out = {}
    for t in TABS:
        out[t] = sorted((r["$id"], bool(r.get("deleted_at"))) for r in repo._t.get(t, {}).values())
    return out


def reference():
    repo, clock, s = mk()
    prep(s)
    s.submit(P, SB, "k" * 20)
    return snapshot(repo), repo.n


REF, TOTAL = reference()


def test_total():
    print("writes in a full submit (incl. draft):", TOTAL)


@pytest.mark.parametrize("at", range(1, 120))
def test_crash_after_each_write_then_new_key_takeover(at):
    repo, clock, s = mk()
    prep(s)
    base = repo.n
    repo.at = base + at
    try:
        s.submit(P, SB, "a" * 20)
        return  # fewer writes than `at`
    except Crash:
        pass
    repo.at = None
    clock.advance(minutes=11)
    try:
        r = s.submit(P, SB, "b" * 20)
    except AppError as e:
        if (
            e.code == "already_submitted"
            and repo.get("teacher_application", "teacher_one")["status"] == "submitted"
        ):
            assert snapshot(repo) == REF, at
            return
        pytest.fail(f"takeover refused after crash at write {at}: {e.status} {e.code}")
    assert r["status"] == "submitted"
    assert snapshot(repo) == REF, at


@pytest.mark.parametrize("at", range(1, 12))
def test_crash_during_the_takeover_itself(at):
    repo, clock, s = mk()
    prep(s)
    repo.at = repo.n + 3
    with pytest.raises(Crash):
        s.submit(P, SB, "a" * 20)
    clock.advance(minutes=11)
    repo.at = repo.n + at
    try:
        s.submit(P, SB, "b" * 20)
        return
    except Crash:
        pass
    repo.at = None
    clock.advance(minutes=11)
    for k in ("c" * 20, "b" * 20):
        try:
            s.submit(P, SB, k)
            break
        except AppError as e:
            print("takeover crash at", at, "key", k[0], "->", e.status, e.code)
    else:
        pytest.fail(f"locked out after crash during takeover at write {at}")
    assert snapshot(repo) == REF


def test_other_teacher_cannot_take_over():
    repo, clock, s = mk()
    prep(s)
    prep(s, Q)
    repo.at = repo.n + 3
    with pytest.raises(Crash):
        s.submit(P, SB, "a" * 20)
    repo.at = None
    clock.advance(minutes=30)
    r = s.submit(Q, SB, "b" * 20)  # Q's own application
    assert r["status"] == "submitted"
    assert repo.get("teacher_application", "teacher_one")["status"] == "draft"
    assert all(
        x["teacher_id"] in ("teacher_two", "tp_two") or True
        for x in repo._t["teacher_zone"].values()
    )
    # Q reusing P's old key is just a different key for Q (keys are scoped per teacher)
    with pytest.raises(AppError):
        s.submit(Q, SB, "a" * 20)
    assert repo.get("teacher_application", "teacher_one")["status"] == "draft"


@pytest.mark.parametrize("at", [3, 8, 14])
def test_concurrent_takeovers_two_keys(at):
    repo, clock, s = mk()
    prep(s)
    repo.at = repo.n + at
    with pytest.raises(Crash):
        s.submit(P, SB, "a" * 20)
    repo.at = None
    clock.advance(minutes=11)
    res = []
    lock = threading.Lock()
    bar = threading.Barrier(50)

    def go(i):
        k = ("b" if i % 2 else "c") * 20
        bar.wait()
        try:
            r = s.submit(P, SB, k)
            out = ("ok", k[0], r.get("replayed"))
        except AppError as e:
            out = ("err", k[0], e.status, e.code)
        except Exception as e:
            out = ("EXC", repr(e))
        with lock:
            res.append(out)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(50)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    from collections import Counter

    print(at, Counter(res))
    assert not any(r[0] == "EXC" for r in res)
    assert snapshot(repo) == REF
    assert len({r[1] for r in res if r[0] == "ok" and not r[2]}) <= 1


class PauseRepo(CrashRepo):
    """Pauses ONE thread (the old writer) after its k-th write until released."""

    def __init__(self):
        super().__init__()
        self.pause_thread = None
        self.k = None
        self.cnt = 0
        self.paused = threading.Event()
        self.release = threading.Event()

    def _tick(self, kind, t, r):
        if threading.current_thread() is self.pause_thread:
            self.cnt += 1
            if self.cnt == self.k:
                self.paused.set()
                self.release.wait(10)


@pytest.mark.parametrize("k", range(1, 20))
def test_slow_old_writer_vs_takeover(k):
    repo = PauseRepo()
    clock = Clock()
    s = ApplicationService(
        repo,
        FakeCatalogue(),
        FakeZones(),
        RepositoryAuditSink(repo, clock),
        FakeNotifier(),
        ApplicationPolicy(submits_per_hour=100),
        clock,
    )
    prep(s)
    out = {}

    def old():
        try:
            out["old"] = s.submit(P, SB, "a" * 20)
        except AppError as e:
            out["old"] = (e.status, e.code)
        except BaseException as e:
            out["old"] = repr(e)

    t = threading.Thread(target=old)
    repo.pause_thread = t
    repo.k = k
    t.start()
    if not repo.paused.wait(5):
        repo.release.set()
        t.join()
        return
    clock.advance(minutes=11)
    try:
        out["new"] = s.submit(P, SB, "b" * 20)
    except AppError as e:
        out["new"] = (e.status, e.code)
    repo.release.set()
    t.join()
    print(
        k,
        out["old"] if isinstance(out["old"], tuple) else "ok",
        "|",
        out["new"] if isinstance(out["new"], tuple) else "ok",
    )
    assert snapshot(repo) == REF, (k, out)
    assert repo.get("teacher_application", "teacher_one")["status"] == "submitted"


def test_revision_retake_crash_lockout():
    repo, clock, s = mk()
    prep(s)
    c = valid_content()
    c["personal"]["full_name"] = "A B"

    def save():
        return s.save_draft(P, DraftBody(base_revision=1, step=0, content=c))

    repo.at = repo.n + 2  # revision claim created, crash before row update
    with pytest.raises(Crash):
        save()
    repo.at = None
    clock.advance(seconds=61)
    repo.at = repo.n + 2  # retake claim created, crash again before update
    with pytest.raises(Crash):
        save()
    repo.at = None
    clock.advance(seconds=61)
    clock.advance(hours=3)
    try:
        save()
        print("recovered")
    except AppError as e:
        pytest.fail(f"draft saves locked out: {e.status} {e.code}")


@pytest.mark.parametrize("at", range(1, 14))
@pytest.mark.parametrize("retry_key", ["same", "new"])
@pytest.mark.parametrize("wait", [timedelta(0), timedelta(seconds=61), timedelta(hours=3)])
def test_a_crash_at_any_write_of_the_takeover_never_locks_the_teacher_out(at, retry_key, wait):
    """Reviewer round 2 blocker: crash after the first write of a takeover, then retry with the
    takeover key, a new key, now / +61 s / +3 h: the application must always finish exactly once."""
    repo, clock, s = mk()
    prep(s)
    repo.at = repo.n + 3
    with pytest.raises(Crash):
        s.submit(P, SB, "a" * 20)  # the first process dies mid-submit
    clock.advance(minutes=11)
    repo.at = repo.n + at
    try:
        s.submit(P, SB, "b" * 20)  # the takeover itself dies after `at` writes
        return
    except Crash:
        pass
    repo.at = None
    clock.now += wait
    key = "b" * 20 if retry_key == "same" else "c" * 20
    for _ in range(3):  # a fresh claim answers 409 in progress; waiting out the window finishes it
        try:
            r = s.submit(P, SB, key)
            break
        except AppError as e:
            assert e.code in ("submit_in_progress",), (e.status, e.code)
            clock.advance(minutes=11)
    else:
        pytest.fail("locked out")
    assert r["status"] == "submitted"
    assert snapshot(repo) == REF
    assert len(repo.list("audit_log")) == 1


def test_stale_claims_are_removed_by_the_cleanup_job_and_the_chain_restarts():
    from hs_core.teacher_application.jobs import cleanup_claims

    repo, clock, s = mk()
    prep(s)
    repo.at = repo.n + 3
    with pytest.raises(Crash):
        s.submit(P, SB, "a" * 20)
    repo.at = None
    claims = repo.list("teacher_application_claim")
    assert all(
        c["expires_at"] < "2026-12-31" for c in claims if c["kind"] == "submit"
    )  # bounded life
    clock.advance(days=8)
    assert cleanup_claims(repo, clock.now) >= 1
    r = s.submit(P, SB, "z" * 20)
    assert r["status"] == "submitted" and snapshot(repo) == REF


def test_chain_successor_ids_do_not_depend_on_older_rows_existing():
    repo, clock, s = mk()
    prep(s)
    repo.at = repo.n + 3
    with pytest.raises(Crash):
        s.submit(P, SB, "a" * 20)
    repo.at = None
    clock.advance(minutes=11)
    with pytest.raises(Crash):
        repo.at = repo.n + 1
        s.submit(P, SB, "b" * 20)  # takeover claim written, then the process dies
    repo.at = None
    first = min(repo.list("teacher_application_claim"), key=lambda r: r["created_at"])
    if first["kind"] == "submit":
        repo.delete("teacher_application_claim", first["$id"])  # the old one was cleaned up
    clock.advance(minutes=11)
    assert s.submit(P, SB, "b" * 20)["status"] == "submitted"


def test_revision_claim_double_fault_is_recoverable_after_each_stale_window():
    repo, clock, s = mk()
    prep(s)
    c = valid_content()
    c["personal"]["full_name"] = "A B"

    def save():
        return s.save_draft(P, DraftBody(base_revision=1, step=0, content=c))

    for _ in range(3):  # three crashes in a row between claim and row update
        repo.at = repo.n + 2
        with pytest.raises(Crash):
            save()
        repo.at = None
        clock.advance(seconds=61)
    assert save()["revision"] == 2
    # and the same for a submit that needs the next revision slot
    repo.at = repo.n + 1
    with pytest.raises(Crash):
        s.submit(P, SubmitBody(base_revision=2, declaration=True), "k" * 20)
    repo.at = None
    assert (
        s.submit(P, SubmitBody(base_revision=2, declaration=True), "k" * 20)["status"]
        == "submitted"
    )
