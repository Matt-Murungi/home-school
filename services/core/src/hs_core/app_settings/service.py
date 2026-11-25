"""The settings service: cached runtime reads with safe defaults, audited admin changes, history,
scheduling, rollback (H05 #134, NFR-18). Design and limits: docs/ops/settings.md.

Storage (two tables, `appwrite/schema/settings.json`):
- `app_setting_change`: the history and the source of truth. One row per change, id derived from
  (setting, revision). It is created, never updated or deleted. Because Appwrite has no
  conditional update, CREATING the row for revision N+1 is the optimistic-concurrency claim: of
  any number of concurrent editors who read revision N exactly one create succeeds.
- `app_setting`: one row per setting that has ever been changed, a projection of the history
  (current value, the revision it reflects, and the one scheduled change if any). Readers use it.

Write order of an edit (every step is audit-first through the AuditedRepository):
  1. audit intent + create the history row  -> this IS the commit (a repeat or a loser gets 409)
  2. audit intent + update/create the value row (the projection)
A crash between 1 and 2 leaves history ahead of the projection; the next edit of that setting and
the `settings-activate` job (every minute) re-project it from the history. The brief's order
("value row, then history") is reversed on purpose: only the history row can be a claim.

Reads never fail to zero or infinity: a value that is missing, unreadable, out of bounds or that
breaks a cross-setting rule is replaced by the DEFAULT (env value, else the code default).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from appwrite.query import Query

from ..audit.context import AuditContext
from ..audit.errors import AuditError, ReasonRequired
from ..audit.service import MAX_REASON, clean_reason, iso, reason_is_enough
from ..jobs.model import KAMPALA
from ..repository import Conflict, NotFound
from .model import (
    ChangePending,
    InvalidValue,
    NoPendingChange,
    NothingToRollBack,
    RevisionConflict,
    SettingDef,
    SettingsUnavailable,
    Spec,
    Value,
    decode,
    encode,
    validate_value,
)
from .registry import REGISTRY

log = logging.getLogger("hs_core.app_settings")

VALUE_TABLE = "app_setting"
HISTORY_TABLE = "app_setting_change"
PAGE = 100
PAST_TOLERANCE = timedelta(seconds=120)  # a slightly slow browser clock still means "now"
MAX_SCHEDULE_AHEAD = timedelta(days=365)
ACTION = "setting.update"  # reason-required (audit registry)
MAX_REASON_INPUT = 2000


def key_hash(key: str) -> str:
    import hashlib

    return hashlib.sha256(key.encode()).hexdigest()[:20]


def value_row_id(key: str) -> str:
    return f"s_{key_hash(key)}"


def history_row_id(key: str, revision: int) -> str:
    return f"c_{key_hash(key)}_{revision}"


def _dt(text: Any) -> datetime | None:
    if not text:
        return None
    try:
        d = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def ceil_to_kampala_midnight(t: datetime) -> datetime:
    local = t.astimezone(KAMPALA)
    mid = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if local == mid:
        return t.astimezone(UTC)
    return (mid + timedelta(days=1)).astimezone(UTC)


@dataclass(frozen=True)
class Pending:
    revision: int
    value: Value
    effective_at: datetime


@dataclass(frozen=True)
class Entry:
    """What the value row says about one setting."""

    value: Value  # effective before `pending` kicks in
    revision: int  # latest history revision this row reflects
    active_revision: int
    pending: Pending | None
    updated_at: datetime | None


@dataclass(frozen=True)
class SettingState:
    key: str
    value: Value
    default: Value
    source: str  # "default" | "override"
    revision: int
    active_revision: int
    pending: Pending | None


class AppSettings:
    def __init__(
        self,
        repo,
        *,
        spec: Spec = REGISTRY,
        defaults: Mapping[str, Value] | None = None,
        clock: Callable[[], datetime] | None = None,
        ttl_s: float = 30.0,
        retry_s: float = 5.0,
        on_event: Callable[[str, dict], None] | None = None,
    ) -> None:
        """`repo` is the AuditedRepository (writes) that also serves the reads."""
        self._audited = repo
        self.spec = spec
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ttl = ttl_s
        self._retry = retry_s
        self._on_event = on_event
        self._alerted: set[tuple] = set()
        self._defaults = self._build_defaults(defaults or {})
        self._entries: dict[str, Entry] = {}
        self._loaded_at: datetime | None = None  # last successful load
        self._tried_at: datetime | None = None
        self._resolved: dict[str, Value] | None = None
        self._resolved_until: datetime | None = None
        self._cache_lock = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    # ------------------------------------------------------------------ defaults
    def _build_defaults(self, env: Mapping[str, Value]) -> dict[str, Value]:
        out: dict[str, Value] = {}
        for key, d in self.spec.defs.items():
            candidate: Value = d.code_default()
            if key in env:
                candidate = env[key]
            try:
                out[key] = validate_value(d, candidate)
            except InvalidValue:
                self._event("env_default_invalid", {"key": key})
                out[key] = validate_value(d, d.code_default())
        return out

    def default(self, key: str) -> Value:
        self.spec.get(key)
        return self._defaults[key]

    def _event(self, name: str, info: dict) -> None:
        """Log every time, alert once per (event, subject) per process (reads repeat)."""
        log.warning("settings %s", name, extra={"event": name, **info})
        mark = (name, tuple(sorted(info.items())))
        if self._on_event and mark not in self._alerted:
            self._alerted.add(mark)
            self._on_event(name, info)

    def _lock(self, key: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.RLock())

    def now(self) -> datetime:
        return self._clock().astimezone(UTC)

    # ------------------------------------------------------------------ reading (runtime)
    def get(self, key: str) -> Value:
        """The value in force now. Never raises for a store problem; an unknown key is a bug."""
        self.spec.get(key)
        return self._resolved_map()[key]

    def snapshot(self) -> dict[str, Value]:
        return dict(self._resolved_map())

    def invalidate(self) -> None:
        with self._cache_lock:
            self._loaded_at = None
            self._tried_at = None
            self._resolved = None

    def _resolved_map(self) -> dict[str, Value]:
        now = self.now()
        with self._cache_lock:
            due = self._loaded_at is None or now - self._loaded_at >= timedelta(seconds=self._ttl)
            if due and (
                self._tried_at is None or now - self._tried_at >= timedelta(seconds=self._retry)
            ):
                self._refresh_locked(now)
            if (
                self._resolved is None
                or self._resolved_until is not None
                and now >= self._resolved_until
            ):
                self._resolve_locked(now)
            assert self._resolved is not None
            return self._resolved

    def _refresh_locked(self, now: datetime) -> None:
        self._tried_at = now
        try:
            rows = self._audited.list(VALUE_TABLE, [Query.limit(PAGE)])
        except Exception as e:  # noqa: BLE001 - keep the last good snapshot, else defaults
            self._event("store_unavailable", {"error": type(e).__name__})
            self._resolved = None  # recompute (defaults when nothing was ever loaded)
            return
        entries: dict[str, Entry] = {}
        for row in rows:
            key = row.get("setting_key")
            d = self.spec.defs.get(key) if isinstance(key, str) else None
            if d is None:
                continue  # a row for a setting this version does not know: ignored
            entry = self._entry(d, row)
            if entry is not None:
                entries[key] = entry
        self._entries = entries
        self._loaded_at = now
        self._resolved = None

    def _entry(self, d: SettingDef, row: Mapping[str, Any]) -> Entry | None:
        try:
            value = decode(d, row.get("value"))
            pending = None
            if row.get("pending_effective_at") is not None:
                eff = _dt(row.get("pending_effective_at"))
                if eff is None:
                    raise InvalidValue("pending time")
                pending = Pending(
                    int(row.get("pending_revision") or 0),
                    decode(d, row.get("pending_value")),
                    eff,
                )
            return Entry(
                value,
                int(row.get("revision") or 0),
                int(row.get("active_revision") or 0),
                pending,
                _dt(row.get("updated_at")),
            )
        except (InvalidValue, TypeError, ValueError):
            self._event("stored_value_invalid", {"key": d.key})
            return None

    @staticmethod
    def _entry_value(e: Entry, t: datetime) -> tuple[Value, int]:
        if e.pending is not None and e.pending.effective_at <= t:
            return e.pending.value, e.pending.revision
        return e.value, e.active_revision

    def _values_at(self, t: datetime, entries: Mapping[str, Entry]) -> dict[str, Value]:
        out: dict[str, Value] = {}
        for key in self.spec.defs:
            e = entries.get(key)
            out[key] = self._entry_value(e, t)[0] if e else self._defaults[key]
        return out

    def _resolve_locked(self, now: datetime) -> None:
        values = self._values_at(now, self._entries)
        for c in self.spec.constraints:  # a stored set that breaks a rule: the DEFAULTS apply
            if not c.check({k: values[k] for k in c.keys}):
                self._event("constraint_violated", {"constraint": c.name})
                for k in c.keys:
                    values[k] = self._defaults[k]
        self._resolved = values
        pendings = [
            e.pending.effective_at
            for e in self._entries.values()
            if e.pending is not None and e.pending.effective_at > now
        ]
        self._resolved_until = min(pendings) if pendings else None

    # ------------------------------------------------------------------ history helpers
    def _history_desc(self, key: str, limit: int = PAGE, before: int | None = None) -> list[dict]:
        q = [Query.equal("setting_key", key)]
        if before is not None:
            q.append(Query.less_than("revision", before))
        q += [Query.order_desc("revision"), Query.limit(limit)]
        try:
            return self._audited.list(HISTORY_TABLE, q)
        except Exception as e:  # noqa: BLE001
            self._event("store_unavailable", {"error": type(e).__name__})
            raise SettingsUnavailable() from None

    def _rows(self) -> dict[str, dict]:
        try:
            rows = self._audited.list(VALUE_TABLE, [Query.limit(PAGE)])
        except Exception as e:  # noqa: BLE001
            self._event("store_unavailable", {"error": type(e).__name__})
            raise SettingsUnavailable() from None
        return {r["setting_key"]: r for r in rows if isinstance(r.get("setting_key"), str)}

    @staticmethod
    def _live(hist: list[dict]) -> list[dict]:
        """Set/rollback rows that were not cancelled, newest revision first."""
        cancelled = {h.get("cancels_revision") for h in hist if h.get("op") == "cancel"}
        return [
            h for h in hist if h.get("op") in ("set", "rollback") and h["revision"] not in cancelled
        ]

    def _split(self, d: SettingDef, hist: list[dict], now: datetime):
        """-> (latest revision, active row or None, pending row or None)."""
        latest = int(hist[0]["revision"]) if hist else 0
        active = pending = None
        for h in self._live(hist):
            eff = _dt(h.get("effective_at"))
            if eff is None:
                continue
            if eff > now:
                pending = pending or h
            elif active is None:
                active = h
        return latest, active, pending

    def value_at(self, key: str, instant: datetime) -> Value:
        """The value that was (or will be) in force at `instant`, from the history. A rule applied
        to an EVENT (a cancellation, an invoice) should be read here with the event's time, so a
        later change never re-judges it."""
        d = self.spec.get(key)
        t = instant.astimezone(UTC)
        for h in self._live(self._history_desc(key)):
            eff = _dt(h.get("effective_at"))
            if eff is not None and eff <= t:
                try:
                    return decode(d, h.get("value"))
                except InvalidValue:
                    break
        return self._defaults[key]

    # ------------------------------------------------------------------ admin: view
    def states(self) -> list[SettingState]:
        """Fresh from the store (not the cache): what the admin screen shows."""
        rows = self._rows()
        now = self.now()
        out = []
        for key, d in self.spec.defs.items():
            e = self._entry(d, rows[key]) if key in rows else None
            if e is None:
                out.append(
                    SettingState(
                        key, self._defaults[key], self._defaults[key], "default", 0, 0, None
                    )
                )
                continue
            value, active_rev = self._entry_value(e, now)
            pending = e.pending if e.pending and e.pending.effective_at > now else None
            out.append(
                SettingState(
                    key,
                    value,
                    self._defaults[key],
                    "override" if active_rev else "default",
                    e.revision,
                    active_rev,
                    pending,
                )  # fmt: skip
            )
        return out

    def history(self, key: str, *, before: int | None = None, limit: int = 50) -> list[dict]:
        self.spec.get(key)
        return self._history_desc(key, limit=max(1, min(limit, PAGE)), before=before)

    # ------------------------------------------------------------------ admin: changes
    def _check_reason(self, reason: str | None) -> str:
        if not reason_is_enough(reason):
            raise ReasonRequired(ACTION)
        if len(reason or "") > MAX_REASON_INPUT:
            raise InvalidValue(
                f"reason must be at most {MAX_REASON_INPUT} characters", "reason_too_long"
            )
        text, _, _ = clean_reason(reason)
        assert text
        return text[:MAX_REASON]

    def _effective_instant(self, d: SettingDef, requested: datetime | None) -> datetime:
        now = self.now()
        base = now
        if requested is not None:
            if requested.tzinfo is None:
                raise InvalidValue("effective_at needs a time zone", "invalid_effective_at")
            base = requested.astimezone(UTC)
            if base < now - PAST_TOLERANCE:
                raise InvalidValue("A change cannot take effect in the past", "effective_in_past")
            base = max(base, now)
            if base > now + MAX_SCHEDULE_AHEAD:
                raise InvalidValue("effective_at is too far ahead", "invalid_effective_at")
        if d.applies == "next_day":
            base = ceil_to_kampala_midnight(base)
        return base

    def _check_constraints(
        self,
        d: SettingDef,
        rows: Mapping[str, dict],
        *,
        new: tuple[datetime, Value] | None,
        drop_pending: bool,
    ) -> None:
        """Every rule that involves `d` must hold at every instant from now on at which one of the
        involved settings changes (the pending change of another setting included)."""
        mine = [c for c in self.spec.constraints if d.key in c.keys]
        if not mine:
            return
        now = self.now()
        entries: dict[str, Entry] = {}
        for k, row in rows.items():
            kd = self.spec.defs.get(k)
            e = self._entry(kd, row) if kd else None
            if e is not None:
                entries[k] = e
        if drop_pending and d.key in entries:
            e = entries[d.key]
            entries[d.key] = Entry(e.value, e.revision, e.active_revision, None, e.updated_at)
        points = {now}
        if new is not None:
            points.add(new[0])
        for c in mine:
            for k in c.keys:
                e = entries.get(k)
                if e and e.pending and e.pending.effective_at > now:
                    points.add(e.pending.effective_at)

        def value_in(k: str, t: datetime) -> Value:
            if k == d.key and new is not None and t >= new[0]:
                return new[1]
            e = entries.get(k)
            return self._entry_value(e, t)[0] if e else self._defaults[k]

        for t in sorted(points):
            for c in mine:
                if not c.check({k: value_in(k, t) for k in c.keys}):
                    raise InvalidValue(f"Not allowed: {c.message}", "constraint_violated")

    def _commit(
        self,
        key: str,
        *,
        op: str,
        value: Value | None,
        eff: datetime | None,
        cancels: int | None,
        previous: Value,
        latest: int,
        reason: str,
        ctx: AuditContext,
    ) -> int:
        revision = latest + 1
        data = {
            "setting_key": key,
            "revision": revision,
            "op": op,
            "value": encode(value) if value is not None else None,
            "previous_value": encode(previous),
            "effective_at": iso(eff) if eff else iso(self.now()),
            "cancels_revision": cancels,
            "reason": reason,
            "actor_id": ctx.actor_id,
            "created_at": iso(self.now()),
        }
        try:
            self._audited.create(
                HISTORY_TABLE, history_row_id(key, revision), data, ctx=ctx,
                action=ACTION, reason=reason,
            )  # fmt: skip
        except Conflict:  # somebody else claimed this revision first
            raise RevisionConflict(revision) from None
        except AuditError:  # unavailable, missing context, reason: the caller maps these
            raise
        except Exception as e:  # noqa: BLE001
            self._event("store_unavailable", {"error": type(e).__name__})
            raise SettingsUnavailable() from None
        return revision

    def _prepare(self, key: str, expected_revision: int):
        d = self.spec.get(key)
        if d.changed_by != ("superadmin",):  # pragma: no cover - registry invariant
            raise AssertionError("only the super admin changes settings")
        hist = self._history_desc(key)
        latest, active, pending = self._split(d, hist, self.now())
        if expected_revision != latest:
            raise RevisionConflict(latest)
        return d, hist, latest, active, pending

    def change(
        self,
        key: str,
        value: Any,
        *,
        reason: str | None,
        expected_revision: int,
        ctx: AuditContext,
        effective_at: datetime | None = None,
    ) -> SettingState:
        d = self.spec.get(key)
        text = self._check_reason(reason)
        new = validate_value(d, value)
        with self._lock(key):
            d, hist, latest, active, pending = self._prepare(key, expected_revision)
            if pending is not None:
                raise ChangePending()
            eff = self._effective_instant(d, effective_at)
            current = self._current_value(d, active)
            if new == current:
                raise InvalidValue("The new value equals the current value", "no_change")
            self._check_constraints(d, self._rows(), new=(eff, new), drop_pending=False)
            self._commit(
                key, op="set", value=new, eff=eff, cancels=None, previous=current,
                latest=latest, reason=text, ctx=ctx,
            )  # fmt: skip
            self.project(key, ctx=ctx, reason=text)
        return self._state(key)

    def rollback(
        self, key: str, *, reason: str | None, expected_revision: int, ctx: AuditContext
    ) -> SettingState:
        """A NEW revision whose value is the one before the current override (history is never
        rewritten). Applies like any change (same rules, same effective-from)."""
        d = self.spec.get(key)
        text = self._check_reason(reason)
        with self._lock(key):
            d, hist, latest, active, pending = self._prepare(key, expected_revision)
            if pending is not None:
                raise ChangePending()
            if active is None:
                raise NothingToRollBack()
            current = self._current_value(d, active)
            target = self._rollback_target(d, hist, self.now())
            if target == current:
                raise NothingToRollBack()
            eff = self._effective_instant(d, None)
            self._check_constraints(d, self._rows(), new=(eff, target), drop_pending=False)
            self._commit(
                key, op="rollback", value=target, eff=eff, cancels=None, previous=current,
                latest=latest, reason=text, ctx=ctx,
            )  # fmt: skip
            self.project(key, ctx=ctx, reason=text)
        return self._state(key)

    def cancel_pending(
        self, key: str, *, reason: str | None, expected_revision: int, ctx: AuditContext
    ) -> SettingState:
        d = self.spec.get(key)
        text = self._check_reason(reason)
        with self._lock(key):
            d, hist, latest, active, pending = self._prepare(key, expected_revision)
            if pending is None:
                raise NoPendingChange()
            self._check_constraints(d, self._rows(), new=None, drop_pending=True)
            self._commit(
                key, op="cancel", value=None, eff=None, cancels=int(pending["revision"]),
                previous=self._current_value(d, active), latest=latest, reason=text, ctx=ctx,
            )  # fmt: skip
            self.project(key, ctx=ctx, reason=text)
        return self._state(key)

    def _rollback_target(self, d: SettingDef, hist: list[dict], now: datetime) -> Value:
        """Replay the live history oldest first as a stack: a `set` pushes its value, a `rollback`
        pops one. The value under the top is where "roll back" goes (the default when nothing is
        left), so rolling back twice walks back two changes instead of toggling."""
        stack: list[Value] = []
        for h in reversed(self._live(hist)):
            eff = _dt(h.get("effective_at"))
            if eff is None or eff > now:
                continue
            if h["op"] == "set":
                try:
                    stack.append(decode(d, h.get("value")))
                except InvalidValue:
                    stack.append(self._defaults[d.key])
            elif stack:
                stack.pop()
        if len(stack) >= 2:
            return stack[-2]
        return self._defaults[d.key]

    def _current_value(self, d: SettingDef, active: dict | None) -> Value:
        if active is None:
            return self._defaults[d.key]
        try:
            return decode(d, active.get("value"))
        except InvalidValue:
            return self._defaults[d.key]

    def _state(self, key: str) -> SettingState:
        self.invalidate()
        return next(s for s in self.states() if s.key == key)

    # ------------------------------------------------------------------ projection
    def project(
        self, key: str, *, ctx: AuditContext | None = None, reason: str = "settings projection"
    ) -> bool:
        """Bring the value row of `key` in line with its history. Idempotent. True if written."""
        d = self.spec.get(key)
        with self._lock(key):
            hist = self._history_desc(key)
            now = self.now()
            latest, active, pending = self._split(d, hist, now)
            if latest == 0:
                return False
            value = self._current_value(d, active)
            want: dict[str, Any] = {
                "setting_key": key,
                "value": encode(value),
                "revision": latest,
                "active_revision": int(active["revision"]) if active else 0,
                "pending_revision": int(pending["revision"]) if pending else None,
                "pending_value": pending.get("value") if pending else None,
                "pending_effective_at": pending.get("effective_at") if pending else None,
                "updated_at": iso(now),
            }
            rid = value_row_id(key)
            try:
                row = self._audited.get(VALUE_TABLE, rid)
            except NotFound:
                row = None
            except Exception as e:  # noqa: BLE001
                self._event("store_unavailable", {"error": type(e).__name__})
                raise SettingsUnavailable() from None
            if row is not None:
                same = all(row.get(k) == v for k, v in want.items() if k not in ("updated_at",))
                if same and self._pending_is_current(row, now):
                    return False
            try:
                if row is None:
                    self._audited.create(
                        VALUE_TABLE, rid, want, ctx=ctx, action="app_setting.create", reason=reason
                    )
                else:
                    self._audited.update(
                        VALUE_TABLE, rid, want, ctx=ctx, action="app_setting.update", reason=reason
                    )
            except AuditError:
                raise
            except Exception as e:  # noqa: BLE001
                self._event("store_unavailable", {"error": type(e).__name__})
                raise SettingsUnavailable() from None
            self.invalidate()
            return True

    @staticmethod
    def _pending_is_current(row: Mapping[str, Any], now: datetime) -> bool:
        """False when the row still carries a pending change whose time has come."""
        eff = _dt(row.get("pending_effective_at"))
        return eff is None or eff > now

    def reconcile(self, ctx: AuditContext | None = None) -> dict[str, int]:
        """The `settings-activate` job body: activate due scheduled changes and repair
        projections that fell behind their history. Idempotent."""
        rows = self._rows()
        now = self.now()
        done = {"activated": 0, "repaired": 0}
        for key in self.spec.defs:
            row = rows.get(key)
            row_rev = int(row.get("revision") or 0) if row else 0
            latest = self._history_desc(key, limit=1)
            latest_rev = int(latest[0]["revision"]) if latest else 0
            if latest_rev > row_rev:
                reason = f"projection repaired up to revision {latest_rev}"
                if self.project(key, ctx=ctx, reason=reason):
                    done["repaired"] += 1
            elif row is not None and not self._pending_is_current(row, now):
                reason = f"scheduled change of revision {row.get('pending_revision')} took effect"
                if self.project(key, ctx=ctx, reason=reason):
                    done["activated"] += 1
        return done
