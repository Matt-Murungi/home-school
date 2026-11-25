"""A01 (#43): TTL cleanup job `otp-cleanup` (hourly, idempotent) and its schedule entry."""

from datetime import timedelta

from hs_core.auth_otp.jobs import cleanup_otp, make_cleanup_handler
from hs_core.jobs.model import JobContext
from hs_core.jobs.schedule import JOBS
from otp_helpers import T0, Env


def ctx(now=T0):
    return JobContext("otp-cleanup", "r1", "otp-cleanup:x", now, {}, None, 0)


def test_job_is_registered_hourly():
    spec = next(j for j in JOBS if j.name == "otp-cleanup")
    assert spec.schedule.every == timedelta(hours=1)


def test_expired_challenges_attempts_and_buckets_are_purged_live_ones_kept():
    e = Env()
    e.request()
    with __import__("pytest").raises(Exception):  # noqa: B017 - wrong guess creates an attempt row
        e.verify(e.wrong_code())
    assert e.rows("attempt") and e.rows("bucket")
    e.clock.advance(60)
    assert cleanup_otp(e.repo, e.clock.now, grace=timedelta(hours=1)) == 0  # nothing expired yet
    e.clock.advance(3 * 86400)
    n = cleanup_otp(e.repo, e.clock.now, grace=timedelta(hours=1))
    assert n > 0 and not e.rows()


def test_cleanup_is_idempotent_and_safe_to_run_twice():
    e = Env()
    e.request()
    e.clock.advance(3 * 86400)
    assert cleanup_otp(e.repo, e.clock.now, grace=timedelta(hours=1)) > 0
    assert cleanup_otp(e.repo, e.clock.now, grace=timedelta(hours=1)) == 0


def test_cleanup_keeps_a_recent_expired_challenge_for_the_grace_period():
    e = Env()
    e.request()
    e.clock.advance(301)
    assert cleanup_otp(e.repo, e.clock.now, grace=timedelta(hours=1)) == 0
    assert e.rows("challenge")


def test_cleanup_handles_more_rows_than_one_page_and_reports_continue():
    e = Env()
    for i in range(130):
        e.repo.create(
            "otp_challenge",
            f"old{i}",
            {
                "kind": "bucket",
                "bucket": f"b{i}",
                "expires_at": "2026-01-01T00:00:00+00:00",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
    handler = make_cleanup_handler(e.repo, clock=e.clock, page=50)
    out = handler(ctx(e.clock.now))
    assert out.__class__.__name__ in ("Continue", "Done")
    while out.__class__.__name__ == "Continue":
        out = handler(ctx(e.clock.now))
    assert not e.rows()


def test_race_delete_of_an_already_gone_row_is_ignored():
    e = Env()
    e.repo.create(
        "otp_challenge",
        "gone",
        {
            "kind": "bucket",
            "bucket": "b",
            "expires_at": "2026-01-01T00:00:00+00:00",
            "created_at": "2026-01-01T00:00:00+00:00",
        },
    )
    real = e.repo.delete

    def delete(table, row_id):
        real(table, row_id)
        raise __import__("hs_core.repository", fromlist=["NotFound"]).NotFound("x")

    e.repo.delete = delete
    cleanup_otp(e.repo, e.clock.now, grace=timedelta(hours=1))
