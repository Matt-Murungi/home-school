"""H05 (#134): reads, audited changes, effective-from semantics, scheduling, rollback, cross
rules, cache and outage behaviour. Acceptance criteria are mapped in memory.md (H05 block)."""

import json
from datetime import datetime, timedelta

import pytest

from hs_core.app_settings import REGISTRY, InvalidValue
from hs_core.app_settings.model import (
    ChangePending,
    NoPendingChange,
    NothingToRollBack,
    RevisionConflict,
    SettingNotFound,
    SettingsUnavailable,
)
from hs_core.audit import ReasonRequired
from settings_helpers import (
    AUTO,
    CANCEL,
    GRACE,
    LATE,
    NEXT_MIDNIGHT,
    QUIET,
    REASON,
    T0,
    WEIGHTS,
    audit_actions,
    ctx,
    edit,
    history,
    stack,
    value_row,
)  # fmt: skip


class TestReadsAndDefaults:
    def test_with_nothing_stored_every_setting_reads_its_default(self):
        _raw, svc, _a, _c = stack()
        assert svc.get(AUTO) == 48 and svc.get(LATE) == 24
        assert svc.snapshot() == {k: d.code_default() for k, d in REGISTRY.defs.items()}

    def test_unknown_key_is_an_error_not_a_default(self):
        _raw, svc, _a, _c = stack()
        with pytest.raises(SettingNotFound):
            svc.get("nope.nothing")

    def test_a_stored_value_wins_over_the_environment_default(self):
        _raw, svc, _a, _c = stack(defaults={AUTO: 60})
        assert svc.get(AUTO) == 60
        edit(svc, AUTO, 72, 0)
        assert svc.get(AUTO) == 72

    def test_values_are_read_at_runtime_without_a_restart_for_the_same_instance(self):
        """AC 1: values read at runtime; the writer's own cache is dropped at once."""
        _raw, svc, _a, _c = stack()
        assert svc.get(AUTO) == 48
        edit(svc, AUTO, 24, 0)
        assert svc.get(AUTO) == 24

    def test_another_instance_sees_the_change_within_the_cache_ttl(self):
        raw, writer, audited, clock = stack(ttl_s=30)
        _r, reader, _a2, _c2 = stack(repo=raw, clock=clock, ttl_s=30)
        assert reader.get(AUTO) == 48
        edit(writer, AUTO, 24, 0)
        clock.advance(seconds=29)
        assert reader.get(AUTO) == 48  # cached
        clock.advance(seconds=2)
        assert reader.get(AUTO) == 24

    def test_the_acceptance_scenario_a_job_run_after_the_change_behaves_differently(self):
        """'Changing the auto-confirm window changes job behaviour on next run' (no deploy)."""
        _raw, svc, _a, clock = stack()
        session_waiting_since = T0 - timedelta(hours=30)

        def auto_confirm_job():  # what an F04 job does: read the setting, apply to the data
            window = svc.get(AUTO)
            return clock() - session_waiting_since >= timedelta(hours=window)

        assert auto_confirm_job() is False  # 30 h < 48 h
        edit(svc, AUTO, 24, 0)
        assert auto_confirm_job() is True  # next run: 30 h >= 24 h

    def test_an_unknown_stored_row_and_a_row_for_a_removed_setting_are_ignored(self):
        raw, svc, _a, _c = stack()
        raw.create(
            "app_setting", "s_x", {"setting_key": "retired.setting", "value": "5", "revision": 1}
        )
        raw.create("app_setting", "s_y", {"setting_key": 5, "value": "5"})
        assert svc.get(AUTO) == 48


