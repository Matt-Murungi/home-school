"""Property test: hundreds of random event sequences against an independent model oracle never
leave a teacher with access outside the windows (and never lock one out inside them).

The oracle below is a separate, deliberately simple restatement of the ADR 0012 / SR 6.2 rules
for the teacher side (child record, logs, home address): current or upcoming assignment,
substitute cover until 24 h after the covered session, address until 24 h after the last held
session unless the assignment was ended by a cancellation or hold. Business writes go through
`PermissionService.run`; windows that lapse with time are handled by the address-revoke job."""

import random
from datetime import timedelta

import pytest

from hs_core.jobs import schedule
from hs_core.jobs.alerts import FakeAlertSink
from hs_core.jobs.runner import Runner
from hs_core.permissions import jobs as pjobs
from hs_core.permissions.service import (
    RetryQueue,
    assignment_changed,
    role_changed,
    substitute_cover,
)
from jobs_helpers import T0
from perm_world import FlakyRepository, H, World, iso

TEACHERS = {"Ta": "ua", "Tb": "ub", "Tc": "uc"}
CHILDREN = {"c1": ("P1", "h1"), "c2": ("P2", "h2")}
IMMEDIATE = {"parent_cancelled", "admin", "teacher_withdrew", "rematch_requested"}
GRACE_REASONS = [
    "trial_not_continued",
    "subscription_ended",
    "parent_cancelled",
    "admin",
    "rematch_requested",
]
HELD = {"confirmed", "checked_out"}


class Oracle:
    def __init__(self):
        self.assign: list[dict] = []
        self.sess: list[dict] = []
        self.profile_ok = {t: True for t in TEACHERS}  # teacher_profile.status == active
        self.label_ok = {t: True for t in TEACHERS}  # holds the `teacher` label

    @property
    def active(self):
        return {t: self.profile_ok[t] and self.label_ok[t] for t in TEACHERS}

    def child(self, t, c, now):
        if not self.active[t]:
            return False
        if any(
            a["t"] == t and a["c"] == c and (a["end"] is None or a["end"] > now)
            for a in self.assign
        ):
            return True
        return any(
            s["sub"]
            and s["t"] == t
            and s["c"] == c
            and not s["cancelled"]
            and now < s["end"] + 24 * H
            for s in self.sess
        )

    def address(self, t, c, now):
        if not self.active[t]:
            return False
        if self.child(t, c, now):
            return True
        for a in self.assign:
            if (
                a["t"] != t
                or a["c"] != c
                or a["end"] is None
                or a["end"] > now
                or a["reason"] in IMMEDIATE
            ):
                continue
            held = [s["end"] for s in self.sess if s["aid"] == a["id"] and s["status"] in HELD]
            if held and now < max(held) + 24 * H:
                return True
        return False


