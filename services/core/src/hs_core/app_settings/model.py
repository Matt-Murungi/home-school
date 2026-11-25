"""Setting definitions, value validation and the typed errors (H05 #134).

A setting is declared in code (registry.py) and holds a NON-SECRET business rule. Secrets and
infrastructure stay in environment variables (docs/ops/settings.md "Which is which").

Value kinds: `int`, `float` and `map` (a fixed set of named integers that are validated and
changed together, e.g. the matching weights that must sum to 100). Everything is validated
strictly: JSON booleans are not integers, NaN and infinity are refused, a map has exactly its
declared fields, and the canonical JSON of a value is capped.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from ..errors import AppError

Kind = Literal["int", "float", "map"]
# `immediate`: from the effective instant (never earlier than now).
# `next_day`: from the next Africa/Kampala midnight at or after the requested instant, so a rule
# that parents, teachers or invoices have already been judged under is never changed under them.
Applies = Literal["immediate", "next_day"]
MAX_JSON_CHARS = 1000
MAX_REASON_CHARS = 500

Value = int | float | dict[str, int]


@dataclass(frozen=True)
class Field:
    name: str
    lo: int
    hi: int
    default: int
    env: str | None = None  # Settings attribute that supplies the default


@dataclass(frozen=True)
class SettingDef:
    key: str
    group: str
    kind: Kind
    description: str
    unit: str = ""
    lo: float | None = None
    hi: float | None = None
    default: Any = None  # int | float (scalar kinds); map kinds take it from `fields`
    env: str | None = None  # Settings attribute that supplies the default (scalar kinds)
    fields: tuple[Field, ...] = ()
    sum_to: int | None = None  # map kinds: the fields must add up to this
    applies: Applies = "next_day"
    changed_by: tuple[str, ...] = ("superadmin",)
    requires_reason: bool = True
    restart_required: bool = False
    validator: Callable[[dict[str, int]], str | None] | None = None  # map kinds: error or None

    def code_default(self) -> Value:
        if self.kind == "map":
            return {f.name: f.default for f in self.fields}
        return self.default


@dataclass(frozen=True)
class Constraint:
    """A rule over several settings, checked against the values in force at one instant."""

    name: str
    keys: tuple[str, ...]
    check: Callable[[Mapping[str, Value]], bool]
    message: str


# ----------------------------------------------------------------------------- errors
class SettingsError(Exception):
    """Base for non-HTTP settings problems."""


class SettingNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(404, "unknown_setting", "Unknown setting")


class InvalidValue(AppError):
    def __init__(self, message: str, code: str = "invalid_value") -> None:
        super().__init__(422, code, message[:300])


class RevisionConflict(AppError):
    def __init__(self, current: int) -> None:
        super().__init__(
            409,
            "revision_conflict",
            "The setting changed since you loaded it; reload and review the new value",
            extra={"current_revision": current},
        )


class ChangePending(AppError):
    def __init__(self) -> None:
        super().__init__(
            409,
            "change_pending",
            "A scheduled change is already waiting for this setting; cancel it first",
        )


class NoPendingChange(AppError):
    def __init__(self) -> None:
        super().__init__(409, "no_pending_change", "There is no scheduled change to cancel")


class NothingToRollBack(AppError):
    def __init__(self) -> None:
        super().__init__(409, "nothing_to_roll_back", "There is no earlier value to go back to")


class SettingsUnavailable(AppError):
    def __init__(self) -> None:
        super().__init__(503, "settings_unavailable", "Settings store temporarily unavailable")


# ----------------------------------------------------------------------------- validation
def _is_int(v: Any) -> bool:
    return type(v) is int  # bool is a subclass of int and is NOT accepted


def _num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:g}"


def _scalar(d: SettingDef, v: Any) -> Value:
    if d.kind == "int":
        if not _is_int(v):
            raise InvalidValue(f"{d.key} must be a whole number")
    else:
        if isinstance(v, bool) or not isinstance(v, int | float):
            raise InvalidValue(f"{d.key} must be a number")
        if isinstance(v, float) and not math.isfinite(v):
            raise InvalidValue(f"{d.key} must be a finite number")
        try:
            v = float(v)
        except OverflowError:
            raise InvalidValue(f"{d.key} is out of range") from None
        if not math.isfinite(v):
            raise InvalidValue(f"{d.key} is out of range")
    assert d.lo is not None and d.hi is not None
    if not d.lo <= v <= d.hi:
        span = f"between {_num(d.lo)} and {_num(d.hi)} {d.unit}".strip()
        raise InvalidValue(f"{d.key} must be {span}")
    return v


def _map(d: SettingDef, v: Any) -> dict[str, int]:
    if not isinstance(v, dict):
        raise InvalidValue(f"{d.key} must be an object with {', '.join(f.name for f in d.fields)}")
    names = {f.name for f in d.fields}
    if set(v) != names:
        raise InvalidValue(f"{d.key} must have exactly the fields: {', '.join(sorted(names))}")
    out: dict[str, int] = {}
    for f in d.fields:
        x = v[f.name]
        if not _is_int(x):
            raise InvalidValue(f"{d.key}.{f.name} must be a whole number")
        if not f.lo <= x <= f.hi:
            raise InvalidValue(f"{d.key}.{f.name} must be between {f.lo} and {f.hi}")
        out[f.name] = x
    if d.sum_to is not None and sum(out.values()) != d.sum_to:
        raise InvalidValue(f"{d.key} must add up to {d.sum_to} (now {sum(out.values())})")
    if d.validator is not None:
        problem = d.validator(out)
        if problem:
            raise InvalidValue(f"{d.key}: {problem}")
    return out


def validate_value(d: SettingDef, raw: Any) -> Value:
    """The normalised value, or InvalidValue. Never coerces ("24", True and 24.5 are not ints)."""
    v = _map(d, raw) if d.kind == "map" else _scalar(d, raw)
    if len(encode(v)) > MAX_JSON_CHARS:
        raise InvalidValue(f"{d.key} is too large")
    return v


def encode(v: Value) -> str:
    return json.dumps(v, sort_keys=True, separators=(",", ":"), allow_nan=False)


def decode(d: SettingDef, text: Any) -> Value:
    """Parse a stored value; InvalidValue when it is not valid for the definition."""
    if not isinstance(text, str) or len(text) > MAX_JSON_CHARS:
        raise InvalidValue("stored value is not valid")
    try:
        raw = json.loads(text)
    except ValueError:
        raise InvalidValue("stored value is not valid") from None
    return validate_value(d, raw)


@dataclass
class Spec:
    """The full registry with constraints; built in registry.py."""

    defs: dict[str, SettingDef] = field(default_factory=dict)
    constraints: tuple[Constraint, ...] = ()
    groups: tuple[tuple[str, str], ...] = ()  # (id, title) in screen order

    def get(self, key: str) -> SettingDef:
        d = self.defs.get(key)
        if d is None:
            raise SettingNotFound()
        return d
