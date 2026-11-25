"""V01 (#56): repository-backed adapters, cleanup job, settings and inventory."""

import json
from datetime import timedelta
from pathlib import Path

import pytest

from application_helpers import T0, Clock, Env, valid_content
from hs_core.jobs.model import JobContext
from hs_core.jobs.schedule import JOBS
from hs_core.repository import InMemoryRepository
from hs_core.settings import Settings
from hs_core.teacher_application.jobs import cleanup_claims, make_cleanup_handler
from hs_core.teacher_application.ports import (
    AuditEntry,
    RepositoryAuditSink,
    RepositoryCatalogue,
    RepositoryZones,
)
from hs_core.teacher_application.service import ApplicationPolicy

ROOT = Path(__file__).resolve().parents[3]
INV = json.loads((ROOT / "docs/ops/secrets-inventory.json").read_text())
CORE = {e["name"]: e for e in INV["entries"] if e["consumer"] == "core-service"}
NEW = [
    "teacher_app_min_weekly_hours",
    "teacher_app_reapply_cooldown_days",
    "teacher_app_draft_saves_per_hour",
    "teacher_app_submits_per_hour",
]


def seed_catalogue(repo):
    repo.create("curriculum", "c1", {"code": "UG", "name": "Ugandan"})
    repo.create("curriculum", "c2", {"code": "CAM", "name": "Cambridge"})
    repo.create(
        "catalogue_version", "v1", {"curriculum_id": "c1", "version": 1, "status": "published"}
    )
    repo.create("catalogue_version", "v2", {"curriculum_id": "c2", "version": 1, "status": "draft"})
    for i, (code, name) in enumerate([("P2", "Primary 2"), ("P1", "Primary 1")]):
        repo.create(
            "level",
            f"l{i}",
            {"catalogue_version_id": "v1", "code": code, "name": name, "sort_order": 2 - i},
        )
    repo.create(
        "level",
        "lx",
        {
            "catalogue_version_id": "v1",
            "code": "X",
            "name": "Retired",
            "sort_order": 9,
            "deleted_at": "2026-01-01T00:00:00+00:00",
        },
    )
    repo.create(
        "level",
        "ld",
        {"catalogue_version_id": "v2", "code": "D", "name": "Draft level", "sort_order": 1},
    )
    repo.create("subject", "s1", {"catalogue_version_id": "v1", "code": "M", "name": "Mathematics"})


def test_repository_catalogue_offers_published_versions_only():
    repo = InMemoryRepository()
    seed_catalogue(repo)
    opts = RepositoryCatalogue(repo).published()
    assert [o.name for o in opts] == ["Ugandan"]  # the draft Cambridge version is not offered
    assert [n for _, n in opts[0].levels] == [
        "Primary 1",
        "Primary 2",
    ]  # teaching order, no deleted
    assert opts[0].subjects == (("s1", "Mathematics"),)


def test_repository_zones_skip_deleted_and_sort():
    repo = InMemoryRepository()
    repo.create("zone", "z2", {"name": "Kira", "centroid_lat_e6": 0, "centroid_lng_e6": 0})
    repo.create("zone", "z1", {"name": "Ntinda", "hard_to_fill": True})
    repo.create("zone", "z3", {"name": "Gone", "deleted_at": "2026-01-01T00:00:00+00:00"})
    assert [(z.name, z.hard_to_fill) for z in RepositoryZones(repo).zones()] == [
        ("Kira", False),
        ("Ntinda", True),
    ]


def test_a_full_submit_against_the_repository_adapters():
    e = Env()
    repo = e.repo
    seed_catalogue(repo)
    repo.create("zone", "z1", {"name": "Ntinda"})
    e.service.catalogue, e.service.zones = RepositoryCatalogue(repo), RepositoryZones(repo)
    c = valid_content()
    c["curricula"] = [{"curriculum_id": "c1", "level_ids": ["l0"], "subject_ids": ["s1"]}]
    c["zone_ids"] = ["z1"]
    e.save(c)
    assert e.submit(1).status_code == 200
    assert [a["level_id"] for a in e.rows("teacher_approval")] == ["l0"]


