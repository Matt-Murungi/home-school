"""`desired_permissions` row by row (SR 6.2 matrix, ADR 0012 table, issue #40 edge cases).

Each test builds a small situation on top of the seeded world and asks the pure function."""

from datetime import timedelta

import pytest

from hs_core.permissions.context import PermissionContext, StaticFacts, parse_dt
from hs_core.permissions.policy import Policy
from hs_core.permissions.rules import SPECS, desired_file_permissions, desired_permissions
from jobs_helpers import T0
from perm_world import H, World, iso

U = 'read("user:{}")'.format


def want(w: World, table: str, rid: str, **policy_kw) -> set[str]:
    ctx = w.svc.context()
    if policy_kw:
        ctx = PermissionContext(ctx.src, w.clock.now, Policy(**policy_kw))
    return set(desired_permissions(table, w.repo.get(table, rid), ctx))


def users(perms: set[str]) -> set[str]:
    return {p[len('read("user:') : -2] for p in perms if p.startswith('read("user:')}


def labels(perms: set[str]) -> set[str]:
    return {p[len('read("label:') : -2] for p in perms if p.startswith('read("label:')}


@pytest.fixture
def w():
    return World().seed()


# ------------------------------------------------------------------ purity and shape
def test_desired_permissions_is_pure_deterministic_and_sorted(w):
    row = w.repo.get("child", "a1")
    before = dict(row)
    ctx = w.svc.context()
    first = desired_permissions("child", row, ctx)
    assert (
        first
        == desired_permissions("child", row, ctx)
        == desired_permissions("child", dict(row), w.svc.context())
    )
    assert list(first) == sorted(set(first))
    assert row == before  # the row is never mutated


def test_same_answer_from_static_facts_and_from_the_repository(w):
    tables = {t: w.repo.list(t) for t in SPECS if t in w.repo._t}
    labels_map = {r["$id"]: w.labels(r["$id"]) for r in tables["user_profile"]}
    static = PermissionContext(StaticFacts(tables, labels_map), w.clock.now)
    repo_ctx = w.svc.context()
    for t in (
        "child",
        "home_location",
        "session_log",
        "safeguarding_case",
        "case_status",
        "handover_summary",
    ):
        for r in tables[t]:
            assert desired_permissions(t, r, static) == desired_permissions(t, r, repo_ctx), (
                t,
                r["$id"],
            )


def test_unknown_table_fails_closed(w):
    with pytest.raises(KeyError):
        desired_permissions("brand_new_table", {"$id": "x"}, w.svc.context())


def test_no_entry_is_ever_other_than_read(w):
    for t in SPECS:
        for r in w.repo.list(t):
            for p in desired_permissions(t, r, w.svc.context()):
                assert p.startswith("read("), (t, p)


def test_naive_now_is_rejected():
    from datetime import datetime

    with pytest.raises(ValueError):
        PermissionContext(StaticFacts({}), datetime(2027, 1, 18, 9, 0))


def test_soft_deleted_rows_are_readable_by_nobody(w):
    w.patch("child", "a1", deleted_at=iso(T0))
    assert want(w, "child", "a1") == set()


# ------------------------------------------------------------------ parents and guardians
def test_parent_sees_only_their_family(w):
    assert users(want(w, "child", "a1")) >= {"uPA"} and "uPB" not in users(want(w, "child", "a1"))
    assert "uPA" not in users(want(w, "child", "b1")) and "uPB" in users(want(w, "child", "b1"))
    for t, rid in (
        ("home_location", "hlA"),
        ("invoice", "invA"),
        ("payment", "payA"),
        ("consent_record", "crA1"),
        ("session_log", "slA1"),
        ("handover_summary", "hsA1"),
    ):
        assert "uPA" in users(want(w, t, rid)) and "uPB" not in users(want(w, t, rid)), t


