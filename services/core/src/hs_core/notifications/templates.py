"""Central template store over the M04 `notification_template` table.

One row per (key, channel, locale, version); versions are inserted, never edited in place (only
`active` flips), so a notification can always be re-rendered with the exact version it used.
Only a super admin may publish (A-50 builds the screen on `TemplateStore.publish`).
Locale `en` is the fallback; other locales (Luganda later, NFR-13) are just more rows.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from appwrite.query import Query

from ..jobs.idempotency import row_id
from ..repository import Conflict, NotFound, Repository
from . import engine
from .engine import VarSpec
from .errors import (
    ExtraVariables,
    MissingVariables,
    PermissionDenied,
    TemplateSyntaxError,
    TemplateTooLong,
    UnknownTemplate,
)

log = logging.getLogger("hs_core.notifications.templates")
TABLE = "notification_template"
CATEGORIES = ("operational", "safeguarding", "marketing")
_KEY = re.compile(r"[a-z][a-z0-9_.]{2,79}")
_LOCALE = re.compile(r"[a-z]{2}(-[A-Z]{2})?")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class Channel(StrEnum):
    """Delivery channels. SMS exists in the table enum but is A01's (OTP) and excluded here."""

    PUSH = "push"
    EMAIL = "email"
    IN_APP = "in_app"


# (title/subject, body) maximum lengths per channel.
LIMITS: dict[Channel, tuple[int, int]] = {
    Channel.PUSH: (65, 240),
    Channel.IN_APP: (80, 500),
    Channel.EMAIL: (150, 5000),
}


@dataclass(frozen=True)
class Actor:
    user_id: str
    roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class TemplateSpec:
    key: str
    channel: Channel
    locale: str
    version: int
    category: str
    title: str
    body: str
    variables: tuple[VarSpec, ...] = ()
    active: bool = True
    created_by_id: str | None = None
    created_at: str | None = None
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def specs(self) -> dict[str, VarSpec]:
        return {v.name: v for v in self.variables}


@dataclass(frozen=True)
class Rendered:
    title: str
    body: str
    html: str | None = None  # email only; `body` is the plain-text part


def _paragraphs_html(text: str) -> str:
    out = []
    for para in re.split(r"\n\s*\n", text.strip()):
        out.append("<p>" + engine.escape_html(para).replace("\n", "<br>") + "</p>")
    return "".join(out)


def render_template(spec: TemplateSpec, values: dict[str, Any]) -> Rendered:
    """Validate and render. Never logs `values`. Email gets an HTML part with every character
    escaped (static text and values alike) next to the plain-text fallback."""
    specs = spec.specs
    title = engine.render_text(spec.title, values, specs)
    body = engine.render_text(spec.body, values, specs)
    tmax, bmax = LIMITS[spec.channel]
    title, body = engine.truncate(title, tmax), engine.truncate(body, bmax)
    if spec.channel is Channel.EMAIL:
        return Rendered(title, body, _paragraphs_html(body))
    return Rendered(title, body)


