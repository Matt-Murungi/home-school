"""M05 round 2: false-green live check, per-row isolation of malformed rows, the lead's reads
of case-linked account rows (H09 search), the audit-log flag source of truth, the README, and
the fake rejecting what Appwrite rejects."""

import importlib.util
import io
from pathlib import Path

import pytest

from hs_core.jobs.alerts import FakeAlertSink
from hs_core.jobs.model import JobContext
from hs_core.permissions import jobs as pjobs
from hs_core.permissions.audit import audit_all
from hs_core.permissions.fake import PermissionAwareRepository, Principal
from hs_core.permissions.service import PermissionEvent, Scope
from jobs_helpers import T0
from perm_world import H, World, iso

ROOT = Path(__file__).resolve().parents[3]


def load_script():
    spec = importlib.util.spec_from_file_location(
        "permissions_check",
        Path(__file__).resolve().parents[1] / "scripts" / "permissions_check.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def users(perms):
    return {p[len('read("user:') : -2] for p in perms if p.startswith('read("user:')}


@pytest.fixture
def w():
    return World().seed()


# ------------------------------------------------------------------ 1. no false green
def test_live_check_refuses_an_in_memory_repository(monkeypatch):
    mod = load_script()
    monkeypatch.setenv("HS_ENV", "dev")
    for k in (
        "APPWRITE_ENDPOINT",
        "APPWRITE_PROJECT_ID",
        "APPWRITE_API_KEY",
        "APPWRITE_DATABASE_ID",
    ):
        monkeypatch.delenv(k, raising=False)
    out = io.StringIO()
    code = mod.main(["--live"], out=out)
    text = out.getvalue()
    assert code != 0
    assert "LiveCheckRefused" in text and "zero differences" not in text and '"rows"' not in text


def test_live_check_accepts_only_a_real_appwrite_repository():
    mod = load_script()
    with pytest.raises(mod.LiveCheckRefused):
        mod.require_real_repository(PermissionAwareRepository())
    from hs_core.repository import AppwriteRepository

    mod.require_real_repository(AppwriteRepository(object(), "db", tables=object()))  # no raise


def test_an_audit_that_saw_no_rows_is_not_green():
    mod = load_script()
    empty = PermissionAwareRepository()
    with pytest.raises(mod.NothingAudited):
        mod.verdict(audit_all(empty).to_dict(), expect_rows=True)


def test_live_run_names_tables_with_zero_rows(w):
    mod = load_script()
    w.repo._t.pop("session_log")
    summary = audit_all(w.repo, w.files, w.clock).to_dict()
    assert "session_log" in summary["empty_tables"]
    msgs = mod.empty_table_notes(summary)
    assert any("session_log" in m for m in msgs)


def test_seeded_dataset_has_rows_in_every_client_readable_table(w):
    assert audit_all(w.repo, w.files, w.clock).to_dict()["empty_tables"] == []


def test_script_fails_when_a_schema_table_has_no_decision(monkeypatch, tmp_path):
    mod = load_script()
    monkeypatch.setattr(mod, "schema_tables", lambda: ["child", "brand_new_table"])
    out = io.StringIO()
    assert mod.main([], out=out) == 1
    assert "brand_new_table" in out.getvalue() and "no permission decision" in out.getvalue()


# ------------------------------------------------------------------ 2. malformed rows
BAD = "bad id!"  # not a valid Appwrite id: read_user refuses it


def break_rows(w):
    """Real accounts whose id cannot appear in a permission string (the rules raise on them)."""
    w.add("user_profile", BAD, status="active")
    w.grant_label(BAD, "parent")
    w.patch("parent_profile", "PB", user_id=BAD)
    w.patch("notification", "nfB", user_id=BAD)
    w.patch("device_token", "dtB", user_id=BAD)


def test_sync_scope_isolates_malformed_rows_and_fixes_the_good_ones(w):
    break_rows(w)
    w.repo.update("child", "a1", {}, permissions=['read("user:uT4")'])  # drift on a good row
    rep = w.svc.sync_scope(Scope("table", "parent_profile"))
    assert [m[:2] for m in rep.malformed] == [("parent_profile", "PB")]
    rep = w.svc.sync_scope(Scope("table", "child"))  # PB's family: the owner lookup yields a bad id
    assert (
        rep.ok and w.as_("uPA").can_read("child", "a1") and not w.as_("uT4").can_read("child", "a1")
    )
    rep = w.svc.sync_scope(Scope("table", "notification"))
    assert ("notification", "nfB") in [m[:2] for m in rep.malformed]
    assert w.as_("uPA").can_read("notification", "nfA")


def test_malformed_report_has_table_and_id_only_no_row_content(w):
    break_rows(w)
    rep = w.svc.sync_scope(Scope("table", "notification"))
    flat = str(rep.malformed)
    assert "nfB" in flat and "ValueError" in flat and "User " not in flat  # no row content


def test_event_apply_with_a_malformed_row_still_applies_the_rest_and_alerts(w):
    alerts = FakeAlertSink()
    w.svc.alerts = alerts
    break_rows(w)
    w.patch("device_token", "dtA", user_id=BAD)
    w.repo.update("child", "a1", {}, permissions=['read("user:uT4")'])
    rep = w.svc.apply(PermissionEvent("manual", "m", (Scope("user", BAD), Scope("child", "a1"))))
    assert rep.malformed and not w.as_("uT4").can_read("child", "a1")
    assert [a.kind for a in alerts.sent] == ["permission_malformed_row"]
    assert "dtB" in alerts.sent[0].error and "User " not in alerts.sent[0].error


def test_reconcile_job_survives_malformed_rows_and_fixes_the_rest():
    w = World().seed()
    alerts = FakeAlertSink()
    w.svc.alerts = alerts
    pjobs.bind(lambda: w.svc)
    try:
        w.add(
            "assignment", "asgDrift", child_id="a2", teacher_id="T4", start_at=iso(T0), end_at=None
        )
        for i in range(3):
            w.add("teacher_zone", f"bz{i}", teacher_id="T4")
        break_rows(w)
        w.clock.now = T0 + 14 * H
        ctx = JobContext("permission-reconcile", "r", "k", w.clock.now, {}, None, 0, None)
        out = pjobs.permission_reconcile(ctx)
        for _ in range(40):
            if type(out).__name__ == "Done":
                break
            ctx = JobContext("permission-reconcile", "r", "k", w.clock.now, {}, out.cursor, 1, None)
            out = pjobs.permission_reconcile(ctx)
        assert type(out).__name__ == "Done"
        assert any(a.kind == "permission_malformed_row" for a in alerts.sent)
        assert w.as_("uPA").can_read("child", "a1")
    finally:
        pjobs.bind(None)


def test_audit_counts_malformed_rows_continues_and_is_not_green(w):
    break_rows(w)
    w.repo.update("child", "a1", {}, permissions=['read("user:uT4")'])
    s = audit_all(w.repo, w.files, w.clock)
    assert s.malformed >= 2 and s.over_grants >= 1 and not s.clean and s.high_severity
    flat = str(s.malformed_samples)
    assert "nfB" in flat and "User " not in flat


def test_audit_job_alerts_for_malformed_rows(w):
    alerts = FakeAlertSink()
    w.svc.alerts = alerts
    pjobs.bind(lambda: w.svc)
    try:
        break_rows(w)
        cursor = None
        for _ in range(60):
            out = pjobs.permission_audit(
                JobContext("permission-audit", "r", "k", None, {}, cursor, 0, None)
            )
            if type(out).__name__ == "Done":
                break
            cursor = out.cursor
        assert any(a.kind == "permission_drift" and "malformed" in a.error for a in alerts.sent)
    finally:
        pjobs.bind(None)


# ------------------------------------------------------------------ 3. README names the real file
def test_appwrite_readme_names_the_real_permissions_migration():
    names = sorted(
        p.name for p in (ROOT / "appwrite" / "migrations").glob("*_permissions_schema.py")
    )
    assert len(names) == 1
    readme = (ROOT / "appwrite" / "README.md").read_text()
    assert names[0] in readme
    import re

    for m in re.findall(r"\d{4}_permissions_schema\.py", readme):
        assert m == names[0]


# ----------------------------------------------------- 4. the lead and case-linked rows
def lead_can(w, table, rid, uid="uL1"):
    return w.as_(uid).can_read(table, rid)


def test_lead_reads_only_case_linked_account_and_session_rows(w):
    for table, rid in (
        ("user_profile", "uPA"),  # parent of the child and reporter
        ("user_profile", "uT1"),  # the teacher in the case
        ("parent_profile", "PA"),
        ("teacher_profile", "T1"),
        ("session", "sA1"),
        ("session", "sSub"),
    ):
        assert lead_can(w, table, rid), (table, rid)
    # sA2 belongs to child a2: no case concerns a2, so it stays invisible
    for table, rid in (
        ("user_profile", "uPB"),
        ("user_profile", "uT4"),
        ("parent_profile", "PB"),
        ("teacher_profile", "T4"),
        ("teacher_profile", "T3"),
        ("session", "sB1"),
        ("session", "sA2"),
    ):
        assert not lead_can(w, table, rid), (table, rid)
    assert not w.as_("uL2").can_read("parent_profile", "PA")  # another lead: not assigned


def test_closing_the_case_withdraws_the_account_rows_but_not_the_case(w):
    w.patch("safeguarding_case", "cs1", status="closed")
    w.svc.apply(PermissionEvent("manual", "close", (Scope("case", "cs1"),)))
    for table, rid in (
        ("user_profile", "uPA"),
        ("user_profile", "uT1"),
        ("parent_profile", "PA"),
        ("teacher_profile", "T1"),
        ("session", "sA1"),
    ):
        assert not lead_can(w, table, rid), (table, rid)
    assert lead_can(w, "safeguarding_case", "cs1")
    assert lead_can(w, "user_profile", "uL1")  # their own account


def test_assigning_a_case_grants_the_account_rows_through_the_event(w):
    w.add(
        "safeguarding_case",
        "cs9",
        child_id="b1",
        teacher_id="T5",
        raised_by_id="uPB",
        assigned_to_id="uL2",
        status="open",
    )
    w.svc.apply(PermissionEvent("case_assigned", "c9", (Scope("case", "cs9"),)))
    assert w.as_("uL2").can_read("parent_profile", "PB") and w.as_("uL2").can_read(
        "teacher_profile", "T5"
    )
    assert w.as_("uL2").can_read("user_profile", "uPB") and w.as_("uL2").can_read("session", "sB1")
    assert not w.as_("uL2").can_read("parent_profile", "PA")


def test_lead_union_with_other_labels_is_exactly_the_union(w):
    ops = w.repo.as_principal(Principal.of("uL1", ["opsadmin"]))
    lead = w.repo.as_principal(Principal.of("uL1", ["safeguardinglead"]))
    both = w.repo.as_principal(Principal.of("uL1", ["opsadmin", "safeguardinglead"]))
    for t in ("user_profile", "parent_profile", "teacher_profile", "session"):
        assert both.readable_ids(t) == ops.readable_ids(t) | lead.readable_ids(t)
    # a lead who is also a parent: their own family plus the case rows, nothing else
    w.grant_label("uL1", "parent")
    assert w.as_("uL1").readable_ids("parent_profile") == {"PA"}


def test_suspended_or_unlabelled_lead_loses_the_account_rows(w):
    w.revoke_label("uL1", "safeguardinglead")
    w.svc.apply(PermissionEvent("role_changed", "r1", (Scope("user", "uL1"),)))
    assert not w.as_("uL1").can_read("parent_profile", "PA")


# ------------------------------------------------------------------ 5. one source for the Q12 flag
def test_wiring_takes_the_audit_flag_from_settings_when_present():
    from hs_core.permissions.wiring import wire_permissions

    class S:
        audit_lead_blanket_read = False

    class R:
        def enqueue(self, *a, **k):
            return True

    try:
        svc = wire_permissions(PermissionAwareRepository(), R(), settings=S())
        assert svc.policy.audit_log_lead_read is False
        svc = wire_permissions(PermissionAwareRepository(), R(), settings=object())
        assert svc.policy.audit_log_lead_read is True  # no such setting yet (M06 not merged)
    finally:
        pjobs.bind(None)


# ----------------------------------------------------- 6. the fake rejects what Appwrite rejects
def test_fake_rejects_invalid_permission_strings_and_long_ids_on_write():
    from hs_core.permissions.fake import InvalidPermission

    repo = PermissionAwareRepository()
    for bad in (
        ["garbage"],
        ['read("user:' + "x" * 40 + '")'],
        ['read("label:bad label")'],
        ['read("label:ops_admin")'],
        ['read("user:")'],
        ['read("nobody:x")'],
    ):
        with pytest.raises(InvalidPermission):
            repo.create("child", "c1", {}, bad)
    repo.create("child", "c2", {}, ['read("user:ok")'])
    with pytest.raises(InvalidPermission):
        repo.update("child", "c2", {}, permissions=["nonsense"])
    repo.update("child", "c2", {}, permissions=[])  # permissions=[] clears the list
    assert repo.get("child", "c2")["$permissions"] == []