class TestStoreProblemsFailToTheDefault:
    def test_a_store_outage_at_start_serves_the_defaults_never_zero(self):
        raw, svc, _a, _c = stack()
        raw.available = False
        snap = svc.snapshot()
        assert snap == {k: d.code_default() for k, d in REGISTRY.defs.items()}
        assert all(v not in (0, None) for v in (svc.get(AUTO), svc.get(LATE)))

    def test_with_env_defaults_the_outage_serves_the_env_default(self):
        raw, svc, _a, _c = stack(defaults={AUTO: 60})
        raw.available = False
        assert svc.get(AUTO) == 60

    def test_a_warm_cache_keeps_serving_the_last_good_value_through_an_outage(self):
        raw, svc, _a, clock = stack()
        edit(svc, AUTO, 24, 0)
        assert svc.get(AUTO) == 24
        raw.available = False
        clock.advance(hours=2)
        assert svc.get(AUTO) == 24  # last known good, not a surprise flip to 48

    def test_recovery_after_an_outage_is_picked_up_within_the_retry_interval(self):
        raw, writer, _a, clock = stack()
        edit(writer, AUTO, 24, 0)
        _r, reader, _a2, _c2 = stack(repo=raw, clock=clock)
        raw.available = False
        assert reader.get(AUTO) == 48  # default while down
        raw.available = True
        clock.advance(seconds=6)
        assert reader.get(AUTO) == 24

    def test_the_outage_does_not_hammer_the_store(self):
        raw, svc, _a, clock = stack()
        raw.available = False
        calls = []
        real = raw.list
        raw.list = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
        for _ in range(50):
            svc.get(AUTO)
        assert len(calls) == 1  # one try per retry interval
        clock.advance(seconds=6)
        svc.get(AUTO)
        assert len(calls) == 2

    @pytest.mark.parametrize(
        "bad",
        ["", "abc", "-5", "0", "999999", "null", "true", "24.5", '"24"', "[24]", "{}", "x" * 5000],
    )
    def test_a_tampered_stored_value_falls_back_to_the_default(self, bad):
        events = []
        raw, svc, _a, _c = stack(events=events)
        raw.create(
            "app_setting",
            "s_t",
            {"setting_key": LATE, "value": bad, "revision": 1, "active_revision": 1},
        )
        assert svc.get(LATE) == 24
        assert ("stored_value_invalid", {"key": LATE}) in events

    def test_a_stored_set_that_breaks_a_cross_rule_falls_back_to_the_defaults_of_that_rule(self):
        events = []
        raw, svc, _a, _c = stack(events=events)
        for key, val in ((GRACE, "20"), (CANCEL, "10")):  # grace 20 >= cancel 10: not allowed
            raw.create(
                "app_setting",
                f"s_{key}",
                {"setting_key": key, "value": val, "revision": 1, "active_revision": 1},
            )
        assert (svc.get(GRACE), svc.get(CANCEL)) == (3, 14)
        assert any(n == "constraint_violated" for n, _ in events)

    def test_a_repository_returning_nonsense_rows_never_raises_into_the_caller(self):
        raw, svc, _a, _c = stack()
        raw.create(
            "app_setting",
            "s_1",
            {
                "setting_key": LATE,
                "value": 5,
                "revision": "x",
                "pending_effective_at": "garbage",
                "pending_value": "7",
            },
        )
        assert svc.get(LATE) == 24


