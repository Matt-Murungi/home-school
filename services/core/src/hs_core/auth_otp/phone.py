"""Ugandan mobile numbers: validation and E.164 normalisation (A01, ACC-1).

A small local normaliser on purpose: Home School and py_momo do not import each other. The
accepted formats are shared with the Flutter app through
`packages/flutter_shared/test/fixtures/ug_phone_cases.json` (both sides test against it).
"""

from __future__ import annotations

import re

COUNTRY_CODE = "256"
_ALLOWED = re.compile(r"\+?[0-9 ().\-]+", re.ASCII)
_NATIONAL = re.compile(r"7[0-9]{8}", re.ASCII)
MAX_RAW_LEN = 32


class InvalidPhone(ValueError):
    """The input is not a Ugandan mobile number. Never carries the input."""


def normalize_ug_phone(raw: object) -> str:
    """`0772 123 456`, `772123456`, `256772123456`, `+256 772-123-456`, `00256...` -> E.164.

    Only mobile numbers (nine digits after +256, starting with 7) are accepted: codes go by SMS.
    """
    if not isinstance(raw, str) or len(raw) > MAX_RAW_LEN:
        raise InvalidPhone
    s = raw.strip()
    if not s or _ALLOWED.fullmatch(s) is None or "+" in s[1:]:
        raise InvalidPhone
    digits = re.sub(r"[^0-9]", "", s)
    plus = s.startswith("+")
    if plus:
        national = digits[3:] if digits.startswith(COUNTRY_CODE) and len(digits) == 12 else None
    elif digits.startswith("00" + COUNTRY_CODE) and len(digits) == 14:
        national = digits[5:]
    elif digits.startswith(COUNTRY_CODE) and len(digits) == 12:
        national = digits[3:]
    elif digits.startswith("0") and len(digits) == 10:
        national = digits[1:]
    elif len(digits) == 9:
        national = digits
    else:
        national = None
    if national is None or _NATIONAL.fullmatch(national) is None:
        raise InvalidPhone
    return f"+{COUNTRY_CODE}{national}"


def mask_phone(e164: str) -> str:
    """`+256772123456` -> `+256 *** *** 456`: safe for logs and screens."""
    return f"+{COUNTRY_CODE} *** *** {e164[-3:]}"
