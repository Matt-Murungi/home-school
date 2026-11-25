"""The subscription state machine as pure data (SR 5.2, 7.8; issue B03).

`TRANSITIONS` maps (status, trigger) to the new status and the domain events the change implies.
`transition()` only looks the pair up: it never sends anything, never touches storage. The caller
(`BillingService`) persists the change and hands the events to the notifier (N03) and the session
scheduler (S01). Anything not in the table raises `ForbiddenTransition`.

    pending_payment --payment_confirmed--> matching --teacher_assigned--> active
    active <--pause / resume--> paused
    active --payment_overdue--> past_due --payment_confirmed--> active
    past_due --grace_expired--> cancelled
    pending_payment | matching | active | paused | past_due --cancel--> cancelled (terminal)

Self loops (`payment_confirmed` while matching/active/paused, `teacher_assigned` while active) are
real transitions: a renewal payment or a rematch is recorded and audited, the status stays.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Status(StrEnum):
    pending_payment = "pending_payment"
    matching = "matching"
    active = "active"
    paused = "paused"
    past_due = "past_due"
    cancelled = "cancelled"


class Trigger(StrEnum):
    payment_confirmed = "payment_confirmed"
    teacher_assigned = "teacher_assigned"
    pause = "pause"
    resume = "resume"
    payment_overdue = "payment_overdue"  # grace period after the due date has run out
    grace_expired = "grace_expired"  # past_due for too long: give up
    cancel = "cancel"


class EventName(StrEnum):
    first_payment_confirmed = "first_payment_confirmed"
    matching_requested = "matching_requested"  # S/MAT-1: create the match_request
    subscription_activated = "subscription_activated"
    subscription_reactivated = "subscription_reactivated"
    renewal_paid = "renewal_paid"
    teacher_changed = "teacher_changed"
    subscription_paused = "subscription_paused"
    subscription_resumed = "subscription_resumed"
    subscription_past_due = "subscription_past_due"
    subscription_cancelled = "subscription_cancelled"
    sessions_cancel_requested = "sessions_cancel_requested"  # S01 cancel_future
    sessions_regenerate_requested = "sessions_regenerate_requested"  # S01 regenerate


class ForbiddenTransition(ValueError):
    """The (status, trigger) pair is not in the table."""

    def __init__(self, frm, trigger) -> None:
        self.frm, self.trigger = frm, trigger
        super().__init__(f"forbidden subscription transition: {frm!s} on {trigger!s}")


@dataclass(frozen=True)
class Transition:
    frm: Status
    trigger: Trigger
    to: Status
    events: tuple[EventName, ...] = ()


E_ = EventName
S, T = Status, Trigger

_ROWS: tuple[Transition, ...] = (
    Transition(
        S.pending_payment,
        T.payment_confirmed,
        S.matching,
        (E_.first_payment_confirmed, E_.matching_requested),
    ),
    Transition(S.pending_payment, T.cancel, S.cancelled, (E_.subscription_cancelled,)),
    Transition(S.matching, T.teacher_assigned, S.active, (E_.subscription_activated,)),
    Transition(S.matching, T.payment_confirmed, S.matching, (E_.renewal_paid,)),
    Transition(S.matching, T.cancel, S.cancelled, (E_.subscription_cancelled,)),
    Transition(S.active, T.payment_confirmed, S.active, (E_.renewal_paid,)),
    Transition(S.active, T.teacher_assigned, S.active, (E_.teacher_changed,)),
    Transition(S.active, T.pause, S.paused, (E_.subscription_paused, E_.sessions_cancel_requested)),
    Transition(S.active, T.payment_overdue, S.past_due, (E_.subscription_past_due,)),
    Transition(
        S.active, T.cancel, S.cancelled, (E_.subscription_cancelled, E_.sessions_cancel_requested)
    ),
    Transition(
        S.paused, T.resume, S.active, (E_.subscription_resumed, E_.sessions_regenerate_requested)
    ),
    Transition(S.paused, T.payment_confirmed, S.paused, (E_.renewal_paid,)),
    Transition(
        S.paused, T.cancel, S.cancelled, (E_.subscription_cancelled, E_.sessions_cancel_requested)
    ),
    Transition(
        S.past_due,
        T.payment_confirmed,
        S.active,
        (E_.subscription_reactivated, E_.sessions_regenerate_requested),
    ),
    Transition(
        S.past_due,
        T.grace_expired,
        S.cancelled,
        (E_.subscription_cancelled, E_.sessions_cancel_requested),
    ),
    Transition(
        S.past_due, T.cancel, S.cancelled, (E_.subscription_cancelled, E_.sessions_cancel_requested)
    ),
)

TRANSITIONS: dict[tuple[Status, Trigger], Transition] = {(t.frm, t.trigger): t for t in _ROWS}


def transition(status: Status | str, trigger: Trigger | str) -> Transition:
    """Look up the transition or raise `ForbiddenTransition`. Pure: no I/O, no sending."""
    try:
        key = (Status(status), Trigger(trigger))
    except ValueError:
        raise ForbiddenTransition(status, trigger) from None
    try:
        return TRANSITIONS[key]
    except KeyError:
        raise ForbiddenTransition(key[0], key[1]) from None