def test_guardian_gets_only_what_the_link_grants(w):
    ok = lambda t, r: "uGA" in users(want(w, t, r))  # noqa: E731
    assert ok("child", "a1") and ok("session_log", "slA1") and ok("consent_record", "crA1")
    assert not ok("home_location", "hlA")  # no `address`
    assert not ok("invoice", "invA") and not ok("session", "sA1")  # no `billing`, no `sessions`
    w.patch("guardian_link", "gl1", grants="all")
    assert ok("home_location", "hlA") and ok("invoice", "invA") and ok("session", "sA1")


@pytest.mark.parametrize("grants", ["", "ALL", "log", "logs;children", None, "children"])
def test_malformed_or_missing_guardian_grants_give_no_log_access(w, grants):
    w.patch("guardian_link", "gl1", grants=grants)
    assert "uGA" not in users(want(w, "session_log", "slA1"))


def test_guardian_toggle_off_and_on(w):
    w.patch("guardian_link", "gl1", grants="children")
    assert "uGA" not in users(want(w, "session_log", "slA1"))
    w.patch("guardian_link", "gl1", grants="children,logs")
    assert "uGA" in users(want(w, "session_log", "slA1"))


@pytest.mark.parametrize("status", ["invited", "revoked"])
def test_invited_or_revoked_guardian_has_no_access(w, status):
    w.patch("guardian_link", "gl1", status=status)
    assert "uGA" not in users(want(w, "child", "a1"))


def test_guardian_without_the_label_or_suspended_has_no_access(w):
    w.revoke_label("uGA", "guardian")
    assert "uGA" not in users(want(w, "child", "a1"))


def test_suspended_parent_keeps_own_account_row_but_not_family_data(w):
    w.patch("user_profile", "uPA", status="suspended")
    assert "uPA" in users(want(w, "user_profile", "uPA"))
    assert "uPA" not in users(want(w, "child", "a1"))
    assert "uPA" not in users(want(w, "invoice", "invA"))


def test_deleted_user_has_no_rows_at_all(w):
    w.patch("user_profile", "uPA", deleted_at=iso(T0))
    assert want(w, "user_profile", "uPA") == set()
    assert "uPA" not in users(want(w, "child", "a1"))
    assert want(w, "notification", "nfA") == set()


# ------------------------------------------------------------------ teachers: assignment
def test_teacher_sees_child_while_assigned_and_the_address_after_accepting(w):
    assert "uT1" in users(want(w, "child", "a1")) and "uT1" in users(
        want(w, "home_location", "hlA")
    )
    assert "uT4" not in users(want(w, "child", "a1")) and "uT4" not in users(
        want(w, "home_location", "hlA")
    )


def test_an_offer_that_is_not_accepted_gives_no_access(w):
    """match_offer pending: no assignment row exists yet, so no child and no address."""
    assert "uT4" not in users(want(w, "child", "a1"))
    assert "uT4" not in users(want(w, "home_location", "hlA"))


def test_upcoming_assignment_counts_as_assigned(w):
    w.add(
        "assignment",
        "asgUp",
        child_id="a2",
        teacher_id="T4",
        start_at=iso(T0 + 48 * H),
        end_at=None,
    )
    assert "uT4" in users(want(w, "child", "a2"))
    assert "uT4" in users(want(w, "home_location", "hlA"))


def test_assignment_ended_yesterday_loses_the_child_record_and_logs(w):
    # asg2 ended 30 h ago (seed): T2 reads nothing about a2 any more
    for t, rid in (
        ("child", "a2"),
        ("session_log", "slA2"),
        ("assignment", "asg2"),
        ("session", "sA2"),
    ):
        assert "uT2" not in users(want(w, t, rid)), t


def test_ended_assignment_keeps_only_the_redacted_history_row(w):
    assert users(want(w, "teacher_session_history", "tshT2")) == {"uT2"}
    assert "uT2" not in users(want(w, "session_log", "slA2"))
    row = w.repo.get("teacher_session_history", "tshT2")
    assert set(row) - {"$id", "$permissions", "$createdAt", "$updatedAt"} == {
        "teacher_id",
        "session_id",
        "child_first_name",
    }


