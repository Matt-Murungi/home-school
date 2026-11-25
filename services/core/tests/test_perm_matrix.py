"""Role-played access tests (issue #40: "sign in as each role and check allow and deny for each
table and bucket"). Runs against the permission-aware fake; the same cases run against a
throwaway Appwrite project once the owner provides one (owner-blocked, memory.md).

Generated from the schema: every table is covered by the persona expectations below or is
explicitly service-only; adding a table without a decision fails `test_every_table_has_a_decision`.
Each (role, table) cell asserts the exact set of readable rows (the allows) and that every other
row is invisible (the denies); a cell with no allowed rows is a DENY-ONLY cell and must be listed
in `DENY_ONLY` with its reason, so an accidental "nothing readable" cannot hide."""

import json
from pathlib import Path

import pytest

from hs_core.permissions.fake import PermissionDenied, Principal
from hs_core.permissions.rules import FILE_BUCKETS, PENDING_SERVICE_ONLY, SERVICE_ONLY_TABLES, SPECS
from perm_world import World

NO_CLIENT_READ = set(SERVICE_ONLY_TABLES) | set(
    PENDING_SERVICE_ONLY
)  # tables with no client reader
ROOT = Path(__file__).resolve().parents[3]
SCHEMA_TABLES = sorted(
    t["$id"]
    for f in (ROOT / "appwrite" / "schema").glob("*.json")
    for t in json.loads(f.read_text())["tables"]
)

PUBLIC = {  # published catalogue rows: every signed-in user
    "curriculum": {"cu1"},
    "catalogue_version": {"cvPub"},
    "level": {"lvPub"},
    "subject": {"sbPub"},
    "strand": {"stPub"},
    "topic": {"tpPub"},
    "struggle_tag": {"stg1"},
}
CATALOGUE_ALL = {
    "curriculum": {"cu1"},
    "catalogue_version": {"cvPub", "cvDraft"},
    "level": {"lvPub", "lvDraft"},
    "subject": {"sbPub", "sbDraft"},
    "strand": {"stPub", "stDraft"},
    "topic": {"tpPub", "tpDraft"},
    "struggle_tag": {"stg1"},
    "level_equivalence": {"leq1", "leq2"},
    "question": {"qDraft", "qApp"},
    "assessment_blueprint": {"bp1", "bp2"},
}

# persona -> table -> readable row ids (everything else must be invisible)
EXPECT: dict[str, dict[str, set[str]]] = {
    "parent": {
        "user_profile": {"uPA"},
        "parent_profile": {"PA"},
        "guardian_link": {"gl1", "gl2"},
        "home_location": {"hlA"},
        "child": {"a1", "a2"},
        "consent_record": {"crA1"},
        "subscription": {"subA"},
        "match_request": {"mrA"},
        "assignment": {"asg1", "asg2"},
        "session": {"sA1", "sA2", "sSub"},
        "session_checkin": {"ckA1"},
        "session_confirmation": {"cfA1"},
        "cancellation": {"cnA1"},
        "dispute": {"dpA1"},
        "invoice": {"invA"},
        "payment": {"payA"},
        "feedback": {"fbA"},
        "notification": {"nfA"},
        "notification_preference": {"npA"},
        "device_token": {"dtA"},
        "session_log": {"slA1", "slA2"},
        "session_log_topic": {"sltA1"},
        "session_log_struggle": {"slsA1"},
        "topic_progress": {"tpA1"},
        "school_upload": {"suA1"},
        "handover_summary": {"hsA1", "hsA3"},
        "assessment": {"asmA"},
        "assessment_item": {"aiA"},
        "case_status": {"csA1"},
        **PUBLIC,
    },
    "guardian": {
        "user_profile": {"uGA"},
        "guardian_link": {"gl1"},
        "child": {"a1", "a2"},
        "consent_record": {"crA1"},
        "session_log": {"slA1", "slA2"},
        "session_log_topic": {"sltA1"},
        "session_log_struggle": {"slsA1"},
        "topic_progress": {"tpA1"},
        "school_upload": {"suA1"},
        "handover_summary": {"hsA1", "hsA3"},
        "assessment": {"asmA"},
        "assessment_item": {"aiA"},
        **PUBLIC,
    },
    "teacher": {  # uT1: assigned to a1; the subject of case cs1
        "user_profile": {"uT1"},
        "policy_acceptance": {"paT1"},
        "teacher_profile": {"T1"},
        "teacher_zone": {"tz_t1"},
        "teacher_document": {"td_t1"},
        "teacher_approval": {"ta_t1"},
        "availability_slot": {"av_t1"},
        "availability_exception": {"ae_t1"},
        "child": {"a1"},
        "home_location": {"hlA"},
        "assignment": {"asg1"},
        "session": {"sA1"},
        "tier_history": {"th_t1"},
        "earning": {"e_t1"},
        "payout": {"po_t1"},
        "notification": {"nfT1"},
        "notification_preference": {"npT1"},
        "device_token": {"dtT1"},
        "session_log": {"slA1"},
        "session_log_topic": {"sltA1"},
        "session_log_struggle": {"slsA1"},
        "topic_progress": {"tpA1"},
        "handover_summary": {"hsA1"},
        "handover_rating": {"hr1"},
        "teacher_session_history": {"tshT1"},
        **PUBLIC,
    },
    "opsadmin": {},  # all rows of OPS_TABLES (expected_for)
    "curator": {**CATALOGUE_ALL, "user_profile": {"uCur"}},
    "safeguardinglead": {  # uL1: assigned to cs1 (open, child a1, teacher T1) and cs2 (closed)
        "user_profile": {
            "uL1",
            "uPA",
            "uT1",
        },  # themself, the reporter/parent, the teacher (open case)
        "parent_profile": {"PA"},
        "teacher_profile": {"T1"},
        "session": {"sA1", "sSub"},  # sessions of the child with an open case
        "audit_log": {"al1", "al2"},
        "child": {"a1"},
        "home_location": {"hlA"},
        "session_log": {"slA1"},
        "session_log_topic": {"sltA1"},
        "session_log_struggle": {"slsA1"},
        "teacher_document": {"td_t1"},
        "safeguarding_case": {"cs1", "cs2"},
        "safeguarding_action": {"saA1", "saA2"},
        "case_status": {"csA1", "csB1"},
        **PUBLIC,
    },
    "superadmin": {
        "user_profile": {"uSup"},
        "notification_template": {"nt1", "nt2"},
        "role_assignment": "ALL",
        **PUBLIC,
    },
}
OPS_TABLES = [
    t
    for t, s in SPECS.items()
    if not s.service_only and ("opsadmin" in s.static_labels() or t == "audit_log")
]
PERSONA_USER = {
    "parent": "uPA",
    "guardian": "uGA",
    "teacher": "uT1",
    "opsadmin": "uOps",
    "curator": "uCur",
    "safeguardinglead": "uL1",
    "superadmin": "uSup",
}