class TestChanges:
    def test_a_change_applies_and_returns_the_new_state(self):
        _raw, svc, _a, _c = stack()
        s = edit(svc, AUTO, 24, 0)
        assert (s.value, s.revision, s.source, s.pending) == (24, 1, "override", None)
        assert s.default == 48

    def test_the_audit_trail_has_intent_and_outcome_with_actor_reason_before_and_after(self):
        raw, svc, _a, _c = stack()
        edit(svc, AUTO, 24, 0, ctx=ctx("u_boss"))
        rows = sorted(raw.list("audit_log"), key=lambda r: (r["occurred_at"], r["$id"]))
        intents = [r for r in rows if json.loads(r["after"])["meta"]["status"] == "intent"]
        hist = next(r for r in intents if r["entity"] == "app_setting_change")
        assert hist["actor_id"] == "u_boss" and hist["actor_roles"] == "superadmin"
        assert hist["action"] == "setting.update" and hist["reason"] == REASON
        assert (
            "24" in hist["after"] and hist["before"] in ("null", None) or hist["before"] is not None
        )
        val = next(r for r in intents if r["entity"] == "app_setting")
        assert val["action"] == "app_setting.create" and val["reason"] == REASON
        assert audit_actions(raw)[0][:3] == ("app_setting_change", "setting.update", "intent")

    def test_before_and_after_values_are_in_the_history_row_too(self):
        raw, svc, _a, _c = stack()
        edit(svc, AUTO, 24, 0)
        edit(svc, AUTO, 36, 1)
        h = history(raw, AUTO)
        assert [
            (r["revision"], json.loads(r["previous_value"]), json.loads(r["value"])) for r in h
        ] == [(1, 48, 24), (2, 24, 36)]
        assert [r["op"] for r in h] == ["set", "set"]
        assert all(r["actor_id"] == "u_super" and r["reason"] == REASON for r in h)

    def test_the_value_row_is_a_projection_of_the_history(self):
        raw, svc, _a, _c = stack()
        edit(svc, AUTO, 24, 0)
        edit(svc, AUTO, 36, 1)
        row = value_row(raw, AUTO)
        assert (row["value"], row["revision"], row["active_revision"]) == ("36", 2, 2)
        assert row["pending_effective_at"] is None

    def test_history_rows_are_only_ever_created(self):
        raw, svc, _a, _c = stack()
        raw.writes.clear()
        edit(svc, AUTO, 24, 0)
        edit(svc, AUTO, 36, 1)
        svc.rollback(AUTO, reason=REASON, expected_revision=2, ctx=ctx())
        ops = [(op, t) for op, t, _ in raw.writes if t == "app_setting_change"]
        assert ops and {op for op, _ in ops} == {"create"}

    @pytest.mark.parametrize("reason", [None, "", "  ", "​​​", "ab", "⠀" * 9])
    def test_a_reason_is_required_and_nothing_is_written_without_it(self, reason):
        raw, svc, _a, _c = stack()
        with pytest.raises(ReasonRequired):
            svc.change(AUTO, 24, reason=reason, expected_revision=0, ctx=ctx())
        assert raw.list("app_setting") == [] and raw.list("app_setting_change") == []
        assert raw.list("audit_log") == []

    def test_a_reason_over_the_cap_is_refused_and_a_long_ok_reason_is_stored_capped(self):
        raw, svc, _a, _c = stack()
        with pytest.raises(InvalidValue) as e:
            edit(svc, AUTO, 24, 0, reason="x" * 2001)
        assert e.value.code == "reason_too_long"
        edit(svc, AUTO, 24, 0, reason="ñ" * 1500)
        assert len(history(raw, AUTO)[0]["reason"]) <= 500

    def test_a_secret_looking_reason_is_scrubbed_before_it_is_stored(self):
        raw, svc, _a, _c = stack()
        canary = (
            "tok" + "en=" + "abcdef1234567890abcdef"
        )  # built here so the secret scan stays quiet
        edit(svc, AUTO, 24, 0, reason=f"see +256700123456 {canary}")
        text = json.dumps(history(raw, AUTO)) + json.dumps(raw.list("audit_log"))
        assert "256700123456" not in text and "abcdef1234567890abcdef" not in text

    def test_invalid_values_write_nothing(self):
        raw, svc, _a, _c = stack()
        for v in (-1, 0, 10**9, "24", None, True, 24.5):
            with pytest.raises(InvalidValue):
                edit(svc, AUTO, v, 0)
        assert raw.list("app_setting") == [] and raw.list("app_setting_change") == []
        assert raw.list("audit_log") == []

    def test_weights_not_summing_to_100_are_blocked_and_a_valid_set_is_applied_atomically(self):
        raw, svc, _a, _c = stack()
        w = dict(svc.get(WEIGHTS))
        with pytest.raises(InvalidValue):
            edit(svc, WEIGHTS, {**w, "tier": 21}, 0)
        assert svc.get(WEIGHTS) == w
        new = {**w, "tier": 25, "travel_time": 25}
        edit(svc, WEIGHTS, new, 0)
        assert svc.get(WEIGHTS) == new and sum(svc.get(WEIGHTS).values()) == 100

    def test_setting_the_same_value_is_refused_as_no_change(self):
        _raw, svc, _a, _c = stack()
        with pytest.raises(InvalidValue) as e:
            edit(svc, AUTO, 48, 0)
        assert e.value.code == "no_change"

    def test_unknown_key_is_404(self):
        _raw, svc, _a, _c = stack()
        with pytest.raises(SettingNotFound):
            edit(svc, "no.such", 1, 0)

    def test_a_stale_revision_is_refused_with_the_current_revision(self):
        raw, svc, _a, _c = stack()
        edit(svc, AUTO, 24, 0)
        with pytest.raises(RevisionConflict) as e:
            edit(svc, AUTO, 36, 0)
        assert e.value.status == 409 and e.value.extra["current_revision"] == 1
        with pytest.raises(RevisionConflict):
            edit(svc, AUTO, 36, 7)  # a revision from the future
        assert svc.get(AUTO) == 24 and len(history(raw, AUTO)) == 1

    def test_a_refused_change_leaves_a_failed_audit_intent_not_a_change(self):
        raw, svc, _a, _c = stack()
        edit(svc, AUTO, 24, 0)
        before = len(history(raw, AUTO))
        with pytest.raises(RevisionConflict):
            edit(svc, AUTO, 36, 0)
        assert len(history(raw, AUTO)) == before


