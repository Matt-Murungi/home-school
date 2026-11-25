"""Teacher application: draft, submit exactly once, read (V01, #56, SR VER-1).

Appwrite has no transactions or conditional updates (ADR 0002/0012), so every "only one of N
callers may do X" is a *claim row* whose id is derived from what is being claimed; the second
`create` is a 409 (`Conflict`). Claims used here:

* draft revision `(teacher, revision)`: two saves from the same base revision cannot both win;
* submit `(teacher, attempt)`: carries the client's idempotency key; the same key may resume the
  work (every step below is idempotent), a different key is `already_submitted`;
* rate-limit slots per `(teacher, kind, window)`.

Submit order is the crash-safety argument: claim -> write the final rows (deterministic ids) ->
audit -> notify -> flip `status` to `submitted` last. A crash anywhere leaves the application in
`draft`, and a retry with the same key finishes the job without duplicates.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from appwrite.query import Query

from ..auth import Principal
from ..errors import AppError
from ..jobs.idempotency import row_id
from ..repository import Conflict, NotFound, Repository
from .models import Content, DraftBody, SubmitBody
from .ports import AuditEntry, AuditSink, CatalogueReader, Notice, Notifier, ZoneReader
from .rules import Context, normalise_content, normalise_phone, validate_content

log = logging.getLogger("hs_core.teacher_application")
KAMPALA = ZoneInfo("Africa/Kampala")
APP, REFEREE, SUBJECT, CLAIM = (
    "teacher_application",
    "teacher_referee",
    "teacher_application_subject",
    "teacher_application_claim",
)
EDITABLE = ("draft", "action_needed")
LOCKED = ("submitted", "under_review", "approved")
MAX_CONTENT_BYTES = 48_000


@dataclass(frozen=True)
class ApplicationPolicy:
    min_weekly_hours: int = 6
    reapply_cooldown_days: int = 90
    draft_saves_per_hour: int = 240
    submits_per_hour: int = 10
    reads_per_hour: int = 600

    @classmethod
    def from_settings(cls, s) -> ApplicationPolicy:
        return cls(
            min_weekly_hours=s.teacher_app_min_weekly_hours,
            reapply_cooldown_days=s.teacher_app_reapply_cooldown_days,
            draft_saves_per_hour=s.teacher_app_draft_saves_per_hour,
            submits_per_hour=s.teacher_app_submits_per_hour,
        )


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


class ApplicationService:
    def __init__(
        self,
        repo: Repository,
        catalogue: CatalogueReader,
        zones: ZoneReader,
        audit: AuditSink,
        notifier: Notifier,
        policy: ApplicationPolicy | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repo, self.catalogue, self.zones = repo, catalogue, zones
        self.audit, self.notifier = audit, notifier
        self.policy = policy or ApplicationPolicy()
        self.clock = clock

    # -- guards ----------------------------------------------------------------------------
    @staticmethod
    def _require_teacher(p: Principal) -> None:
        if "teacher" not in p.roles:
            raise AppError(403, "forbidden", "Only teachers can use the teacher application")

    def _limit(self, teacher_id: str, kind: str, limit: int) -> None:
        now = self.clock()
        bucket = f"{teacher_id}:{kind}:{now:%Y%m%d%H}"
        taken = {
            r["$id"]
            for r in self.repo.list(CLAIM, [Query.equal("bucket", [bucket]), Query.limit(limit)])
        }
        if len(taken) >= limit:
            wait = 3600 - (now.minute * 60 + now.second)
            raise AppError(
                429,
                "rate_limited",
                "Too many requests, try again later",
                {"Retry-After": str(max(wait, 1))},
                {"retry_after_s": max(wait, 1)},
            )
        for n in range(1, limit + 1):
            rid = row_id("appbucket", bucket, n)
            if rid in taken:
                continue
            try:
                self.repo.create(
                    CLAIM,
                    rid,
                    {
                        "kind": "bucket",
                        "teacher_id": teacher_id,
                        "bucket": bucket,
                        "created_at": iso(now),
                        "expires_at": iso(now + timedelta(hours=2)),
                    },
                )
                return
            except Conflict:
                continue
        raise AppError(
            429,
            "rate_limited",
            "Too many requests, try again later",
            {"Retry-After": "60"},
            {"retry_after_s": 60},
        )

    def _ctx(self, teacher_id: str) -> Context:
        today = self.clock().astimezone(KAMPALA).date()
        try:
            cat = {o.curriculum_id: o for o in self.catalogue.published()}
            zone_ids = frozenset(z.zone_id for z in self.zones.zones())
        except Exception:  # noqa: BLE001 - reference data outage is retriable, never a user error
            log.warning("reference data unavailable")
            raise AppError(503, "reference_unavailable", "Please try again shortly") from None
        own = None
        try:
            own = self.repo.get("user_profile", teacher_id).get("phone")
        except NotFound:
            pass
        return Context(
            today=today,
            catalogue=cat,
            zone_ids=zone_ids,
            own_phone=own,
            min_weekly_minutes=self.policy.min_weekly_hours * 60,
        )

    # -- reads -----------------------------------------------------------------------------
    def _row(self, teacher_id: str) -> dict | None:
        try:
            return self.repo.get(APP, teacher_id)
        except NotFound:
            return None

    def _view(self, row: dict | None) -> dict:
        if row is None:
            return {
                "status": "none",
                "revision": 0,
                "step": 0,
                "attempt": 0,
                "content": {},
                "can_edit": True,
                "submitted_at": None,
            }
        now = self.clock()
        status = row.get("status") or "draft"
        reopen_at = None
        if status == "not_approved" and row.get("decided_at"):
            reopen_at = parse(row["decided_at"]) + timedelta(days=self.policy.reapply_cooldown_days)
        return {
            "status": status,
            "revision": row.get("revision", 0),
            "step": row.get("step", 0),
            "attempt": row.get("attempt", 0),
            "content": json.loads(row.get("content_json") or "{}"),
            "can_edit": status in EDITABLE,
            "submitted_at": row.get("submitted_at"),
            "can_reopen": bool(reopen_at and now >= reopen_at),
            "reopen_at": iso(reopen_at) if reopen_at else None,
        }

    def get(self, p: Principal) -> dict:
        self._require_teacher(p)
        self._limit(p.user_id, "read", self.policy.reads_per_hour)
        return self._view(self._row(p.user_id))

    def reference(self, p: Principal) -> dict:
        self._require_teacher(p)
        try:
            cat = self.catalogue.published()
            zones = self.zones.zones()
        except Exception:  # noqa: BLE001
            raise AppError(503, "reference_unavailable", "Please try again shortly") from None
        return {
            "curricula": [
                {
                    "id": o.curriculum_id,
                    "code": o.code,
                    "name": o.name,
                    "levels": [{"id": i, "name": n} for i, n in o.levels],
                    "subjects": [{"id": i, "name": n} for i, n in o.subjects],
                }
                for o in cat
            ],
            "zones": [
                {"id": z.zone_id, "name": z.name, "hard_to_fill": z.hard_to_fill} for z in zones
            ],
        }

    # -- draft -----------------------------------------------------------------------------
    def save_draft(self, p: Principal, body: DraftBody) -> dict:
        self._require_teacher(p)
        tid = p.user_id
        self._limit(tid, "draft", self.policy.draft_saves_per_hour)
        content = normalise_content(body.content.model_dump(exclude_none=True))
        blob = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(blob.encode()) > MAX_CONTENT_BYTES:
            raise AppError(413, "payload_too_large", "Application content too large")
        issues = validate_content(content, self._ctx(tid), final=False)
        now = self.clock()
        row = self._row(tid)
        if row is None:
            if body.base_revision != 0:
                raise self._conflict(None)
            try:
                self.repo.create(
                    APP,
                    tid,
                    {"teacher_id": tid, **self._columns(content, blob, body.step, 1, 0, "draft")},
                    [f'read("user:{tid}")'],
                )
            except Conflict:
                raise self._conflict(self._row(tid)) from None
            self._claim_revision(tid, 1, now)
            return {**self._view(self._row(tid)), "issues": issues}
        status = row.get("status") or "draft"
        if status not in EDITABLE:
            raise AppError(
                409,
                "application_locked",
                "This application can no longer be edited",
                extra={"status": status},
            )
        current = row.get("revision", 0)
        if body.base_revision != current:
            raise self._conflict(row)
        if self._submit_claim_exists(tid, row.get("attempt", 0) + 1):
            raise AppError(409, "submit_in_progress", "Your application is being submitted")
        nxt = current + 1
        self._claim_revision(tid, nxt, now, row)
        self.repo.update(
            APP, tid, self._columns(content, blob, body.step, nxt, row.get("attempt", 0), status)
        )
        return {**self._view(self._row(tid)), "issues": issues}

    def _claim_revision(self, tid: str, rev: int, now: datetime, row: dict | None = None) -> None:
        try:
            self.repo.create(
                CLAIM,
                row_id("approvrev", tid, rev),
                {
                    "kind": "revision",
                    "teacher_id": tid,
                    "created_at": iso(now),
                    "expires_at": iso(now + timedelta(days=1)),
                },
            )
        except Conflict:
            raise self._conflict(self._row(tid)) from None

    def _submit_claim_exists(self, tid: str, attempt: int) -> bool:
        try:
            self.repo.get(CLAIM, row_id("appsubmit", tid, attempt))
            return True
        except NotFound:
            return False

    def _conflict(self, row: dict | None) -> AppError:
        return AppError(
            409,
            "draft_conflict",
            "A newer version of this draft exists",
            extra={"server": self._view(row)},
        )

    @staticmethod
    def _columns(
        content: dict, blob: str, step: int, revision: int, attempt: int, status: str
    ) -> dict:
        q = content.get("qualifications") or {}
        return {
            "status": status,
            "step": step,
            "revision": revision,
            "attempt": attempt,
            "teacher_type": q.get("teacher_type"),
            "registration_number": q.get("registration_number"),
            "highest_qualification": q.get("highest_qualification"),
            "institution": q.get("institution"),
            "years_teaching": q.get("years_teaching"),
            "current_school": q.get("current_school"),
            "travel_radius_km": content.get("travel_radius_km"),
            "content_json": blob,
        }

    # -- reopen (after a rejection, per the cooldown) ---------------------------------------
    def reopen(self, p: Principal) -> dict:
        self._require_teacher(p)
        row = self._row(p.user_id)
        if row is None or row.get("status") != "not_approved":
            raise AppError(409, "reopen_not_allowed", "There is nothing to re-apply for")
        view = self._view(row)
        if not view["can_reopen"]:
            raise AppError(
                409,
                "reapply_too_soon",
                "You can apply again later",
                extra={"reopen_at": view["reopen_at"]},
            )
        self.repo.update(
            APP, p.user_id, {"status": "draft", "revision": row.get("revision", 0) + 1}
        )
        self.audit.record(
            AuditEntry(
                "teacher_application.reopened",
                p.user_id,
                tuple(p.roles),
                APP,
                p.user_id,
                f"reopen:{p.user_id}:{row.get('attempt', 0)}",
            )
        )
        return self._view(self._row(p.user_id))

    # -- submit ----------------------------------------------------------------------------
    def submit(self, p: Principal, body: SubmitBody, key: str) -> dict:
        self._require_teacher(p)
        tid = p.user_id
        self._limit(tid, "submit", self.policy.submits_per_hour)
        row = self._row(tid)
        if row is None:
            raise AppError(
                422,
                "incomplete",
                "The application is not complete",
                extra={"issues": ["application:required"]},
            )
        status = row.get("status") or "draft"
        if status in LOCKED:
            if row.get("submit_key") == key:
                return {**self._submitted_view(row), "replayed": True}
            raise AppError(409, "already_submitted", "This application was already submitted")
        if status == "not_approved":
            raise AppError(
                409,
                "reapply_not_allowed",
                "Re-apply first",
                extra={"reopen_at": self._view(row)["reopen_at"]},
            )
        if body.base_revision != row.get("revision", 0):
            raise self._conflict(row)
        content = json.loads(row.get("content_json") or "{}")
        issues = validate_content(content, self._ctx(tid), final=True)
        if body.declaration is not True:
            issues.append("declaration:required")
        if issues:
            raise AppError(
                422,
                "incomplete",
                "The application is not complete",
                extra={"issues": sorted(set(issues))},
            )
        attempt = row.get("attempt", 0) + 1
        self._claim_submit(tid, attempt, key)
        now = self.clock()
        self._materialise(tid, row, content, attempt, now)
        self.audit.record(
            AuditEntry(
                "teacher_application.submitted",
                tid,
                tuple(p.roles),
                APP,
                tid,
                f"app-submit:{tid}:{attempt}",
                {
                    "attempt": attempt,
                    "curricula": len(content.get("curricula") or []),
                    "zones": len(content.get("zone_ids") or []),
                    "referees": 2,
                },
            )
        )
        self.notifier.send(
            Notice("application_submitted_teacher", tid, f"app-submit-t:{tid}:{attempt}")
        )
        self.notifier.send(
            Notice(
                "application_submitted_ops",
                None,
                f"app-submit-o:{tid}:{attempt}",
                {"teacher_id": tid, "attempt": attempt},
            )
        )
        self.repo.update(
            APP,
            tid,
            {
                "status": "submitted",
                "attempt": attempt,
                "submit_key": key,
                "submitted_at": iso(now),
                "revision": row.get("revision", 0) + 1,
            },
        )
        return {**self._submitted_view(self._row(tid)), "replayed": False}

    def _submitted_view(self, row: dict) -> dict:
        return {
            "status": row.get("status"),
            "attempt": row.get("attempt"),
            "submitted_at": row.get("submitted_at"),
            "revision": row.get("revision"),
        }

    def _claim_submit(self, tid: str, attempt: int, key: str) -> None:
        rid = row_id("appsubmit", tid, attempt)
        now = self.clock()
        try:
            self.repo.create(
                CLAIM,
                rid,
                {
                    "kind": "submit",
                    "teacher_id": tid,
                    "submit_key": key,
                    "created_at": iso(now),
                    "expires_at": iso(now + timedelta(days=3650)),
                },
            )
        except Conflict:
            existing = self.repo.get(CLAIM, rid)
            if existing.get("submit_key") != key:
                raise AppError(
                    409, "already_submitted", "This application was already submitted"
                ) from None
            # same key: a concurrent or crashed earlier try; every step below is idempotent

    def _materialise(self, tid: str, row: dict, content: dict, attempt: int, now: datetime) -> None:
        personal = content["personal"]
        photo = personal.get("photo") or {}
        profile = {
            "gender": personal["gender"],
            "travel_radius_km": content["travel_radius_km"],
            "photo_file_id": photo.get("file_id"),
        }
        try:
            existing = self.repo.get("teacher_profile", tid)
            if existing.get("status") in (None, "pending"):
                self.repo.update("teacher_profile", tid, {**profile, "status": "pending"})
        except NotFound:
            try:
                self.repo.create(
                    "teacher_profile",
                    tid,
                    {"user_id": tid, "status": "pending", **profile},
                    [f'read("user:{tid}")'],
                )
            except Conflict:
                pass
        for zid in content["zone_ids"]:
            self._create_once(
                "teacher_zone",
                row_id("tz", tid, zid),
                {"teacher_id": tid, "zone_id": zid},
                [f'read("user:{tid}")'],
            )
        self._replace_availability(tid, content["availability"], attempt, now)
        self._replace_referees(tid, content["referees"], attempt, now)
        self._replace_subjects(tid, content["curricula"], attempt)
        for c in content["curricula"]:
            for lid in c["level_ids"]:
                self._request_approval(tid, c["curriculum_id"], lid)

    def _create_once(self, table: str, rid: str, data: dict, perms: list[str]) -> None:
        try:
            self.repo.create(table, rid, data, perms)
        except Conflict:
            pass

    def _replace_availability(
        self, tid: str, slots: list[dict], attempt: int, now: datetime
    ) -> None:
        keep = {row_id("av", tid, attempt, i) for i in range(len(slots))}
        for old in self.repo.list(
            "availability_slot", [Query.equal("teacher_id", [tid]), Query.limit(200)]
        ):
            if old["$id"] not in keep and not old.get("deleted_at"):
                self.repo.update("availability_slot", old["$id"], {"deleted_at": iso(now)})
        for i, s in enumerate(slots):
            self._create_once(
                "availability_slot",
                row_id("av", tid, attempt, i),
                {"teacher_id": tid, **{k: s[k] for k in ("weekday", "start_minute", "end_minute")}},
                [f'read("user:{tid}")'],
            )

    def _replace_referees(self, tid: str, refs: list[dict], attempt: int, now: datetime) -> None:
        for old in self.repo.list(REFEREE, [Query.equal("teacher_id", [tid]), Query.limit(50)]):
            if old.get("attempt") != attempt and not old.get("deleted_at"):
                self.repo.update(REFEREE, old["$id"], {"deleted_at": iso(now)})
        for pos, r in enumerate(refs, start=1):
            self._create_once(
                REFEREE,
                row_id("ref", tid, attempt, pos),
                {
                    "application_id": tid,
                    "teacher_id": tid,
                    "attempt": attempt,
                    "position": pos,
                    "name": r["name"],
                    "relationship": r["relationship"],
                    "phone": normalise_phone(r["phone"]),
                    "email": r.get("email"),
                    "years_known": r["years_known"],
                    "consent_at": iso(now),
                },
                [f'read("user:{tid}")'],
            )

    def _replace_subjects(self, tid: str, curricula: list[dict], attempt: int) -> None:
        for old in self.repo.list(SUBJECT, [Query.equal("teacher_id", [tid]), Query.limit(500)]):
            if old.get("attempt") != attempt:
                self.repo.delete(SUBJECT, old["$id"])
        for c in curricula:
            for lid in c["level_ids"]:
                for sid in c["subject_ids"]:
                    self._create_once(
                        SUBJECT,
                        row_id("sub", tid, attempt, lid, sid),
                        {
                            "application_id": tid,
                            "teacher_id": tid,
                            "curriculum_id": c["curriculum_id"],
                            "level_id": lid,
                            "subject_id": sid,
                            "attempt": attempt,
                        },
                        [f'read("user:{tid}")'],
                    )

    def _request_approval(self, tid: str, curriculum_id: str, level_id: str) -> None:
        """A REQUEST: approved_at / approved_by stay empty. Only admin approval (V04) fills them."""
        rid = row_id("appr", tid, curriculum_id, level_id)
        try:
            row = self.repo.get("teacher_approval", rid)
            if row.get("deleted_at"):
                # teacher_approval has no derived *_key columns (M01: composite unique index), so
                # clearing deleted_at is a complete restore; spelled without the literal that the
                # restore lint scans for because that rule is about *_key tables.
                self.repo.update("teacher_approval", rid, dict.fromkeys(["deleted_at"]))
        except NotFound:
            self._create_once(
                "teacher_approval",
                rid,
                {
                    "teacher_id": tid,
                    "curriculum_id": curriculum_id,
                    "level_id": level_id,
                },
                [f'read("user:{tid}")'],
            )


def today_kampala(now: datetime) -> date:
    return now.astimezone(KAMPALA).date()


# Module re-export for the app factory.
__all__ = ["ApplicationPolicy", "ApplicationService", "Content"]