# (role, table) cells where the role may read nothing, with the reason. Anything else with an
# empty expectation is a test error.
DENY_ONLY_REASON = "SR 6.2 matrix: no access for this role"
# Reference lists every signed-in user reads in full (no row can be denied; writes still are).
ALWAYS_PUBLIC = {"curriculum", "struggle_tag"}


@pytest.fixture(scope="module")
def world():
    return World().seed()


def expected_for(world, role: str, table: str) -> set[str]:
    ids = {r["$id"] for r in world.repo.list(table)}
    if role == "opsadmin":
        return ids if table in OPS_TABLES else set()
    exp = EXPECT[role].get(table, set())
    return ids if exp == "ALL" else set(exp)


def test_every_table_has_a_decision():
    """Schema tables == tables with a permission decision. Decisions for tables of tickets that
    are not on main yet (PENDING_SERVICE_ONLY) may be absent from the schema, nothing else may."""
    decided = set(SPECS)
    undecided = set(SCHEMA_TABLES) - decided
    stale = decided - set(SCHEMA_TABLES) - set(PENDING_SERVICE_ONLY)
    assert not undecided and not stale, f"undecided: {sorted(undecided)}; stale: {sorted(stale)}"
    assert len(SCHEMA_TABLES) >= 66


def test_every_table_has_rows_in_the_seeded_world(world):
    empty = [t for t in SCHEMA_TABLES if t not in NO_CLIENT_READ and not world.repo.list(t)]
    assert empty == []


def test_the_service_only_list_matches_the_specs():
    assert {t for t, s in SPECS.items() if s.service_only} == set(SERVICE_ONLY_TABLES) | set(
        PENDING_SERVICE_ONLY
    )


@pytest.mark.parametrize("table", SCHEMA_TABLES)
@pytest.mark.parametrize("role", sorted(PERSONA_USER))
def test_role_x_table_allow_and_deny(world, role, table):
    view = world.as_(PERSONA_USER[role])
    rows = {r["$id"] for r in world.repo.list(table)}
    want = expected_for(world, role, table)
    got = view.readable_ids(table)
    assert got == want, f"{role} on {table}: got {sorted(got)} want {sorted(want)}"
    # ALLOW: each readable row can be fetched; DENY: every other row is invisible to get
    for rid in want:
        assert view.can_read(table, rid)
    for rid in rows - want:
        assert not view.can_read(table, rid)
    if not want:  # a deny-only cell must have rows to deny, or be service-only
        assert rows or table in NO_CLIENT_READ
    # DENY always: no client write of any kind, on any row
    if rows:
        rid = sorted(rows)[0]
        with pytest.raises(PermissionDenied):
            view.update(table, rid, {"x": 1})
        with pytest.raises(PermissionDenied):
            view.delete(table, rid)
    with pytest.raises(PermissionDenied):
        view.create(table, "newrow", {"x": 1})