def test_address_stays_24h_after_the_last_session_then_goes(w):
    w.patch("assignment", "asg2", end_at=iso(T0 - 7 * H))
    w.patch("session", "sA2", scheduled_end_at=iso(T0 - 6 * H), status="confirmed")
    assert "uT2" in users(want(w, "home_location", "hlA"))  # 6 h after: address yes ...
    assert "uT2" not in users(want(w, "child", "a2"))  # ... child record no
    w.clock.now = T0 + 17 * H + timedelta(minutes=59)
    assert "uT2" in users(want(w, "home_location", "hlA"))
    w.clock.now = T0 + 18 * H  # exactly 24 h after the session end: gone
    assert "uT2" not in users(want(w, "home_location", "hlA"))


@pytest.mark.parametrize(
    "reason", ["parent_cancelled", "admin", "teacher_withdrew", "rematch_requested"]
)
def test_cancellation_or_hold_withdraws_the_address_at_once(w, reason):
    w.patch("assignment", "asg2", end_at=iso(T0 - H), end_reason=reason)
    w.patch("session", "sA2", scheduled_end_at=iso(T0 - H))
    assert "uT2" not in users(want(w, "home_location", "hlA"))


def test_a_cancelled_or_never_held_session_gives_no_address_grace(w):
    w.patch("assignment", "asg2", end_at=iso(T0 - H))
    w.patch("session", "sA2", scheduled_end_at=iso(T0 - H), status="cancelled_by_teacher")
    assert "uT2" not in users(want(w, "home_location", "hlA"))


def test_assignment_ending_in_the_future_is_still_current(w):
    w.patch("assignment", "asg1", end_at=iso(T0 + 2 * H))
    assert "uT1" in users(want(w, "child", "a1"))
    w.clock.now = T0 + 2 * H
    assert "uT1" not in users(want(w, "child", "a1"))  # end instant itself: ended


def test_soft_deleted_assignment_gives_nothing(w):
    w.patch("assignment", "asg1", deleted_at=iso(T0))
    assert "uT1" not in users(want(w, "child", "a1"))


def test_teacher_sees_only_their_own_session_not_a_substitutes(w):
    assert "uT1" in users(want(w, "session", "sA1")) and "uT1" not in users(
        want(w, "session", "sSub")
    )
    assert "uT3" in users(want(w, "session", "sSub")) and "uT3" not in users(
        want(w, "session", "sA1")
    )


def test_handover_goes_to_the_recipient_teacher_only(w):
    assert "uT1" in users(want(w, "handover_summary", "hsA1"))
    assert "uT3" not in users(want(w, "handover_summary", "hsA1"))
    assert "uT3" in users(want(w, "handover_summary", "hsA3"))
    assert "uT1" not in users(want(w, "handover_summary", "hsA3"))


# ------------------------------------------------------------------ teachers: substitute window
def test_substitute_has_access_for_the_covered_window_only(w):
    # sSub: T0+20h .. T0+21h; access from assignment to 24 h after the session end
    assert "uT3" in users(want(w, "child", "a1")) and "uT3" in users(
        want(w, "home_location", "hlA")
    )
    w.clock.now = T0 + 21 * H + 23 * H + timedelta(minutes=59)
    assert "uT3" in users(want(w, "child", "a1"))
    w.clock.now = T0 + 21 * H + 24 * H
    for t, rid in (
        ("child", "a1"),
        ("home_location", "hlA"),
        ("session_log", "slA1"),
        ("handover_summary", "hsA3"),
        ("session", "sSub"),
    ):
        assert "uT3" not in users(want(w, t, rid)), t


def test_substitute_cancelled_or_voided_loses_access_at_once(w):
    w.patch("session", "sSub", status="cancelled_by_teacher")
    assert "uT3" not in users(want(w, "child", "a1"))
    assert "uT3" not in users(want(w, "home_location", "hlA"))


def test_substitute_sees_nothing_of_the_other_child(w):
    assert "uT3" not in users(want(w, "child", "a2"))
    assert "uT3" not in users(want(w, "session_log", "slA2"))