def sample_values(variables: tuple[VarSpec, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for v in variables:
        out[v.name] = {
            "str": "Sample",
            "int": 30,
            "ugx": 50000,
            "datetime": datetime(2027, 1, 19, 14, 0, tzinfo=UTC),
            "date": date(2027, 1, 19),
        }[v.type]
    return out


class TemplateStore:
    def __init__(
        self,
        repo: Repository,
        clock: Callable[[], datetime] | None = None,
        audit: Callable[[dict], None] | None = None,
    ) -> None:
        self.repo = repo
        self.clock = clock or (lambda: datetime.now(UTC))
        self.audit = audit  # M06 wires the audit log here; payload has ids, never copy text

    # ------------------------------------------------------------------ reads
    @staticmethod
    def _spec(r: dict) -> TemplateSpec:
        return TemplateSpec(
            key=r["key"],
            channel=Channel(r["channel"]),
            locale=r["locale"],
            version=r["version"],
            category=r["category"],
            title=r["title"],
            body=r["body"],
            variables=tuple(VarSpec.decode(v) for v in (r.get("allowed_variables") or [])),
            active=bool(r.get("active", True)),
            created_by_id=r.get("created_by_id"),
            created_at=r.get("created_at"),
        )

    def versions(self, key: str, channel: Channel, locale: str = "en") -> list[TemplateSpec]:
        rows = self.repo.list(
            TABLE,
            [
                Query.equal("key", [key]),
                Query.equal("channel", [Channel(channel).value]),
                Query.equal("locale", [locale]),
                Query.limit(200),
            ],
        )
        return sorted((self._spec(r) for r in rows), key=lambda s: s.version)

    def get_active(self, key: str, channel: Channel, locale: str = "en") -> TemplateSpec:
        for loc in dict.fromkeys((locale, "en")):
            live = [s for s in self.versions(key, channel, loc) if s.active]
            if live:
                return live[-1]
        raise UnknownTemplate(f"no active template {key}/{Channel(channel).value}")

    def get_version(self, key: str, channel: Channel, locale: str, version: int) -> TemplateSpec:
        try:
            return self._spec(
                self.repo.get(TABLE, row_id(TABLE, key, Channel(channel).value, locale, version))
            )
        except NotFound:
            raise UnknownTemplate(f"no template {key}/{channel}/{locale}/v{version}") from None

    def dry_run(self, key: str, channel: Channel, locale: str = "en") -> Rendered:
        """Admin 'test send': renders the active template with sample values, sends nothing."""
        spec = self.get_active(key, channel, locale)
        return render_template(spec, sample_values(spec.variables))

    # ------------------------------------------------------------------ writes
    def publish(
        self,
        actor: Actor,
        *,
        key: str,
        channel: Channel,
        locale: str,
        title: str,
        body: str,
        variables: tuple[VarSpec, ...],
        category: str,
    ) -> TemplateSpec:
        if "superadmin" not in actor.roles:
            raise PermissionDenied("only a super admin may edit notification templates")
        channel = Channel(channel)
        spec = TemplateSpec(key, channel, locale, 0, category, title, body, tuple(variables))
        self._validate(spec)
        for _ in range(50):  # optimistic version allocation; the unique id is the atomic claim
            existing = self.versions(key, channel, locale)
            nxt = (existing[-1].version if existing else 0) + 1
            new = replace(
                spec, version=nxt, created_by_id=actor.user_id, created_at=self.clock().isoformat()
            )
            try:
                self._insert(new)
            except Conflict:
                continue
            for old in existing:
                if old.active and old.version < nxt:
                    self.repo.update(
                        TABLE,
                        row_id(TABLE, key, channel.value, locale, old.version),
                        {"active": False},
                    )
            if self.audit:
                self.audit(
                    {
                        "action": "notification_template.publish",
                        "actor_id": actor.user_id,
                        "key": key,
                        "channel": channel.value,
                        "locale": locale,
                        "version": nxt,
                    }
                )
            log.info("template published", extra={"template": key, "version": nxt})
            return new
        raise RuntimeError("could not allocate a template version")  # pragma: no cover

    def _insert(self, s: TemplateSpec) -> None:
        self.repo.create(
            TABLE,
            row_id(TABLE, s.key, s.channel.value, s.locale, s.version),
            {
                "key": s.key,
                "channel": s.channel.value,
                "locale": s.locale,
                "title": s.title,
                "body": s.body,
                "allowed_variables": [v.encode() for v in s.variables],
                "category": s.category,
                "version": s.version,
                "active": s.active,
                "created_by_id": s.created_by_id,
                "created_at": s.created_at or self.clock().isoformat(),
            },
        )

    def seed_defaults(self) -> int:
        """Insert version 1 of every default template that has no version yet. Idempotent."""
        from . import catalog

        created = 0
        for spec in catalog.default_templates():
            if self.versions(spec.key, spec.channel, spec.locale):
                continue
            try:
                self._insert(replace(spec, created_at=self.clock().isoformat()))
                created += 1
            except Conflict:
                pass
        return created

    # ------------------------------------------------------------------ validation
    def _validate(self, s: TemplateSpec) -> None:
        if not _KEY.fullmatch(s.key):
            raise TemplateSyntaxError("bad template key")
        if not _LOCALE.fullmatch(s.locale):
            raise TemplateSyntaxError("bad locale")
        if s.category not in CATEGORIES:
            raise TemplateSyntaxError("bad category")
        if not s.title.strip() or not s.body.strip():
            raise TemplateSyntaxError("title and body are required")
        if _CONTROL.search(s.title):
            raise TemplateSyntaxError("title must be a single line")
        tmax, bmax = LIMITS[s.channel]
        if len(s.title) > tmax or len(s.body) > bmax:
            raise TemplateTooLong(f"{s.channel.value} limit is {tmax} title / {bmax} body")
        declared = {v.name for v in s.variables}
        if len(declared) != len(s.variables):
            raise TemplateSyntaxError("duplicate variable declaration")
        used = set(engine.parse_placeholders(s.title)) | set(engine.parse_placeholders(s.body))
        if used - declared:
            raise MissingVariables(used - declared)
        if declared - used:
            raise ExtraVariables(declared - used)
        from . import catalog

        ev = catalog.EVENTS.get(s.key)
        if ev is not None:
            want = {v.name: v.type for v in ev.variables}
            got = {v.name: v.type for v in s.variables}
            if set(got) - set(want):
                raise ExtraVariables(set(got) - set(want))
            if set(want) - set(got):
                raise MissingVariables(set(want) - set(got))
            if got != want:
                raise TemplateSyntaxError("variable types must match the event")
            if s.category != ev.category:
                raise TemplateSyntaxError("category cannot be changed for a catalog event")
            if s.channel not in ev.template_channels:
                raise TemplateSyntaxError("the event has no template for this channel")
