"""PII scrubbing applied before any event leaves the process (Sentry `before_send`).

Policy (ops/README and docs/ops/runbook.md "PII rules"): events carry no request bodies, cookies,
auth headers, local variables, user email/IP/name, phone numbers, emails or bearer tokens. Keys that
look personal are masked at any depth; free text is masked by pattern. Names and addresses cannot be
found reliably in free text, so the rules are: never put them in exception messages (use ids) and
rely on key masking + dropped request bodies + dropped frame variables. If scrubbing fails the
event is dropped, never sent raw. Structures are walked with cycle detection and node/size caps
(cap exhaustion = scrub failure = drop; each drop is counted and logged once, PII-free).
Log `extra=` fields get the same treatment (logging_setup).

Name-aware masking (`name=value`, `name: value`, `name%3Dvalue`, `"name": "value"`): the name is
found with a bounded pattern and judged in Python, so scanning is linear (a 1 MB run of `token`
takes milliseconds). Credential-like names (token, secret, password, passwd, pwd, pass, apikey,
`*_key`, jwt, session, cookie, authorization, signature) are masked; counters/settings whose last
part is `count/used/policy/length/...` (`session_count=3`, `password_policy=ok`) and plurals such as
`tokens_used=5` are NOT masked. Limits: only the last 64 characters of a name are examined; a
secret value is taken up to the next whitespace/delimiter (4096 chars max); a value that follows a
prose word and a space only (`the token abc`) is not detectable.

The same rules are mirrored for the Flutter apps and the admin app (docs/ops/integration.md).
"""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)
_dropped = {"n": 0}

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
_EXACT_KEYS = {
    "name", "username", "nin", "dob",
    # A01 phone OTP: the code, its hash and salt, and the SMS body that contains the code.
    "otp", "otpcode", "salt", "codehash", "smsbody", "verificationcode", "passcode",
}  # fmt: skip
# Request keys dropped entirely.
_DROP_REQUEST = ("data", "cookies", "env")

# Resource caps: one event may not stall the request path or balloon.
MAX_NODES = 50_000  # containers + scalars visited per scrub call
MAX_CHARS = 1_000_000  # total characters of output strings per scrub call
MAX_TEXT = 256_000  # a single string is truncated beyond this
MAX_DEPTH = 30
CIRCULAR = "[Circular]"

_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s{1,8}[A-Za-z0-9._~+/=-]{6,}")
# Header lines: the whole value up to end of line is a secret.
_HEADER_LINE = re.compile(
    r"(?i)\b(set-cookie|cookie|proxy-authorization|authorization|x-appwrite-key"
    r"|x-appwrite-jwt|x-appwrite-session|x-api-key)([ \t]{0,8}:[ \t]{0,8})[^\r\n]+"
)
_APPWRITE_KEY = re.compile(r"(?i)\bstandard_[0-9a-f]{6,}")

# name=value / name: value / name%3Dvalue / "name": "value" for secret-looking names.
#
# Linear by construction (R2-1): the regex only finds a bounded candidate name (at most
# MAX_NAME word characters directly before the separator, no alternation of keywords, so no
# backtracking over a long run) and a bounded separator; whether the name is secret is decided in
# Python (`_secret_name`). Values are bounded and consumed, so the scan resumes after them.
MAX_NAME = 64
MAX_VALUE = 4096
# A run of name characters, optionally followed by (closing quote,) separator and spaces. The
# separator group is optional so a run without one matches once, in one pass (no per-position
# retries); the candidate name is the last MAX_NAME characters of the run.
_RUN = re.compile(r"[\w.-]+(?:([\"']?[ \t]{0,8}(?:=|:|%3[Dd])[ \t]{0,8}))?")
_VALUE = re.compile(
    r"\[Filtered\]|\"[^\"]{0,N}\"?|'[^']{0,N}'?|[^\s&;,\"')\]}]{1,N}".replace("N", str(MAX_VALUE))
)
_SECRET_WORDS = ("token", "secret", "password", "passwd", "apikey", "jwt", "signature")
_SECRET_PARTS = {"cookie", "authorization", "pass", "pwd", "key", "session"}
# Last name part of a counter/setting, not a credential (`session_count`, `password_policy`).
_BENIGN_LAST = {
    "count", "counts", "used", "policy", "length", "len", "ttl", "seconds", "expiry", "expires",
    "enabled", "required", "type", "minutes", "max", "min", "limit", "total",
}  # fmt: skip


