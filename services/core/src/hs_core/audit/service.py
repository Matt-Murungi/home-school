"""AuditService: the only writer of `audit_log` (always through AppendOnlyRepository).

Protocol (docs/ops/audit.md "Protocol"): AUDIT-FIRST.
  1. an intent row is created (deterministic id from the change key: request or job key, entity,
     entity id, n-th change): the claim is atomic (Appwrite 409) so a retry never adds a row;
  2. the business write runs;
  3. if it fails and we SEE the failure, a `failed` outcome row refers to the intent;
  4. if the process dies, `audit-recover` (recover.py) compares the entity with the intent and
     writes a `not_applied` outcome row when the change never landed.
Rows are never updated (append-only); the outcome is a second row that points at the intent. A
change with an intent row and no outcome row is, by definition, a change that happened.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from appwrite.permission import Permission
from appwrite.query import Query
from appwrite.role import Role

from ..append_only import AppendOnlyRepository
from ..observability.scrub import scrub_text
from ..repository import Conflict, NotFound, Repository
from . import payload, registry
from .context import AuditContext, short_id
from .errors import AuditUnavailable, ReasonRequired

log = logging.getLogger("hs_core.audit")
TABLE = "audit_log"
MAX_REASON = 500
_LOCK_STRIPES = 64

Lazy = Mapping[str, Any] | None | Callable[[], Mapping[str, Any] | None]


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="microseconds")


def clean_reason(reason: str | None) -> str | None:
    text = (reason or "").strip()
    if not text:
        return None
    return scrub_text(text)[:MAX_REASON].strip() or None


def meta_of(row: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(row.get("after") or "{}").get("meta") or {}
    except (ValueError, AttributeError):
        return {}


class _Pending:
    __slots__ = ("iid", "n", "seq", "replayed", "ctx", "action", "entity", "entity_id", "reason")

    def __init__(self, iid, n, seq, replayed, ctx, action, entity, entity_id, reason):
        self.iid, self.n, self.seq, self.replayed = iid, n, seq, replayed
        self.ctx, self.action, self.entity, self.entity_id = ctx, action, entity, entity_id
        self.reason = reason


def _state_hash(entity: str, row: Mapping[str, Any] | None) -> str:
    return "none" if row is None else payload.stable_hash(payload.redact_state(entity, row))


def _resolve(v: Lazy) -> Mapping[str, Any] | None:
    return v() if callable(v) else v


class AuditService:
    def __init__(
        self,
        repo: Repository,
        *,
        clock: Callable[[], datetime] | None = None,
        lead_blanket_read: bool = True,
    ) -> None:
        self._log = AppendOnlyRepository(repo)  # the only handle on audit_log
        self._clock = clock or (lambda: datetime.now(UTC))
        self.lead_blanket_read = lead_blanket_read
        self._locks = [threading.RLock() for _ in range(_LOCK_STRIPES)]

    # ------------------------------------------------------------------ ids and grants
    def intent_row_id(self, key: str) -> str:
        return "a" + hashlib.sha256(f"audit-intent|{key}".encode()).hexdigest()[:35]

    def outcome_row_id(self, intent_id: str) -> str:
        return "o" + hashlib.sha256(f"audit-outcome|{intent_id}".encode()).hexdigest()[:35]

    def permissions(self) -> list[str]:
        labels = ["opsadmin", "superadmin"] + (
            ["safeguardinglead"] if self.lead_blanket_read else []
        )
        return [Permission.read(Role.label(label)) for label in labels]

    def now(self) -> datetime:
        return self._clock()

    def _lock_for(self, entity: str, entity_id: str) -> threading.RLock:
        return self._locks[hash((entity, entity_id)) % _LOCK_STRIPES]

    # ------------------------------------------------------------------ low-level rows
    def _get_row(self, row_id: str) -> dict | None:
        try:
            return self._log.get(TABLE, row_id)
        except NotFound:
            return None
        except Exception as e:  # noqa: BLE001
            raise AuditUnavailable(type(e).__name__) from None

    def _next_seq(self, entity: str, entity_id: str) -> int:
        try:
            rows = self._log.list(
                TABLE,
                [
                    Query.equal("entity", [entity]),
                    Query.equal("entity_id", [entity_id]),
                    Query.order_desc("occurred_at"),
                    Query.limit(10),
                ],
            )
        except Exception as e:  # noqa: BLE001
            raise AuditUnavailable(type(e).__name__) from None
        return 1 + max((int(meta_of(r).get("seq", 0)) for r in rows), default=0)

    def _create(self, row_id: str, data: dict) -> bool:
        """True when this call wrote the row, False when it already existed (lost the claim)."""
        try:
            self._log.create(TABLE, row_id, data, self.permissions())
            return True
        except Conflict:
            return False
        except Exception as e:  # noqa: BLE001
            raise AuditUnavailable(type(e).__name__) from None

    def _check_reason(self, action: str, reason: str | None) -> None:
        if action in registry.REASON_REQUIRED and not (reason or "").strip():
            raise ReasonRequired(action)

    # ------------------------------------------------------------------ begin
    def _begin(
        self,
        ctx: AuditContext,
        *,
        action: str,
        entity: str,
        entity_id: str,
        op: str,
        before: Lazy,
        after: Lazy,
        reason: str | None,
        status: str,
    ) -> _Pending:
        n = ctx.count(entity, entity_id)
        while True:
            iid = self.intent_row_id(ctx.key(entity, entity_id, n))
            existing = self._get_row(iid)
            if existing is None:
                break
            if status == "intent" and self._get_row(self.outcome_row_id(iid)) is not None:
                n += 1  # that attempt ended in a recorded failure: this is a new attempt
                continue
            seq = int(meta_of(existing).get("seq", 0))
            return _Pending(
                iid, n, seq, True, ctx, action, entity, entity_id, existing.get("reason")
            )
        b, a = _resolve(before), _resolve(after)
        seq = self._next_seq(entity, entity_id)
        meta: dict[str, Any] = {
            "status": status,
            "source": ctx.source,
            "seq": seq,
            "op": op,
            "key": ctx.key(entity, entity_id, n),
            "before_state": _state_hash(entity, b),
            "after_state": _state_hash(entity, a),
        }
        if ctx.job:
            meta["job"] = ctx.job
        b_text, a_text, _, _ = payload.make_sides(entity, b, a, meta)
        reason_text = clean_reason(reason)
        data = {
            "actor_id": ctx.actor_id,
            "actor_roles": ctx.roles_text,
            "action": action[:120],
            "entity": entity[:64],
            "entity_id": short_id(str(entity_id)),
            "before": b_text,
            "after": a_text,
            "reason": reason_text,
            "ip": ctx.ip,
            "request_id": ctx.request_id,
            "occurred_at": iso(self._clock()),
        }
        wrote = self._create(iid, data)
        if not wrote:  # a concurrent identical request claimed it first: that is the row
            existing = self._get_row(iid) or {}
            seq = int(meta_of(existing).get("seq", seq))
        return _Pending(iid, n, seq, not wrote, ctx, action, entity, entity_id, reason_text)

    # ------------------------------------------------------------------ public API
    def run_change(
        self,
        ctx: AuditContext,
        *,
        action: str,
        entity: str,
        entity_id: str,
        op: str,
        before: Lazy = None,
        after: Lazy = None,
        reason: str | None = None,
        apply: Callable[[], Any],
        on_replay: Callable[[Exception], Any] | None = None,
    ) -> Any:
        """Audit-first change. `before`/`after` may be callables (evaluated only for a fresh
        intent). `on_replay(exc)` decides what an error means when this attempt replays an intent
        left by an earlier attempt of the same request (e.g. a create that already landed)."""
        self._check_reason(action, reason)
        with self._lock_for(entity, str(entity_id)):
            pending = self._begin(
                ctx, action=action, entity=entity, entity_id=str(entity_id), op=op,
                before=before, after=after, reason=reason, status="intent",
            )  # fmt: skip
            try:
                result = apply()
            except Exception as exc:  # noqa: BLE001 - BaseException (a crash) is left to recovery
                try:
                    if not (pending.replayed and on_replay is not None):
                        raise
                    result = on_replay(exc)
                except Exception as final:
                    self._mark_failed(pending, final)
                    ctx.advance_to(entity, str(entity_id), pending.n + 1)
                    raise
            ctx.advance_to(entity, str(entity_id), pending.n + 1)
            return result

    def record(
        self,
        ctx: AuditContext,
        *,
        action: str,
        entity: str,
        entity_id: str,
        before: Lazy = None,
        after: Lazy = None,
        reason: str | None = None,
        op: str = "action",
    ) -> str:
        """One idempotent row for something that needs no business write (a read, an export)."""
        self._check_reason(action, reason)
        with self._lock_for(entity, str(entity_id)):
            p = self._begin(
                ctx, action=action, entity=entity, entity_id=str(entity_id), op=op,
                before=before, after=after, reason=reason, status="recorded",
            )  # fmt: skip
            ctx.advance_to(entity, str(entity_id), p.n + 1)
            return p.iid

    # ------------------------------------------------------------------ outcomes
    def _mark_failed(self, pending: _Pending, exc: BaseException) -> None:
        """The business write failed and we saw it: add a `failed` row. Best effort: if this write
        fails too, the intent stays unmatched and audit-recover settles it."""
        ctx = pending.ctx
        try:
            self._write_outcome(
                pending.iid, status="failed", action=pending.action, entity=pending.entity,
                entity_id=pending.entity_id, seq=pending.seq, actor_id=ctx.actor_id,
                actor_roles=ctx.roles_text, source=ctx.source, job=ctx.job, ip=ctx.ip,
                request_id=ctx.request_id, error=type(exc).__name__,
            )  # fmt: skip
        except Exception as e:  # noqa: BLE001
            log.warning("failed-outcome row not written", extra={"error_type": type(e).__name__})

    def mark_not_applied(self, intent: Mapping[str, Any], *, job: str, run_key: str) -> bool:
        """Recovery: the intent never landed. Idempotent (fixed outcome id). True if written."""
        m = meta_of(intent)
        return self._write_outcome(
            intent["$id"], status="not_applied", action=intent["action"], entity=intent["entity"],
            entity_id=intent["entity_id"], seq=int(m.get("seq", 0)), actor_id=None,
            actor_roles="system", source="system", job=job, ip=None,
            request_id=short_id(run_key), error=None,
        )  # fmt: skip

    def _write_outcome(
        self, intent_id: str, *, status: str, action: str, entity: str, entity_id: str, seq: int,
        actor_id: str | None, actor_roles: str, source: str, job: str | None, ip: str | None,
        request_id: str, error: str | None,
    ) -> bool:  # fmt: skip
        meta: dict[str, Any] = {"status": status, "ref": intent_id, "seq": seq, "source": source}
        if job:
            meta["job"] = job
        if error:
            meta["error"] = error
        _, a_text, _, _ = payload.make_sides(entity, None, None, meta)
        return self._create(
            self.outcome_row_id(intent_id),
            {
                "actor_id": actor_id, "actor_roles": actor_roles, "action": action,
                "entity": entity, "entity_id": entity_id, "before": None, "after": a_text,
                "reason": None, "ip": ip, "request_id": request_id,
                "occurred_at": iso(self._clock()),
            },
        )  # fmt: skip

    def has_outcome(self, intent_id: str) -> bool:
        return self._get_row(self.outcome_row_id(intent_id)) is not None
