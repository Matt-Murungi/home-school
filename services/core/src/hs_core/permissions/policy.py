"""Policy switches and constants for the permission model (ADR 0012, SR 6.2).

Everything an owner answer can change is here, one line each. Nothing here is read from the
environment: these are product rules, not deployment settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

# Appwrite labels (ADR 0012 principle 4). Alphanumeric, lower case, exact match only.
LABELS = ("parent", "guardian", "teacher", "opsadmin", "curator", "safeguardinglead", "superadmin")

# SR 6.1 role names -> labels (underscores removed). Used when a role name comes from the SR text.
SR_ROLE_TO_LABEL = {
    "parent": "parent",
    "guardian": "guardian",
    "teacher": "teacher",
    "ops_admin": "opsadmin",
    "curator": "curator",
    "safeguarding_lead": "safeguardinglead",
    "super_admin": "superadmin",
}

# assignment.end_reason values after which the home address is withdrawn at once (ADR 0012
# "removed ... at once on cancellation or a safeguarding hold"). The others keep the 24 h
# grace after the last session. Owner question 27.
IMMEDIATE_ADDRESS_REVOKE_REASONS = frozenset(
    {"parent_cancelled", "admin", "teacher_withdrew", "rematch_requested"}
)
# session statuses that count as "a session took place" for the 24 h rule.
HELD_SESSION_STATUSES = frozenset(
    {
        "checked_in",
        "checked_out",
        "awaiting_confirmation",
        "confirmed",
        "disputed",
        "incomplete",
        "no_show",
    }
)
# session statuses that void a substitute cover (the substitute never attends).
CANCELLED_SESSION_STATUSES = frozenset(
    {"cancelled_by_parent_late", "cancelled_by_parent_ontime", "cancelled_by_teacher", "void"}
)

GUARDIAN_SCOPES = ("children", "logs", "sessions", "billing", "address", "consents")


@dataclass(frozen=True)
class Policy:
    # Owner question 12 (ADR 0012, deviation from SR 6.2 "Read (cases)"): the safeguarding lead
    # reads every audit_log row through a row-level `read("label:safeguardinglead")`. Default is
    # the recorded recommendation (accept for the pilot). False = ops admin only.
    audit_log_lead_read: bool = True
    # Owner question 28: an unassigned open case is invisible to every client (a lead sees it
    # only after assignment). True would add `read("label:safeguardinglead")` to unassigned
    # cases so any lead can triage. Default False: strictest reading of the scope rule.
    unassigned_cases_visible_to_leads: bool = False
    # A closed case stays readable (case, actions, status) by its assigned lead; the related
    # child/address/log/document rows are withdrawn at close either way (ADR 0012 scope rule).
    lead_keeps_closed_case_record: bool = True
    # ADR 0012: address until 24 h after the last session; substitute until 24 h after the
    # covered session.
    address_grace: timedelta = timedelta(hours=24)
    substitute_grace: timedelta = timedelta(hours=24)
    # How far back the reconcile job looks for recently touched business rows.
    reconcile_lookback: timedelta = timedelta(hours=48)


DEFAULT_POLICY = Policy()


def is_label(value: object) -> bool:
    """Exact, case-sensitive match against the known labels: `ops_admin`, `OpsAdmin`, `opsadmin `
    and anything else grant nothing."""
    return isinstance(value, str) and value in LABELS


def clean_labels(values: object) -> frozenset[str]:
    if not isinstance(values, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(v for v in values if is_label(v))


def roles_to_labels(roles: list[str]) -> list[str]:
    """SR role names or labels -> the sorted label list the core service may set. Unknown names
    raise: a typo must never silently grant or drop a role."""
    out: set[str] = set()
    for r in roles:
        if r in SR_ROLE_TO_LABEL:
            out.add(SR_ROLE_TO_LABEL[r])
        elif is_label(r):
            out.add(r)
        else:
            raise ValueError(f"unknown role or label: {r!r}")
    return sorted(out)