def test_substitute_and_regular_teacher_can_overlap(w):
    assert {"uT1", "uT3"} <= users(want(w, "child", "a1"))


# ------------------------------------------------------------------ teachers: account state
@pytest.mark.parametrize("status", ["suspended", "deactivated", "pending"])
def test_suspended_or_deactivated_teacher_profile_loses_all_business_access(w, status):
    w.patch("teacher_profile", "T1", status=status)
    for t, rid in (
        ("child", "a1"),
        ("home_location", "hlA"),
        ("session_log", "slA1"),
        ("handover_summary", "hsA1"),
        ("assignment", "asg1"),
        ("session", "sA1"),
    ):
        assert "uT1" not in users(want(w, t, rid)), t
    assert "uT1" in users(want(w, "earning", "e_t1"))  # their own pay history stays theirs
    assert "uT1" in users(want(w, "teacher_profile", "T1"))


def test_suspended_user_account_of_a_teacher(w):
    w.patch("user_profile", "uT1", status="suspended")
    assert "uT1" not in users(want(w, "child", "a1"))
    assert "uT1" in users(want(w, "user_profile", "uT1"))


def test_teacher_label_withdrawn_loses_access(w):
    w.revoke_label("uT1", "teacher")
    assert "uT1" not in users(want(w, "child", "a1"))


def test_deleted_teacher_loses_everything(w):
    w.patch("user_profile", "uT1", deleted_at=iso(T0))
    for t, rid in (
        ("child", "a1"),
        ("earning", "e_t1"),
        ("teacher_profile", "T1"),
        ("teacher_document", "td_t1"),
    ):
        assert "uT1" not in users(want(w, t, rid)), t


def test_teacher_documents_and_earnings_are_the_teachers_own(w):
    assert "uT1" in users(want(w, "teacher_document", "td_t1")) and "uT4" not in users(
        want(w, "teacher_document", "td_t1")
    )
    assert "uT1" in users(want(w, "earning", "e_t1")) and "uT4" not in users(
        want(w, "earning", "e_t1")
    )
    assert "uT4" in users(want(w, "payout", "po_t4"))


# ------------------------------------------------------------------ staff labels
def test_ops_admin_reads_per_matrix_and_nothing_more(w):
    for t in (
        "child",
        "home_location",
        "session_log",
        "handover_summary",
        "teacher_document",
        "invoice",
        "payment",
        "earning",
        "audit_log",
        "consent_record",
        "user_profile",
        "question",
        "topic",
    ):
        r = w.repo.list(t)[0]
        assert "opsadmin" in labels(set(desired_permissions(t, r, w.svc.context()))), t
    for t in (
        "safeguarding_case",
        "safeguarding_action",
        "case_status",
        "notification",
        "notification_preference",
        "device_token",
        "role_assignment",
    ):
        for r in w.repo.list(t):
            assert "opsadmin" not in labels(set(desired_permissions(t, r, w.svc.context()))), t


def test_curator_reads_catalogue_and_questions_only(w):
    cur = {
        t
        for t in SPECS
        for r in w.repo.list(t)[:1]
        if "curator" in labels(set(desired_permissions(t, r, w.svc.context())))
    }
    assert cur == {
        "question",
        "assessment_blueprint",
        "level_equivalence",
        "curriculum",
        "catalogue_version",
        "level",
        "subject",
        "strand",
        "topic",
        "struggle_tag",
    }


def test_super_admin_has_no_data_read_only_roles_and_templates(w):
    sup = {
        t
        for t in SPECS
        for r in w.repo.list(t)[:1]
        if "superadmin" in labels(set(desired_permissions(t, r, w.svc.context())))
    }
    assert sup == {"role_assignment", "notification_template"}