def _secret_name(name: str) -> bool:
    """Decide in Python (no regex alternation): is `name` a credential-like field name?

    Parts split on `_ - .`; a part is secret if it equals/ends with token, secret, password,
    passwd, apikey, jwt, signature, equals cookie/authorization/pass/pwd/key/session or starts with
    `session`. Plural/other words (`tokens_used`) and settings whose last part is a counter-like
    word (`session_count`, `password_policy`) are not masked. Only the last 64 characters of a
    longer name are considered.
    """
    parts = [p for p in re.split(r"[_.-]", name.lower()[-MAX_NAME:]) if p]
    if not parts:
        return False
    if len(parts) > 1 and parts[-1] in _BENIGN_LAST:
        return False
    for p in parts:
        if p in _SECRET_PARTS or p.startswith("session") or p.endswith(_SECRET_WORDS):
            if p == "key" and p is not parts[-1]:
                continue  # `key` only as the last part (`api_key`), not `key_count`/`key_id`
            return True
    return False


def _mask_pairs(s: str) -> str:
    out: list[str] = []
    pos = 0  # start of text not yet emitted
    scan = 0
    while True:
        m = _RUN.search(s, scan)
        if m is None:
            break
        scan = m.end()
        if m.group(1) is None:
            continue
        name = s[m.start() : m.start(1)]
        v = _VALUE.match(s, scan)
        if v is None or not _secret_name(name):
            continue  # a non-secret name may precede another pair (`x:token=abc`): keep scanning
        out.append(s[pos:scan])
        out.append(FILTERED)
        pos = scan = v.end()
    out.append(s[pos:])
    return "".join(out)


# Emails, also percent-encoded (`%40`, `%2E`). The lookbehind keeps matching linear.
# A01: a one-time code spelled out in free text ("your code is 123456", "otp=123456", "pin 1234").
# The name must start a word and the digits follow within 24 non-digit characters, so the scan is
# linear. Over-masks a 4-8 digit number right after "code"/"pin" (e.g. "error code 5000").
_OTP_TEXT = re.compile(r"(?i)(?<!\w)(otp|passcode|code|pin)\b([^\d\n]{0,24}?)(\d{4,8})(?!\d)")
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
    r"|(?<![\w.])(?:00)?256"
    + _SEP
    + r"(?:\(0\)"
    + _SEP
    + r")?\d{2,3}(?:"
    + _SEP
    + r"\d{3}){2}(?!\w)"
    r"|(?<![\w.])0[37]\d{2}(?:" + _SEP + r"\d{3}){2}(?!\w)"
    r"|(?<![\w.])\(0[37]\d{2}\)" + _SEP + r"\d{6}(?!\w)"
)


def dropped_count() -> int:
    """Events/breadcrumbs dropped (fail closed) since start; for tests and diagnostics."""
    return _dropped["n"]


def reset_dropped() -> None:
    _dropped["n"] = 0


def _note_drop(kind: str, exc: Exception) -> None:
    _dropped["n"] += 1
    log.warning("%s dropped by scrubber: %s", kind, type(exc).__name__)  # class name only: no PII


def scrub_text(s: str) -> str:
    if len(s) > MAX_TEXT:
        s = s[:MAX_TEXT] + "[Truncated]"
    s = _HEADER_LINE.sub(lambda m: f"{m.group(1)}{m.group(2)}{FILTERED}", s)
    s = _JWT.sub(FILTERED, s)
    s = _BEARER.sub(lambda m: f"{m.group(1)} {FILTERED}", s)
    s = _APPWRITE_KEY.sub(FILTERED, s)
    s = _mask_pairs(s)
    s = _OTP_TEXT.sub(lambda m: f"{m.group(1)}{m.group(2)}{FILTERED}", s)
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
    except Exception as exc:  # noqa: BLE001 - fail closed
        _note_drop("event", exc)
        return None


def scrub_breadcrumb(crumb: dict | None, hint: Any = None) -> dict | None:
    if crumb is None:
        return None
    try:
        return _walk(crumb)
    except Exception as exc:  # noqa: BLE001
        _note_drop("breadcrumb", exc)
        return None
