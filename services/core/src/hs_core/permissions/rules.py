# ruff: noqa: E501
"""`desired_permissions`: the ONE place that turns business state into Appwrite permission
strings (SR 6.2 matrix, ADR 0012 table). Pure and deterministic: same row + same context = same
tuple, sorted. Every write path of the core service goes through the `PermissionService`, which
calls this and applies the difference.

Principles enforced here (each has a test):
- Clients get `read(...)` only. Never create/update/delete/write, never `any`.
- `read("users")` appears only on published catalogue rows (and approved question media files).
- Individuals by `read("user:<id>")`, groups by `read("label:<x>")`; labels are exact matches.
- Role-based grants need the role's label AND an active account; own-account rows need only a
  non-deleted account (a suspended user keeps their own record, loses everything else).
- Unknown tables raise (fail closed): a new table needs a decision in SPECS.
- A soft-deleted row is readable by nobody (the core service reads it for retention).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from .context import PermissionContext
from .strings import READ_USERS, canonical, read_label, read_user

Row = dict
UsersFn = Callable[[Row, PermissionContext], Iterable[str | None]]
LabelsFn = Callable[[Row, PermissionContext], Iterable[str]]
PublicFn = Callable[[Row, PermissionContext], bool]

PUBLISHED = ("published", "retired")  # catalogue versions whose content users may read


@dataclass(frozen=True)
class Spec:
    table: str
    audience: dict[str, str] = field(default_factory=dict)  # role -> who/when (docs)
    labels: tuple[str, ...] | LabelsFn = ()
    users: UsersFn | None = None
    public: PublicFn | None = None  # read("users"): published catalogue content only
    service_only: bool = False
    note: str = ""

    def static_labels(self) -> tuple[str, ...]:
        return tuple(self.labels) if not callable(self.labels) else ()


# ------------------------------------------------------------------ user helpers
def own(col: str) -> UsersFn:
    return lambda r, c: [r.get(col)] if c.user_exists(r.get(col)) else []


def own_teacher(col: str = "teacher_id") -> UsersFn:
    return lambda r, c: [c.teacher_user_raw(r.get(col))]


def family(scope: str, col: str = "parent_id") -> UsersFn:
    return lambda r, c: c.family_users(r.get(col), scope)


def child_family(scope: str, col: str = "child_id") -> UsersFn:
    return lambda r, c: c.child_family_users(r.get(col), scope)


def combine(*fns: UsersFn) -> UsersFn:
    def run(r: Row, c: PermissionContext) -> set[str]:
        out: set[str] = set()
        for f in fns:
            out.update(u for u in f(r, c) if u)
        return out

    return run


def _teacher_on_child(
    r: Row, c: PermissionContext, child_col: str = "child_id"
) -> list[str | None]:
    """The row's own teacher (teacher_id) while that teacher may read the row's child."""
    t, child = r.get("teacher_id"), r.get(child_col)
    return [c.teacher_user(t)] if t and child and c.access(t, child).child else []


def teachers_of_child(col: str = "child_id") -> UsersFn:
    return lambda r, c: c.teachers_for_child(r.get(col))


def leads_of_child(col: str = "child_id") -> UsersFn:
    return lambda r, c: c.case_leads(child_id=r.get(col))


def _session_fam(scope: str) -> UsersFn:
    return lambda r, c: c.child_family_users(c.session_child(r.get("session_id")), scope)


def _log_child(r: Row, c: PermissionContext) -> str | None:
    return c.log_child(r.get("session_log_id"))


def _log_child_users(scope: str, teachers: bool, leads: bool) -> UsersFn:
    def run(r: Row, c: PermissionContext) -> set[str]:
        child = _log_child(r, c)
        out = set(c.child_family_users(child, scope))
        if teachers:
            out |= c.teachers_for_child(child)
        if leads and child:
            out |= c.case_leads(child_id=child)
        return out

    return run


def _assessment_family(r: Row, c: PermissionContext):
    return c.child_family_users(c.assessment_child(r.get("assessment_id")), "children")


def _subscription_family(r: Row, c: PermissionContext):
    sub = c.subscription_row(r.get("subscription_id"))
    return c.family_users(sub.get("parent_id") if sub else None, "sessions")


def _match_request_family(r: Row, c: PermissionContext):
    return (
        c.family_users(r.get("parent_id"), "sessions")
        if r.get("parent_id")
        else _subscription_family(r, c)
    )


def _case_of(r: Row, c: PermissionContext) -> Row | None:
    case = c.src.row("safeguarding_case", r.get("case_id"))
    return case if case and not case.get("deleted_at") else None


def _lead_closed_ok(c: PermissionContext) -> bool:
    return c.policy.lead_keeps_closed_case_record


# ------------------------------------------------------------------ the rules
def _case_users(r: Row, c: PermissionContext) -> set[str]:
    u = c.case_lead(r, closed_ok=_lead_closed_ok(c))
    return {u} if u else set()


def _case_labels(r: Row, c: PermissionContext) -> tuple[str, ...]:
    if (
        c.policy.unassigned_cases_visible_to_leads
        and not r.get("assigned_to_id")
        and c.case_open(r)
    ):
        return ("safeguardinglead",)
    return ()


def _action_users(r: Row, c: PermissionContext) -> set[str]:
    case = _case_of(r, c)
    return _case_users(case, c) if case else set()


def _action_labels(r: Row, c: PermissionContext) -> tuple[str, ...]:
    case = _case_of(r, c)
    return _case_labels(case, c) if case else ()


def _case_status_users(r: Row, c: PermissionContext) -> set[str]:
    out = {r["reporter_id"]} if c.user_exists(r.get("reporter_id")) else set()
    case = _case_of(r, c)
    return out | (_case_users(case, c) if case else set())


def _audit_labels(r: Row, c: PermissionContext) -> tuple[str, ...]:
    return ("opsadmin", "safeguardinglead") if c.policy.audit_log_lead_read else ("opsadmin",)


def _published_version(r: Row, c: PermissionContext) -> bool:
    return c.catalogue_status(r.get("catalogue_version_id")) in PUBLISHED


def _version_published(r: Row, c: PermissionContext) -> bool:
    return r.get("status") in PUBLISHED


def _always(r: Row, c: PermissionContext) -> bool:
    return True


OPS = ("opsadmin",)
CUR_OPS = ("curator", "opsadmin")

A = dict  # audience descriptors read better as A(parent=..., teacher=...)

_SPECS: list[Spec] = [
    # ---- core (M01)
    Spec(
        "user_profile",
        A(
            self="own account (even while suspended)",
            lead="the reporter, the child's parent and the teacher of an open assigned case (assigned lead)",
            ops="read",
        ),
        OPS,
        lambda r, c: set(own("$id")(r, c)) | set(c.case_leads_for_user(r["$id"])),
    ),
    Spec("role_assignment", A(superadmin="read (role management)"), ("superadmin",)),
    Spec(
        "parent_profile",
        A(parent="own", lead="the family of an open assigned case (assigned lead)", ops="read"),
        OPS,
        lambda r, c: set(own("user_id")(r, c)) | set(c.case_leads(parent_id=r["$id"])),
    ),
    Spec(
        "guardian_link",
        A(parent="own links", guardian="own link while invited or active", ops="read"),
        OPS,
        lambda r, c: (
            [c.parent_user(r.get("parent_id"))]
            + (
                [r.get("user_id")]
                if r.get("status") in ("invited", "active") and c.user_exists(r.get("user_id"))
                else []
            )
        ),
    ),
    Spec(
        "zone",
        A(ops="read"),
        OPS,
        note="Clients get zones through a core endpoint (owner question 29).",
    ),
    Spec(
        "home_location",
        A(
            parent="own; guardian with grant `address`",
            teacher="after accepting, until 24 h after the last session; substitute until 24 h after the covered session",
            lead="while an open case concerns the family (assigned lead)",
            ops="read",
        ),
        OPS,
        lambda r, c: (
            set(c.family_users(r.get("parent_id"), "address"))
            | set(c.teachers_for_address(r.get("parent_id")))
            | set(c.case_leads(parent_id=r.get("parent_id")))
        ),
    ),
    Spec(
        "child",
        A(
            parent="own children; guardian with grant `children`",
            teacher="only while assigned (current or upcoming, incl. substitute window)",
            lead="while an open case concerns the child (assigned lead)",
            ops="read",
        ),
        OPS,
        lambda r, c: (
            set(c.family_users(r.get("parent_id"), "children"))
            | set(c.teachers_for_child(r["$id"]))
            | set(c.case_leads(child_id=r["$id"]))
        ),
    ),
    Spec(
        "consent_record",
        A(parent="own children; guardian with grant `consents`", ops="read"),
        OPS,
        family("consents"),
    ),
    Spec("policy_acceptance", A(self="own", ops="read"), OPS, own("user_id")),
    Spec(
        "teacher_profile",
        A(teacher="own", lead="the teacher of an open assigned case (assigned lead)", ops="read"),
        OPS,
        lambda r, c: (
            {u for u in [c.teacher_user_raw(r["$id"])] if u}
            | set(c.case_leads(teacher_id=r["$id"]))
        ),
    ),
    Spec("teacher_zone", A(teacher="own", ops="read"), OPS, own_teacher()),
    Spec(
        "teacher_document",
        A(
            teacher="own",
            lead="while an open case concerns the teacher (assigned lead)",
            ops="read",
        ),
        OPS,
        lambda r, c: (
            {u for u in [c.teacher_user_raw(r.get("teacher_id"))] if u}
            | set(c.case_leads(teacher_id=r.get("teacher_id")))
        ),
    ),
    Spec("teacher_approval", A(teacher="own", ops="read"), OPS, own_teacher()),
    # ---- teacher application (V01). `teacher_id` is the applicant's USER id (row id = user id),
    # so `own("teacher_id")` applies: owner while the account is not deleted (a suspended or
    # label-less teacher still sees their own application), plus ops admin. Nobody else, in
    # particular no safeguarding lead and no parent. Appwrite permissions are row level, so the
    # private columns (date of birth, current school, referee contacts) are safe only because the
    # row audience is owner + ops; they are never copied into rows with a wider audience
    # (teacher_profile gets gender and radius only, the audit entry carries counts).
    Spec(
        "teacher_application",
        A(
            teacher="own application, also while suspended; nothing once the account is deleted",
            ops="read",
        ),
        OPS,
        own("teacher_id"),
        note="Holds date of birth and current school (private to owner and ops admin).",
    ),
    Spec(
        "teacher_referee",
        A(teacher="own referees (read only)", ops="read (contacts the referees in V04)"),
        OPS,
        own("teacher_id"),
        note="Third-party contact details: owner and ops admin only.",
    ),
    Spec(
        "teacher_application_subject",
        A(teacher="own", ops="read"),
        OPS,
        own("teacher_id"),
    ),
    Spec("availability_slot", A(teacher="own", ops="read"), OPS, own_teacher()),
    Spec("availability_exception", A(teacher="own", ops="read"), OPS, own_teacher()),
    # ---- sessions (M02)
    Spec(
        "plan",
        A(ops="read"),
        OPS,
        note="Plans and prices reach the apps through a core endpoint (owner question 29).",
    ),
    Spec(
        "subscription",
        A(parent="own children; guardian with grant `sessions`", ops="read"),
        OPS,
        family("sessions"),
    ),
    Spec(
        "match_request",
        A(parent="own; guardian with grant `sessions`", ops="read"),
        OPS,
        _subscription_family,
    ),
    Spec(
        "match_offer",
        A(ops="read"),
        OPS,
        note="Carries the ranking breakdown: teachers see offers through a core endpoint.",
    ),
    Spec(
        "assignment",
        A(
            parent="own children; guardian with grant `sessions`",
            teacher="own, while current or upcoming",
            ops="read",
        ),
        OPS,
        lambda r, c: (
            set(c.child_family_users(r.get("child_id"), "sessions")) | set(_teacher_on_child(r, c))
        ),
    ),
    Spec(
        "session",
        A(
            parent="own children; guardian with grant `sessions`",
            teacher="own sessions while the teacher may read the child (substitute: covered session window)",
            lead="sessions of a child with an open assigned case (assigned lead)",
            ops="read",
        ),
        OPS,
        lambda r, c: (
            set(c.child_family_users(r.get("child_id"), "sessions"))
            | set(_teacher_on_child(r, c))
            | set(c.case_leads(child_id=r.get("child_id")))
        ),
    ),
    Spec(
        "session_checkin",
        A(parent="own children; guardian with grant `sessions`", ops="read"),
        OPS,
        _session_fam("sessions"),
    ),
    Spec(
        "session_confirmation",
        A(parent="own children; guardian with grant `sessions`", ops="read"),
        OPS,
        _session_fam("sessions"),
    ),
    Spec(
        "cancellation",
        A(parent="own children; guardian with grant `sessions`", ops="read"),
        OPS,
        _session_fam("sessions"),
    ),
    Spec(
        "dispute",
        A(parent="own children; guardian with grant `sessions`", ops="read"),
        OPS,
        _session_fam("sessions"),
    ),
    # ---- money (M04)
    Spec("tier", A(ops="read"), OPS),
    Spec("tier_history", A(teacher="own", ops="read"), OPS, own_teacher()),
    Spec("bonus_rule", A(ops="read"), OPS),
    Spec(
        "invoice",
        A(parent="own; guardian with grant `billing`", ops="read"),
        OPS,
        family("billing"),
    ),
    Spec(
        "payment",
        A(parent="own; guardian with grant `billing`", ops="read"),
        OPS,
        family("billing"),
    ),
    Spec("earning", A(teacher="own", ops="read"), OPS, own_teacher()),
    Spec("payout_batch", A(ops="read"), OPS),
    Spec("payout", A(teacher="own", ops="read"), OPS, own_teacher()),
    Spec("ledger_account", A(ops="read"), OPS),
    Spec("ledger_transaction", A(ops="read"), OPS),
    Spec("ledger_entry", A(ops="read"), OPS),
    Spec(
        "feedback",
        A(parent="own; guardian with grant `sessions`", ops="read"),
        OPS,
        family("sessions"),
    ),
    # ---- platform (M04)
    Spec(
        "audit_log",
        A(
            ops="read (label)",
            lead="read (label); owner question 12, policy flag `audit_log_lead_read`",
        ),
        _audit_labels,
    ),
    Spec("notification_template", A(ops="read", superadmin="read"), ("opsadmin", "superadmin")),
    Spec("notification", A(self="own notifications only"), (), own("user_id")),
    Spec(
        "notification_preference",
        A(self="own (changes go through the core service)"),
        (),
        own("user_id"),
    ),
    Spec(
        "device_token",
        A(self="own live token rows (revoked rows: nobody)"),
        (),
        lambda r, c: [] if r.get("revoked_at") else own("user_id")(r, c),
    ),
    # ---- records (M03)
    Spec(
        "session_log",
        A(
            parent="own children; guardian with grant `logs`",
            teacher="while the teacher may read the child (assigned incl. substitute window)",
            lead="while an open case concerns the child (assigned lead)",
            ops="read",
        ),
        OPS,
        lambda r, c: (
            set(c.child_family_users(r.get("child_id"), "logs"))
            | set(c.teachers_for_child(r.get("child_id")))
            | set(c.case_leads(child_id=r.get("child_id")))
        ),
    ),
    Spec(
        "session_log_topic",
        A(parent="as the log", teacher="as the log", lead="as the log", ops="read"),
        OPS,
        _log_child_users("logs", True, True),
    ),
    Spec(
        "session_log_struggle",
        A(parent="as the log", teacher="as the log", lead="as the log", ops="read"),
        OPS,
        _log_child_users("logs", True, True),
    ),
    Spec(
        "topic_progress",
        A(parent="own children; guardian with grant `logs`", teacher="while assigned", ops="read"),
        OPS,
        lambda r, c: (
            set(c.child_family_users(r.get("child_id"), "logs"))
            | set(c.teachers_for_child(r.get("child_id")))
        ),
    ),
    Spec(
        "school_upload",
        A(parent="own children; guardian with grant `children`", ops="read"),
        OPS,
        child_family("children"),
    ),
    Spec(
        "handover_summary",
        A(
            parent="own children; guardian with grant `logs`",
            teacher="the recipient teacher while assigned (substitute: covered session window)",
            ops="read",
        ),
        OPS,
        lambda r, c: (
            set(c.child_family_users(r.get("child_id"), "logs")) | set(_teacher_on_child(r, c))
        ),
    ),
    Spec("handover_rating", A(teacher="own ratings", ops="read"), OPS, own_teacher()),
    Spec(
        "teacher_session_history",
        A(
            teacher="own anonymised history (first name, class, date, duration), kept after the assignment ends",
            ops="read",
        ),
        OPS,
        own_teacher(),
    ),
    # ---- assessments (M03)
    Spec(
        "question",
        A(curator="read", ops="read"),
        CUR_OPS,
        note="answer_key is in the row: never readable by parents or teachers.",
    ),
    Spec("assessment_blueprint", A(curator="read", ops="read"), CUR_OPS),
    Spec(
        "assessment",
        A(parent="own children; guardian with grant `children`", ops="read"),
        OPS,
        child_family("children"),
    ),
    Spec(
        "assessment_item",
        A(parent="own children; guardian with grant `children`", ops="read"),
        OPS,
        _assessment_family,
    ),
    # ---- catalogue (M03): published/retired content is readable by every signed-in user
    Spec(
        "curriculum",
        A(everyone="read (published content)", curator="read", ops="read"),
        CUR_OPS,
        public=_always,
    ),
    Spec(
        "catalogue_version",
        A(everyone="published and retired versions", curator="read all", ops="read"),
        CUR_OPS,
        public=_version_published,
    ),
    Spec(
        "level",
        A(everyone="of a published or retired version", curator="read", ops="read"),
        CUR_OPS,
        public=_published_version,
    ),
    Spec("level_equivalence", A(curator="read", ops="read"), CUR_OPS),
    Spec(
        "subject",
        A(everyone="of a published or retired version", curator="read", ops="read"),
        CUR_OPS,
        public=_published_version,
    ),
    Spec(
        "strand",
        A(everyone="of a published or retired version", curator="read", ops="read"),
        CUR_OPS,
        public=_published_version,
    ),
    Spec(
        "topic",
        A(everyone="of a published or retired version", curator="read", ops="read"),
        CUR_OPS,
        public=_published_version,
    ),
    Spec(
        "struggle_tag",
        A(everyone="read (configuration list)", curator="read", ops="read"),
        CUR_OPS,
        public=_always,
    ),
    # ---- safeguarding (M04)
    Spec(
        "safeguarding_case",
        A(
            lead="the assigned lead only (also after close, policy flag lead_keeps_closed_case_record)"
        ),
        _case_labels,
        _case_users,
        note="Reporters never read this row (they read case_status). Ops admin has no read.",
    ),
    Spec(
        "safeguarding_action",
        A(lead="the assigned lead of the case only"),
        _action_labels,
        _action_users,
    ),
    Spec(
        "case_status",
        A(
            reporter="the user who raised the case: reference, status and dates only",
            lead="the assigned lead",
        ),
        (),
        _case_status_users,
    ),
]

SERVICE_ONLY_TABLES: dict[str, str] = {
    "schema_migration": "migration runner bookkeeping",
    "sample_item": "CI sample table",
    "access_window": "permission service bookkeeping (when time-limited access lapses)",
    "teacher_application_claim": "V01 submit/revision claims and rate-limit slots, server only",
}
# Tables owned by tickets that are not on main yet. Decided now so their merge does not leave a
# table without a decision; `test_decisions_cover_every_table` allows these to be absent.
PENDING_SERVICE_ONLY: dict[str, str] = {
    "otp_challenge": "A01 one-time code hashes, server only",
    "travel_time_cache": "X01 zone-pair travel time cache, server only",
    "travel_usage": "X01 Google Routes usage counters, server only",
    "job_run": "F04 job runner state, server only (planned schema)",
    "job_attempt": "F04 job runner attempts, server only (planned schema)",
    "subscription_transition": "B03 subscription state log, server only (ops reads through an endpoint)",
    "billing_credit": "B03 billing credits, server only",
    "billing_payment": "B03 payment flags, server only",
    "billing_claim": "B03 idempotency claims, server only",
    "teacher_schedule_lease": "S01 per-teacher scheduling lease, server only",
    "scheduling_issue": "S01 scheduling problems, server only (admin queue through an endpoint)",
}

SPECS: dict[str, Spec] = {s.table: s for s in _SPECS}
for _t, _why in SERVICE_ONLY_TABLES.items():
    SPECS[_t] = Spec(_t, service_only=True, note=_why)
for _t, _why in PENDING_SERVICE_ONLY.items():
    SPECS[_t] = Spec(_t, service_only=True, note=_why)


def desired_permissions(entity: str, row: Row, ctx: PermissionContext) -> tuple[str, ...]:
    """The exact permission list the row must carry now. `entity` is the table name."""
    spec = SPECS.get(entity)
    if spec is None:
        raise KeyError(f"no permission decision for table {entity!r}: add it to rules.SPECS")
    if spec.service_only or row.get("deleted_at"):
        return ()
    labels = spec.labels(row, ctx) if callable(spec.labels) else spec.labels
    perms = {read_label(label) for label in labels}
    if spec.users is not None:
        perms.update(read_user(u) for u in spec.users(row, ctx) if u)
    if spec.public is not None and spec.public(row, ctx):
        perms.add(READ_USERS)
    return canonical(perms)


# ------------------------------------------------------------------ files (SEC-5, ADR 0012)
FILE_BUCKETS: dict[str, tuple[str, tuple[str, ...]]] = {
    # bucket -> (owning table, columns holding file ids)
    "teacher-docs": ("teacher_document", ("file_id",)),
    "school-reports": ("school_upload", ("file_id",)),
    "question-media": ("question", ("image_file_id", "audio_file_id")),
}


def desired_file_permissions(
    bucket: str, owner_row: Row, ctx: PermissionContext
) -> tuple[str, ...]:
    """Permissions of a file in `bucket`, given the row that references it. Files get the same
    list as their row (ADR 0012), with one exception: media of an APPROVED question is readable
    by signed-in users (an exam image without its stem or answer key is not user data); draft
    media stays with curators and ops admin."""
    if bucket not in FILE_BUCKETS:
        raise KeyError(f"no file permission decision for bucket {bucket!r}")
    table = FILE_BUCKETS[bucket][0]
    if table == "question":
        if owner_row.get("deleted_at"):
            return ()
        perms = {read_label(label) for label in CUR_OPS}
        if owner_row.get("status") == "approved":
            perms.add(READ_USERS)
        return canonical(perms)
    return desired_permissions(table, owner_row, ctx)


# SR 6.3 / issue #40: clients never write money, tier, earning, payout, session status or
# check-in validity. No table has ANY client create/update/delete (the rules above only ever emit
# `read`); this is the explicit list of the protected data, checked against the schema and the fake.
WRITE_PROTECTED: dict[str, tuple[str, ...]] = {
    "money": ("invoice", "payment", "ledger_account", "ledger_transaction", "ledger_entry"),
    "tier": ("tier", "tier_history", "bonus_rule", "teacher_profile"),
    "earning": ("earning",),
    "payout": ("payout_batch", "payout"),
    "session status": ("session", "assignment", "cancellation", "dispute", "session_confirmation"),
    "check-in validity": ("session_checkin",),
    "safeguarding and audit": (
        "safeguarding_case",
        "safeguarding_action",
        "case_status",
        "audit_log",
    ),
    "consent and policy": ("consent_record", "policy_acceptance"),
}
# Tables a client may write: none. (ADR 0012 principle 2 allowed notification_preference and the
# curator catalogue tables; M03/M04 and this ticket route those through the core service instead.)
CLIENT_WRITABLE_TABLES: frozenset[str] = frozenset()
