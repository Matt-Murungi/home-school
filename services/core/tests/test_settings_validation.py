"""H05 (#134) edge cases: an invalid value (negative window, wrong type, huge, NaN) is a
validation error, weights that do not add up to 100 are blocked, nothing is coerced."""

import math

import pytest

from hs_core.app_settings import REGISTRY, InvalidValue, validate_value
from settings_helpers import AUTO, LATE, QUIET, RATE, WEIGHTS

D = REGISTRY.defs
GOOD_WEIGHTS = {
    "travel_time": 30, "tier": 20, "struggle_fit": 15, "workload_balance": 15,
    "reliability": 15, "cluster_bonus": 5,
}  # fmt: skip


def bad(key, value):
    with pytest.raises(InvalidValue) as e:
        validate_value(D[key], value)
    assert e.value.status == 422 and e.value.code == "invalid_value"
    return e.value


class TestScalars:
    @pytest.mark.parametrize("v", [-1, -24, 0, 169, 10**9])
    def test_a_negative_zero_or_out_of_range_window_is_a_validation_error(self, v):
        bad(LATE, v)  # lo is 1 hour, hi is 168

    @pytest.mark.parametrize("v", [1, 24, 168])
    def test_the_bounds_themselves_are_accepted(self, v):
        assert validate_value(D[LATE], v) == v

    @pytest.mark.parametrize(
        "v", ["24", "", " 24", 24.0, 24.5, True, False, None, [24], {"v": 24}, "٢٤", b"24"]
    )
    def test_nothing_is_coerced_into_a_whole_number(self, v):
        bad(LATE, v)

    @pytest.mark.parametrize("v", [10**30, -(10**30), 10**5000], ids=["1e30", "-1e30", "1e5000"])
    def test_huge_numbers_are_refused_not_crashed_on(self, v):
        bad(AUTO, v)

    def test_a_float_setting_takes_ints_and_floats_but_not_nan_inf_or_bool(self):
        d = D["travel.fallback_speed_kmh"]
        assert validate_value(d, 30) == 30.0 and validate_value(d, 25.5) == 25.5
        for v in (math.nan, math.inf, -math.inf, True, "20", None, 1e400, 4.9, 120.1):
            bad("travel.fallback_speed_kmh", v)

    def test_the_error_message_names_the_setting_and_never_echoes_a_huge_value(self):
        e = bad(LATE, "x" * 100_000)
        assert LATE in e.message and len(e.message) < 400


class TestMaps:
    def test_weights_that_add_up_to_100_are_accepted(self):
        assert validate_value(D[WEIGHTS], GOOD_WEIGHTS) == GOOD_WEIGHTS

    @pytest.mark.parametrize("delta", [-1, 1, -100, 50])
    def test_weights_not_adding_up_to_100_are_blocked(self, delta):
        w = {**GOOD_WEIGHTS, "tier": GOOD_WEIGHTS["tier"] + delta}
        if not 0 <= w["tier"] <= 100:
            w["tier"] = max(0, min(100, w["tier"]))
        e = bad(WEIGHTS, w)
        assert "100" in e.message

    def test_a_weight_may_be_zero_but_not_negative_or_above_100(self):
        zero = {**GOOD_WEIGHTS, "cluster_bonus": 0, "tier": 25}
        assert validate_value(D[WEIGHTS], zero)["cluster_bonus"] == 0
        bad(WEIGHTS, {**GOOD_WEIGHTS, "cluster_bonus": -5, "tier": 30})
        bad(WEIGHTS, {**GOOD_WEIGHTS, "tier": 101, "travel_time": -1})

    def test_weights_have_exactly_the_declared_fields(self):
        missing = {k: v for k, v in GOOD_WEIGHTS.items() if k != "tier"}
        bad(WEIGHTS, missing)
        bad(WEIGHTS, {**GOOD_WEIGHTS, "extra": 0})
        bad(WEIGHTS, {**GOOD_WEIGHTS, "tier": 20.0})
        bad(WEIGHTS, {**GOOD_WEIGHTS, "tier": "20"})
        bad(WEIGHTS, {**GOOD_WEIGHTS, "tier": True})
        bad(WEIGHTS, [30, 20, 15, 15, 15, 5])
        bad(WEIGHTS, 100)
        bad(WEIGHTS, None)

    def test_unicode_and_look_alike_field_names_are_not_the_declared_fields(self):
        w = dict(GOOD_WEIGHTS)
        w["tіer"] = w.pop("tier")  # Cyrillic i
        bad(WEIGHTS, w)

    def test_quiet_hours_must_differ_and_stay_within_a_day(self):
        assert validate_value(D[QUIET], {"start_hour": 21, "end_hour": 7})
        bad(QUIET, {"start_hour": 7, "end_hour": 7})
        bad(QUIET, {"start_hour": 24, "end_hour": 7})
        bad(QUIET, {"start_hour": -1, "end_hour": 7})

    def test_a_returned_map_is_a_copy_not_the_callers_object(self):
        src = dict(GOOD_WEIGHTS)
        out = validate_value(D[WEIGHTS], src)
        src["tier"] = 99
        assert out["tier"] == 20


def test_every_registered_scalar_rejects_one_below_and_one_above():
    for d in REGISTRY.defs.values():
        if d.kind == "map":
            continue
        step = 1 if d.kind == "int" else 0.5
        for v in (d.lo - step, d.hi + step):
            with pytest.raises(InvalidValue):
                validate_value(d, v)
        validate_value(d, d.lo)
        validate_value(d, d.hi)


def test_rate_limit_example_from_the_issue_negative_window():
    bad(RATE, -5)
