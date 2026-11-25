"""PII scrubbing applied before any event leaves the process (Sentry `before_send`).

Policy (ops/README and docs/ops/runbook.md "PII rules"): events carry no request bodies, cookies,
auth headers, local variables, user email/IP/name, phone numbers, emails or bearer tokens. Keys that
look personal are masked at any depth; free text is masked by pattern. Names and addresses cannot be
found reliably in free text, so the rules are: never put them in exception messages (use ids) and
rely on key masking + dropped request bodies + dropped frame variables. If scrubbing fails the
event is dropped, never sent raw.

The same rules are mirrored for the Flutter apps and the admin app (docs/ops/integration.md).
"""

from __future__ import annotations

import re
from typing import Any

FILTERED = "[Filtered]"

# Keys whose value is always masked (substring match, case-insensitive, `-`/`_` ignored).
SENSITIVE_KEYS = (
    "authorization",
    "cookie",
    "token",
    "secret",
    "password",
    "passwd",
    "apikey",
    "xappwritekey",
    "jwt",
    "session",
    "phone",
    "msisdn",
    "mobile",
    "email",
    "childname",
    "studentname",
    "pupilname",
    "guardianname",
    "parentname",
    "teachername",
    "fullname",
    "firstname",
    "lastname",
    "address",
    "location",
    "latitude",
    "longitude",
    "dob",
    "dateofbirth",
    "ipaddress",
    "nationalid",
    "nin",
)
_EXACT_KEYS = {"name", "username", "nin", "dob"}
# Request keys dropped entirely.
_DROP_REQUEST = ("data", "cookies", "env")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{6,}")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
# +CC numbers, Uganda national (07xx/03xx) and 256-prefixed numbers, with space/dash/dot separators.
_PHONE = re.compile(
    r"(?<![\w.])(?:\+\d{1,3}[\s.-]?\(?\d{2,4}\)?(?:[\s.-]?\d{2,4}){2,3}"
    r"|256[\s.-]?\d{2,3}(?:[\s.-]?\d{3}){2}"
    r"|0[37]\d{2}(?:[\s.-]?\d{3}){2})(?!\w)"
)


def scrub_text(s: str) -> str:
    s = _JWT.sub(FILTERED, s)
    s = _BEARER.sub(lambda m: f"{m.group(1)} {FILTERED}", s)
    s = _EMAIL.sub(FILTERED, s)
    return _PHONE.sub(FILTERED, s)


def _sensitive(key: str) -> bool:
    k = re.sub(r"[-_\s]", "", key.lower())
    if k in _EXACT_KEYS:
        return True
    if k in {"requestid", "traceid", "spanid", "userid", "eventid"}:
        return False
    return any(w in k for w in SENSITIVE_KEYS if len(w) > 3)


def _walk(v: Any, depth: int = 0) -> Any:
    if depth > 30:
        return FILTERED
    if isinstance(v, str):
        return scrub_text(v)
    if isinstance(v, dict):
        return {
            k: (FILTERED if isinstance(k, str) and _sensitive(k) else _walk(x, depth + 1))
            for k, x in v.items()
        }
    if isinstance(v, (list, tuple)):
        return [_walk(x, depth + 1) for x in v]
    return v


def _scrub_frames(ev: dict) -> None:
    ex = ev.get("exception")
    values = ex.get("values") if isinstance(ex, dict) else None
    for val in values or []:
        st = val.get("stacktrace") if isinstance(val, dict) else None
        for fr in (st or {}).get("frames") or []:
            if isinstance(fr, dict):
                fr.pop("vars", None)


def scrub_event(event: dict | None, hint: Any = None) -> dict | None:
    """`before_send` hook. Returns the scrubbed event, or None (drop) if scrubbing fails."""
    if event is None:
        return None
    try:
        ev = dict(event)
        _scrub_frames(ev)
        req = ev.get("request")
        if isinstance(req, dict):
            req = {k: v for k, v in req.items() if k not in _DROP_REQUEST}
            ev["request"] = req
        user = ev.get("user")
        if isinstance(user, dict):
            ev["user"] = {"id": user["id"]} if "id" in user else {}
        ev.pop("server_name", None)
        return _walk(ev)
    except Exception:  # noqa: BLE001 - fail closed
        return None


def scrub_breadcrumb(crumb: dict | None, hint: Any = None) -> dict | None:
    if crumb is None:
        return None
    try:
        return _walk(crumb)
    except Exception:  # noqa: BLE001
        return None
