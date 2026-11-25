"""PII scrubbing applied before any event leaves the process (Sentry `before_send`).

Policy (ops/README and docs/ops/runbook.md "PII rules"): events carry no request bodies, cookies,
auth headers, local variables, user email/IP/name, phone numbers, emails or bearer tokens. Keys that
look personal are masked at any depth; free text is masked by pattern. Names and addresses cannot be
found reliably in free text, so the rules are: never put them in exception messages (use ids) and
rely on key masking + dropped request bodies + dropped frame variables. If scrubbing fails the
event is dropped, never sent raw. Structures are walked with cycle detection and node/size caps
(cap exhaustion = scrub failure = drop). Log `extra=` fields get the same treatment (logging_setup).

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

# Resource caps: one event may not stall the request path or balloon.
MAX_NODES = 50_000  # containers + scalars visited per scrub call
MAX_CHARS = 1_000_000  # total characters of output strings per scrub call
MAX_TEXT = 256_000  # a single string is truncated beyond this
MAX_DEPTH = 30
CIRCULAR = "[Circular]"

_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{6,}")
# Header lines: the whole value up to end of line is a secret.
_HEADER_LINE = re.compile(
    r"(?i)\b(set-cookie|cookie|proxy-authorization|authorization|x-appwrite-key"
    r"|x-appwrite-jwt|x-appwrite-session|x-api-key)([ \t]*:[ \t]*)[^\r\n]+"
)
_APPWRITE_KEY = re.compile(r"(?i)\bstandard_[0-9a-f]{6,}")
# Names whose value is a secret. `key` only as a whole word or `_key`/`-key` suffix.
_SECRET_NAME = (
    r"[\w.-]*(?:token|secret|passw(?:or)?d|pwd|apikey|api[_-]key|jwt|session|signature)[\w.-]*"
    r"|(?:[\w.]*[_-])?key"
)
# name=value (query strings, forms, env style); value ends at whitespace/&/;/quote/bracket.
_KV_EQ = re.compile(
    r"(?i)(?<![\w.-])(" + _SECRET_NAME + r")(\s*=\s*)(\"[^\"]*\"|'[^']*'|[^\s&;,\"')\]}]+)"
)
# "name": "value" (JSON / dict repr inside strings). Quoted name required.
_KV_JSON = re.compile(
    r"(?i)([\"'])(" + _SECRET_NAME + r")\1(\s*:\s*)(\"[^\"]*\"|'[^']*'|[^\s,}\]]+)"
)
# Emails, also percent-encoded (`%40`, `%2E`). The lookbehind keeps matching linear.
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+(?:@|%40)[A-Za-z0-9-]+(?:(?:\.|%2[Ee])[A-Za-z0-9-]+)+"
)
# Phones. SEP = space/dot/dash/plus/%20. Forms: +CC (with optional "(0)" trunk, optional brackets),
# bare 256..., Uganda national 07xx/03xx. The lookbehind stops matches starting mid-number.
_SEP = r"(?:[\s.+-]|%20)?"
_PHONE = re.compile(
    r"(?:\((?=(?:\+|%2[Bb])\d{1,3}\))|(?<![\w.]))(?:\+|%2[Bb])\d{1,3}\)?"
    + _SEP
    + r"(?:\(0\)"
    + _SEP
    + r")?"
    r"\(?\d{2,4}\)?(?:" + _SEP + r"\d{2,4}){2,3}(?!\w)"
    r"|(?<![\w.])256" + _SEP + r"(?:\(0\)" + _SEP + r")?\d{2,3}(?:" + _SEP + r"\d{3}){2}(?!\w)"
    r"|(?<![\w.])0[37]\d{2}(?:" + _SEP + r"\d{3}){2}(?!\w)"
)


def scrub_text(s: str) -> str:
    if len(s) > MAX_TEXT:
        s = s[:MAX_TEXT] + "[Truncated]"
    s = _HEADER_LINE.sub(lambda m: f"{m.group(1)}{m.group(2)}{FILTERED}", s)
    s = _JWT.sub(FILTERED, s)
    s = _BEARER.sub(lambda m: f"{m.group(1)} {FILTERED}", s)
    s = _APPWRITE_KEY.sub(FILTERED, s)
    s = _KV_EQ.sub(lambda m: f"{m.group(1)}{m.group(2)}{FILTERED}", s)
    s = _KV_JSON.sub(lambda m: f'{m.group(1)}{m.group(2)}{m.group(1)}{m.group(3)}"{FILTERED}"', s)
    s = _EMAIL.sub(FILTERED, s)
    return _PHONE.sub(FILTERED, s)


def _decode(b: bytes | bytearray | memoryview) -> str:
    return bytes(b[:MAX_TEXT]).decode("utf-8", errors="replace")


def _sensitive(key: str) -> bool:
    k = re.sub(r"[-_\s]", "", key.lower())
    if k in _EXACT_KEYS:
        return True
    if k in {"requestid", "traceid", "spanid", "userid", "eventid"}:
        return False
    return any(w in k for w in SENSITIVE_KEYS if len(w) > 3)


class ScrubLimitExceeded(Exception):
    """Structure too large or too branched to scrub within the caps (callers fail closed)."""


class _State:
    __slots__ = ("nodes", "chars")

    def __init__(self) -> None:
        self.nodes = 0
        self.chars = 0

    def text(self, s: str) -> str:
        if self.chars + min(len(s), MAX_TEXT) > MAX_CHARS:
            raise ScrubLimitExceeded("output size cap")  # before spending regex time
        out = scrub_text(s)
        self.chars += len(out)
        if self.chars > MAX_CHARS:
            raise ScrubLimitExceeded("output size cap")
        return out


def _key(k: Any, st: _State) -> Any:
    if isinstance(k, (bytes, bytearray)):
        k = _decode(k)
    if isinstance(k, str):
        return st.text(k)
    if k is None or isinstance(k, (int, float, bool)):
        return k
    return st.text(str(k))


def _walk(v: Any, depth: int = 0, st: _State | None = None, path: set[int] | None = None) -> Any:
    st = st or _State()
    path = set() if path is None else path
    st.nodes += 1
    if st.nodes > MAX_NODES:
        raise ScrubLimitExceeded("node cap")
    if depth > MAX_DEPTH:
        return FILTERED
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, str):
        return st.text(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return st.text(_decode(v))
    if isinstance(v, (dict, list, tuple, set, frozenset)):
        if id(v) in path:
            return CIRCULAR
        path.add(id(v))
        try:
            if isinstance(v, dict):
                out = {}
                for k, x in v.items():
                    name = _decode(k) if isinstance(k, (bytes, bytearray)) else k
                    masked = isinstance(name, str) and _sensitive(name)
                    out[_key(k, st)] = FILTERED if masked else _walk(x, depth + 1, st, path)
                    st.nodes += 1
                    if st.nodes > MAX_NODES:
                        raise ScrubLimitExceeded("node cap")
                return out
            return [_walk(x, depth + 1, st, path) for x in v]
        finally:
            path.discard(id(v))
    return st.text(str(v))


def scrub_data(v: Any) -> Any:
    """Scrub any value with the event rules (keys masked, text patterns, bytes, cycles, caps).

    Raises `ScrubLimitExceeded` (or another error) when it cannot; callers must then drop the data.
    """
    return _walk(v)


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