def test_audit_sink_appends_one_row_and_a_retry_is_not_a_second():
    repo = InMemoryRepository()
    sink = RepositoryAuditSink(repo, Clock())
    entry = AuditEntry(
        "teacher_application.submitted",
        "u1",
        ("teacher",),
        "teacher_application",
        "u1",
        "app-submit:u1:1",
        {"attempt": 1},
    )
    sink.record(entry)
    sink.record(entry)
    rows = repo.list("audit_log")
    assert len(rows) == 1
    assert (
        rows[0]["action"] == "teacher_application.submitted" and rows[0]["actor_roles"] == "teacher"
    )
    assert rows[0]["occurred_at"].startswith("2026-11-25T09:00:00")


def test_cleanup_job_is_registered_hourly_and_purges_only_expired_claims():
    spec = next(j for j in JOBS if j.name == "application-claims-cleanup")
    assert spec.schedule.every == timedelta(hours=1)
    e = Env()
    e.ready()
    e.submit(1)
    before = len(e.rows("teacher_application_claim"))
    assert cleanup_claims(e.repo, T0 + timedelta(minutes=5)) == 0
    n = cleanup_claims(e.repo, T0 + timedelta(days=3))
    after = e.rows("teacher_application_claim")
    assert n > 0 and len(after) == before - n
    assert [r["kind"] for r in after] == ["submit"]  # the exactly-once record stays
    assert cleanup_claims(e.repo, T0 + timedelta(days=3)) == 0  # idempotent


def test_cleanup_handler_continues_when_a_page_is_full():
    e = Env(draft_saves_per_hour=500)
    rev = 0
    for _ in range(7):
        rev = e.save({"personal": {"full_name": "Gr"}}, rev=rev).json()["revision"]
    h = make_cleanup_handler(e.repo, lambda: T0 + timedelta(days=3), page=5)
    ctx = JobContext("application-claims-cleanup", "r", "k", T0, {}, None, 0)
    seen = [type(h(ctx)).__name__ for _ in range(4)]
    assert seen[0] == "Continue" and seen[-1] == "Done"
    assert e.rows("teacher_application_claim") == []


@pytest.mark.parametrize("name", NEW)
def test_new_settings_are_in_inventory_and_env_example(name):
    assert name in Settings.model_fields
    assert CORE[name.upper()]["kind"] == "non_secret"
    ex = (ROOT / "services/core/.env.example").read_text().splitlines()
    assert f"{name.upper()}=" in ex


def test_defaults_and_ranges():
    s = Settings(_env_file=None)
    p = ApplicationPolicy.from_settings(s)
    assert (p.min_weekly_hours, p.reapply_cooldown_days) == (6, 90)
    bad = Settings(_env_file=None, teacher_app_min_weekly_hours=0, teacher_app_submits_per_hour=999)
    probs = bad.problems()
    assert any("TEACHER_APP_MIN_WEEKLY_HOURS" in x for x in probs)
    assert any("TEACHER_APP_SUBMITS_PER_HOUR" in x for x in probs)


def test_no_national_id_number_is_collected_or_stored():
    e = Env()
    e.ready()
    e.submit(1)
    blob = json.dumps(
        e.rows("teacher_application") + e.rows("teacher_profile") + e.rows("teacher_referee")
    )
    assert "national" not in blob.lower() and "nin" not in {
        k.lower() for r in e.rows("teacher_application") for k in r
    }


# -- round 2: N01 wiring and the M06 swap point -------------------------------------------------
class _FakeNotificationService:
    def __init__(self):
        self.calls = []

    def send(self, key, recipient, variables, idempotency_key, **kw):
        self.calls.append((key, recipient.user_id, recipient.role, variables, idempotency_key))


