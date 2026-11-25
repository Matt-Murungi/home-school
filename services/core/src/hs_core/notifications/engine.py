"""Safe template engine: `{{name}}` placeholders and nothing else.

No expressions, attribute access, filters, loops or includes. Values are data: they are
substituted in one pass and never evaluated again. Every declared variable must be supplied and
no undeclared one may be (fail loudly, naming the variables, never their values). Values have
types; strings are limited to one line of at most 200 characters.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from .errors import (
    ExtraVariables,
    MissingVariables,
    TemplateSyntaxError,
    VariableTypeError,
)

KAMPALA = ZoneInfo("Africa/Kampala")
MAX_VALUE_LEN = 200
TYPES = ("str", "int", "ugx", "datetime", "date")
_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}")
_PLACEHOLDER = re.compile(r"\{\{\s*([^{}\s]*)\s*\}\}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f  ]")
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass(frozen=True)
class VarSpec:
    name: str
    type: str = "str"
    pii: bool = False  # declared personal data (kept out of push where the copy avoids it)

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.name):
            raise TemplateSyntaxError(f"bad variable name {self.name!r}")
        if self.type not in TYPES:
            raise TemplateSyntaxError(f"bad variable type {self.type!r} for {self.name}")

    def encode(self) -> str:
        """Storage form for `notification_template.allowed_variables` (varchar 64 each)."""
        return f"{self.name}:{self.type}" + (":pii" if self.pii else "")

    @staticmethod
    def decode(text: str) -> VarSpec:
        parts = text.split(":")
        if len(parts) not in (2, 3) or (len(parts) == 3 and parts[2] != "pii"):
            raise TemplateSyntaxError(f"bad stored variable {text!r}")
        return VarSpec(parts[0], parts[1], len(parts) == 3)


def escape_html(text: str) -> str:
    return html.escape(text, quote=True)


def parse_placeholders(text: str) -> list[str]:
    """Names in first-use order. Any brace syntax other than a plain `{{name}}` is an error, so
    nothing that looks like code (`{{ x.y }}`, `{% %}`, `${}`) can slip in unnoticed."""
    names: list[str] = []
    rest = _PLACEHOLDER.sub(lambda m: _check(m.group(1), names), text)
    for bad in ("{{", "}}", "{%", "%}", "${"):
        if bad in rest:
            raise TemplateSyntaxError(f"unsupported template syntax {bad!r}")
    return names


def _check(name: str, names: list[str]) -> str:
    if not _NAME.fullmatch(name):
        raise TemplateSyntaxError("placeholder must be a plain variable name")
    if name not in names:
        names.append(name)
    return ""


def format_value(spec: VarSpec, value: Any) -> str:
    t = spec.type
    if t == "str":
        if not isinstance(value, str):
            raise VariableTypeError(spec.name, "a string")
        if len(value) > MAX_VALUE_LEN or _CONTROL.search(value):
            raise VariableTypeError(spec.name, f"one line of at most {MAX_VALUE_LEN} characters")
        return value
    if t in ("int", "ugx"):
        if isinstance(value, bool) or not isinstance(value, int):
            raise VariableTypeError(spec.name, "an integer")
        if t == "ugx":
            if value < 0:
                raise VariableTypeError(spec.name, "a non-negative integer amount in UGX")
            return f"UGX {value:,}"
        return str(value)
    if t == "datetime":
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise VariableTypeError(spec.name, "a timezone-aware datetime")
        d = value.astimezone(KAMPALA)
        return f"{_DAYS[d.weekday()]} {d.day} {_MONTHS[d.month - 1]} {d.year}, {d:%H:%M}"
    if t == "date":
        if not isinstance(value, date) or isinstance(value, datetime):
            raise VariableTypeError(spec.name, "a date")
        return f"{value.day} {_MONTHS[value.month - 1]} {value.year}"
    raise VariableTypeError(spec.name, "a supported type")  # pragma: no cover


def validate_values(values: dict[str, Any], specs: dict[str, VarSpec]) -> None:
    missing = set(specs) - set(values)
    if missing:
        raise MissingVariables(missing)
    extra = set(values) - set(specs)
    if extra:
        raise ExtraVariables(extra)


def render_text(text: str, values: dict[str, Any], specs: dict[str, VarSpec]) -> str:
    """Single-pass substitution. Raises on missing/extra/mistyped variables."""
    validate_values(values, specs)
    formatted = {n: format_value(specs[n], values[n]) for n in specs}
    unknown = [n for n in parse_placeholders(text) if n not in specs]
    if unknown:
        raise MissingVariables(unknown)
    return _PLACEHOLDER.sub(lambda m: formatted[m.group(1)], text)


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rstrip()
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space].rstrip()
    return cut + "…"


def to_utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC)