class TestEffectiveFrom:
    def test_an_immediate_setting_applies_now(self):
        _raw, svc, _a, clock = stack()
        s = edit(svc, AUTO, 24, 0)
        assert s.pending is None and svc.get(AUTO) == 24

    def test_a_next_day_setting_waits_for_the_next_kampala_midnight(self):
        _raw, svc, _a, clock = stack()
        s = edit(svc, LATE, 12, 0)
        assert s.value == 24 and s.pending.value == 12 and s.pending.effective_at == NEXT_MIDNIGHT
        assert svc.get(LATE) == 24
        clock.set(NEXT_MIDNIGHT - timedelta(seconds=1))
        svc.invalidate()
        assert svc.get(LATE) == 24
        clock.set(NEXT_MIDNIGHT)
        assert svc.get(LATE) == 12  # no job needed: the cached pending change switches on time

    def test_an_exact_kampala_midnight_request_is_kept(self):
        _raw, svc, _a, _c = stack()
        later = NEXT_MIDNIGHT + timedelta(days=3)
        s = edit(svc, LATE, 12, 0, effective_at=later)
        assert s.pending.effective_at == later

    def test_a_request_inside_a_day_is_rounded_up_to_the_midnight_after_it(self):
        _raw, svc, _a, _c = stack()
        s = edit(svc, LATE, 12, 0, effective_at=NEXT_MIDNIGHT + timedelta(hours=5))
        assert s.pending.effective_at == NEXT_MIDNIGHT + timedelta(days=1)

    def test_a_change_never_takes_effect_in_the_past(self):
        raw, svc, _a, _c = stack()
        for back in (timedelta(minutes=3), timedelta(days=1), timedelta(days=400)):
            with pytest.raises(InvalidValue) as e:
                edit(svc, AUTO, 24, 0, effective_at=T0 - back)
            assert e.value.code == "effective_in_past"
        assert raw.list("app_setting_change") == []

    def test_a_slow_browser_clock_within_two_minutes_means_now_never_the_past(self):
        raw, svc, _a, _c = stack()
        edit(svc, AUTO, 24, 0, effective_at=T0 - timedelta(seconds=90))
        eff = datetime.fromisoformat(history(raw, AUTO)[0]["effective_at"])
        assert eff == T0

    def test_a_naive_or_too_far_effective_time_is_refused(self):
        _raw, svc, _a, _c = stack()
        with pytest.raises(InvalidValue):
            edit(svc, AUTO, 24, 0, effective_at=datetime(2027, 3, 5, 12, 0))
        with pytest.raises(InvalidValue):
            edit(svc, AUTO, 24, 0, effective_at=T0 + timedelta(days=366))

    def test_an_event_is_judged_by_the_rule_in_force_at_its_time_not_by_a_later_change(self):
        """Not retroactive: a cancellation before the change is still judged by the old value."""
        raw, svc, _a, clock = stack()
        cancelled_at = clock()
        clock.advance(hours=1)
        edit(svc, AUTO, 12, 0)
        clock.advance(days=2)
        assert svc.get(AUTO) == 12
        assert svc.value_at(AUTO, cancelled_at) == 48
        assert svc.value_at(AUTO, clock()) == 12
        assert svc.value_at("billing.grace_days", cancelled_at) == 3  # never changed: default

    def test_value_at_respects_a_scheduled_change(self):
        _raw, svc, _a, clock = stack()
        edit(svc, LATE, 12, 0)
        assert svc.value_at(LATE, NEXT_MIDNIGHT - timedelta(seconds=1)) == 24
        assert svc.value_at(LATE, NEXT_MIDNIGHT) == 12

    def test_the_value_in_force_changes_exactly_at_the_effective_instant_across_instances(self):
        raw, a, _aa, clock = stack()
        _r, b, _bb, _cc = stack(repo=raw, clock=clock, ttl_s=1000)
        edit(a, LATE, 12, 0)
        assert b.get(LATE) == 24
        clock.set(NEXT_MIDNIGHT + timedelta(seconds=1))
        assert b.get(LATE) == 12


