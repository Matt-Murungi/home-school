"""NotificationService: resolves preferences, priority and quiet hours, records one `notification`
row per channel, dispatches through the channel interfaces and records delivery status.

Row lifecycle (stored status in brackets; the M04 enum is queued, sent, delivered, failed,
fallback, blocked; no schema change):
  queued [queued]            waiting for the first attempt or a retry
  deferred [queued]          queued with scheduled_at in the future because of quiet hours
  sent / delivered / failed  as named
  suppressed [blocked]       muted marketing, rate limit, expired, template error
  fallback [fallback]        push failed for a critical event and an email row took over

Exactly-once: the row id is a hash of (idempotency key, event, recipient, channel); creating it
is the atomic claim (Conflict = already claimed, ADR 0002 style). A worker that dies after the
provider accepted leaves the row `queued` with a lease; a retry after the lease resumes it and
the provider is called with the SAME idempotency key (row id + attempt group), so it dedupes.
In-process, per-key and per-row locks stop two threads working on one row. Across processes,
Appwrite has no conditional update, so the lease and the provider key are the protection.

Nothing here logs variables, tokens, addresses or message text: ids and names only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Protocol

from appwrite.permission import Permission
from appwrite.query import Query
from appwrite.role import Role

from ..jobs.alerts import Alert, AlertSink, LogAlertSink
from ..jobs.idempotency import row_id
from ..repository import Conflict, NotFound, Repository
from . import engine, preferences, quiet_hours
from .catalog import EVENTS, EventDef
from .channels import (
    AddressSuppression,
    ChannelResult,
    EmailChannel,
    EmailSender,
    FailureKind,
    InAppChannel,
    InAppMessage,
    PushChannel,
    PushMessage,
    send_each,
)
from .config import NotificationConfig
from .devices import DeviceRegistry, token_hash
from .errors import (
    InvalidIdempotencyKey,
    NaiveDatetime,
    RecipientMismatch,
    TemplateError,
    UnknownEvent,
)
from .preferences import PreferenceStore
from .templates import (
    Channel,
    Rendered,
    TemplateStore,
    render_template,
)

log = logging.getLogger("hs_core.notifications")
TABLE = "notification"
_EMAIL_RE = re.compile(r"\S+@\S+")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
GENERIC_SAFEGUARDING = Rendered(
    "Safeguarding: action needed",
    "A safeguarding case needs your attention now. Open the Home School app.",
)


def notification_row_id(dedupe_key: str) -> str:
    return row_id(TABLE, dedupe_key)


def notification_owner_id(row: dict) -> str:
    """Owner of a notification row (M05 derives the row's read permission from this)."""
    return row["user_id"]


@dataclass(frozen=True)
class Recipient:
    user_id: str
    role: str  # parent | teacher | admin
    locale: str = "en"


@dataclass(frozen=True)
class NotificationRef:
    id: str
    channel: str
    status: str  # queued | deferred | sent | delivered | failed | fallback | suppressed
    deferred: bool = False


@dataclass(frozen=True)
class SendResult:
    notifications: tuple[NotificationRef, ...]
    duplicate: bool = False


class ContactDirectory(Protocol):
    def email_for(self, user_id: str) -> str | None: ...


class NoDirectory:
    def email_for(self, user_id: str) -> str | None:
        return None


class FakeContactDirectory:
    def __init__(self) -> None:
        self._email: dict[str, str] = {}

    def set_email(self, user_id: str, email: str) -> None:
        self._email[user_id] = email

    def email_for(self, user_id: str) -> str | None:
        return self._email.get(user_id)


class EscalationHooks(Protocol):
    """Safeguarding acknowledgement and escalation (interface only; the G tickets wire it).
    `arm` must be idempotent per `group_id`."""

    def arm(self, *, group_id: str, lead_id: str, backup_id: str, after: timedelta) -> None: ...

    def acknowledge(self, group_id: str, user_id: str) -> None: ...


class NullEscalation:
    def arm(self, *, group_id: str, lead_id: str, backup_id: str, after: timedelta) -> None:
        log.warning("safeguarding escalation not wired", extra={"group": group_id})

    def acknowledge(self, group_id: str, user_id: str) -> None:
        pass


class FakeEscalation:
    def __init__(self) -> None:
        self.armed: list[dict[str, Any]] = []
        self.acknowledged: list[tuple[str, str]] = []

    def arm(self, *, group_id: str, lead_id: str, backup_id: str, after: timedelta) -> None:
        if all(a["group_id"] != group_id for a in self.armed):
            self.armed.append(
                {"group_id": group_id, "lead_id": lead_id, "backup_id": backup_id, "after": after}
            )

    def acknowledge(self, group_id: str, user_id: str) -> None:
        self.acknowledged.append((group_id, user_id))


def _transient_error(result: ChannelResult) -> str:
    """`transient`, or `transient:exception:<TypeName>` when the provider call raised."""
    return f"transient:{result.detail}" if result.detail.startswith("exception:") else "transient"


def _iso(d: datetime) -> str:
    return d.astimezone(UTC).isoformat()


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def public_status(row: dict) -> str:
    st = row["status"]
    if st == "blocked":
        return "expired" if row.get("last_error") == "suppressed:expired" else "suppressed"
    if st == "queued" and _meta(row).get("deferred"):
        return "deferred"
    return st


def _load(row: dict) -> dict:
    try:
        d = json.loads(row.get("payload") or "{}")
    except ValueError:
        d = {}
    d.setdefault("vars", {})
    d.setdefault("meta", {})
    return d


def _meta(row: dict) -> dict:
    return _load(row)["meta"]


def is_renderable(row: dict) -> bool:
    """Can `render_notification` show this row? False once the retention hook has removed the
    variables (`payload_expired`), for unreadable payloads and for soft-deleted rows. The inbox
    API must skip (or show a neutral line for) rows where this is False."""
    if row.get("deleted_at") or not row.get("payload"):
        return False
    try:
        d = json.loads(row["payload"])
    except ValueError:
        return False
    if not isinstance(d, dict):
        return False
    meta = d.get("meta") or {}
    return not meta.get("payload_expired")


def _encode_vars(values: dict[str, Any], specs: dict[str, engine.VarSpec]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for n, v in values.items():
        out[n] = v.isoformat() if specs[n].type in ("datetime", "date") else v
    return out


def _decode_vars(values: dict[str, Any], specs: dict[str, engine.VarSpec]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for n, v in values.items():
        t = specs[n].type if n in specs else "str"
        out[n] = (
            datetime.fromisoformat(v)
            if t == "datetime"
            else (date.fromisoformat(v) if t == "date" else v)
        )
    return out


class _Locks:
    def __init__(self, n: int = 256) -> None:
        self._l = [threading.RLock() for _ in range(n)]

    def __call__(self, key: str) -> threading.RLock:
        return self._l[int(hashlib.sha1(key.encode()).hexdigest()[:8], 16) % len(self._l)]


class NotificationService:
    def __init__(
        self,
        repo: Repository,
        *,
        store: TemplateStore,
        push: PushChannel,
        email_sender: EmailSender,
        in_app: InAppChannel,
        directory: ContactDirectory,
        devices: DeviceRegistry,
        preferences: PreferenceStore,
        config: NotificationConfig | None = None,
        clock: Callable[[], datetime] | None = None,
        alerts: AlertSink | None = None,
        escalation: EscalationHooks | None = None,
        suppression: AddressSuppression | None = None,
        email_from: str = "",
        email_reply_to: str | None = None,
    ) -> None:
        self.repo = repo
        self.store = store
        self.push = push
        self.email = EmailChannel(email_sender, email_from, email_reply_to, suppression)
        self.in_app = in_app
        self.directory = directory
        self.devices = devices
        self.prefs = preferences
        self.config = config or NotificationConfig()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.alerts = alerts or LogAlertSink()
        self.escalation = escalation or NullEscalation()
        self._send_locks = _Locks()
        self._row_locks = _Locks()

    def _now(self) -> datetime:
        return self.clock().astimezone(UTC)

    # ======================================================================== public API
    def send(
        self,
        event_key: str,
        recipient: Recipient,
        variables: dict[str, Any],
        idempotency_key: str,
        *,
        deep_link: str | None = None,
        not_after: datetime | None = None,
        subject_id: str | None = None,
    ) -> SendResult:
        """`subject_id`: what the notification is about (child, session or invoice id). It is
        part of the burst-limit key so that several events about different children are not
        suppressed as one burst; callers of per-subject events SHOULD pass it. `not_after`
        must be timezone-aware; when omitted the event's default lifetime applies."""
        ev = EVENTS.get(event_key)
        if ev is None:
            raise UnknownEvent(event_key)
        if (
            not idempotency_key
            or len(idempotency_key) > 120
            or _CONTROL.search(idempotency_key)
            or idempotency_key != idempotency_key.strip()
        ):
            raise InvalidIdempotencyKey("idempotency key must be 1-120 printable characters")
        if recipient.role != ev.role:
            raise RecipientMismatch(f"{event_key} is for {ev.role}")
        if deep_link is not None and (
            not deep_link.startswith("homeschool://")
            or len(deep_link) > 500
            or _CONTROL.search(deep_link)
        ):
            raise ValueError("deep link must be a homeschool:// link of at most 500 characters")
        if not_after is not None and not_after.utcoffset() is None:
            raise NaiveDatetime("not_after must be timezone-aware (UTC or Africa/Kampala)")
        if subject_id is not None and (len(subject_id) > 64 or _CONTROL.search(subject_id)):
            raise ValueError("subject_id must be at most 64 printable characters")
        now = self._now()
        with self._send_locks(f"{idempotency_key}|{recipient.user_id}"):
            try:
                specs = {
                    ch: self.store.get_active(ev.key, ch, recipient.locale) for ch in ev.channels
                }
                for spec in specs.values():
                    render_template(spec, variables)  # fail before any row exists
            except TemplateError as e:
                return self._template_failure(ev, recipient, variables, idempotency_key, e, now)
            refs, dup = [], False
            for ch in ev.channels:
                ref, existed = self._enqueue(
                    ev,
                    recipient,
                    ch,
                    specs[ch],
                    variables,
                    idempotency_key,
                    deep_link,
                    not_after,
                    now,
                    subject_id,
                )
                refs.append(ref)
                dup = dup or existed
            return SendResult(tuple(refs), dup)

    def send_safeguarding(
        self,
        event_key: str,
        lead: Recipient,
        backup: Recipient | None,
        variables: dict[str, Any],
        idempotency_key: str,
    ) -> SendResult:
        """Push AND email at once to the lead and the backup, then arm the escalation hook."""
        ev = EVENTS.get(event_key)
        if ev is None:
            raise UnknownEvent(event_key)
        if ev.category != "safeguarding":
            raise ValueError("not a safeguarding event")
        results = [self.send(event_key, lead, variables, idempotency_key)]
        if backup is not None and backup.user_id != lead.user_id:
            results.append(self.send(event_key, backup, variables, idempotency_key))
            self.escalation.arm(
                group_id=row_id("safeguarding", idempotency_key),
                lead_id=lead.user_id,
                backup_id=backup.user_id,
                after=timedelta(minutes=self.config.escalate_after_min),
            )
        else:
            self._alert(
                "safeguarding_no_backup",
                row_id("sg-nobackup", idempotency_key),
                "no backup recipient for a safeguarding notification",
            )
        return SendResult(
            tuple(n for r in results for n in r.notifications),
            any(r.duplicate for r in results),
        )

    def acknowledge(self, group_id: str, user_id: str) -> None:
        self.escalation.acknowledge(group_id, user_id)

    def record_delivery(
        self, notification_id: str, kind: str, occurred_at: datetime, reason: str = ""
    ) -> None:
        """Provider receipt (email webhook, push receipt). Idempotent and order-safe: a later
        `delivered` never reopens a row that failed."""
        if kind not in (
            "delivered",
            "bounced",
            "bounced_soft",
            "complained",
            "failed",
            "suppressed",
        ):
            raise ValueError(f"unknown delivery event {kind!r}")
        with self._row_locks(notification_id):
            row = self.repo.get(TABLE, notification_id)
            payload = _load(row)
            meta, status, patch = payload["meta"], row["status"], {}
            at = _iso(occurred_at)
            if kind == "delivered":
                if status == "sent":
                    patch["status"] = "delivered"
            elif kind == "bounced":
                if status in ("sent", "delivered"):
                    patch["status"] = "failed"
                    patch["last_error"] = "bounced:" + _EMAIL_RE.sub("[address]", reason)[:120]
                    self._maybe_alert_failed(row)
            elif kind in ("failed", "suppressed"):
                # the provider did not send it (our request failed late, or the address is on the
                # provider's suppression list): not delivered
                if status in ("sent", "delivered"):
                    patch["status"] = "failed"
                    patch["last_error"] = f"provider_{kind}"
                    self._maybe_alert_failed(row)
            elif kind == "bounced_soft":
                soft = meta.setdefault("soft_bounces", [])
                if at not in soft:
                    soft.append(at)
            else:
                meta["complained"] = True
            if patch or payload["meta"] != _load(row)["meta"]:
                patch["payload"] = json.dumps(payload)
                self.repo.update(TABLE, notification_id, patch)
        log.info("delivery event", extra={"notification_id": notification_id, "event": kind})

    def dispatch_due(self, now: datetime | None = None, page: int = 40) -> int:
        """Send queued rows whose time has come (deferred by quiet hours, or retries). Chunked
        and idempotent: processed rows leave the queue; rows under a live lease are skipped."""
        now = (now or self._now()).astimezone(UTC)
        rows = self.repo.list(
            TABLE,
            [
                Query.equal("status", ["queued"]),
                Query.less_than_equal("scheduled_at", _iso(now)),
                Query.order_asc("scheduled_at"),
                Query.limit(page),
            ],
        )
        done = 0
        for r in rows:
            try:  # one failing row must not block the rows behind it
                done += bool(self._dispatch(r["$id"], now, owner=False))
            except Exception as e:  # noqa: BLE001 - BaseException still propagates
                self._log_row_error(r["$id"], e)
        return done

    @staticmethod
    def _log_row_error(rid: str, exc: Exception) -> None:
        log.error(
            "notification dispatch failed for one row",
            extra={"notification_id": rid, "error_type": type(exc).__name__},
        )

    def render_notification(self, row: dict) -> Rendered:
        """The inbox text of a row, from the exact template version it was created with."""
        payload = _load(row)
        spec = self.store.get_version(
            row["template_key"],
            Channel(row["channel"]),
            payload["meta"].get("locale", "en"),
            row["template_version"],
        )
        return render_template(spec, _decode_vars(payload["vars"], spec.specs))

    # ======================================================================== enqueue
    def _dedupe_key(self, idem: str, event_key: str, user_id: str, channel: Channel) -> str:
        raw = "\x1f".join((idem, event_key, user_id, channel.value))
        return hashlib.sha256(raw.encode()).hexdigest()[:40]

    @staticmethod
    def _default_not_after(
        ev: EventDef, variables: dict[str, Any], now: datetime
    ) -> datetime | None:
        """The event's default lifetime: `ttl_minutes` from now, cut short at the session start
        (`expires_at_var`) when that variable is a timezone-aware datetime."""
        if ev.ttl_minutes is None:
            return None
        limit = now + timedelta(minutes=ev.ttl_minutes)
        at = variables.get(ev.expires_at_var) if ev.expires_at_var else None
        if isinstance(at, datetime) and at.utcoffset() is not None:
            limit = min(limit, at.astimezone(UTC))
        return limit

    def _enqueue(
        self, ev, recipient, channel, spec, variables, idem, deep_link, not_after, now, subject=None
    ):
        dedupe = self._dedupe_key(idem, ev.key, recipient.user_id, channel)
        rid = notification_row_id(dedupe)
        existing = self._get(rid)
        if existing is not None:
            return self._resume(existing, now), True
        reason = preferences.blocked_reason(
            ev.category, channel.value, self.prefs.get(recipient.user_id)
        )
        if (
            reason is None
            and ev.priority != "critical"
            and self._over_rate_limit(recipient.user_id, ev.key, channel, now, subject or "")
        ):
            reason = "rate_limited"
        meta: dict[str, Any] = {"locale": spec.locale, "priority": ev.priority, "group": 0}
        if subject:
            meta["subject"] = subject
        scheduled = now
        if reason is None and self._defers(ev, channel, now):
            scheduled = quiet_hours.next_allowed(
                now, self.config.quiet_start_hour, self.config.quiet_end_hour
            )
            meta["deferred"] = True
        # The default lifetime runs from the first moment the row may be sent (after quiet hours),
        # so an overnight deferral alone never expires it; the session start still does.
        not_after = not_after or self._default_not_after(ev, variables, scheduled)
        if not_after is not None:
            meta["not_after"] = _iso(not_after)
        if reason is None and not meta.get("deferred"):
            meta["lease_until"] = _iso(now + timedelta(seconds=self.config.lease_s))
        row = self._new_row(
            rid,
            dedupe,
            ev,
            recipient.user_id,
            channel,
            spec,
            variables,
            deep_link,
            meta,
            scheduled,
            now,
            status="blocked" if reason else "queued",
            last_error=f"suppressed:{reason}" if reason else None,
        )
        if row is None:  # lost the claim race: someone created it between our get and create
            return self._resume(self.repo.get(TABLE, rid), now), True
        if reason is None and scheduled <= now:
            self._dispatch(rid, now, owner=True)
        final = self.repo.get(TABLE, rid)
        return self._ref(final), False

    def _new_row(
        self,
        rid,
        dedupe,
        ev,
        user_id,
        channel,
        spec,
        variables,
        deep_link,
        meta,
        scheduled,
        now,
        *,
        status,
        last_error,
    ):
        data = {
            "user_id": user_id,
            "channel": channel.value,
            "template_key": ev.key,
            "template_version": spec.version,
            "category": ev.category,
            "payload": json.dumps({"vars": _encode_vars(variables, spec.specs), "meta": meta}),
            "deep_link": deep_link,
            "status": status,
            "attempts": 0,
            "last_error": last_error,
            "dedupe_key": dedupe,
            "scheduled_at": _iso(scheduled),
            "sent_at": None,
            "created_at": _iso(now),
        }
        try:
            return self.repo.create(TABLE, rid, data, [Permission.read(Role.user(user_id))])
        except Conflict:
            return None

    def _defers(self, ev: EventDef | None, channel: Channel, now: datetime) -> bool:
        if channel is not Channel.PUSH:
            return False
        if ev is not None and (ev.priority == "critical" or ev.urgent):
            return False
        return quiet_hours.is_quiet(now, self.config.quiet_start_hour, self.config.quiet_end_hour)

    def _over_rate_limit(
        self, user_id: str, key: str, channel: Channel, now: datetime, subject: str = ""
    ) -> bool:
        """Rows of this user in the window with the same template, channel and subject. Every row
        in the window is counted (cursor paging, 100 per page), so a very busy user is not
        undercounted. Still approximate under concurrent sends (no atomic counter)."""
        since = _iso(now - timedelta(seconds=self.config.rate_window_s))
        base = [
            Query.equal("user_id", [user_id]),
            Query.greater_than_equal("created_at", since),
            Query.order_asc("created_at"),
            Query.limit(100),
        ]
        n, cursor = 0, None
        while True:
            rows = self.repo.list(TABLE, base + ([Query.cursor_after(cursor)] if cursor else []))
            for r in rows:
                if (
                    r["template_key"] == key
                    and r["channel"] == channel.value
                    and r["status"] != "blocked"
                    and _meta(r).get("subject", "") == subject
                ):
                    n += 1
                    if n >= self.config.rate_limit:
                        return True
            if len(rows) < 100:
                return False
            cursor = rows[-1]["$id"]

    def _get(self, rid: str) -> dict | None:
        try:
            return self.repo.get(TABLE, rid)
        except NotFound:
            return None

    def _resume(self, row: dict, now: datetime) -> NotificationRef:
        """An existing row for this key: finish it if a worker died holding it (lease expired)."""
        if row["status"] == "queued":
            try:
                self._dispatch(row["$id"], now, owner=False)
            except Exception as e:  # noqa: BLE001 - a retry must not raise provider/store errors
                self._log_row_error(row["$id"], e)
            row = self.repo.get(TABLE, row["$id"])
        return self._ref(row)

    @staticmethod
    def _ref(row: dict) -> NotificationRef:
        st = public_status(row)
        return NotificationRef(row["$id"], row["channel"], st, st == "deferred")

    # ======================================================================== dispatch
    def _dispatch(self, rid: str, now: datetime, *, owner: bool) -> bool:
        """One attempt for one row. Returns True when it did something."""
        with self._row_locks(rid):
            row = self._get(rid)
            if row is None or row["status"] != "queued" or _dt(row["scheduled_at"]) > now:
                return False
            payload = _load(row)
            meta = payload["meta"]
            lease = meta.get("lease_until")
            if not owner and lease and _dt(lease) > now:
                return False  # another worker is on it
            channel = Channel(row["channel"])
            ev = EVENTS.get(row["template_key"])
            if meta.get("not_after") and _dt(meta["not_after"]) < now:
                return self._block(rid, row, payload, "expired")
            reason = preferences.blocked_reason(
                row["category"], channel.value, self.prefs.get(row["user_id"])
            )
            if reason:
                return self._block(rid, row, payload, reason)
            if self._defers(ev, channel, now):  # e.g. a retry that landed in quiet hours
                meta["deferred"] = True
                meta.pop("lease_until", None)
                again = quiet_hours.next_allowed(
                    now, self.config.quiet_start_hour, self.config.quiet_end_hour
                )
                self.repo.update(
                    TABLE, rid, {"scheduled_at": _iso(again), "payload": json.dumps(payload)}
                )
                return True
            meta.pop("deferred", None)
            meta["lease_until"] = _iso(now + timedelta(seconds=self.config.lease_s))
            attempts = row["attempts"] + 1
            self.repo.update(TABLE, rid, {"attempts": attempts, "payload": json.dumps(payload)})
            result = self._deliver(rid, row, payload, channel)
            self._finish(rid, row, payload, channel, ev, result, attempts, now)
            return True

    def _block(self, rid: str, row: dict, payload: dict, reason: str) -> bool:
        payload["meta"].pop("lease_until", None)
        self.repo.update(
            TABLE,
            rid,
            {
                "status": "blocked",
                "last_error": f"suppressed:{reason}",
                "payload": json.dumps(payload),
            },
        )
        return True

    def _rendered(self, row: dict, payload: dict) -> Rendered:
        if payload["meta"].get("generic"):
            return (
                GENERIC_SAFEGUARDING
                if row["channel"] != "email"
                else Rendered(
                    GENERIC_SAFEGUARDING.title,
                    GENERIC_SAFEGUARDING.body,
                    "<p>" + engine.escape_html(GENERIC_SAFEGUARDING.body) + "</p>",
                )
            )
        return self.render_notification(row)

    def _deliver(self, rid: str, row: dict, payload: dict, channel: Channel) -> ChannelResult:
        """Render and hand to the channel. NEVER raises an `Exception`: whatever a provider (or
        the renderer) throws becomes a TRANSIENT failure that carries the exception TYPE NAME
        only (the message may hold tokens, addresses or URLs). BaseException (KeyboardInterrupt,
        SystemExit, a killed worker) is not caught on purpose."""
        try:
            rendered = self._rendered(row, payload)
        except TemplateError as e:
            self._alert(
                "notification_template_error",
                f"{row['template_key']}:{e.code}",
                f"render:{e.code}:{','.join(e.names)}",
            )
            return ChannelResult(False, FailureKind.PERMANENT, detail=f"template_{e.code}")
        except Exception as e:  # noqa: BLE001 - see docstring
            return self._raised(rid, channel, e)
        try:
            return self._send_on_channel(rid, row, payload, channel, rendered)
        except Exception as e:  # noqa: BLE001 - see docstring
            return self._raised(rid, channel, e)

    def _raised(self, rid: str, channel: Channel, exc: Exception) -> ChannelResult:
        name = type(exc).__name__
        log.warning(
            "notification channel raised",
            extra={"notification_id": rid, "channel": channel.value, "error_type": name},
        )
        return ChannelResult(False, FailureKind.TRANSIENT, detail=f"exception:{name}")

    def _send_on_channel(
        self, rid: str, row: dict, payload: dict, channel: Channel, rendered: Rendered
    ) -> ChannelResult:
        group = payload["meta"].get("group", 0)
        uid = row["user_id"]
        if channel is Channel.PUSH:
            return self._deliver_push(rid, row, rendered, group, payload["meta"])
        if channel is Channel.EMAIL:
            to = self.directory.email_for(uid)
            if not to:
                return ChannelResult(False, FailureKind.PERMANENT, detail="no_email")
            return self.email.send(
                to,
                rendered.title,
                rendered.body,
                rendered.html or "",
                {"template": row["template_key"], "notification_id": rid},
                f"{rid}:{group}",
            )
        return self.in_app.send(
            InAppMessage(uid, rendered.title, rendered.body, row.get("deep_link"), f"{rid}:{group}")
        )

    def _deliver_push(
        self, rid: str, row: dict, rendered: Rendered, group: int, meta: dict
    ) -> ChannelResult:
        targets = self.devices.active_tokens(row["user_id"])
        data = {"notification_id": rid}
        if row.get("deep_link"):
            data["deep_link"] = row["deep_link"]
        priority = meta.get("priority", "operational")
        ttl_s = None
        if meta.get("not_after"):
            ttl_s = max(0, int((_dt(meta["not_after"]) - self._now()).total_seconds()))
        messages = [
            PushMessage(
                t.token,
                rendered.title,
                rendered.body,
                data,
                f"{rid}:{group}:{token_hash(t.token)[:12]}",
                priority=priority,
                ttl_s=ttl_s,
                channel=f"hs_{priority}",
            )
            for t in targets
        ]
        results = send_each(self.push, messages) if messages else []
        accepted: ChannelResult | None = None
        transient = False
        transient_detail = "transient"
        retry_after: float | None = None
        unregistered = 0
        for t, res in zip(targets, results, strict=True):
            if res.accepted:
                accepted = accepted or res
            elif res.failure is FailureKind.UNREGISTERED:
                unregistered += 1
                self.devices.revoke_invalid(t.row_id)
            elif res.failure is FailureKind.TRANSIENT:
                transient = True
                if res.detail.startswith("exception:"):
                    transient_detail = res.detail
                if res.retry_after_s is not None:
                    retry_after = max(retry_after or 0.0, res.retry_after_s)
        if accepted:
            return accepted
        if transient:
            return ChannelResult(
                False, FailureKind.TRANSIENT, detail=transient_detail, retry_after_s=retry_after
            )
        gone = unregistered == len(targets)  # includes "no device registered"
        return ChannelResult(
            False, FailureKind.PERMANENT, detail="no_device" if gone else "permanent"
        )

    def _finish(self, rid, row, payload, channel, ev, result, attempts, now) -> None:
        meta = payload["meta"]
        meta.pop("lease_until", None)
        if result.accepted:
            if result.provider_message_id:
                meta["provider_id"] = result.provider_message_id
            self.repo.update(
                TABLE,
                rid,
                {
                    "status": "delivered" if channel is Channel.IN_APP else "sent",
                    "sent_at": _iso(now),
                    "last_error": None,
                    "payload": json.dumps(payload),
                },
            )
            log.info(
                "notification sent",
                extra={
                    "notification_id": rid,
                    "channel": channel.value,
                    "template": row["template_key"],
                },
            )
            return
        transient = result.failure is FailureKind.TRANSIENT
        if transient and attempts < self.config.max_attempts:
            meta["group"] = meta.get("group", 0) + 1
            wait = self.config.retry_base_s * 2 ** (attempts - 1)
            if result.retry_after_s:  # provider back-pressure (already clamped by the adapter)
                wait = max(wait, result.retry_after_s)
            self.repo.update(
                TABLE,
                rid,
                {
                    "status": "queued",
                    "last_error": _transient_error(result),
                    "scheduled_at": _iso(now + timedelta(seconds=wait)),
                    "payload": json.dumps(payload),
                },
            )
            log.warning(
                "notification attempt failed, will retry",
                extra={"notification_id": rid, "attempt": attempts, "detail": result.detail[:60]},
            )
            return
        error = ("permanent:" + result.detail) if not transient else "transient_exhausted"
        status = "failed"
        fallback = (
            channel is Channel.PUSH
            and ev is not None
            and ev.fallback_email
            and Channel.EMAIL not in ev.channels
        )
        if fallback:
            status = "fallback"
        self.repo.update(
            TABLE,
            rid,
            {"status": status, "last_error": error[:500], "payload": json.dumps(payload)},
        )
        log.warning(
            "notification failed",
            extra={"notification_id": rid, "error": error[:80], "channel": channel.value},
        )
        if fallback:
            self._fallback_email(row, payload, now)
        else:
            self._maybe_alert_failed({**row, "status": "failed"})

    def _fallback_email(self, push_row: dict, payload: dict, now: datetime) -> None:
        ev = EVENTS[push_row["template_key"]]
        dedupe = push_row["dedupe_key"] + ":fallback"
        rid = notification_row_id(dedupe)
        if self._get(rid) is not None:
            return
        spec = self.store.get_active(ev.key, Channel.EMAIL, payload["meta"].get("locale", "en"))
        meta = {
            "locale": spec.locale,
            "priority": ev.priority,
            "group": 0,
            "fallback_of": push_row["$id"],
            "lease_until": _iso(now + timedelta(seconds=self.config.lease_s)),
        }
        if payload["meta"].get("generic"):
            meta["generic"] = True
        values = _decode_vars(payload["vars"], spec.specs)
        row = self._new_row(
            rid,
            dedupe,
            ev,
            push_row["user_id"],
            Channel.EMAIL,
            spec,
            values,
            push_row.get("deep_link"),
            meta,
            now,
            now,
            status="queued",
            last_error=None,
        )
        if row is not None:
            self._dispatch(rid, now, owner=True)

    def _maybe_alert_failed(self, row: dict) -> None:
        ev = EVENTS.get(row["template_key"])
        if ev is None or ev.priority != "critical":
            return
        if row["channel"] != "push" or ev.category == "safeguarding":
            self._alert(
                "notification_delivery_failed",
                row["$id"],
                f"{row['template_key']} {row['channel']}",
            )

    # ======================================================================== failures
    def _template_failure(self, ev, recipient, variables, idem, err, now) -> SendResult:
        names = ",".join(err.names)
        log.error(
            "notification blocked: template error",
            extra={"template": ev.key, "error_code": err.code, "variables": names},
        )
        self._alert("notification_template_error", f"{ev.key}:{err.code}", f"{err.code}:{names}")
        refs = []
        for ch in ev.channels:
            rid = uuid.uuid4().hex[:36]
            spec = self._any_spec(ev, ch, recipient.locale)
            self.repo.create(
                TABLE,
                rid,
                {
                    "user_id": recipient.user_id,
                    "channel": ch.value,
                    "template_key": ev.key,
                    "template_version": spec,
                    "category": ev.category,
                    "payload": json.dumps({"vars": {}, "meta": {"blocked": err.code}}),
                    "status": "blocked",
                    "attempts": 0,
                    "last_error": f"template:{err.code}:{names}"[:500],
                    "scheduled_at": _iso(now),
                    "created_at": _iso(now),
                },
                [Permission.read(Role.user(recipient.user_id))],
            )
            refs.append(NotificationRef(rid, ch.value, "suppressed"))
        if ev.category == "safeguarding":
            refs += list(self._send_generic(ev, recipient, idem, now))
            return SendResult(tuple(refs))
        raise err

    def _any_spec(self, ev: EventDef, ch: Channel, locale: str) -> int:
        try:
            return self.store.get_active(ev.key, ch, locale).version
        except TemplateError:
            return 1

    def _send_generic(self, ev, recipient, idem, now):
        """A broken safeguarding template must never stop the alert: send the built-in text."""
        out = []
        for ch in ev.channels:
            dedupe = self._dedupe_key(idem, ev.key, recipient.user_id, ch) + "g"
            rid = notification_row_id(dedupe)
            if self._get(rid) is not None:
                continue
            meta = {
                "locale": "en",
                "priority": "critical",
                "group": 0,
                "generic": True,
                "lease_until": _iso(now + timedelta(seconds=self.config.lease_s)),
            }
            row = self._new_row(
                rid,
                dedupe,
                ev,
                recipient.user_id,
                ch,
                SimpleNamespace(version=self._any_spec(ev, ch, "en"), specs={}),
                {},
                None,
                meta,
                now,
                now,
                status="queued",
                last_error=None,
            )
            if row is not None:
                self._dispatch(rid, now, owner=True)
            out.append(self._ref(self.repo.get(TABLE, rid)))
        return out

    def _alert(self, kind: str, dedupe_key: str, error: str) -> None:
        try:
            self.alerts.send(Alert(kind, "notifications", dedupe_key, error[:300]))
        except Exception:  # noqa: BLE001 - an alert sink problem must not break a send
            log.error("notification alert sink failed", extra={"alert_kind": kind})