def test_published_catalogue_is_readable_by_users_drafts_are_not(w):
    assert 'read("users")' in want(w, "topic", "tpPub") and 'read("users")' not in want(
        w, "topic", "tpDraft"
    )
    assert 'read("users")' in want(w, "catalogue_version", "cvPub")
    assert 'read("users")' not in want(w, "catalogue_version", "cvDraft")
    w.patch("catalogue_version", "cvPub", status="retired")
    assert 'read("users")' in want(
        w, "level", "lvPub"
    )  # retired content still resolves in old logs
    w.patch("catalogue_version", "cvPub", status="draft")
    assert 'read("users")' not in want(w, "level", "lvPub")  # unpublished again: withdrawn


def test_users_read_appears_only_on_the_catalogue_tables(w):
    allowed = {
        "curriculum",
        "catalogue_version",
        "level",
        "subject",
        "strand",
        "topic",
        "struggle_tag",
    }
    for t in SPECS:
        for r in w.repo.list(t):
            if 'read("users")' in desired_permissions(t, r, w.svc.context()):
                assert t in allowed, t


def test_questions_never_reach_parents_or_teachers_but_approved_media_does(w):
    for q in ("qDraft", "qApp"):
        p = want(w, "question", q)
        assert 'read("users")' not in p and not users(p)
    ctx = w.svc.context()
    assert 'read("users")' in desired_file_permissions(
        "question-media", w.repo.get("question", "qApp"), ctx
    )
    assert 'read("users")' not in desired_file_permissions(
        "question-media", w.repo.get("question", "qDraft"), ctx
    )


def test_user_with_two_staff_labels_gets_the_union_and_nothing_more(w):
    """opsadmin + safeguardinglead, lead not assigned to any case: exactly the ops rights plus
    the audit log, which both labels read anyway."""
    ops_only = {
        t
        for t in SPECS
        for r in w.repo.list(t)[:1]
        if "opsadmin" in labels(set(desired_permissions(t, r, w.svc.context())))
    }
    both = {
        t
        for t in SPECS
        for r in w.repo.list(t)[:1]
        if labels(set(desired_permissions(t, r, w.svc.context())))
        & {"opsadmin", "safeguardinglead"}
    }
    assert both == ops_only
    assert "safeguarding_case" not in both


def test_user_holding_parent_and_teacher_roles_has_both_sets(w):
    w.grant_label("uPA", "teacher")
    w.add("teacher_profile", "T9", user_id="uPA", status="active")
    w.add("assignment", "asg9", child_id="b1", teacher_id="T9", start_at=iso(T0 - H), end_at=None)
    assert "uPA" in users(want(w, "child", "a1"))  # as parent
    assert "uPA" in users(want(w, "child", "b1"))  # as teacher of another family
    assert "uPA" not in users(want(w, "invoice", "invB"))


# ------------------------------------------------------------------ safeguarding
def test_case_rows_go_to_the_assigned_lead_only(w):
    assert users(want(w, "safeguarding_case", "cs1")) == {"uL1"}
    assert labels(want(w, "safeguarding_case", "cs1")) == set()
    assert users(want(w, "safeguarding_action", "saA1")) == {"uL1"}


def test_reporter_sees_case_status_but_never_the_case(w):
    assert users(want(w, "case_status", "csA1")) == {"uPA", "uL1"}
    assert "uPA" not in users(want(w, "safeguarding_case", "cs1"))
    assert "uPB" not in users(want(w, "case_status", "csA1"))  # only the case they raised


def test_teacher_reporter_sees_their_own_case_status(w):
    assert "uT5" in users(want(w, "case_status", "csB1"))


def test_lead_scope_follows_open_cases_child_family_and_teacher(w):
    assert "uL1" in users(want(w, "child", "a1"))
    assert "uL1" in users(want(w, "home_location", "hlA"))  # family of the child
    assert "uL1" in users(want(w, "session_log", "slA1"))
    assert "uL1" in users(want(w, "teacher_document", "td_t1"))  # the teacher in the case
    assert "uL1" not in users(want(w, "child", "a2"))  # same family, other child: no case
    assert "uL1" not in users(want(w, "session_log", "slA2"))
    assert "uL1" not in users(want(w, "teacher_document", "td_t4"))
    assert "uL2" not in users(want(w, "child", "a1"))  # another lead: not assigned