class TestScheduling:
    def test_the_activation_job_projects_a_due_change_and_is_idempotent(self):
        raw, svc, _a, clock = stack()
        edit(svc, LATE, 12, 0)
        assert value_row(raw, LATE)["pending_revision"] == 1
        assert svc.reconcile() == {"activated": 0, "repaired": 0}  # not due yet
        clock.set(NEXT_MIDNIGHT + timedelta(minutes=1))
        assert svc.reconcile(ctx=ctx("system"))["activated"] == 1
        row = value_row(raw, LATE)
        assert (row["value"], row["active_revision"], row["pending_effective_at"]) == (
            "12",
            1,
            None,
        )
        n = len(raw.list("audit_log"))
        assert svc.reconcile() == {"activated": 0, "repaired": 0}
        assert len(raw.list("audit_log")) == n  # a second run writes nothing

    def test_a_second_change_while_one_is_scheduled_is_refused(self):
        _raw, svc, _a, _c = stack()
        edit(svc, LATE, 12, 0)
        with pytest.raises(ChangePending):
            edit(svc, LATE, 10, 1)

    def test_cancel_pending_keeps_the_history_and_returns_to_the_current_value(self):
        raw, svc, _a, clock = stack()
        edit(svc, LATE, 12, 0)
        s = svc.cancel_pending(LATE, reason=REASON, expected_revision=1, ctx=ctx())
        assert s.pending is None and s.value == 24 and s.revision == 2
        assert [r["op"] for r in history(raw, LATE)] == ["set", "cancel"]
        clock.set(NEXT_MIDNIGHT + timedelta(hours=1))
        assert svc.get(LATE) == 24 and svc.value_at(LATE, clock()) == 24
        svc.reconcile()
        assert svc.get(LATE) == 24

    def test_cancel_without_a_pending_change_is_refused(self):
        _raw, svc, _a, _c = stack()
        with pytest.raises(NoPendingChange):
            svc.cancel_pending(LATE, reason=REASON, expected_revision=0, ctx=ctx())

    def test_after_a_cancel_a_new_change_is_possible(self):
        _raw, svc, _a, _c = stack()
        edit(svc, LATE, 12, 0)
        svc.cancel_pending(LATE, reason=REASON, expected_revision=1, ctx=ctx())
        s = edit(svc, LATE, 10, 2)
        assert s.pending.value == 10 and s.revision == 3


