"""H05 (#134): the services that read their tuning from the environment today read it from the
settings service: a stored value wins at the next use, with no restart."""

from datetime import timedelta

import pytest

from hs_core.notifications import build_notification_service
from hs_core.notifications.config import LiveNotificationConfig, NotificationConfig
from hs_core.notifications.templates import Channel
from hs_core.repository import InMemoryRepository
from hs_core.settings import Settings
from hs_core.travel.budget import BudgetGuard
from hs_core.travel.factory import LiveTravelConfig, build_travel_service, config_from_settings
from settings_helpers import REASON, ManualClock, edit, stack


def test_live_notification_config_follows_the_stored_values_without_a_restart():
    _raw, svc, _a, _c = stack()
    live = LiveNotificationConfig(NotificationConfig(), svc)
    assert (live.quiet_start_hour, live.quiet_end_hour, live.rate_limit) == (21, 7, 5)
    edit(svc, "notify.quiet_hours", {"start_hour": 22, "end_hour": 6}, 0)
    edit(svc, "notify.rate_limit", 9, 0)
    edit(svc, "notify.rate_window_s", 300, 0)
    edit(svc, "notify.max_attempts", 6, 0)
    edit(svc, "notify.escalate_after_min", 30, 0)
    assert (live.quiet_start_hour, live.quiet_end_hour) == (22, 6)
    assert (live.rate_limit, live.rate_window_s, live.max_attempts, live.escalate_after_min) == (
        9,
        300,
        6,
        30,
    )


def test_infrastructure_fields_of_the_notification_config_stay_in_the_environment():
    _raw, svc, _a, _c = stack()
    live = LiveNotificationConfig(NotificationConfig(retry_base_s=77, lease_s=88), svc)
    assert (live.retry_base_s, live.lease_s) == (77, 88)


def test_the_environment_default_reaches_the_live_notification_config():
    from hs_core.app_settings import env_defaults

    s = Settings(_env_file=None, notify_rate_limit=11)
    _raw, svc, _a, _c = stack(defaults=env_defaults(s))
    assert LiveNotificationConfig(NotificationConfig.from_settings(s), svc).rate_limit == 11


def test_build_notification_service_uses_the_live_config_when_given_settings():
    raw, svc, audited, _c = stack()
    service = build_notification_service(Settings(_env_file=None), audited, app_settings=svc)
    assert isinstance(service.config, LiveNotificationConfig)
    assert service.config.rate_limit == 5
    edit(svc, "notify.rate_limit", 8, 0)
    assert service.config.rate_limit == 8


def test_without_settings_the_notification_service_keeps_the_plain_environment_config():
    service = build_notification_service(Settings(_env_file=None), InMemoryRepository())
    assert isinstance(service.config, NotificationConfig)


def test_a_quiet_hours_change_changes_when_the_service_defers():
    """The behaviour, not only the attribute: the service decides 'quiet' from the live config."""
    from datetime import UTC, datetime

    _raw, svc, audited, _c = stack()
    service = build_notification_service(Settings(_env_file=None), audited, app_settings=svc)
    evening = datetime(2027, 3, 1, 17, 30, tzinfo=UTC)  # 20:30 in Kampala: not quiet at 21-07
    assert service._defers(None, Channel.PUSH, evening) is False
    edit(svc, "notify.quiet_hours", {"start_hour": 20, "end_hour": 7}, 0)
    assert service._defers(None, Channel.PUSH, evening) is True


def test_live_travel_config_and_budget_follow_the_stored_values():
    raw, svc, audited, clock = stack()
    t = build_travel_service(Settings(_env_file=None), audited, app_settings=svc, clock=clock)
    assert isinstance(t.config, LiveTravelConfig)
    assert t.config.cache_ttl == timedelta(days=7) and t.config.within_zone_minutes == 10
    edit(svc, "travel.monthly_element_budget", 40000, 0)
    edit(svc, "travel.daily_element_budget", 2000, 0)
    edit(svc, "travel.cache_ttl_days", 3, 0)
    edit(svc, "travel.within_zone_minutes", 15, 0)
    edit(svc, "travel.fallback_speed_kmh", 30.5, 0)
    edit(svc, "travel.fallback_circuity", 1.2, 0)
    assert t.config.cache_ttl == timedelta(days=3) and t.config.within_zone_minutes == 15
    assert (t.config.fallback_speed_kmh, t.config.fallback_circuity) == (30.5, 1.2)
    assert (t._budget.daily_elements, t._budget.monthly_elements) == (2000, 40000)
    assert t.config.negative_ttl == config_from_settings(Settings(_env_file=None)).negative_ttl


def test_budget_guard_still_accepts_plain_numbers():
    from hs_core.travel.alerts import LogTravelAlertSink
    from hs_core.travel.budget import RepositoryUsageStore

    g = BudgetGuard(
        RepositoryUsageStore(InMemoryRepository()), daily_elements=10, monthly_elements=100,
        cost_micro_usd_per_element=1, alerts=LogTravelAlertSink(), clock=ManualClock(),
    )  # fmt: skip
    assert (g.daily_elements, g.monthly_elements) == (10, 100)


@pytest.mark.parametrize("name", ["settings.py"])
def test_registered_rules_are_not_read_from_settings_by_their_services_any_more(name):
    """The old direct reads of registered business rules are gone from the wired services."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "hs_core"
    # the notification and travel services read `self.config.<field>` (live objects)
    for rel in ("notifications/service.py", "travel/service.py"):
        assert "settings.notify_" not in (root / rel).read_text()
        assert "settings.travel_" not in (root / rel).read_text()
    assert REASON
