"""THE list of business-rule settings (H05 #134, NFR-18, A-50). Add a setting here and nowhere
else; docs/ops/settings.md is generated from this file (a test fails when it is stale).

What belongs here: rules the business decides (windows, cut-offs, grace, limits, weights,
budgets). What does not: secrets, credentials, URLs, timeouts and other infrastructure tuning
(those stay in environment variables, docs/ops/settings.md "Which is which"). No key may look
like a secret (tests/test_settings_registry.py).

`env` names the `Settings` attribute whose value is the DEFAULT when nothing is stored. A value
stored by the super admin wins over the environment.
"""

from __future__ import annotations

from .model import Constraint, Field, SettingDef, Spec

GROUPS = (
    ("business_rules", "Business rules"),
    ("matching", "Matching"),
    ("notifications", "Notifications"),
    ("travel", "Travel time"),
)


def _quiet(v: dict[str, int]) -> str | None:
    return "start_hour and end_hour must differ" if v["start_hour"] == v["end_hour"] else None


_DEFS = [
    # ---- business rules: sessions and check-in
    SettingDef(
        "session.late_cancellation_notice_hours",
        "business_rules",
        "int",
        "A parent cancelling less than this long before the session start is a late cancellation "
        "(the teacher is paid). M02 question 19; not in the SR.",
        unit="hours",
        lo=1,
        hi=168,
        default=24,
        env="late_cancellation_notice_hours",
    ),
    SettingDef(
        "session.auto_confirm_after_hours",
        "business_rules",
        "int",
        "A session awaiting confirmation is confirmed automatically after this long without a "
        "dispute (SR 7.4).",
        unit="hours",
        lo=1,
        hi=720,
        default=48,
        applies="immediate",
    ),
    SettingDef(
        "checkin.geofence_radius_m",
        "business_rules",
        "int",
        "A check-in farther than this from the home pin is flagged and needs parent "
        "confirmation (SR 7.5). Value not in the SR: owner question.",
        unit="metres",
        lo=20,
        hi=2000,
        default=150,
        applies="immediate",
    ),
    # ---- business rules: billing (B03 questions B03-1..B03-4)
    SettingDef(
        "billing.renewal_lead_days",
        "business_rules",
        "int",
        "The next period's invoice is created this long before renewal.",
        unit="days",
        lo=1,
        hi=30,
        default=5,
        env="billing_renewal_lead_days",
    ),
    SettingDef(
        "billing.grace_days",
        "business_rules",
        "int",
        "Days after the due date before an unpaid subscription becomes past due (SR 8).",
        unit="days",
        lo=0,
        hi=30,
        default=3,
        env="billing_grace_days",
    ),
    SettingDef(
        "billing.past_due_cancel_days",
        "business_rules",
        "int",
        "A subscription past due for this long is cancelled. Must be longer than the grace period.",
        unit="days",
        lo=1,
        hi=90,
        default=14,
        env="billing_past_due_cancel_days",
    ),
    SettingDef(
        "billing.max_pause_days",
        "business_rules",
        "int",
        "Longest a parent may pause a subscription (SUB-4).",
        unit="days",
        lo=1,
        hi=180,
        default=30,
        env="billing_max_pause_days",
    ),
    SettingDef(
        "scheduling.horizon_days",
        "business_rules",
        "int",
        "How far ahead recurring sessions are generated (S01).",
        unit="days",
        lo=7,
        hi=90,
        default=28,
        env="session_gen_horizon_days",
    ),
    # ---- matching
    SettingDef(
        "matching.weights",
        "matching",
        "map",
        "Weights of the matching score, in percent. They must add up to 100 (SR 7.2).",
        unit="percent",
        applies="immediate",
        sum_to=100,
        fields=(
            Field("travel_time", 0, 100, 30),
            Field("tier", 0, 100, 20),
            Field("struggle_fit", 0, 100, 15),
            Field("workload_balance", 0, 100, 15),
            Field("reliability", 0, 100, 15),
            Field("cluster_bonus", 0, 100, 5),
        ),
    ),
    SettingDef(
        "matching.offer_expiry_minutes",
        "matching",
        "int",
        "How long a teacher has to answer an offer (SR 7.2: default 2 hours).",
        unit="minutes",
        lo=5,
        hi=1440,
        default=120,
        applies="immediate",
    ),
    SettingDef(
        "matching.substitute_offer_expiry_minutes",
        "matching",
        "int",
        "How long a teacher has to answer a substitute offer. Not longer than a normal offer.",
        unit="minutes",
        lo=5,
        hi=720,
        default=30,
        applies="immediate",
    ),
    SettingDef(
        "matching.max_offer_failures",
        "matching",
        "int",
        "Declined or expired offers before a request moves to the admin queue (SR 7.2: 5).",
        unit="offers",
        lo=1,
        hi=20,
        default=5,
        applies="immediate",
    ),
    SettingDef(
        "matching.max_rematches",
        "matching",
        "int",
        "Rematches a parent may ask for per subscription. Value not in the SR: owner question.",
        unit="rematches",
        lo=0,
        hi=10,
        default=2,
    ),
    # ---- notifications (N01; quiet hours are global, SR 8)
    SettingDef(
        "notify.quiet_hours",
        "notifications",
        "map",
        "Non-critical notifications wait outside these Africa/Kampala hours (SR 8: 21:00-07:00).",
        unit="hour of day",
        applies="immediate",
        validator=_quiet,
        fields=(
            Field("start_hour", 0, 23, 21, "notify_quiet_start_hour"),
            Field("end_hour", 0, 23, 7, "notify_quiet_end_hour"),
        ),
    ),
    SettingDef(
        "notify.rate_limit",
        "notifications",
        "int",
        "Non-critical notifications per recipient and template per window.",
        unit="notifications",
        lo=1,
        hi=100,
        default=5,
        env="notify_rate_limit",
        applies="immediate",
    ),
    SettingDef(
        "notify.rate_window_s",
        "notifications",
        "int",
        "The window of the notification rate limit.",
        unit="seconds",
        lo=10,
        hi=86400,
        default=600,
        env="notify_rate_window_s",
        applies="immediate",
    ),
    SettingDef(
        "notify.max_attempts",
        "notifications",
        "int",
        "Provider attempts before a notification fails for good.",
        unit="attempts",
        lo=1,
        hi=10,
        default=4,
        env="notify_max_attempts",
        applies="immediate",
    ),
    SettingDef(
        "notify.escalate_after_min",
        "notifications",
        "int",
        "A safeguarding notification nobody acknowledged is escalated to the backup after this.",
        unit="minutes",
        lo=1,
        hi=240,
        default=15,
        env="notify_escalate_after_min",
        applies="immediate",
    ),
    # ---- travel time (X01)
    SettingDef(
        "travel.daily_element_budget",
        "travel",
        "int",
        "Most route elements (origin x destination) the provider may be asked for per day.",
        unit="elements",
        lo=1,
        hi=1_000_000,
        default=1000,
        env="travel_daily_element_budget",
        applies="immediate",
    ),
    SettingDef(
        "travel.monthly_element_budget",
        "travel",
        "int",
        "Most route elements per month; alerts at 80 and 100 percent, hard stop at 100.",
        unit="elements",
        lo=1,
        hi=10_000_000,
        default=15000,
        env="travel_monthly_element_budget",
        applies="immediate",
    ),
    SettingDef(
        "travel.cache_ttl_days",
        "travel",
        "int",
        "How long a zone-pair travel time is cached before it is refreshed (SR 7.2: weekly).",
        unit="days",
        lo=1,
        hi=90,
        default=7,
        env="travel_cache_ttl_days",
        applies="immediate",
    ),
    SettingDef(
        "travel.within_zone_minutes",
        "travel",
        "int",
        "Assumed travel time between two homes in the same zone.",
        unit="minutes",
        lo=1,
        hi=120,
        default=10,
        env="travel_within_zone_minutes",
        applies="immediate",
    ),
    SettingDef(
        "travel.fallback_speed_kmh",
        "travel",
        "float",
        "Average speed of the straight-line estimate used when no provider answer exists.",
        unit="km/h",
        lo=5.0,
        hi=120.0,
        default=20.0,
        env="travel_fallback_speed_kmh",
        applies="immediate",
    ),
    SettingDef(
        "travel.fallback_circuity",
        "travel",
        "float",
        "Road-distance factor of the straight-line estimate (1.0 = as the crow flies).",
        unit="factor",
        lo=1.0,
        hi=3.0,
        default=1.4,
        env="travel_fallback_circuity",
        applies="immediate",
    ),
]

_CONSTRAINTS = (
    Constraint(
        "grace_before_cancel",
        ("billing.grace_days", "billing.past_due_cancel_days"),
        lambda v: v["billing.grace_days"] < v["billing.past_due_cancel_days"],
        "billing.grace_days must be shorter than billing.past_due_cancel_days",
    ),
    Constraint(
        "substitute_not_longer",
        ("matching.substitute_offer_expiry_minutes", "matching.offer_expiry_minutes"),
        lambda v: (
            v["matching.substitute_offer_expiry_minutes"] <= v["matching.offer_expiry_minutes"]
        ),
        "the substitute offer window must not be longer than the normal offer window",
    ),
    Constraint(
        "daily_within_monthly",
        ("travel.daily_element_budget", "travel.monthly_element_budget"),
        lambda v: v["travel.daily_element_budget"] <= v["travel.monthly_element_budget"],
        "the daily travel budget must not exceed the monthly budget",
    ),
)

REGISTRY = Spec(defs={d.key: d for d in _DEFS}, constraints=_CONSTRAINTS, groups=GROUPS)
