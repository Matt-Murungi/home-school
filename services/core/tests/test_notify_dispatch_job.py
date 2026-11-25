"""N01 (#126): `notification-dispatch` job in the F04 schedule; settings and inventory."""

from datetime import UTC, datetime, timedelta

import pytest

from hs_core.jobs.model import Continue, Done, JobContext
from hs_core.jobs.schedule import JOBS
from hs_core.notifications import dispatch
from hs_core.notifications.config import NotificationConfig
from hs_core.settings import ConfigError, Settings, load_settings
from notify_helpers import LESSON, NIGHT, Rig


def ctx():
    return JobContext(
        "notification-dispatch",
        "r1",
        "notification-dispatch:x",
        datetime(2027, 1, 19, 4, 0, tzinfo=UTC),
        {},
        None,
        0,
    )


def test_job_is_in_the_schedule_every_minute_with_small_catch_up():
    spec = next(j for j in JOBS if j.name == "notification-dispatch")
    assert spec.schedule.every == timedelta(minutes=1)
    assert spec.catch_up <= timedelta(minutes=10)  # F04 follow-up (1): bounded cold start
    assert spec.timeout >= timedelta(seconds=10)


def test_handler_dispatches_due_rows_chunked_then_done():
    rig = Rig(NIGHT)
    from hs_core.notifications.service import Recipient

    for i in range(5):
        r = Recipient(f"u{i}", "parent")
        rig.user(r)
        rig.svc.send("session_checked_out_parent", r, {"time": LESSON}, f"k{i}")
    rig.clock.now = datetime(2027, 1, 19, 4, 0, tzinfo=UTC)
    h = dispatch.make_handler(rig.svc, page=3)
    first = h(ctx())
    assert isinstance(first, Continue) and first.items == 3
    second = h(ctx())
    assert isinstance(second, Done) and second.items == 2
    assert isinstance(h(ctx()), Done)  # idempotent re-run
    assert rig.push.deliveries == 5


def test_unbound_handler_raises_clearly():
    dispatch._bound.clear()
    with pytest.raises(RuntimeError):
        dispatch.notification_dispatch(ctx())


def test_settings_defaults_ranges_and_config():
    s = Settings(_env_file=None)
    c = NotificationConfig.from_settings(s)
    assert (c.quiet_start_hour, c.quiet_end_hour) == (21, 7)
    assert c.rate_limit >= 1 and c.max_attempts >= 1 and c.lease_s >= 30
    for bad in (
        {"notify_quiet_start_hour": 24},
        {"notify_quiet_end_hour": -1},
        {"notify_quiet_start_hour": 7, "notify_quiet_end_hour": 7},
        {"notify_rate_limit": 0},
        {"notify_max_attempts": 0},
        {"notify_escalate_after_min": 0},
    ):
        probs = Settings(_env_file=None, **bad).problems()
        assert probs, bad
    with pytest.raises(ConfigError):
        import os

        os.environ["NOTIFY_QUIET_START_HOUR"] = "25"
        try:
            load_settings(env_file=None)
        finally:
            del os.environ["NOTIFY_QUIET_START_HOUR"]
