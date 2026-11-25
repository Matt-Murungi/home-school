"""Interfaces the application service depends on, each with a fake for tests.

* `AuditSink`: M06 (#41, in flight) builds the real audit logger. Until it lands the default sink
  appends to the `audit_log` table (M04) through `create` only. Entries never carry personal data:
  only field names and ids.
* `Notifier`: N01 (#126, in flight) builds the notification service. The default only logs the
  event; the fake records it.
* `CatalogueReader`: K01 (the catalogue read API) is not built. The default reads the published
  M03 tables directly; the app only ever talks to `GET /v1/teacher/application/catalogue`.
* `ZoneReader`: reads the M01 `zone` table.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from appwrite.query import Query

from ..jobs.idempotency import row_id
from ..logging_setup import request_id_var
from ..repository import Conflict, Repository
from .rules import CatalogueOption

log = logging.getLogger("hs_core.teacher_application")


# -- audit ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class AuditEntry:
    action: str
    actor_id: str
    actor_roles: tuple[str, ...]
    entity: str
    entity_id: str
    dedupe_key: str
    detail: dict[str, Any] = field(default_factory=dict)  # ids and field names only, no PII


class AuditSink(Protocol):
    def record(self, entry: AuditEntry) -> None: ...


class FakeAuditSink:
    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []
        self.fail = False
        self._seen: set[str] = set()
        self._lock = threading.Lock()

    def record(self, entry: AuditEntry) -> None:
        if self.fail:
            raise RuntimeError("audit store down")
        with self._lock:
            if entry.dedupe_key in self._seen:
                return
            self._seen.add(entry.dedupe_key)
            self.entries.append(entry)


def build_audit_sink(repo: Repository) -> AuditSink:
    """THE swap point for M06 (#41, not merged yet). `RepositoryAuditSink` writes `audit_log`
    directly with `create`. When M06 merges, return a sink that goes through M06's
    `AuditedRepository` (actor, roles, request id and per-entity PII rules) instead; nothing
    else in this package changes because the service only knows the `AuditSink` protocol."""
    return RepositoryAuditSink(repo)


class RepositoryAuditSink:
    """Append-only: one `create` with an id derived from the dedupe key (a retry is a 409)."""

    def __init__(self, repo: Repository, clock=lambda: datetime.now(UTC)) -> None:
        self._repo, self._clock = repo, clock

    def record(self, e: AuditEntry) -> None:
        import json

        try:
            self._repo.create(
                "audit_log",
                row_id("audit", e.dedupe_key),
                {
                    "actor_id": e.actor_id,
                    "actor_roles": ",".join(e.actor_roles)[:200],
                    "action": e.action,
                    "entity": e.entity,
                    "entity_id": e.entity_id,
                    "after": json.dumps(e.detail, sort_keys=True)[:16000],
                    "request_id": (request_id_var.get() or "")[:36] or None,
                    "occurred_at": self._clock().astimezone(UTC).isoformat(),
                },
            )
        except Conflict:
            return  # already recorded by an earlier try of the same submit


# -- notifications -------------------------------------------------------------------------------
@dataclass(frozen=True)
class Notice:
    template: str  # e.g. application_submitted_teacher, application_submitted_ops
    user_id: str | None  # None = the ops queue
    dedupe_key: str
    data: dict[str, Any] = field(default_factory=dict)


class Notifier(Protocol):
    def send(self, notice: Notice) -> None: ...


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[Notice] = []
        self.fail = False
        self._seen: set[str] = set()
        self._lock = threading.Lock()

    def send(self, notice: Notice) -> None:
        if self.fail:
            raise RuntimeError("notification service down")
        with self._lock:
            if notice.dedupe_key in self._seen:
                return
            self._seen.add(notice.dedupe_key)
            self.sent.append(notice)


# Notice templates of this ticket -> (N01 event key, recipient role). N01's catalogue
# (`hs_core/notifications/catalog.py`) has NO `application_submitted` event yet (PRD matrix gap:
# neither a teacher confirmation nor an ops-queue event exist). Until N01 adds them the adapter
# records the gap instead of inventing events in someone else's catalogue; adding the keys there
# and nothing else here turns the notifications on.
EVENT_FOR_NOTICE: dict[str, tuple[str, str]] = {
    "application_submitted_teacher": ("application_submitted_teacher", "teacher"),
    "application_submitted_ops": ("application_submitted_admin", "admin"),
}


class NotificationServiceNotifier:
    """`Notifier` over N01's `NotificationService`. Never calls a channel itself: the service
    queues and its dispatch job sends (fakes in tests, N02 adapters later)."""

    def __init__(self, service: Any, events: Any | None = None) -> None:
        from ..notifications import catalog

        self._service = service
        self._events = events if events is not None else catalog.EVENTS
        self.gaps: list[str] = []

    def send(self, notice: Notice) -> None:
        from ..notifications.service import Recipient

        key, role = EVENT_FOR_NOTICE.get(notice.template, (None, None))
        if key is None or key not in self._events:
            self.gaps.append(notice.template)
            log.warning("notification event missing", extra={"template": notice.template})
            return
        if notice.user_id is None:  # the ops queue has no single recipient to address yet
            self.gaps.append(notice.template)
            log.warning("no recipient for ops notice", extra={"template": notice.template})
            return
        self._service.send(
            key, Recipient(notice.user_id, role), dict(notice.data), notice.dedupe_key
        )


class LogNotifier:
    def send(self, notice: Notice) -> None:
        log.info("notice", extra={"template": notice.template, "dedupe": notice.dedupe_key})


# -- catalogue and zones -------------------------------------------------------------------------
class CatalogueReader(Protocol):
    def published(self) -> list[CatalogueOption]: ...


class FakeCatalogue:
    def __init__(self, options: list[CatalogueOption] | None = None) -> None:
        self.options = options if options is not None else default_options()
        self.fail = False

    def published(self) -> list[CatalogueOption]:
        if self.fail:
            raise RuntimeError("catalogue down")
        return list(self.options)


def default_options() -> list[CatalogueOption]:
    return [
        CatalogueOption(
            "cur_ug",
            "UG",
            "Ugandan",
            (("lvl_p1", "Primary 1"), ("lvl_p2", "Primary 2"), ("lvl_s1", "Senior 1")),
            (("sub_math", "Mathematics"), ("sub_eng", "English"), ("sub_sci", "Science")),
        ),
        CatalogueOption(
            "cur_cam",
            "CAM",
            "Cambridge",
            (("lvl_cp1", "Stage 1"), ("lvl_cp2", "Stage 2")),
            (("sub_cmath", "Mathematics"), ("sub_cen", "English")),
        ),
        CatalogueOption(
            "cur_us",
            "US",
            "American",
            (("lvl_g1", "Grade 1"), ("lvl_g2", "Grade 2")),
            (("sub_umath", "Math"),),
        ),
    ]


class RepositoryCatalogue:
    """Published catalogue versions only (M03): a draft or retired version is never offered."""

    def __init__(self, repo: Repository) -> None:
        self._repo = repo

    def _all(self, table: str, *queries: str) -> list[dict]:
        return self._repo.list(table, [*queries, Query.limit(500)])

    def published(self) -> list[CatalogueOption]:
        versions = [
            v
            for v in self._all("catalogue_version", Query.equal("status", ["published"]))
            if not v.get("deleted_at")
        ]
        curricula = {c["$id"]: c for c in self._all("curriculum") if not c.get("deleted_at")}
        out = []
        for v in versions:
            cur = curricula.get(v.get("curriculum_id"))
            if cur is None:
                continue
            by_v = Query.equal("catalogue_version_id", [v["$id"]])
            levels = sorted(
                (x for x in self._all("level", by_v) if not x.get("deleted_at")),
                key=lambda x: (x.get("sort_order") or 0, x["name"]),
            )
            subjects = sorted(
                (x for x in self._all("subject", by_v) if not x.get("deleted_at")),
                key=lambda x: x["name"],
            )
            out.append(
                CatalogueOption(
                    cur["$id"],
                    cur["code"],
                    cur["name"],
                    tuple((x["$id"], x["name"]) for x in levels),
                    tuple((x["$id"], x["name"]) for x in subjects),
                )
            )
        return sorted(out, key=lambda o: o.name)


@dataclass(frozen=True)
class ZoneOption:
    zone_id: str
    name: str
    hard_to_fill: bool = False


class ZoneReader(Protocol):
    def zones(self) -> list[ZoneOption]: ...


class FakeZones:
    def __init__(self, zones: list[ZoneOption] | None = None) -> None:
        self._zones = (
            zones
            if zones is not None
            else [
                ZoneOption("zone_ntinda", "Ntinda"),
                ZoneOption("zone_kira", "Kira"),
                ZoneOption("zone_kololo", "Kololo"),
                ZoneOption("zone_muyenga", "Muyenga", True),
            ]
        )

    def zones(self) -> list[ZoneOption]:
        return list(self._zones)


class RepositoryZones:
    def __init__(self, repo: Repository) -> None:
        self._repo = repo

    def zones(self) -> list[ZoneOption]:
        rows = self._repo.list("zone", [Query.limit(500)])
        return sorted(
            (
                ZoneOption(r["$id"], r["name"], bool(r.get("hard_to_fill")))
                for r in rows
                if not r.get("deleted_at")
            ),
            key=lambda z: z.name,
        )
