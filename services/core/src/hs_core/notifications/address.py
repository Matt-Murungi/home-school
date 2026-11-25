"""Strict single email address validation (N02 #127, answering the N01 reviewers' note).

One bare address, nothing else. Decisions (record in docs/ops/notifications.md):
- exactly one `@`; no display name (`Name <a@b>`), no angle brackets, no quotes, no comments, no
  lists (`,` `;` space), no surrounding whitespace, no CR/LF/tab, no control or format characters
  (zero-width, BOM, bidi controls, line separators, NBSP): anything outside printable ASCII is
  rejected, which also removes every Unicode homoglyph and normalisation trick;
- total length at most 254 (RFC 5321 path limit), local part at most 64, each domain label 1-63,
  domain at most 253; the domain needs a dot and an alphabetic top-level label of 2+ letters (no
  bare hosts, no IP literals, no numeric TLD);
- local part: dot-atom made of letters, digits and `#$&'*+/=?^_{}~-` (RFC 5322 atext minus the
  routing characters `% ! |` and the backtick); dots only between atoms;
- internationalised addresses: NOT supported (no SMTPUTF8). A Unicode domain must be converted to
  punycode (`xn--...`) by the caller; a Unicode local part is rejected. Revisit when a real user
  needs it.
This is a hygiene gate in front of the provider, not a deliverability check.
"""

from __future__ import annotations

import re

MAX_LENGTH = 254
_ATEXT = "A-Za-z0-9#$&'*+/=?^_{}~-"
_LOCAL = re.compile(rf"[{_ATEXT}]+(?:\.[{_ATEXT}]+)*")
_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_TLD = re.compile(r"[A-Za-z]{2,63}|xn--[A-Za-z0-9-]{1,59}")


def validate_address(value: object) -> str | None:
    """The address unchanged when it is a single clean address, else None."""
    if not isinstance(value, str) or not 3 <= len(value) <= MAX_LENGTH:
        return None
    if not value.isascii() or not value.isprintable() or value != value.strip():
        return None
    local, sep, domain = value.partition("@")
    if not sep or "@" in domain or len(local) > 64 or len(domain) > 253:
        return None
    if not _LOCAL.fullmatch(local):
        return None
    labels = domain.split(".")
    if len(labels) < 2 or not all(_LABEL.fullmatch(x) for x in labels):
        return None
    if not _TLD.fullmatch(labels[-1]):
        return None
    return value


def normalize_address(value: object) -> str | None:
    """Lower-cased valid address (suppression matching is case-insensitive), or None."""
    ok = validate_address(value)
    return ok.lower() if ok is not None else None
