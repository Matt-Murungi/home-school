"""A small seeded dataset for the permission code: two families, five teachers, staff, cases and
at least one row in every table, on the permission-aware fake. Used by the role-played tests and
by `scripts/permissions_check.py` (the CI drift check). Never imported by application code paths.
Everything is relative to `T0`; the cast is described in docs/ops/permissions.md."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .fake import FakeFileStore, PermissionAwareRepository
from .service import PermissionService, Scope

T0 = datetime(2027, 1, 18, 9, 0, tzinfo=UTC)
H = timedelta(hours=1)


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def iso(dt: datetime) -> str:
    return dt.isoformat()


class SampleWorld:
    def __init__(self, clock=None, repo_cls=PermissionAwareRepository, **service_kw) -> None:
        self.clock = clock or Clock(T0)
        self.repo = repo_cls(clock=self.clock)
        self.files = FakeFileStore()
        self.svc = PermissionService(self.repo, files=self.files, clock=self.clock, **service_kw)

    # ---------------------------------------------------------------- raw rows (server writes)
    def add(self, table: str, rid: str, **data) -> dict:
        return PermissionAwareRepository.create(self.repo, table, rid, data, [])

    def patch(self, table: str, rid: str, **data) -> dict:
        return PermissionAwareRepository.update(self.repo, table, rid, data)

    def perm_writes(self) -> list[tuple[str, str, str]]:
        return [w for w in self.repo.writes if w[0] == "update"]

    def user(self, uid: str, labels: list[str] | None = None, status: str = "active") -> None:
        self.add("user_profile", uid, name=f"User {uid}", status=status)
        for lab in labels or []:
            self.grant_label(uid, lab)

    def grant_label(self, uid: str, label: str) -> None:
        if ("role_assignment" in self.repo._t) and f"ra_{uid}_{label}" in self.repo._t[
            "role_assignment"
        ]:
            self.patch(
                "role_assignment", f"ra_{uid}_{label}", active_key=f"{uid}:{label}", revoked_at=None
            )
            return
        self.add(
            "role_assignment",
            f"ra_{uid}_{label}",
            user_id=uid,
            role=label,
            active_key=f"{uid}:{label}",
            granted_at=iso(self.clock.now),
        )

    def revoke_label(self, uid: str, label: str) -> None:
        self.patch(
            "role_assignment", f"ra_{uid}_{label}", active_key=None, revoked_at=iso(self.clock.now)
        )

    def labels(self, uid: str) -> list[str]:
        return sorted(
            r["role"]
            for r in self.repo.list("role_assignment")
            if r["user_id"] == uid and r.get("active_key")
        )

    # ---------------------------------------------------------------- recompute everything
    def rebuild(self, tables: list[str] | None = None) -> None:
        from hs_core.permissions.rules import SPECS

        for t in tables or sorted(SPECS):
            self.svc.sync_scope(Scope("table", t))
        if tables is None:
            for child in self.repo.list("child"):
                self.svc.sync_scope(Scope("child", child["$id"]))  # also writes access windows

    # ---------------------------------------------------------------- the cast
    def seed(self) -> SampleWorld:
        c, now = self.add, self.clock.now
        users = {
            "uPA": ["parent"],
            "uPB": ["parent"],
            "uGA": ["guardian"],
            "uT1": ["teacher"],
            "uT2": ["teacher"],
            "uT3": ["teacher"],
            "uT4": ["teacher"],
            "uT5": ["teacher"],
            "uOps": ["opsadmin"],
            "uCur": ["curator"],
            "uL1": ["safeguardinglead"],
            "uL2": ["safeguardinglead"],
            "uSup": ["superadmin"],
            "uOL": ["opsadmin", "safeguardinglead"],
            "uNone": [],
        }
        for u, labs in users.items():
            self.user(u, labs)
        c("parent_profile", "PA", user_id="uPA")
        c("parent_profile", "PB", user_id="uPB")
        c(
            "guardian_link",
            "gl1",
            parent_id="PA",
            user_id="uGA",
            status="active",
            grants="logs,children,consents",
        )
        c(
            "guardian_link",
            "gl2",
            parent_id="PA",
            user_id="uNone",
            status="revoked",
            grants="all",
            revoked_at=iso(now - 3 * H),
        )
        c("zone", "z1", name="Kololo")
        c("zone", "z2", name="Ntinda")
        c("home_location", "hlA", parent_id="PA", label="Home")
        c("home_location", "hlB", parent_id="PB", label="Home")
        c(
            "child",
            "a1",
            parent_id="PA",
            name="Amina Nakato",
            date_of_birth="2018-03-01",
            class_level="P3",
        )
        c(
            "child",
            "a2",
            parent_id="PA",
            name="Brian Nakato",
            date_of_birth="2016-05-02",
            class_level="P5",
        )
        c(
            "child",
            "b1",
            parent_id="PB",
            name="Carol Okello",
            date_of_birth="2017-01-01",
            class_level="P4",
        )
        c("consent_record", "crA1", parent_id="PA", child_id="a1")
        c("consent_record", "crB1", parent_id="PB", child_id="b1")
        c("policy_acceptance", "paT1", user_id="uT1")
        c("policy_acceptance", "paT4", user_id="uT4")
        for t, u in (("T1", "uT1"), ("T2", "uT2"), ("T3", "uT3"), ("T4", "uT4"), ("T5", "uT5")):
            c("teacher_profile", t, user_id=u, status="active")
        for t in ("T1", "T4"):
            n = t.lower()
            c("teacher_zone", f"tz_{n}", teacher_id=t, zone_id="z1")
            c("teacher_document", f"td_{n}", teacher_id=t, file_id=f"f_td_{n}", type="cv")
            c("teacher_approval", f"ta_{n}", teacher_id=t)
            c("availability_slot", f"av_{n}", teacher_id=t)
            c("availability_exception", f"ae_{n}", teacher_id=t)
            self.files.put("teacher-docs", f"f_td_{n}", [])
            u = f"u{t}"
            c("teacher_application", f"tapp_{n}", teacher_id=u, status="submitted")
            c("teacher_referee", f"tref_{n}", teacher_id=u, application_id=f"tapp_{n}")
            c("teacher_application_subject", f"tsub_{n}", teacher_id=u, application_id=f"tapp_{n}")
        # sessions domain
        for i in (1, 2):
            c("plan", f"plan{i}", price_ugx=100000 * i)
        c("subscription", "subA", child_id="a1", parent_id="PA")
        c("subscription", "subB", child_id="b1", parent_id="PB")
        c("match_request", "mrA", subscription_id="subA")
        c("match_request", "mrB", subscription_id="subB")
        c("match_offer", "mo1", teacher_id="T1")
        c("match_offer", "mo4", teacher_id="T4")
        c(
            "assignment",
            "asg1",
            child_id="a1",
            teacher_id="T1",
            start_at=iso(now - 30 * 24 * H),
            end_at=None,
        )
        c(
            "assignment",
            "asg2",
            child_id="a2",
            teacher_id="T2",
            start_at=iso(now - 60 * 24 * H),
            end_at=iso(now - 30 * H),
            end_reason="trial_not_continued",
        )
        c(
            "assignment",
            "asg5",
            child_id="b1",
            teacher_id="T5",
            start_at=iso(now - 10 * 24 * H),
            end_at=None,
        )
        c(
            "session",
            "sA1",
            child_id="a1",
            teacher_id="T1",
            assignment_id="asg1",
            status="confirmed",
            scheduled_start_at=iso(now - 50 * H),
            scheduled_end_at=iso(now - 49 * H),
            is_substitute=False,
        )
        c(
            "session",
            "sA2",
            child_id="a2",
            teacher_id="T2",
            assignment_id="asg2",
            status="confirmed",
            scheduled_start_at=iso(now - 32 * H),
            scheduled_end_at=iso(now - 31 * H),
            is_substitute=False,
        )
        c(
            "session",
            "sSub",
            child_id="a1",
            teacher_id="T3",
            assignment_id=None,
            status="scheduled",
            scheduled_start_at=iso(now + 20 * H),
            scheduled_end_at=iso(now + 21 * H),
            is_substitute=True,
        )
        c(
            "session",
            "sB1",
            child_id="b1",
            teacher_id="T5",
            assignment_id="asg5",
            status="confirmed",
            scheduled_start_at=iso(now - 5 * H),
            scheduled_end_at=iso(now - 4 * H),
            is_substitute=False,
        )
        for tbl, pre in (
            ("session_checkin", "ck"),
            ("session_confirmation", "cf"),
            ("cancellation", "cn"),
            ("dispute", "dp"),
        ):
            c(tbl, f"{pre}A1", session_id="sA1")
            c(tbl, f"{pre}B1", session_id="sB1")
        # money
        c("tier", "tr1", name="Standard")
        c("tier", "tr2", name="Senior")
        c("tier_history", "th_t1", teacher_id="T1")
        c("tier_history", "th_t4", teacher_id="T4")
        c("bonus_rule", "br1", name="x")
        c("invoice", "invA", parent_id="PA", subscription_id="subA")
        c("invoice", "invB", parent_id="PB", subscription_id="subB")
        c("payment", "payA", parent_id="PA", invoice_id="invA")
        c("payment", "payB", parent_id="PB", invoice_id="invB")
        c("earning", "e_t1", teacher_id="T1")
        c("earning", "e_t4", teacher_id="T4")
        c("payout_batch", "pb1", status="draft")
        c("payout", "po_t1", teacher_id="T1", batch_id="pb1")
        c("payout", "po_t4", teacher_id="T4", batch_id="pb1")
        for i in (1, 2):
            c("ledger_account", f"la{i}", code=f"acct{i}")
            c("ledger_transaction", f"lt{i}", kind="payment")
            c("ledger_entry", f"le{i}", transaction_id=f"lt{i}", line=1)
        c("feedback", "fbA", parent_id="PA", child_id="a1", teacher_id="T1", session_id="sA1")
        c("feedback", "fbB", parent_id="PB", child_id="b1", teacher_id="T5", session_id="sB1")
        # platform
        c("audit_log", "al1", action="x")
        c("audit_log", "al2", action="y")
        c("notification_template", "nt1", key="k")
        c("notification_template", "nt2", key="k2")
        for u, n in (("uPA", "nfA"), ("uPB", "nfB"), ("uT1", "nfT1")):
            c("notification", n, user_id=u, status="sent")
        for u, n in (("uPA", "npA"), ("uPB", "npB"), ("uT1", "npT1")):
            c("notification_preference", n, user_id=u)
        c("device_token", "dtA", user_id="uPA")
        c("device_token", "dtA2", user_id="uPA", revoked_at=iso(now - H))
        c("device_token", "dtT1", user_id="uT1")
        c("device_token", "dtB", user_id="uPB")
        # records
        c("session_log", "slA1", child_id="a1", teacher_id="T1", session_id="sA1")
        c("session_log", "slA2", child_id="a2", teacher_id="T2", session_id="sA2")
        c("session_log", "slB1", child_id="b1", teacher_id="T5", session_id="sB1")
        for tbl, pre in (("session_log_topic", "slt"), ("session_log_struggle", "sls")):
            c(tbl, f"{pre}A1", session_log_id="slA1")
            c(tbl, f"{pre}B1", session_log_id="slB1")
        c("topic_progress", "tpA1", child_id="a1")
        c("topic_progress", "tpB1", child_id="b1")
        c("school_upload", "suA1", child_id="a1", file_id="f_su_a1")
        c("school_upload", "suB1", child_id="b1", file_id="f_su_b1")
        self.files.put("school-reports", "f_su_a1", [])
        self.files.put("school-reports", "f_su_b1", [])
        c("handover_summary", "hsA1", child_id="a1", teacher_id="T1", assignment_id="asg1")
        c("handover_summary", "hsA3", child_id="a1", teacher_id="T3", session_id="sSub")
        c("handover_summary", "hsB1", child_id="b1", teacher_id="T5", assignment_id="asg5")
        c("handover_rating", "hr1", handover_summary_id="hsA1", teacher_id="T1")
        c("handover_rating", "hr5", handover_summary_id="hsB1", teacher_id="T5")
        c(
            "teacher_session_history",
            "tshT1",
            teacher_id="T1",
            session_id="sA1",
            child_first_name="Amina",
        )
        c(
            "teacher_session_history",
            "tshT2",
            teacher_id="T2",
            session_id="sA2",
            child_first_name="Brian",
        )
        # assessments
        c("question", "qDraft", status="draft", image_file_id="f_q_draft")
        c("question", "qApp", status="approved", image_file_id="f_q_app", audio_file_id=None)
        self.files.put("question-media", "f_q_draft", [])
        self.files.put("question-media", "f_q_app", [])
        c("assessment_blueprint", "bp1", type="practice")
        c("assessment_blueprint", "bp2", type="checkpoint")
        c("assessment", "asmA", child_id="a1")
        c("assessment", "asmB", child_id="b1")
        c("assessment_item", "aiA", assessment_id="asmA")
        c("assessment_item", "aiB", assessment_id="asmB")
        # catalogue
        c("curriculum", "cu1", code="UG")
        c("catalogue_version", "cvPub", curriculum_id="cu1", status="published")
        c("catalogue_version", "cvDraft", curriculum_id="cu1", status="draft")
        for tbl, pre in (("level", "lv"), ("subject", "sb"), ("strand", "st"), ("topic", "tp")):
            c(tbl, f"{pre}Pub", catalogue_version_id="cvPub")
            c(tbl, f"{pre}Draft", catalogue_version_id="cvDraft")
        c("level_equivalence", "leq1", level_id="lvPub", equivalent_level_id="lvDraft")
        c("level_equivalence", "leq2", level_id="lvDraft", equivalent_level_id="lvPub")
        c("struggle_tag", "stg1", label="x")
        # safeguarding
        c(
            "safeguarding_case",
            "cs1",
            child_id="a1",
            teacher_id="T1",
            raised_by_id="uPA",
            assigned_to_id="uL1",
            status="investigating",
        )
        c(
            "safeguarding_case",
            "cs2",
            child_id="b1",
            teacher_id=None,
            raised_by_id="uT5",
            assigned_to_id="uL1",
            status="closed",
        )
        c("safeguarding_action", "saA1", case_id="cs1")
        c("safeguarding_action", "saA2", case_id="cs2")
        c("case_status", "csA1", case_id="cs1", reporter_id="uPA")
        c("case_status", "csB1", case_id="cs2", reporter_id="uT5")
        # service only
        c("schema_migration", "sm1", version=1)
        c("sample_item", "si1", title="x")
        self.rebuild()
        return self
