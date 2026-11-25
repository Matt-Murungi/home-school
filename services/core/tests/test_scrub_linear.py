"""F08 round 3: linear scrubbing (R2-1) and colon/space/encoded secret forms."""

import logging
import random
import time

import pytest

from hs_core.logging_setup import JsonFormatter
from hs_core.observability import scrub as scrub_mod
from hs_core.observability.scrub import (
    FILTERED,
    MAX_TEXT,
    scrub_breadcrumb,
    scrub_event,
    scrub_text,
)

BOUND = 2.0  # seconds, generous: the linear scrubber needs well under 0.5 s on an idle machine
WORDS = [
    "token",
    "session",
    "secret-",
    "passwd",
    "api_key",
    "apikey",
    "password",
    "cookie",
    "authorization",
    "key",
]
SIZES = [100_000, 256_000, 1_000_000]


def repeat_to(word: str, size: int) -> str:
    return word * (size // len(word) + 1)


def under_bound(fn, *a):
    t = time.perf_counter()
    out = fn(*a)
    elapsed = time.perf_counter() - t
    assert elapsed < BOUND, f"{elapsed:.2f}s"
    return out


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("word", WORDS)
def test_scrub_text_is_linear_on_repeated_keyword(word, size):
    s = repeat_to(word, size)
    out = under_bound(scrub_text, s)
    assert len(out) <= MAX_TEXT + 64


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("word", WORDS)
def test_scrub_event_message_is_linear_on_repeated_keyword(word, size):
    ev = {
        "message": "GET /" + repeat_to(word, size),
        "logentry": {"message": repeat_to(word, size)},
    }
    out = under_bound(scrub_event, ev, {})
    assert out is not None
    assert len(out["message"]) <= MAX_TEXT + 64


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("word", WORDS)
def test_log_record_is_linear_on_repeated_keyword(word, size):
    rec = logging.LogRecord("t", logging.INFO, "f", 1, "GET /%s", (repeat_to(word, size),), None)
    out = under_bound(JsonFormatter().format, rec)
    assert len(out) <= MAX_TEXT + 1000


@pytest.mark.parametrize("word", WORDS)
def test_exception_text_is_linear_on_repeated_keyword(word):
    try:
        raise ValueError(repeat_to(word, 256_000))
    except ValueError:
        import sys

        rec = logging.LogRecord("t", logging.ERROR, "f", 1, "boom", (), sys.exc_info())
    out = under_bound(JsonFormatter().format, rec)
    assert len(out) <= 2 * MAX_TEXT + 2000


@pytest.mark.parametrize(
    "build",
    [
        lambda w: w * 20_000 + "=",
        lambda w: (w + "=") * 30_000,
        lambda w: (w + " ") * 30_000,
        lambda w: (w + ": ") * 30_000,
        lambda w: (w + '="') * 30_000,
        lambda w: (w + "%3D") * 30_000,
        lambda w: w + " " * 200_000 + "x",
        lambda w: (w + "=" + " " * 50) * 3_000,
        lambda w: '"' + (w + '": "') * 30_000,
        lambda w: ("a" + " " * 100) * 1_500 + w,
    ],
)
@pytest.mark.parametrize("word", ["token", "session", "api_key", "cookie", "authorization", "key"])
def test_shaped_adversarial_inputs_are_linear(word, build):
    under_bound(scrub_text, build(word)[:200_000])


def test_fuzz_random_concatenations_stay_within_budget():
    rnd = random.Random(20251125)
    atoms = [*WORDS, "=", ":", "-", "_", ".", '"', "'", "%3D", "%20", " ", "\t", "\n", "0", "7"]
    atoms += ["12", "256", "+256", "eyJ", "Bearer ", "@", "standard_", "(0)", "a", "x" * 70]
    t0 = time.perf_counter()
    for _ in range(40):
        n = rnd.choice([1_000, 20_000, 200_000])
        parts: list[str] = []
        total = 0
        while total < n:
            a = rnd.choice(atoms)
            parts.append(a)
            total += len(a)
        s = "".join(parts)
        t = time.perf_counter()
        scrub_text(s)
        assert time.perf_counter() - t < BOUND
    assert time.perf_counter() - t0 < 20


# ---- name-aware masking ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,leak",
    [
        ("token=abc123", "abc123"),
        ("access_token=abc123&x=1", "abc123"),
        ("a_session_proj=zzz999", "zzz999"),
        ("a_session_proj_legacy=zzz999", "zzz999"),
        ("client_secret=hunter2", "hunter2"),
        ("apiKey=hunter2", "hunter2"),
        ("X-Appwrite-Key = hunter2", "hunter2"),
        ("cookie=abc123", "abc123"),
        ("pass=abc123", "abc123"),
        ("secret: abc123", "abc123"),
        ("api_key: abc123", "abc123"),
        ("Token: abc123", "abc123"),
        ("password : abc123", "abc123"),
        ("password: abc123 next", "abc123"),
        ("token%3Dabc123", "abc123"),
        ("?phone=x&token%3Dabc123&y=1", "abc123"),
        ('{"token": "abc123"}', "abc123"),
        ("{'password': 'abc123'}", "abc123"),
        ("jwt: eyJhbGciOiJI", "eyJhbGciOiJI"),
        ("signature=abc123", "abc123"),
    ],
)
def test_secret_name_forms_are_masked(raw, leak):
    out = scrub_text(raw)
    assert leak not in out, out
    assert FILTERED in out


