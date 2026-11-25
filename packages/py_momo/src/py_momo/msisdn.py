"""Phone number normalisation to the MSISDN form MoMo expects (digits, country code, no plus)."""

from __future__ import annotations

import re

from .errors import InvalidMsisdn

_UG = re.compile(r"^256[37]\d{8}$")
_ANY = re.compile(r"^[1-9]\d{7,14}$")


def normalize_msisdn(raw: str, *, strict_uganda: bool = True) -> str:
    """Accept +256 7XX XXX XXX, 256..., 00256..., 07XXXXXXXX, 7XXXXXXXX (spaces/dashes/parens ok).

    With strict_uganda=False (sandbox only: test numbers are not Ugandan) any E.164-length number
    that already carries a country code is accepted.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise InvalidMsisdn("phone number is empty")
    s = raw.strip()
    if re.search(r"[^\d\s\-().+]", s):
        raise InvalidMsisdn("phone number contains invalid characters")
    has_plus = s.startswith("+")
    d = re.sub(r"\D", "", s)
    if d.startswith("00"):
        d = d[2:]
        has_plus = True
    if d.startswith("256"):
        pass
    elif d.startswith("0") and len(d) == 10 and not has_plus:
        d = "256" + d[1:]
    elif len(d) == 9 and not has_plus and d[0] in "37":
        d = "256" + d
    if _UG.match(d):
        return d
    if not strict_uganda and _ANY.match(d):
        return d
    raise InvalidMsisdn("not a valid Ugandan mobile number")
