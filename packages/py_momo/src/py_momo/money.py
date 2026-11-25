"""Conversion boundary between our integer UGX and the provider's decimal strings.

Inside our code amounts are `int` UGX. Floats and bools are rejected. UGX has no minor unit, so
the provider amount string is the integer digits. Fractions never get rounded silently: use
`to_ugx` with an explicit rounding mode where a Decimal (e.g. a percentage fee) must be converted.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .errors import InvalidAmount


def check_ugx(amount: object, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise InvalidAmount(f"amount must be an int of UGX, got {type(amount).__name__}")
    if amount <= 0:
        raise InvalidAmount("amount must be positive")
    if minimum is not None and amount < minimum:
        raise InvalidAmount(f"amount {amount} is below the provider minimum {minimum}")
    if maximum is not None and amount > maximum:
        raise InvalidAmount(f"amount {amount} is above the provider maximum {maximum}")
    return amount


def to_provider_amount(
    amount: int, *, minimum: int | None = None, maximum: int | None = None
) -> str:
    return str(check_ugx(amount, minimum=minimum, maximum=maximum))


def from_provider_amount(value: object) -> int:
    """Parse '1000' or '1000.00'. A non-zero fraction is an error, never rounded."""
    if isinstance(value, bool) or isinstance(value, float) or value is None:
        raise InvalidAmount("provider amount must be a string or int")
    try:
        d = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise InvalidAmount(f"unparseable provider amount {value!r}") from exc
    if not d.is_finite() or d != d.to_integral_value():
        raise InvalidAmount(f"provider amount {value!r} is not a whole number of UGX")
    return int(d)


def to_ugx(value: Decimal | str | int, rounding: str = ROUND_HALF_UP) -> int:
    """Explicit rounding for callers holding fractional values. Floats are rejected."""
    if isinstance(value, bool) or isinstance(value, float):
        raise InvalidAmount("floats are not allowed for money")
    try:
        d = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise InvalidAmount(f"unparseable amount {value!r}") from exc
    if not d.is_finite():
        raise InvalidAmount("amount must be finite")
    return int(d.quantize(Decimal(1), rounding=rounding))