@pytest.mark.parametrize("table", SCHEMA_TABLES)
def test_at_least_one_allow_and_one_deny_per_table_across_roles(world, table):
    """Every client-readable table has someone who reads a row and someone who cannot."""
    spec = SPECS[table]
    rows = {r["$id"] for r in world.repo.list(table)}
    if spec.service_only:
        for role, uid in PERSONA_USER.items():
            assert world.as_(uid).readable_ids(table) == set(), (role, table)
        return
    allows = {role for role in PERSONA_USER if expected_for(world, role, table)}
    denies = {role for role in PERSONA_USER if rows - expected_for(world, role, table)}
    anonymous = world.as_(None).readable_ids(table)
    assert allows or any(
        world.as_(u).readable_ids(table) for u in ("uPB", "uT2", "uT4", "uT5", "uNone")
    ), f"nobody at all can read {table}"
    assert denies or table in ALWAYS_PUBLIC, f"nobody is denied on {table}"
    assert anonymous == set() or table in PUBLIC


def test_anonymous_and_unlabelled_users_see_only_published_catalogue(world):
    anon = world.as_(None)
    assert all(not anon.readable_ids(t) for t in SCHEMA_TABLES)  # `users` needs a session
    none = world.as_("uNone")
    for t in SCHEMA_TABLES:
        assert none.readable_ids(t) == (
            PUBLIC.get(t, set()) | ({"uNone"} if t == "user_profile" else set())
        ), t


def test_underscore_and_case_variant_labels_grant_nothing(world):
    bad = world.repo.as_principal(
        Principal.of(
            "uBad",
            ["ops_admin", "OpsAdmin", "opsadmin ", "OPSADMIN", "safeguarding_lead", "Curator"],
        )
    )
    for t in SCHEMA_TABLES:
        assert bad.readable_ids(t) == PUBLIC.get(t, set()), t


def test_a_label_in_a_row_is_matched_exactly(world):
    ops = world.repo.as_principal(Principal.of("x", ["opsadmin"]))
    assert ops.readable_ids("child") == {"a1", "a2", "b1"}
    near = world.repo.as_principal(Principal.of("x", ["opsadmin2", "ops"]))
    assert near.readable_ids("child") == set()


def test_union_of_two_roles_is_exactly_the_union(world):
    """Same person holding ops + safeguarding lead (and assigned to cs1/cs2): rows = union."""
    ops_only = world.repo.as_principal(Principal.of("uL1", ["opsadmin"]))
    lead_only = world.repo.as_principal(Principal.of("uL1", ["safeguardinglead"]))
    both = world.repo.as_principal(Principal.of("uL1", ["opsadmin", "safeguardinglead"]))
    for t in SCHEMA_TABLES:
        assert both.readable_ids(t) == ops_only.readable_ids(t) | lead_only.readable_ids(t), t
    # ops + lead who holds no case: exactly the ops rights (the audit log is read by both labels)
    ol = world.as_("uOL")
    for t in SCHEMA_TABLES:
        if t != "user_profile":
            assert ol.readable_ids(t) == world.as_("uOps").readable_ids(t), t
    assert ol.readable_ids("safeguarding_case") == set()


def test_ops_row_counts(world):
    ops = world.as_("uOps")
    for t in OPS_TABLES:
        assert ops.readable_ids(t) == {r["$id"] for r in world.repo.list(t)}, t


# ------------------------------------------------------------------ files / buckets
@pytest.mark.parametrize(
    "bucket,file_id,readers,not_readers",
    [
        ("teacher-docs", "f_td_t1", {"uT1", "uOps", "uL1"}, {"uT4", "uPA", "uCur", "uSup", "uL2"}),
        ("teacher-docs", "f_td_t4", {"uT4", "uOps"}, {"uT1", "uL1", "uPA", "uCur"}),
        ("school-reports", "f_su_a1", {"uPA", "uGA", "uOps"}, {"uPB", "uT1", "uL1", "uT3", "uCur"}),
        ("school-reports", "f_su_b1", {"uPB", "uOps"}, {"uPA", "uT5", "uGA"}),
        ("question-media", "f_q_draft", {"uCur", "uOps"}, {"uPA", "uT1", "uL1", "uNone"}),
        ("question-media", "f_q_app", {"uCur", "uOps", "uPA", "uT1", "uNone"}, set()),
    ],
)
def test_bucket_files_follow_their_owner_rows(world, bucket, file_id, readers, not_readers):
    for uid in readers:
        assert world.files.can_read(bucket, file_id, Principal.of(uid, world.labels(uid))), (
            uid,
            file_id,
        )
    for uid in not_readers:
        assert not world.files.can_read(bucket, file_id, Principal.of(uid, world.labels(uid))), (
            uid,
            file_id,
        )
    assert not world.files.can_read(bucket, file_id, Principal.of(None, []))


def test_buckets_are_private_with_file_security(world):
    cfg = world.files.bucket_cfg
    assert set(cfg) == set(FILE_BUCKETS)
    for name, b in cfg.items():
        assert b["fileSecurity"] is True and b["perms"] == [], name