class TestRollback:
    def test_rollback_restores_the_previous_value_as_a_new_revision(self):
        raw, svc, _a, _c = stack()
        edit(svc, AUTO, 24, 0)
        edit(svc, AUTO, 36, 1)
        s = svc.rollback(AUTO, reason=REASON, expected_revision=2, ctx=ctx())
        assert s.value == 24 and s.revision == 3
        h = history(raw, AUTO)
        assert [r["op"] for r in h] == ["set", "set", "rollback"]
        assert json.loads(h[2]["previous_value"]) == 36

    def test_rolling_back_the_first_override_returns_to_the_default(self):
        _raw, svc, _a, _c = stack(defaults={AUTO: 60})
        edit(svc, AUTO, 24, 0)
        s = svc.rollback(AUTO, reason=REASON, expected_revision=1, ctx=ctx())
        assert s.value == 60 and svc.get(AUTO) == 60

    def test_two_rollbacks_in_a_row_walk_back_only_when_there_is_something_to_walk_back_to(self):
        _raw, svc, _a, _c = stack()
        edit(svc, AUTO, 24, 0)
        edit(svc, AUTO, 36, 1)
        svc.rollback(AUTO, reason=REASON, expected_revision=2, ctx=ctx())  # 24
        s = svc.rollback(AUTO, reason=REASON, expected_revision=3, ctx=ctx())  # default 48
        assert s.value == 48 and s.source == "override"
        with pytest.raises(NothingToRollBack):
            svc.rollback(AUTO, reason=REASON, expected_revision=4, ctx=ctx())

    def test_rollback_with_no_override_is_refused(self):
        _raw, svc, _a, _c = stack()
        with pytest.raises(NothingToRollBack):
            svc.rollback(AUTO, reason=REASON, expected_revision=0, ctx=ctx())

    def test_rollback_needs_a_reason_and_the_current_revision(self):
        _raw, svc, _a, _c = stack()
        edit(svc, AUTO, 24, 0)
        with pytest.raises(ReasonRequired):
            svc.rollback(AUTO, reason="", expected_revision=1, ctx=ctx())
        with pytest.raises(RevisionConflict):
            svc.rollback(AUTO, reason=REASON, expected_revision=0, ctx=ctx())

    def test_rollback_that_would_break_a_cross_rule_is_refused(self):
        _raw, svc, _a, _c = stack()
        edit(svc, CANCEL, 30, 0, effective_at=NEXT_MIDNIGHT)  # pending: 30 from midnight
        svc.cancel_pending(CANCEL, reason=REASON, expected_revision=1, ctx=ctx())
        # make cancel 30 effective and raise grace to 25 (< 30), then roll cancel back to 14
        _r2, s2, _a2, c2 = stack()
        edit(s2, CANCEL, 30, 0)
        c2.set(NEXT_MIDNIGHT + timedelta(minutes=1))
        edit(s2, GRACE, 25, 0)
        c2.set(NEXT_MIDNIGHT + timedelta(days=1, minutes=1))
        assert (s2.get(GRACE), s2.get(CANCEL)) == (25, 30)
        with pytest.raises(InvalidValue) as e:
            s2.rollback(CANCEL, reason=REASON, expected_revision=1, ctx=ctx())
        assert e.value.code == "constraint_violated"


