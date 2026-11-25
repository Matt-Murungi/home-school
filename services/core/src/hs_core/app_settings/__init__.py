"""Business-rule settings (H05 #134, NFR-18). Read docs/ops/settings.md first.

    settings = request.app.state.app_settings      # AppSettings
    hours = settings.get("session.late_cancellation_notice_hours")
    hours_then = settings.value_at("session.late_cancellation_notice_hours", cancelled_at)

Declare a setting in registry.py. Do not read `Settings` (environment) for a business rule that is
registered there: the environment value is only its default.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .model import Constraint, Field, InvalidValue, SettingDef, Spec, Value, validate_value
from .registry import REGISTRY
from .service import AppSettings, SettingState

__all__ = [
    "REGISTRY", "AppSettings", "Constraint", "Field", "InvalidValue", "SettingDef",
    "SettingState", "Spec", "Value", "env_defaults", "validate_value",
]  # fmt: skip


def env_defaults(settings: Any, spec: Spec = REGISTRY) -> Mapping[str, Value]:
    """The defaults the environment supplies (the `env` attribute of each definition). An
    attribute the running version of `Settings` does not have is skipped (code default)."""
    out: dict[str, Value] = {}
    for key, d in spec.defs.items():
        if d.kind == "map":
            vals = {f.name: getattr(settings, f.env, None) if f.env else None for f in d.fields}
            if all(v is not None for v in vals.values()):
                out[key] = vals  # type: ignore[assignment]
            elif any(v is not None for v in vals.values()):
                out[key] = {
                    f.name: (vals[f.name] if vals[f.name] is not None else f.default)
                    for f in d.fields
                }
        elif d.env:
            v = getattr(settings, d.env, None)
            if v is not None:
                out[key] = v
    return out
