"""N01 (#126): mutation tests. Each deliberately breaks one safety rule and asserts that the
scenario check (also run un-mutated in the normal tests) now fails."""

import uuid

import pytest

from hs_core.notifications import engine, preferences, quiet_hours, service
from notify_helpers import (
    check_email_html_is_escaped,
    check_quiet_hours_defers_push,
    check_retry_after_crash_does_not_double_send,
    check_safeguarding_cannot_be_muted,
)


def test_unmutated_scenarios_pass():
    check_quiet_hours_defers_push()
    check_safeguarding_cannot_be_muted()
    check_retry_after_crash_does_not_double_send()
    check_email_html_is_escaped()


def test_mutation_skip_quiet_hours_is_caught(monkeypatch):
    monkeypatch.setattr(quiet_hours, "is_quiet", lambda *a, **k: False)
    with pytest.raises(AssertionError):
        check_quiet_hours_defers_push()


def test_mutation_allow_muting_safeguarding_is_caught(monkeypatch):
    real = preferences.blocked_reason

    def mutated(category, channel, prefs):
        return real("marketing", channel, prefs)  # treats every category as marketing

    monkeypatch.setattr(preferences, "blocked_reason", mutated)
    with pytest.raises(AssertionError):
        check_safeguarding_cannot_be_muted()


def test_mutation_double_send_on_retry_is_caught(monkeypatch):
    monkeypatch.setattr(service, "notification_row_id", lambda *a: uuid.uuid4().hex[:36])
    with pytest.raises(AssertionError):
        check_retry_after_crash_does_not_double_send()


def test_mutation_unescaped_html_is_caught(monkeypatch):
    monkeypatch.setattr(engine, "escape_html", lambda s: s)
    with pytest.raises(AssertionError):
        check_email_html_is_escaped()