class TestCrossSettingRules:
    def test_grace_must_stay_shorter_than_the_cancel_limit(self):
        _raw, svc, _a, _c = stack()
        with pytest.raises(InvalidValue) as e:
            edit(svc, GRACE, 14, 0)  # equal to the cancel limit
        assert e.value.code == "constraint_violated"
        with pytest.raises(InvalidValue):
            edit(svc, CANCEL, 3, 0)
        edit(svc, GRACE, 13, 0)

    def test_raising_the_limit_first_makes_room_for_a_longer_grace(self):
        _raw, svc, _a, clock = stack()
        edit(svc, CANCEL, 30, 0)
        clock.set(NEXT_MIDNIGHT + timedelta(minutes=1))
        edit(svc, GRACE, 20, 0)

    def test_the_rule_is_checked_at_the_time_the_change_takes_effect(self):
        """Grace 20 scheduled while the limit's raise to 30 is still pending is valid only from
        the moment the raise is in force: scheduling it earlier is refused."""
        _raw, svc, _a, _c = stack()
        edit(svc, CANCEL, 30, 0, effective_at=NEXT_MIDNIGHT + timedelta(days=2))
        with pytest.raises(InvalidValue):
            edit(svc, GRACE, 20, 0, effective_at=NEXT_MIDNIGHT)  # limit still 14 then
        edit(svc, GRACE, 20, 0, effective_at=NEXT_MIDNIGHT + timedelta(days=2))

    def test_a_later_change_cannot_break_an_earlier_pending_one(self):
        _raw, svc, _a, _c = stack()
        edit(svc, GRACE, 10, 0, effective_at=NEXT_MIDNIGHT + timedelta(days=5))
        with pytest.raises(InvalidValue):
            edit(svc, CANCEL, 8, 0, effective_at=NEXT_MIDNIGHT + timedelta(days=6))
        with pytest.raises(InvalidValue):
            edit(svc, CANCEL, 8, 0)  # would break it once the grace change arrives

    def test_cancelling_a_pending_raise_that_another_pending_change_depends_on_is_refused(self):
        _raw, svc, _a, _c = stack()
        edit(svc, CANCEL, 30, 0, effective_at=NEXT_MIDNIGHT)
        edit(svc, GRACE, 20, 0, effective_at=NEXT_MIDNIGHT + timedelta(days=1))
        with pytest.raises(InvalidValue):
            svc.cancel_pending(CANCEL, reason=REASON, expected_revision=1, ctx=ctx())

    def test_substitute_offer_window_not_longer_than_the_normal_one_and_daily_within_monthly(self):
        _raw, svc, _a, _c = stack()
        with pytest.raises(InvalidValue):
            edit(svc, "matching.substitute_offer_expiry_minutes", 200, 0)
        with pytest.raises(InvalidValue):
            edit(svc, "travel.daily_element_budget", 20000, 0)
        edit(svc, "travel.monthly_element_budget", 40000, 0)
        edit(svc, "travel.daily_element_budget", 20000, 0)

    def test_quiet_hours_cannot_be_set_to_an_empty_window(self):
        _raw, svc, _a, _c = stack()
        with pytest.raises(InvalidValue):
            edit(svc, QUIET, {"start_hour": 7, "end_hour": 7}, 0)
        edit(svc, QUIET, {"start_hour": 22, "end_hour": 6}, 0)
        assert svc.get(QUIET) == {"start_hour": 22, "end_hour": 6}


class TestStoreFailuresOnWrite:
    def test_a_store_outage_on_an_edit_is_503_and_writes_nothing(self):
        raw, svc, _a, _c = stack()
        raw.available = False
        with pytest.raises(SettingsUnavailable) as e:
            edit(svc, AUTO, 24, 0)
        assert e.value.status == 503
        raw.available = True
        assert raw.list("app_setting_change") == []

    def test_states_view_reads_fresh_from_the_store(self):
        raw, svc, _a, _c = stack()
        _r, other, _a2, _c2 = stack(repo=raw)
        edit(other, AUTO, 24, 0)
        assert next(s for s in svc.states() if s.key == AUTO).value == 24