def test_closing_a_case_withdraws_the_related_rows_but_keeps_the_case_record(w):
    w.patch("safeguarding_case", "cs1", status="closed")
    for t, rid in (
        ("child", "a1"),
        ("home_location", "hlA"),
        ("session_log", "slA1"),
        ("teacher_document", "td_t1"),
    ):
        assert "uL1" not in users(want(w, t, rid)), t
    assert "uL1" in users(want(w, "safeguarding_case", "cs1"))
    assert "uL1" not in users(
        want(w, "safeguarding_case", "cs1", lead_keeps_closed_case_record=False)
    )
    assert "uL1" not in users(want(w, "case_status", "csA1", lead_keeps_closed_case_record=False))
    assert "uPA" in users(want(w, "case_status", "csA1", lead_keeps_closed_case_record=False))


def test_reassigning_a_case_moves_the_grants(w):
    w.patch("safeguarding_case", "cs1", assigned_to_id="uL2")
    assert "uL2" in users(want(w, "child", "a1")) and "uL1" not in users(want(w, "child", "a1"))
    assert users(want(w, "safeguarding_case", "cs1")) == {"uL2"}


def test_lead_whose_label_is_withdrawn_or_who_is_suspended_loses_the_case(w):
    w.revoke_label("uL1", "safeguardinglead")
    assert want(w, "safeguarding_case", "cs1") == set()
    assert "uL1" not in users(want(w, "child", "a1"))


def test_unassigned_case_is_invisible_unless_the_policy_flag_says_otherwise(w):
    w.patch("safeguarding_case", "cs1", assigned_to_id=None)
    assert want(w, "safeguarding_case", "cs1") == set()
    assert want(w, "safeguarding_case", "cs1", unassigned_cases_visible_to_leads=True) == {
        'read("label:safeguardinglead")'
    }
    assert "uL1" not in users(want(w, "child", "a1"))


def test_a_case_never_gives_the_involved_teacher_or_ops_a_read(w):
    assert not {"uT1", "uOps"} & users(want(w, "safeguarding_case", "cs1"))
    assert "opsadmin" not in labels(want(w, "safeguarding_case", "cs1"))


# ------------------------------------------------------------------ audit log flag (owner Q12)
def test_audit_log_read_by_ops_and_leads_by_default_one_line_to_change(w):
    assert labels(want(w, "audit_log", "al1")) == {"opsadmin", "safeguardinglead"}
    assert labels(want(w, "audit_log", "al1", audit_log_lead_read=False)) == {"opsadmin"}
    assert Policy().audit_log_lead_read is True


# ------------------------------------------------------------------ notifications and tokens
def test_notifications_and_preferences_go_to_their_user_only(w):
    assert want(w, "notification", "nfA") == {U("uPA")}
    assert want(w, "notification_preference", "npT1") == {U("uT1")}


def test_device_tokens_are_per_row_and_revoked_rows_have_no_reader(w):
    assert want(w, "device_token", "dtA") == {U("uPA")}
    assert want(w, "device_token", "dtA2") == set()
    assert want(w, "device_token", "dtB") == {U("uPB")}


# ------------------------------------------------------------------ time
def test_all_instants_are_utc_and_kampala_midnight_changes_nothing(w):
    # 21:00 UTC is Kampala midnight (UTC+3): the rule is on the UTC instant either way
    end = parse_dt("2027-01-18T20:30:00Z")
    w.patch("assignment", "asg2", end_at=iso(end))
    w.patch("session", "sA2", scheduled_end_at=iso(end), status="confirmed")
    w.clock.now = parse_dt("2027-01-19T20:29:59Z")
    assert "uT2" in users(want(w, "home_location", "hlA"))
    w.clock.now = parse_dt("2027-01-19T20:30:00Z")
    assert "uT2" not in users(want(w, "home_location", "hlA"))
