"""The notification events of PRD 7.13 and SR 8, and their default templates.

COPY STATUS: every title and body below is English DRAFT FOR OWNER REVIEW (N01 #126). The event
keys, channels, variables, categories and priorities are the engineering contract; the wording is
a placeholder the owner approves or edits (a super admin can publish new versions without a
deploy). Push copy never names the child (lock-screen privacy); safeguarding copy carries no case
detail (ADR 0005). The audience is part of the key because the copy differs per role.

Priorities (not stored in the table, which only has `category`):
  critical     sent at once, never deferred by quiet hours, never rate limited, push failure falls
               back to email: all safeguarding events plus substitute assigned, session cancelled,
               payment failed (SR 8)
  operational  normal; push waits out quiet hours unless the event is `urgent`
  marketing    can be muted per user; push waits out quiet hours
"""

from __future__ import annotations

from dataclasses import dataclass

from .engine import VarSpec
from .templates import Channel, TemplateSpec

COPY_STATUS = "draft for owner review (N01 #126)"
SEED_ACTOR = "system:seed-draft"
P, E, A = Channel.PUSH, Channel.EMAIL, Channel.IN_APP


@dataclass(frozen=True)
class EventDef:
    key: str
    role: str  # parent | teacher | admin
    category: str  # operational | safeguarding | marketing
    priority: str  # critical | operational | marketing
    channels: tuple[Channel, ...]
    variables: tuple[VarSpec, ...]
    push_title: str
    push_body: str
    email_subject: str = ""
    email_body: str = ""
    urgent: bool = False  # operational, but time-boxed: not deferred by quiet hours
    fallback_email: bool = False  # push failure falls back to email (critical events)

    @property
    def template_channels(self) -> tuple[Channel, ...]:
        """Channels that have a template: the delivery channels, plus email when it is only the
        push-failure fallback."""
        if self.fallback_email and E not in self.channels:
            return (*self.channels, E)
        return self.channels


def _v(*names: str) -> tuple[VarSpec, ...]:
    types = {
        "lesson_time": "datetime",
        "time": "datetime",
        "first_lesson": "datetime",
        "amount": "ugx",
        "due_date": "date",
        "respond_within_minutes": "int",
        "teacher_first_name": "str",
        "substitute_first_name": "str",
        "subject": "str",
        "reason_label": "str",
        "headline": "str",
        "document_label": "str",
        "expiry_date": "date",
        "tier_name": "str",
    }
    pii = {"teacher_first_name", "substitute_first_name"}
    return tuple(VarSpec(n, types[n], n in pii) for n in names)


_OPEN = "\n\nOpen the Home School app for details."
EVENTS: dict[str, EventDef] = {}


def _add(
    key,
    role,
    channels,
    variables,
    push_title,
    push_body,
    subject="",
    body="",
    *,
    category="operational",
    priority=None,
    urgent=False,
    fallback=False,
):
    priority = priority or {"safeguarding": "critical", "marketing": "marketing"}.get(
        category, "operational"
    )
    EVENTS[key] = EventDef(
        key,
        role,
        category,
        priority,
        tuple(channels),
        _v(*variables),
        push_title,
        push_body,
        subject,
        body,
        urgent,
        fallback or priority == "critical",
    )


# --- teacher offers and assignments ---------------------------------------------------------
_add(
    "assignment_offer_teacher",
    "teacher",
    [P, A],
    ["subject", "respond_within_minutes"],
    "New lesson offer",
    "A new {{subject}} lesson is waiting. Respond within {{respond_within_minutes}} minutes.",
    urgent=True,
)
_add(
    "teacher_assigned_parent",
    "parent",
    [P, E, A],
    ["teacher_first_name", "first_lesson"],
    "Tutor assigned",
    "{{teacher_first_name}} will be your tutor. First lesson: {{first_lesson}}.",
    "Your tutor is assigned",
    "Good news: {{teacher_first_name}} will be your tutor.\n\nFirst lesson: {{first_lesson}}."
    + _OPEN,
)
_add(
    "substitute_assigned_parent",
    "parent",
    [P, E, A],
    ["substitute_first_name", "lesson_time"],
    "Substitute tutor assigned",
    "{{substitute_first_name}} will take your lesson on {{lesson_time}}.",
    "A substitute tutor is assigned",
    "{{substitute_first_name}} will take your lesson on {{lesson_time}}." + _OPEN,
    priority="critical",
)
# --- reminders -------------------------------------------------------------------------------
for _role in ("parent", "teacher"):
    _add(
        f"session_reminder_day_before_{_role}",
        _role,
        [P, A],
        ["lesson_time"],
        "Lesson tomorrow",
        "Reminder: your lesson is on {{lesson_time}}.",
    )
    _add(
        f"session_reminder_1h_{_role}",
        _role,
        [P, A],
        ["lesson_time"],
        "Lesson in one hour",
        "Your lesson starts at {{lesson_time}}.",
        urgent=True,
    )