def test_text_after_masked_value_is_kept():
    assert scrub_text("password: abc123 next").endswith(" next")
    assert scrub_text("a=1&token=abc&b=2").endswith("&b=2")
    assert scrub_text("a=1&token=abc&b=2").startswith("a=1&")


@pytest.mark.parametrize(
    "raw",
    [
        "session_count=3",
        "tokens_used=5",
        "password_policy=ok",
        "token_count: 7",
        "key_count=2",
        "monkey=banana",
        "Error: token expired at 10:00",  # name is not followed by a colon
        "session started",
        "x" * 100 + "=1",
        "http://example.test:8080/path",
    ],
)
def test_counters_and_prose_are_not_masked(raw):
    assert scrub_text(raw) == raw


def test_name_longer_than_64_still_masks_its_tail_name():
    out = scrub_text("a" * 200 + "token=abc123")
    assert "abc123" not in out


def test_non_secret_name_does_not_swallow_a_later_secret():
    assert "abc123" not in scrub_text("x:token=abc123")
    assert "abc123" not in scrub_text("note=ok token=abc123")


def test_no_stray_bracket_after_appwrite_key():
    out = scrub_text("APPWRITE_API_KEY=" + "standard_" + "ab12cd34ef56")
    assert out == "APPWRITE_API_KEY=" + FILTERED


# ---- phones --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw", ["00256772123456", "(0772) 123456", "call (0772) 123456 now", "+256 772 123456"]
)
def test_more_phone_forms(raw):
    out = scrub_text(raw)
    assert "123456" not in out and "772" not in out.replace("[Filtered]", "")


@pytest.mark.parametrize("raw", ["order 0012345678901", "UGX 40,000", "v1.2.3", "id 1234567"])
def test_phone_false_positives_unchanged(raw):
    assert scrub_text(raw) == raw


# ---- fail-closed drops are visible -----------------------------------------------------------


def test_dropped_event_logs_one_pii_free_warning_and_counts(caplog):
    scrub_mod.reset_dropped()
    d: dict = {"message": "secret person 0772123456"}
    ev = {"extra": {str(i): {"k": i} for i in range(60_000)}, "message": d["message"]}
    with caplog.at_level(logging.WARNING, logger="hs_core.observability.scrub"):
        assert scrub_event(ev, {}) is None
    msgs = [r.getMessage() for r in caplog.records]
    assert msgs == ["event dropped by scrubber: ScrubLimitExceeded"]
    assert scrub_mod.dropped_count() == 1


def test_dropped_breadcrumb_counts(caplog):
    scrub_mod.reset_dropped()
    crumb = {"data": {str(i): i for i in range(60_000)}}
    with caplog.at_level(logging.WARNING, logger="hs_core.observability.scrub"):
        assert scrub_breadcrumb(crumb) is None
    assert scrub_mod.dropped_count() == 1
    assert [r.getMessage() for r in caplog.records] == [
        "breadcrumb dropped by scrubber: ScrubLimitExceeded"
    ]
