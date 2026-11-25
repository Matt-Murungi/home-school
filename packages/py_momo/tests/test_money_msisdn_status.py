from decimal import ROUND_DOWN, Decimal

import pytest

from py_momo import FailureReason, Status, normalize_msisdn
from py_momo.errors import InvalidAmount, InvalidMsisdn
from py_momo.logs import mask_msisdn
from py_momo.money import check_ugx, from_provider_amount, to_provider_amount, to_ugx
from py_momo.status import map_reason, map_status


@pytest.mark.parametrize("bad", [1000.0, 10.5, "1000", None, True, 0, -5])
def test_amount_must_be_positive_int(bad):
    with pytest.raises(InvalidAmount):
        check_ugx(bad)


def test_amount_limits():
    assert to_provider_amount(500, minimum=500, maximum=1000) == "500"
    with pytest.raises(InvalidAmount):
        to_provider_amount(499, minimum=500)
    with pytest.raises(InvalidAmount):
        to_provider_amount(1001, maximum=1000)


def test_provider_amount_parsing_never_rounds():
    assert from_provider_amount("1000") == 1000
    assert from_provider_amount("1000.00") == 1000
    with pytest.raises(InvalidAmount):
        from_provider_amount("1000.5")
    with pytest.raises(InvalidAmount):
        from_provider_amount(1000.0)
    with pytest.raises(InvalidAmount):
        from_provider_amount("abc")


def test_explicit_rounding_boundary():
    assert to_ugx(Decimal("1000.5")) == 1001  # half up default
    assert to_ugx("1000.5", ROUND_DOWN) == 1000
    assert to_ugx(Decimal("999.49")) == 999
    with pytest.raises(InvalidAmount):
        to_ugx(1000.5)


@pytest.mark.parametrize(
    "raw",
    [
        "+256772123456",
        "256772123456",
        "0772123456",
        "772123456",
        "00256772123456",
        "+256 772-123 456",
        "(0772) 123456",
        " 0772123456 ",
    ],
)
def test_msisdn_formats_normalise(raw):
    assert normalize_msisdn(raw) == "256772123456"


@pytest.mark.parametrize(
    "raw",
    ["", "  ", "0772", "+254712345678", "07721234567", "abc", "+256 172123456", None, 772123456],
)
def test_msisdn_rejects(raw):
    with pytest.raises(InvalidMsisdn):
        normalize_msisdn(raw)


def test_msisdn_sandbox_allows_foreign_test_numbers():
    assert normalize_msisdn("46733123450", strict_uganda=False) == "46733123450"


def test_mask_msisdn():
    m = mask_msisdn("256772123456")
    assert m == "256***456" and "772123" not in m
    assert mask_msisdn("123") == "***"


def test_status_mapping():
    assert map_status("SUCCESSFUL") is Status.SUCCESSFUL
    assert map_status("failed") is Status.FAILED
    assert map_status("PENDING") is Status.PENDING
    assert map_status("SOMETHING_NEW") is Status.PENDING  # never assume final
    assert map_status(None) is Status.PENDING


@pytest.mark.parametrize("r", [x for x in FailureReason if x is not FailureReason.UNKNOWN])
def test_every_enumerated_reason_maps(r):
    assert map_reason(r.name) == (r, r.name)


def test_unknown_reason_preserved_raw():
    assert map_reason("BRAND_NEW_CODE") == (FailureReason.UNKNOWN, "BRAND_NEW_CODE")
    assert (
        map_reason({"code": "NOT_ENOUGH_FUNDS", "message": "x"})[0]
        is FailureReason.NOT_ENOUGH_FUNDS
    )
    assert map_reason(None) == (None, None)


def test_msisdn_prefix_check_is_loose_on_purpose_airtel_and_others_not_rejected_locally():
    """Documented behaviour (README + owner note): only `256` + [3|7] + 8 digits is checked.
    Airtel (070/075), Africell (079) and 03x numbers are NOT rejected locally; MTN answers
    payer/payee not found at request time. Local MTN-only prefix filtering was rejected because
    prefixes change with number portability."""
    for raw in ("0701234567", "0751234567", "0791234567", "0391234567", "0772123456"):
        assert normalize_msisdn(raw).startswith("256")
    for raw in ("0201234567", "0401234567", "077212345"):  # wrong lead digit / short: rejected
        with pytest.raises(InvalidMsisdn):
            normalize_msisdn(raw)