class Sim:
    def __init__(self, seed: int, flaky: bool = False):
        self.rnd = random.Random(seed)
        self.w = World(repo_cls=FlakyRepository)
        w = self.w
        for t, u in TEACHERS.items():
            w.user(u, ["teacher"])
            w.add("teacher_profile", t, user_id=u, status="active")
        for c, (p, h) in CHILDREN.items():
            w.user(f"u{p}", ["parent"])
            w.add("parent_profile", p, user_id=f"u{p}")
            w.add("home_location", h, parent_id=p)
            w.add("child", c, parent_id=p, name=f"Kid {c}")
            w.add("session_log", f"log_{c}", child_id=c, teacher_id="Ta")
        w.rebuild()
        self.alerts = FakeAlertSink()
        w.svc.alerts = self.alerts
        pjobs.bind(lambda: w.svc)
        specs = [j for j in schedule.JOBS if j.name in ("address-revoke", "permission-reconcile")]
        import dataclasses

        specs = [
            dataclasses.replace(j, schedule=None) if j.name == "permission-reconcile" else j
            for j in specs
        ]
        self.runner = Runner(w.repo, specs, alert=self.alerts, clock=w.clock)
        w.svc.queue = RetryQueue(self.runner, w.clock)
        self.o = Oracle()
        self.n = 0
        self.flaky = flaky

    # ---- operations (each = business write + permission step via svc.run)
    def op_start(self):
        t, c = self.rnd.choice(list(TEACHERS)), self.rnd.choice(list(CHILDREN))
        if any(a["t"] == t and a["c"] == c and a["end"] is None for a in self.o.assign):
            return
        self.n += 1
        aid = f"as{self.n}"
        now = self.w.clock.now
        start = now + timedelta(hours=self.rnd.choice([-5, 0, 3]))
        row = {"id": aid, "t": t, "c": c, "end": None, "reason": None}
        self.o.assign.append(row)
        self.w.svc.run(
            assignment_changed(f"assignment:{aid}:start", c, t),
            lambda: self.w.add(
                "assignment", aid, child_id=c, teacher_id=t, start_at=iso(start), end_at=None
            ),
        )

    def op_session(self):
        live = [a for a in self.o.assign if a["end"] is None]
        if not live:
            return
        a = self.rnd.choice(live)
        self.n += 1
        sid = f"s{self.n}"
        end = self.w.clock.now - timedelta(minutes=self.rnd.choice([10, 90, 400]))
        self.o.sess.append(
            {
                "id": sid,
                "aid": a["id"],
                "t": a["t"],
                "c": a["c"],
                "end": end,
                "status": "confirmed",
                "sub": False,
                "cancelled": False,
            }
        )
        self.w.add(
            "session",
            sid,
            child_id=a["c"],
            teacher_id=a["t"],
            assignment_id=a["id"],
            status="confirmed",
            scheduled_start_at=iso(end - H),
            scheduled_end_at=iso(end),
            is_substitute=False,
        )

    def op_end(self):
        live = [a for a in self.o.assign if a["end"] is None]
        if not live:
            return
        a = self.rnd.choice(live)
        reason = self.rnd.choice(GRACE_REASONS)
        end = self.w.clock.now - timedelta(minutes=self.rnd.choice([0, 30, 600]))
        a["end"], a["reason"] = end, reason
        self.w.svc.run(
            assignment_changed(f"assignment:{a['id']}:end", a["c"], a["t"], ended=True),
            lambda: self.w.patch("assignment", a["id"], end_at=iso(end), end_reason=reason),
        )

    def op_substitute(self):
        t, c = self.rnd.choice(list(TEACHERS)), self.rnd.choice(list(CHILDREN))
        self.n += 1
        sid = f"sub{self.n}"
        start = self.w.clock.now + timedelta(hours=self.rnd.choice([-30, -2, 1, 20]))
        end = start + H
        self.o.sess.append(
            {
                "id": sid,
                "aid": None,
                "t": t,
                "c": c,
                "end": end,
                "status": "scheduled",
                "sub": True,
                "cancelled": False,
            }
        )
        self.w.svc.run(
            substitute_cover(f"session:{sid}:cover", c, t),
            lambda: self.w.add(
                "session",
                sid,
                child_id=c,
                teacher_id=t,
                assignment_id=None,
                status="scheduled",
                scheduled_start_at=iso(start),
                scheduled_end_at=iso(end),
                is_substitute=True,
            ),
        )

    def op_cancel_sub(self):
        subs = [s for s in self.o.sess if s["sub"] and not s["cancelled"]]
        if not subs:
            return
        s = self.rnd.choice(subs)
        s["cancelled"] = True
        self.w.svc.run(
            substitute_cover(f"session:{s['id']}:cancel", s["c"], s["t"]),
            lambda: self.w.patch("session", s["id"], status="cancelled_by_teacher"),
        )

    def op_suspend(self):
        t = self.rnd.choice(list(TEACHERS))
        status = "suspended" if self.o.profile_ok[t] else "active"
        self.o.profile_ok[t] = not self.o.profile_ok[t]
        self.w.svc.run(
            role_changed(f"teacher:{t}:{status}:{self.n}", TEACHERS[t]),
            lambda: self.w.patch("teacher_profile", t, status=status),
        )
        self.n += 1

    def op_label(self):
        t = self.rnd.choice(list(TEACHERS))
        u = TEACHERS[t]
        self.n += 1
        if self.o.label_ok[t]:
            self.o.label_ok[t] = False
            self.w.svc.run(
                role_changed(f"label:{u}:off:{self.n}", u),
                lambda: self.w.revoke_label(u, "teacher"),
            )
        else:
            self.o.label_ok[t] = True
            self.w.svc.run(
                role_changed(f"label:{u}:on:{self.n}", u), lambda: self.w.grant_label(u, "teacher")
            )

    def tick(self):
        self.runner.tick()

    # ---- the property
    def check(self, settled: bool = True):
        w, now = self.w, self.w.clock.now
        for t, u in TEACHERS.items():
            view = w.as_(u)
            for c, (_p, h) in CHILDREN.items():
                for table, rid, want in (
                    ("child", c, self.o.child(t, c, now)),
                    ("session_log", f"log_{c}", self.o.child(t, c, now)),
                    ("home_location", h, self.o.address(t, c, now)),
                ):
                    got = view.can_read(table, rid)
                    if got and not want:
                        raise AssertionError(
                            f"OVER-GRANT {u} reads {table}/{rid} outside its window at {now}"
                        )
                    if settled and want and not got:
                        raise AssertionError(
                            f"UNDER-GRANT {u} cannot read {table}/{rid} inside its window at {now}"
                        )

    def step(self):
        ops = [
            self.op_start,
            self.op_start,
            self.op_session,
            self.op_end,
            self.op_substitute,
            self.op_cancel_sub,
            self.op_suspend,
            self.op_label,
        ]
        if self.flaky and self.rnd.random() < 0.25:
            self.w.repo.fail = lambda op, t, r: (
                op == "update"
                and t in ("child", "home_location", "session_log")
                and self.rnd.random() < 0.5
            )
        self.rnd.choice(ops)()
        self.w.repo.fail = None
        self.w.clock.now += timedelta(minutes=self.rnd.choice([1, 5, 20, 120, 700, 1500]))
        self.tick()