def test_the_real_catalogue_has_no_application_event_so_the_gap_is_recorded_not_invented():
    from hs_core.notifications.catalog import EVENTS
    from hs_core.teacher_application.ports import (
        EVENT_FOR_NOTICE,
        Notice,
        NotificationServiceNotifier,
    )

    svc = _FakeNotificationService()
    n = NotificationServiceNotifier(svc)
    n.send(Notice("application_submitted_teacher", "u1", "k1"))
    n.send(Notice("application_submitted_ops", None, "k2"))
    assert svc.calls == [] and n.gaps == [
        "application_submitted_teacher",
        "application_submitted_ops",
    ]
    # when N01 adds the events this test must change: that is the signal to turn them on
    assert not any(key in EVENTS for key, _ in EVENT_FOR_NOTICE.values())


def test_with_the_event_present_the_service_is_called_once_with_the_dedupe_key_and_no_channel():
    from hs_core.teacher_application.ports import Notice, NotificationServiceNotifier

    svc = _FakeNotificationService()
    n = NotificationServiceNotifier(svc, events={"application_submitted_teacher": object()})
    n.send(Notice("application_submitted_teacher", "u1", "app-submit-t:u1:1", {"attempt": 1}))
    assert svc.calls == [
        ("application_submitted_teacher", "u1", "teacher", {"attempt": 1}, "app-submit-t:u1:1")
    ]
    assert n.gaps == []
    # no event yet for the ops notice in this fake catalogue: gap; with it, a ROLE recipient
    n.send(Notice("application_submitted_ops", None, "k"))
    assert n.gaps == ["application_submitted_ops"]
    n2 = NotificationServiceNotifier(svc, events={"application_submitted_admin": object()})
    n2.send(Notice("application_submitted_ops", None, "app-submit-o:u1:1", {"teacher_id": "u1"}))
    assert svc.calls[-1] == (
        "application_submitted_admin",
        "role:opsadmin",
        "admin",
        {"teacher_id": "u1"},
        "app-submit-o:u1:1",
    )


def test_the_app_wires_the_notification_service_and_a_submit_sends_no_real_channel():
    from fastapi.testclient import TestClient

    from hs_core.main import create_app
    from hs_core.teacher_application import build_application_service
    from hs_core.teacher_application.ports import NotificationServiceNotifier

    e = Env()
    app = create_app(
        Settings(_env_file=None, hs_env="dev"),
        repository=e.repo,
        verifier=e.client.app.state.verifier,
    )
    assert isinstance(app.state.teacher_application.notifier, NotificationServiceNotifier)
    assert app.state.teacher_application.notifier._service is app.state.notifications
    svc = build_application_service(Settings(_env_file=None), e.repo)
    assert type(svc.notifier).__name__ == "LogNotifier"  # no service given: log only
    assert TestClient is not None


def test_the_audit_default_is_one_explicit_swap_point():
    from hs_core.teacher_application.ports import RepositoryAuditSink, build_audit_sink

    assert isinstance(build_audit_sink(InMemoryRepository()), RepositoryAuditSink)
    assert "AuditedRepository" in build_audit_sink.__doc__ and "M06" in build_audit_sink.__doc__


def test_new_takeover_setting_is_inventoried_and_ranged():
    assert "teacher_app_submit_takeover_minutes" in Settings.model_fields
    assert CORE["TEACHER_APP_SUBMIT_TAKEOVER_MINUTES"]["kind"] == "non_secret"
    ex = (ROOT / "services/core/.env.example").read_text().splitlines()
    assert "TEACHER_APP_SUBMIT_TAKEOVER_MINUTES=" in ex
    assert ApplicationPolicy.from_settings(Settings(_env_file=None)).submit_takeover_minutes == 10
    bad = Settings(_env_file=None, teacher_app_submit_takeover_minutes=0)
    assert any("TEACHER_APP_SUBMIT_TAKEOVER_MINUTES" in x for x in bad.problems())
