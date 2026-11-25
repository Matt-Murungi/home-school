"""What is audited, which actions need a reason, and what is never written (M06 #41).

Edit this file when a ticket adds an audited table or a reason-required action; the tests in
tests/test_audit_payload.py compare it with the schema (every free-text column of an audited
table must be redacted or explicitly allow-listed here).
"""

from __future__ import annotations

# The ledger tables (`ledger_account`, `ledger_transaction`, `ledger_entry`) are NOT here on
# purpose: they are append-only, idempotent by their own deterministic ids and sealed by the
# ledger's own protocol, and are audited by ONE summary row per posting / per account created
# (`audit.adapters.LedgerAuditAdapter`, docs/ops/audit.md "Hooks"). Per-row audit would write
# about 11 audit rows per posting.
# SEC-6 / ADM-6: money, tiers, sessions and their children, logs, safeguarding, app settings,
# role and approval changes. `app_setting` has no table yet (PLANNED): the name is reserved so the
# settings ticket cannot forget the audit.
AUDITED_TABLES = frozenset(
    {
        # money
        "invoice", "payment", "earning", "payout", "payout_batch",
        # tiers
        "tier", "tier_history", "bonus_rule",
        # plans, sessions and their children
        "plan", "subscription", "assignment", "session", "session_checkin",
        "session_confirmation", "cancellation", "dispute",
        # logs
        "session_log", "session_log_topic", "session_log_struggle",
        # safeguarding
        "safeguarding_case", "safeguarding_action", "case_status",
        # settings, roles, approvals
        "app_setting", "role_assignment", "user_profile", "teacher_profile",
        "teacher_approval", "teacher_document",
    }
)  # fmt: skip
PLANNED_TABLES = frozenset({"app_setting"})  # audited by name before the table exists

# Case content: reading these rows is itself audited (read_audited) and the plain
# AuditedRepository.get/list refuse them.
CASE_TABLES = frozenset({"safeguarding_case", "safeguarding_action"})

# Actions whose UI requires a reason (SEC-6). A missing or blank reason raises ReasonRequired
# before anything is written. The first group are the named admin actions; the second are the
# default action names of the settings table.
REASON_REQUIRED = frozenset(
    {
        "dispute.resolve",
        "tier.override",
        "payment.refund",
        "user.suspend",
        "payout.approve",
        "ledger.manual_adjustment",
        "setting.update",
        "app_setting.create",
        "app_setting.update",
        "app_setting.delete",
    }
)

# --------------------------------------------------------------------------- redaction
# Key rules are applied to the NORMALISED key tokens (see payload._tokens). A hit replaces the
# value by "[redacted]" at any depth; the field still shows up as changed. Values that are not
# redacted by key are passed through the F08 text scrubber.
GLOBAL_TOKENS = frozenset(
    {
        # credentials
        "password", "passwd", "pwd", "secret", "token", "jwt", "otp", "passcode", "pepper",
        "salt", "apikey", "authorization", "cookie", "signature", "credential", "credentials",
        # contact details and home location (full numbers, coordinates, address text)
        "phone", "msisdn", "mobile", "email", "address", "directions", "landmark",
        "lat", "lng", "lon", "latitude", "longitude", "geohash", "dob", "birthdate",
        # round 2: generic keys that can hold a child's/family's location, identity or story. The
        # per-key decision (docs/ops/audit.md "Redaction decisions"): redact; `title` is kept.
        "coordinates", "coordinate", "location", "pickup", "pin", "pan", "nin", "passport",
        "guardian", "comment", "comments", "note", "notes", "description",
        # document files and raw provider payloads
        "file", "raw",
        # assessment answer keys (decision: never logged; an exam-integrity secret)
        "answer", "answers",
    }
)  # fmt: skip
# Joined forms (`codehash`, `tokenhash`): hashes of credentials are still credential material.
ID_KEYS_OK_TOKENS = frozenset({"location", "pickup", "guardian", "comment", "note", "description"})
GLOBAL_JOINED_CONTAINS = ("nationalid", "cardpan", "passportnumber")
GLOBAL_JOINED_SUFFIXES = ("codehash", "tokenhash", "phonehash", "secretkey", "accesskey")
# Person-name keys. A bare `name` is a person's name except on these label tables.
NAME_IS_A_LABEL = frozenset(
    {"tier", "bonus_rule", "ledger_account", "plan", "payout_batch", "app_setting"}
)

# Per-entity extra keys (exact, normalised: lower case, no separators) for free text that can
# describe a child or a family. Audit rows carry ids, not names or stories.
ENTITY_REDACT: dict[str, frozenset[str]] = {
    "user_profile": frozenset({"name", "displayname", "photo"}),
    "teacher_profile": frozenset({"bio", "photofileid"}),
    "teacher_document": frozenset({"fileid", "notes", "documentnumber"}),
    "teacher_approval": frozenset({"notes"}),
    "payment": frozenset({"rawcallback", "failurereason"}),
    "payout": frozenset({"accountnumber", "destination", "lasterror"}),
    "session_log": frozenset({"note", "notes", "homework"}),
    "safeguarding_case": frozenset({"description", "outcome", "summary", "narrative", "notes"}),
    "safeguarding_action": frozenset({"detail", "notes"}),
    "case_status": frozenset({"summary", "message"}),
    "dispute": frozenset({"reason", "resolutionnote"}),
    "cancellation": frozenset({"reason"}),
    "role_assignment": frozenset({"revokereason"}),
    # parent / staff free text (decisions of round 2: stored only as "[redacted]", the structured
    # reason code or status column carries the audit-relevant fact)
    "subscription": frozenset({"cancelreason"}),
    "assignment": frozenset({"endreason"}),
    "earning": frozenset({"note"}),
}

# Free-text or JSON columns of audited tables that are deliberately stored: configuration the
# admins must be able to review (tier criteria, bonus conditions), and short staff- or
# system-written reasons and descriptions (still passed through the text scrubber). Free text
# typed by parents, teachers or reporters is NOT here: it is in ENTITY_REDACT.
TEXT_ALLOWED = frozenset(
    {
        ("tier", "criteria"),
        ("bonus_rule", "conditions"),
        ("subscription", "preferred_slots"),
        ("tier_history", "reason"),  # staff reason for a tier change
    }
)