@pytest.mark.parametrize("block", range(8))
def test_random_event_sequences_never_leave_access_outside_the_windows(block):
    """8 x 20 = 160 sequences of 12 events each, every step checked against the oracle."""
    try:
        for seed in range(block * 20, block * 20 + 20):
            sim = Sim(seed)
            for _ in range(12):
                sim.step()
                sim.check()
    finally:
        pjobs.bind(None)


@pytest.mark.parametrize("block", range(3))
def test_with_failing_permission_writes_there_is_never_a_lasting_over_grant(block):
    """Writes fail at random. Under-grants and short over-grants are the documented failure modes,
    but after the retry jobs have had their minutes the state must equal the oracle again."""
    try:
        for seed in range(1000 + block * 10, 1000 + block * 10 + 10):
            sim = Sim(seed, flaky=True)
            for _ in range(12):
                sim.step()
            for _ in range(12):  # settle: failures off, retry jobs and address-revoke run
                sim.w.clock.now += timedelta(minutes=3)
                sim.tick()
            sim.w.svc.apply(  # last resort: a full recompute
                assignment_changed(f"settle:{seed}", "c1", "Ta")
            )
            sim.w.rebuild()
            sim.check()
    finally:
        pjobs.bind(None)


def test_the_oracle_itself_catches_a_missed_revoke():
    """Self-check of the test: if the revoke is never applied, the property fails."""
    sim = Sim(7)
    sim.o.assign.append({"id": "asX", "t": "Ta", "c": "c1", "end": None, "reason": None})
    sim.w.add(
        "assignment", "asX", child_id="c1", teacher_id="Ta", start_at=iso(T0 - H), end_at=None
    )
    sim.w.rebuild()
    sim.check()
    sim.o.assign[-1].update(
        end=T0 - H, reason="admin"
    )  # the oracle says ended; permissions untouched
    with pytest.raises(AssertionError, match="OVER-GRANT"):
        sim.check()
    pjobs.bind(None)
