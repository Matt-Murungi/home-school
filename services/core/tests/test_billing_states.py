"""B03: the subscription state machine (pure table), every allowed and forbidden transition."""

from __future__ import annotations

import itertools
import random

import pytest

from hs_core.billing.states import (
    TRANSITIONS,
    EventName,
    ForbiddenTransition,
    Status,
    Trigger,
    transition,
)

ALLOWED = {
    (Status.pending_payment, Trigger.payment_confirmed): Status.matching,
    (Status.pending_payment, Trigger.cancel): Status.cancelled,
    (Status.matching, Trigger.teacher_assigned): Status.active,
    (Status.matching, Trigger.payment_confirmed): Status.matching,
    (Status.matching, Trigger.cancel): Status.cancelled,
    (Status.active, Trigger.payment_confirmed): Status.active,
    (Status.active, Trigger.teacher_assigned): Status.active,
    (Status.active, Trigger.pause): Status.paused,
    (Status.active, Trigger.payment_overdue): Status.past_due,
    (Status.active, Trigger.cancel): Status.cancelled,
    (Status.paused, Trigger.resume): Status.active,
    (Status.paused, Trigger.payment_confirmed): Status.paused,
    (Status.paused, Trigger.cancel): Status.cancelled,
    (Status.past_due, Trigger.payment_confirmed): Status.active,
    (Status.past_due, Trigger.grace_expired): Status.cancelled,
    (Status.past_due, Trigger.cancel): Status.cancelled,
}


def test_the_table_is_exactly_the_documented_set():
    assert {k: v.to for k, v in TRANSITIONS.items()} == ALLOWED


@pytest.mark.parametrize("pair", sorted(ALLOWED, key=str))
def test_every_allowed_transition_succeeds_and_lands_where_documented(pair):
    status, trigger = pair
    t = transition(status, trigger)
    assert (t.frm, t.trigger, t.to) == (status, trigger, ALLOWED[pair])


@pytest.mark.parametrize(
    "pair",
    sorted(
        (p for p in itertools.product(Status, Trigger) if p not in ALLOWED),
        key=str,
    ),
)
def test_every_other_pair_is_forbidden_with_a_named_error(pair):
    status, trigger = pair
    with pytest.raises(ForbiddenTransition) as e:
        transition(status, trigger)
    assert e.value.frm == status and e.value.trigger == trigger
    assert isinstance(e.value, ValueError)


def test_cancelled_is_terminal():
    for trigger in Trigger:
        with pytest.raises(ForbiddenTransition):
            transition(Status.cancelled, trigger)


def test_unknown_status_or_trigger_is_forbidden_not_a_keyerror():
    with pytest.raises(ForbiddenTransition):
        transition("archived", Trigger.cancel)
    with pytest.raises(ForbiddenTransition):
        transition(Status.active, "explode")


def test_strings_are_accepted_like_the_stored_enum_values():
    assert transition("pending_payment", "payment_confirmed").to == Status.matching


def test_status_values_are_the_schema_enum():
    import json
    from pathlib import Path

    p = Path(__file__).resolve().parents[3] / "appwrite/schema/sessions.json"
    tables = {t["$id"]: t for t in json.loads(p.read_text())["tables"]}
    cols = {c["key"]: c for c in tables["subscription"]["columns"]}
    assert [s.value for s in Status] == cols["status"]["elements"]


def test_transitions_return_the_events_they_imply_without_sending_anything():
    assert transition(Status.pending_payment, Trigger.payment_confirmed).events == (
        EventName.first_payment_confirmed,
        EventName.matching_requested,
    )
    assert (
        EventName.subscription_activated
        in transition(Status.matching, Trigger.teacher_assigned).events
    )
    cancel = transition(Status.active, Trigger.cancel).events
    assert EventName.subscription_cancelled in cancel
    assert EventName.sessions_cancel_requested in cancel
    assert (
        EventName.sessions_regenerate_requested in transition(Status.paused, Trigger.resume).events
    )
    assert (
        EventName.subscription_past_due in transition(Status.active, Trigger.payment_overdue).events
    )
    assert transition(Status.past_due, Trigger.payment_confirmed).events[0] == (
        EventName.subscription_reactivated
    )
    assert EventName.sessions_cancel_requested in transition(Status.active, Trigger.pause).events


def test_every_transition_declares_at_least_one_event_except_pure_self_loops():
    for key, t in TRANSITIONS.items():
        assert t.events or t.to == key[0], key


def test_random_sequences_only_ever_visit_documented_states():
    rng = random.Random(1234)
    for _ in range(400):
        status = Status.pending_payment
        for _ in range(30):
            trigger = rng.choice(list(Trigger))
            try:
                t = transition(status, trigger)
            except ForbiddenTransition:
                assert (status, trigger) not in ALLOWED
                continue
            assert (status, trigger) in ALLOWED
            status = t.to
            assert status in Status
        # cancelled is absorbing: nothing ever left it
    # reachability sanity: every status can be reached
    seen = {Status.pending_payment}
    frontier = [Status.pending_payment]
    while frontier:
        s = frontier.pop()
        for (frm, _), t in TRANSITIONS.items():
            if frm == s and t.to not in seen:
                seen.add(t.to)
                frontier.append(t.to)
    assert seen == set(Status)
