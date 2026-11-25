"""PII scrubbing (F08): realistic Sentry-shaped payloads, nested data, exception messages."""

import json

from hs_core.observability.scrub import FILTERED, scrub_event, scrub_text

PHONES = ["+256772123456", "0772 123 456", "0701-234-567", "256 712 345 678", "+44 7911 123456"]
EMAILS = ["mary.nakato@example.com", "T.Okello+kids@mail.example.co.ug"]
CHILD = "Amara Nakato"
ADDRESS = "Plot 12, Bukoto Street, Kamwokya"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1XzEifQ.c2lnbmF0dXJlLXZhbHVl"


def realistic_event():
    return {
        "event_id": "abc",
        "level": "error",
        "environment": "staging",
        "release": "1.2.3",
        "tags": {"request_id": "req-1", "parent_phone": PHONES[0]},
        "user": {"id": "u_parent", "email": EMAILS[0], "username": "mary", "ip_address": "1.2.3.4"},
        "request": {
            "url": f"https://api.example.com/v1/children?phone={PHONES[0]}&email={EMAILS[0]}",
            "method": "POST",
            "query_string": f"phone={PHONES[0]}",
            "cookies": {"a_session_x": "s3cret"},
            "headers": {
                "Authorization": f"Bearer {JWT}",
                "Cookie": "a_session=abc",
                "X-Appwrite-Key": "k",
                "User-Agent": "okhttp",
                "X-Request-ID": "req-1",
            },
            "data": {"child_name": CHILD, "address": ADDRESS, "phone": PHONES[1]},
            "env": {"REMOTE_ADDR": "1.2.3.4"},
        },
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "value": f"cannot notify {PHONES[0]} / {EMAILS[0]}",
                    "stacktrace": {
                        "frames": [
                            {
                                "function": "send",
                                "vars": {"phone": PHONES[2], "child_name": CHILD},
                                "context_line": f'send("{PHONES[2]}")',
                            }
                        ]
                    },
                }
            ]
        },
        "logentry": {"message": "failed for %s", "formatted": f"failed for {PHONES[3]}"},
        "breadcrumbs": {
            "values": [
                {"message": f"GET /v1/me for {EMAILS[1]}", "data": {"url": f"/x?t={JWT}"}},
                {"category": "http", "data": {"body": {"name": CHILD}}},
            ]
        },
        "extra": {
            "payload": [{"nested": {"guardian_name": "Mary N", "note": f"call {PHONES[4]}"}}]
        },
        "contexts": {"trace": {"trace_id": "t1"}},
    }


def test_no_pii_survives_anywhere_in_a_realistic_event():
    out = scrub_event(realistic_event(), {})
    blob = json.dumps(out)
    for needle in [*PHONES, *EMAILS, CHILD, ADDRESS, JWT, "s3cret", "Mary N", "a_session=abc"]:
        assert needle not in blob, needle
    assert "1.2.3.4" not in blob


def test_useful_context_is_kept():
    out = scrub_event(realistic_event(), {})
    assert out["tags"]["request_id"] == "req-1"
    assert out["environment"] == "staging" and out["release"] == "1.2.3"
    assert out["user"] == {"id": "u_parent"}
    assert out["request"]["method"] == "POST"
    assert out["request"]["headers"]["X-Request-ID"] == "req-1"
    assert out["request"]["headers"]["User-Agent"] == "okhttp"
    assert out["exception"]["values"][0]["type"] == "ValueError"
    assert out["exception"]["values"][0]["stacktrace"]["frames"][0]["function"] == "send"


def test_request_body_cookies_and_sensitive_headers_dropped():
    r = scrub_event(realistic_event(), {})["request"]
    assert "data" not in r and "cookies" not in r and "env" not in r
    assert r["headers"]["Authorization"] == FILTERED
    assert r["headers"]["Cookie"] == FILTERED
    assert r["headers"]["X-Appwrite-Key"] == FILTERED


def test_frame_local_variables_dropped():
    fr = scrub_event(realistic_event(), {})["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert "vars" not in fr


def test_exception_message_with_phone_is_masked():
    ev = {"exception": {"values": [{"type": "X", "value": "MoMo failed for 0772123456 (retry)"}]}}
    v = scrub_event(ev, {})["exception"]["values"][0]["value"]
    assert "0772123456" not in v and "MoMo failed for" in v


def test_sensitive_keys_masked_at_any_depth_case_insensitive():
    ev = {"extra": {"a": [{"b": {"Child_Name": "Zed", "ParentEmail": "q@x.com", "ok": 1}}]}}
    b = scrub_event(ev, {})["extra"]["a"][0]["b"]
    assert b == {"Child_Name": FILTERED, "ParentEmail": FILTERED, "ok": 1}


def test_scrub_text_leaves_ids_and_numbers_alone():
    s = "req 3f2a9c1e7b4d4a0e8c1d2e3f4a5b6c7d status=503 amount=25000 UGX at 2026-11-25T10:00:00Z"
    assert scrub_text(s) == s


def test_scrub_text_masks_bearer_and_jwt():
    assert JWT not in scrub_text(f"token {JWT}")
    assert "abcdef123456" not in scrub_text("Authorization: Bearer abcdef123456")


def test_scrub_is_idempotent_and_never_raises_on_odd_input():
    ev = realistic_event()
    once = scrub_event(ev, {})
    assert scrub_event(json.loads(json.dumps(once)), {}) == once
    for odd in ({}, {"request": None}, {"exception": {"values": None}}, {"tags": [("a", "b")]}):
        scrub_event(odd, {})


def test_scrub_failure_drops_event_rather_than_leaking(monkeypatch):
    import hs_core.observability.scrub as s

    monkeypatch.setattr(s, "_walk", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert s.scrub_event({"message": "0772123456"}, {}) is None