# --- session lifecycle -----------------------------------------------------------------------
_add(
    "session_checked_in_parent",
    "parent",
    [P, A],
    ["time"],
    "Tutor checked in",
    "Your tutor checked in at {{time}}.",
)
_add(
    "session_checked_out_parent",
    "parent",
    [P, A],
    ["time"],
    "Lesson finished",
    "Your tutor checked out at {{time}}.",
)
_add(
    "session_confirm_request_parent",
    "parent",
    [P, A],
    ["lesson_time"],
    "Please confirm the lesson",
    "Did the lesson on {{lesson_time}} take place? Please confirm.",
)
_add(
    "session_log_published_parent",
    "parent",
    [P, A],
    ["lesson_time"],
    "Lesson notes ready",
    "Notes for the lesson on {{lesson_time}} are ready to read.",
)
_add(
    "session_cancelled_parent",
    "parent",
    [P, E, A],
    ["lesson_time", "reason_label"],
    "Lesson cancelled",
    "The lesson on {{lesson_time}} was cancelled: {{reason_label}}.",
    "Your lesson was cancelled",
    "The lesson on {{lesson_time}} was cancelled.\n\nReason: {{reason_label}}." + _OPEN,
    priority="critical",
)
_add(
    "session_cancelled_teacher",
    "teacher",
    [P, A],
    ["lesson_time", "reason_label"],
    "Lesson cancelled",
    "The lesson on {{lesson_time}} was cancelled: {{reason_label}}.",
    "A lesson was cancelled",
    "The lesson on {{lesson_time}} was cancelled.\n\nReason: {{reason_label}}." + _OPEN,
    priority="critical",
)
_add(
    "session_disputed_teacher",
    "teacher",
    [P, A],
    ["lesson_time"],
    "Lesson disputed",
    "The lesson on {{lesson_time}} was disputed. Open the app to respond.",
)
# --- money -----------------------------------------------------------------------------------
_add(
    "payment_due_parent",
    "parent",
    [P, E, A],
    ["amount", "due_date"],
    "Payment due",
    "{{amount}} is due on {{due_date}}.",
    "Payment due",
    "A payment of {{amount}} is due on {{due_date}}." + _OPEN,
)
_add(
    "payment_failed_parent",
    "parent",
    [P, E, A],
    ["amount"],
    "Payment failed",
    "Your payment of {{amount}} did not go through. Please try again.",
    "Your payment did not go through",
    "Your payment of {{amount}} did not go through. Please try again in the app." + _OPEN,
    priority="critical",
)
_add(
    "payment_received_parent",
    "parent",
    [P, E, A],
    ["amount"],
    "Payment received",
    "We received your payment of {{amount}}. Thank you.",
    "Payment received",
    "We received your payment of {{amount}}. Thank you." + _OPEN,
)
_add(
    "payout_sent_teacher",
    "teacher",
    [P, E, A],
    ["amount"],
    "Payout sent",
    "A payout of {{amount}} has been sent.",
    "Your payout was sent",
    "A payout of {{amount}} has been sent to you." + _OPEN,
)
# --- matching --------------------------------------------------------------------------------
for _role, _who in (("parent", "your child"), ("admin", "a family")):
    _add(
        f"matching_failed_{_role}",
        _role,
        [P, E, A],
        [],
        "No tutor found yet",
        f"We could not find a tutor for {_who} yet. We are still looking."
        if _role == "parent"
        else "A matching request failed and needs attention.",
        "Tutor matching update",
        (
            "We could not find a tutor yet and are still looking."
            if _role == "parent"
            else "A matching request failed and needs attention."
        )
        + _OPEN,
    )
    _add(
        f"no_substitute_{_role}",
        _role,
        [P, E, A],
        [],
        "No substitute available",
        "No substitute tutor is available yet."
        if _role == "parent"
        else "No substitute was found for a lesson. Action needed.",
        "No substitute available yet",
        (
            "No substitute tutor is available yet. We are working on it."
            if _role == "parent"
            else "No substitute was found for a lesson. Action needed."
        )
        + _OPEN,
    )
# --- teacher account -------------------------------------------------------------------------
_add(
    "document_expiring_teacher",
    "teacher",
    [P, E, A],
    ["document_label", "expiry_date"],
    "Document expiring",
    "Your {{document_label}} expires on {{expiry_date}}.",
    "A document is expiring",
    "Your {{document_label}} expires on {{expiry_date}}. Please upload a new one." + _OPEN,
)
_add(
    "tier_changed_teacher",
    "teacher",
    [P, E, A],
    ["tier_name"],
    "Your tier changed",
    "Your tier is now {{tier_name}}.",
    "Your tier changed",
    "Your tier is now {{tier_name}}." + _OPEN,
)
# --- safeguarding (lead and backup; minimal detail) -------------------------------------------
_add(
    "safeguarding_concern_admin",
    "admin",
    [P, E, A],
    [],
    "Safeguarding: action needed",
    "A safeguarding case needs your attention now.",
    "Safeguarding: action needed",
    "A safeguarding case needs your attention.\n\nOpen the Home School admin app to view it. "
    "Please acknowledge as soon as you can.",
    category="safeguarding",
)
# --- marketing (sample; PRD lists none yet) ---------------------------------------------------
_add(
    "marketing_announcement_parent",
    "parent",
    [P, E, A],
    ["headline"],
    "News from Home School",
    "{{headline}}",
    "News from Home School",
    "{{headline}}" + _OPEN,
    category="marketing",
)


def default_templates() -> list[TemplateSpec]:
    out: list[TemplateSpec] = []
    for ev in EVENTS.values():
        for ch in ev.template_channels:
            if ch is E:
                title, body = ev.email_subject, ev.email_body
            else:
                title, body = ev.push_title, ev.push_body
            out.append(
                TemplateSpec(
                    ev.key, ch, "en", 1, ev.category, title, body, ev.variables, True, SEED_ACTOR
                )
            )
    return out
