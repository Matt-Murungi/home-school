"""What goes into `audit_log.before` / `after`: redaction, minimal diff, stable hash, truncation.

Rules (docs/ops/audit.md "Redaction"):
- Sensitive values are replaced by "[redacted]" BEFORE anything is hashed, stored or logged. A
  redacted field that changed still appears (as changed) without its value.
- Rows carry ids, not names: person-name keys, contact details, coordinates, address text,
  document files, raw provider payloads, credentials and answer keys are redacted by key (global
  tokens plus the per-entity table in registry.py). Remaining string values go through the F08
  text scrubber (`scrub_text`: phone numbers, e-mails, bearer tokens, JWTs, key=value secrets).
- `before` holds only the changed fields' old values, `after` the new ones, each with a stable
  SHA-256 `hash` of the full (pre-truncation) fields and a `truncated` marker. A hash of the whole
  redacted entity state (`state` hash, returned separately) lets crash recovery compare the
  entity with an intent without storing it.
- Each column is cut to MAX_SIDE_CHARS characters (the column is a ~16k text). Truncation never
  produces invalid JSON: long strings are shortened, then values are replaced by their size, then
  only field names are kept.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ..observability.scrub import scrub_text
from . import registry

REDACTED = "[redacted]"
MAX_SIDE_CHARS = 15_000  # of the ~16k text column; leaves room for the envelope and meta
_MISSING = object()
_TOKEN = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")
_KEY_PREFIXES = frozenset({"api", "access", "secret", "private", "signing", "encryption", "answer"})
_MAX_DEPTH = 12


def _tokens(key: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(str(key)[-96:])]


def is_redacted(entity: str, key: str) -> bool:
    toks = _tokens(key)
    if not toks:
        return False
    joined = "".join(toks)
    if joined in registry.ENTITY_REDACT.get(entity, ()):
        return True
    if any(t in registry.GLOBAL_TOKENS for t in toks) or joined == "apikey":
        return True
    if any(joined.endswith(s) for s in registry.GLOBAL_JOINED_SUFFIXES):
        return True
    if len(toks) > 1 and toks[-1] == "key" and toks[-2] in _KEY_PREFIXES:
        return True
    return joined.endswith("name") and entity not in registry.NAME_IS_A_LABEL


def _clean_str(s: str) -> str:
    return s.encode("utf-8", "replace").decode("utf-8")  # lone surrogates cannot be stored


def _plain(v: Any, entity: str, depth: int) -> Any:
    """JSON-native copy of `v` with nested keys redacted and strings scrubbed."""
    if depth > _MAX_DEPTH:
        return REDACTED
    if v is None or isinstance(v, (bool, int)):
        return v
    if isinstance(v, float):
        return v
    if isinstance(v, str):
        return _clean_str(scrub_text(v))
    if isinstance(v, (bytes, bytearray, memoryview)):
        return f"[bytes:{len(bytes(v))}]"
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, Mapping):
        return {
            _clean_str(str(k)): (
                REDACTED if is_redacted(entity, str(k)) else _plain(x, entity, depth + 1)
            )
            for k, x in v.items()
        }
    if isinstance(v, (list, tuple)):
        return [_plain(x, entity, depth + 1) for x in v]
    if isinstance(v, (set, frozenset)):
        return sorted((_plain(x, entity, depth + 1) for x in v), key=str)
    return _clean_str(scrub_text(str(v)))


def normalize(row: Mapping[str, Any]) -> dict[str, Any]:
    """Drop Appwrite system keys and nulls (an unset column and a missing one are the same state);
    keep permissions (a grant change is an audited change)."""
    out = {k: v for k, v in row.items() if not str(k).startswith("$") and v is not None}
    if row.get("$permissions") is not None:
        out["_permissions"] = sorted(str(p) for p in row["$permissions"])
    return out


def redact_value(entity: str, key: str, value: Any) -> Any:
    return REDACTED if is_redacted(entity, key) else _plain(value, entity, 0)


def redact_state(entity: str, row: Mapping[str, Any]) -> dict[str, Any]:
    return {k: redact_value(entity, k, v) for k, v in normalize(row).items()}


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def stable_hash(obj: Any) -> str:
    return hashlib.sha256(_canonical(obj).encode("utf-8", "backslashreplace")).hexdigest()


def _shorten(v: Any, cap: int) -> Any:
    text = v if isinstance(v, str) else _canonical(v)
    if len(text) <= cap:
        return v
    return f"{text[:cap]}...[+{len(text) - cap} chars]"


def _envelope(fields: Any, digest: str, truncated: bool, meta: dict | None) -> str:
    d: dict[str, Any] = {"v": 1, "fields": fields, "hash": digest, "truncated": truncated}
    if meta:
        d["meta"] = meta
    return _canonical(d)


def _fit(fields: dict[str, Any], digest: str, meta: dict | None, limit: int) -> str:
    text = _envelope(fields, digest, False, meta)
    if len(text) <= limit:
        return text
    for cap in (1000, 200, 40):
        short = {k: _shorten(v, cap) for k, v in fields.items()}
        text = _envelope(short, digest, True, meta)
        if len(text) <= limit:
            return text
    sizes = {k: f"[omitted {len(_canonical(v))} chars]" for k, v in fields.items()}
    text = _envelope(sizes, digest, True, meta)
    if len(text) <= limit:
        return text
    names = sorted(str(k)[:64] for k in fields)
    n = len(names)
    while n > 0:
        text = _envelope({"_names": names[:n], "_omitted": len(names) - n}, digest, True, meta)
        if len(text) <= limit:
            return text
        n //= 2
    return _envelope({"_omitted": len(names)}, digest, True, meta)


def make_sides(
    entity: str,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    meta: dict | None = None,
    limit: int | None = None,
) -> tuple[str | None, str | None, str, str]:
    """-> (before_text, after_text, before_state_hash, after_state_hash).

    `before_text` is None for a create. `after_text` is always produced when `meta` is given (a
    delete or a read still carries its meta); otherwise None when there is no after state.
    """
    limit = MAX_SIDE_CHARS if limit is None else limit
    b = normalize(before) if before is not None else None
    a = normalize(after) if after is not None else None
    if b is not None and a is not None:
        changed = sorted(k for k in {*b, *a} if b.get(k, _MISSING) != a.get(k, _MISSING))
    else:
        changed = sorted((b if b is not None else a) or {})
    b_fields = (
        {k: redact_value(entity, k, (b or {}).get(k)) for k in changed} if b is not None else {}
    )
    a_fields = (
        {k: redact_value(entity, k, (a or {}).get(k)) for k in changed} if a is not None else {}
    )
    b_text = _fit(b_fields, stable_hash(b_fields), None, limit) if b is not None else None
    a_text = None
    if a is not None or meta:
        a_text = _fit(a_fields, stable_hash(a_fields), meta, limit)
    b_state = stable_hash(redact_state(entity, before)) if before is not None else "none"
    a_state = stable_hash(redact_state(entity, after)) if after is not None else "none"
    return b_text, a_text, b_state, a_state
