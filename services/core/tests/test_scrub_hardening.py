"""F08 round 2: cycles/size caps, free-text secrets, phone formats, bytes, log `extra`."""

import io
import json
import logging
import time

import pytest

from hs_core.logging_setup import JsonFormatter
from hs_core.observability.scrub import (
    FILTERED,
    scrub_breadcrumb,
    scrub_event,
    scrub_text,
)

FAST = 1.5  # seconds; the real runs take milliseconds


def timed(fn, *a):
    t = time.perf_counter()
    out = fn(*a)
    assert time.perf_counter() - t < FAST
    return out


# ---- (1) cycles, shared references, caps -------------------------------------------------


def test_dict_with_several_back_references_is_fast_and_marked():
    d: dict = {}
    d["self"] = d
    d["x"] = d
    out = timed(scrub_event, {"extra": d}, {})
    assert out["extra"]["self"] == "[Circular]" and out["extra"]["x"] == "[Circular]"


def test_list_with_several_back_references_is_fast():
    a: list = []
    a.append(a)
    a.append(a)
    out = timed(scrub_event, {"extra": {"a": a}}, {})
    assert out["extra"]["a"] == ["[Circular]", "[Circular]"]


def test_breadcrumb_cycle_is_fast():
    d: dict = {"message": "hi"}
    d["data"] = {"back": d, "again": d}
    out = timed(scrub_breadcrumb, d)
    assert out["data"] == {"back": "[Circular]", "again": "[Circular]"}


def test_shared_but_acyclic_reference_is_not_marked_circular():
    shared = {"ok": 1}
    out = scrub_event({"extra": {"a": shared, "b": shared}}, {})
    assert out["extra"] == {"a": {"ok": 1}, "b": {"ok": 1}}


def test_exponential_dag_hits_node_cap_and_drops_event():
    node: list = ["leaf"]
    for _ in range(40):  # 2**40 paths, no cycle
        node = [node, node]
    assert timed(scrub_event, {"extra": node}, {}) is None


def test_wide_structure_ok_and_too_wide_dropped():
    assert len(scrub_event({"extra": list(range(5000))}, {})["extra"]) == 5000
    assert timed(scrub_event, {"extra": list(range(500_000))}, {}) is None


def test_deep_structure_is_bounded():
    v: dict = {}
    cur = v
    for _ in range(5000):
        cur["n"] = {}
        cur = cur["n"]
    cur["email"] = "a@b.com"
    out = timed(scrub_event, {"extra": v}, {})
    assert "a@b.com" not in json.dumps(out)


def test_one_megabyte_string_is_fast_and_scrubbed():
    s = "x" * 500_000 + " mary@example.com " + "y" * 500_000
    out = timed(scrub_event, {"message": s}, {})
    assert "mary@example.com" not in out["message"]
    assert len(out["message"]) < 400_000


@pytest.mark.parametrize("chunk", ["a", "1", "a.", "%", "+1 ", "0", "256 ", "(", "0772 ", "a%40"])
def test_no_quadratic_regex_behaviour(chunk):
    timed(scrub_text, chunk * (900_000 // len(chunk)))


def test_total_output_size_cap_drops_event():
    big = ["z" * 200_000 for _ in range(50)]
    assert timed(scrub_event, {"extra": big}, {}) is None


# ---- (2) free-text secrets ---------------------------------------------------------------

SECRET_CASES = [
    "a_session_64f1c2=SESSVALUE123",
    "a_session_64f1c2_legacy=SESSVALUE123",
    "Cookie: a_session_64f1c2=SESSVALUE123; a_session_64f1c2_legacy=SESSVALUE123",
    "Set-Cookie: a_session_64f1c2=SESSVALUE123; path=/; HttpOnly",
    "Authorization: Basic SESSVALUE123",
    "authorization: token SESSVALUE123",
    "X-Appwrite-Key: standard_SESSVALUE123".replace("SESSVALUE123", "abc123def456"),
    "key standard_0123456789abcdef0123 leaked",
    "https://x.test/cb?a=1&token=SESSVALUE123&b=2",
    "https://x.test/cb?secret=SESSVALUE123",
    "https://x.test/cb?key=SESSVALUE123#frag",
    "password=SESSVALUE123&user=bob",
    "api_key=SESSVALUE123",
    "access_token=SESSVALUE123 refresh_token=SESSVALUE123",
    "refresh_token=SESSVALUE123",
    "client_secret=SESSVALUE123",
    '{"token":"SESSVALUE123"}',
    '{"password": "SESSVALUE123", "x": 1}',
    "{'access_token': 'SESSVALUE123'}",
    'body: {"api_key":"SESSVALUE123"}',
]


@pytest.mark.parametrize("case", SECRET_CASES)
def test_free_text_secrets_masked(case):
    out = scrub_text(case)
    for needle in ("SESSVALUE123", "abc123def456", "0123456789abcdef0123"):
        assert needle not in out, (case, out)


def test_secret_masking_keeps_surrounding_context():
    out = scrub_text("GET https://x.test/cb?a=1&token=SESSVALUE123&b=2 failed")
    assert out.startswith("GET https://x.test/cb?a=1&token=") and "&b=2 failed" in out


def test_encoded_email_and_phone_in_query_strings():
    q = "https://x.test/v?email=mary.nakato%40example.com&phone=%2B256772123456&n=1"
    out = scrub_text(q)
    for needle in ("mary", "example.com", "772123456", "%40", "%2B256"):
        assert needle not in out, out
    assert out.endswith("&n=1")
    out = scrub_text("phone=%2B256%20772%20123%20456&x=1")
    assert "772" not in out and "123" not in out and out.endswith("&x=1")
    assert "772" not in scrub_text("phone=+256+772+123456&x=1")


def test_secret_in_event_request_url_and_breadcrumb():
    ev = {
        "request": {
            "url": "https://x.test/cb?token=SESSVALUE123",
            "query_string": "secret=SESSVALUE123",
        },
        "breadcrumbs": {"values": [{"message": "Cookie: a_session_p=SESSVALUE123"}]},
    }
    assert "SESSVALUE123" not in json.dumps(scrub_event(ev, {}))


# ---- (3) phone formats and false positives -----------------------------------------------

PHONE_FORMS = [
    "+256 (0) 772 123456",
    "(+256) 772 123 456",
    "0772-123-456",
    "256772123456",
    "+256772123456",
    "+256 772 123 456",
    "0772 123 456",
    "0772123456",
    "+44 7911 123456",
]


@pytest.mark.parametrize("phone", PHONE_FORMS)
@pytest.mark.parametrize(
    "tmpl,expect",
    [
        ("{}", FILTERED),
        ("call {} now", f"call {FILTERED} now"),
        ("({})", f"({FILTERED})"),
        ("tel:{}.", f"tel:{FILTERED}."),
        ("[{}]", f"[{FILTERED}]"),
        ('"{}"', f'"{FILTERED}"'),
        ("a,{},b", f"a,{FILTERED},b"),
        ("tel={}", f"tel={FILTERED}"),
    ],
)
def test_phone_format_masked_without_fragments(phone, tmpl, expect):
    assert scrub_text(tmpl.format(phone)) == expect


@pytest.mark.parametrize(
    "s",
    [
        "UGX 40,000",
        "UGX 1,250,000 paid",
        "amount=256000 UGX",
        "amount 25000",
        "date 2026-11-25 and 25/11/2026 10:00:00",
        "2026-03-07T10:00:00Z",
        "req 3f2a9c1e7b4d4a0e8c1d2e3f4a5b6c7d",
        "id=0371234567890123",
        "v1.2.3 build 20261125 py3.12.1",
        "version 0.772.123",
        "ticket F08 #34 line 1234567",
        "lesson_id=l_0772",
        "status=503 took 1234 ms",
        "ip 10.0.3.7 port 8080",
        "UGX 40,000 x 3 = UGX 120,000",
    ],
)
def test_ordinary_numbers_not_mangled(s):
    assert scrub_text(s) == s


# ---- (4) bytes ---------------------------------------------------------------------------


def test_bytes_values_are_decoded_and_scrubbed():
    ev = {"extra": {"raw": b"call 0772123456 mary@example.com", "bad": b"\xff\xfe 0772123456"}}
    out = scrub_event(ev, {})
    blob = json.dumps(out)
    assert "0772123456" not in blob and "mary@example.com" not in blob
    assert isinstance(out["extra"]["raw"], str)


def test_bytes_and_bytearray_keys_are_checked():
    out = scrub_event({"extra": {b"password": "hunter2", b"ok": 1, "b'token'": "t0k"}}, {})
    blob = json.dumps(out)
    assert "hunter2" not in blob and "t0k" not in blob
    assert out["extra"]["ok"] == 1 or out["extra"].get("ok") == 1


def test_bytearray_value_scrubbed():
    assert "0772123456" not in json.dumps(scrub_event({"extra": [bytearray(b"0772123456")]}, {}))


def test_unknown_objects_are_stringified_and_scrubbed():
    class Thing:
        def __repr__(self):
            return "Thing(phone=0772123456)"

        __str__ = __repr__

    out = scrub_event({"extra": {"t": Thing(), "n": 3, "f": 1.5, "b": True, "z": None}}, {})
    assert "0772123456" not in json.dumps(out)
    assert out["extra"]["n"] == 3 and out["extra"]["z"] is None


# ---- log `extra` fields ------------------------------------------------------------------


def _log_line(**extra):
    lg = logging.getLogger("hs.test.extra")
    lg.setLevel(logging.INFO)
    stream = io.StringIO()
    h = logging.StreamHandler(stream)
    h.setFormatter(JsonFormatter())
    lg.handlers[:] = [h]
    lg.propagate = False
    lg.info("hello", extra=extra)
    return json.loads(stream.getvalue())


def test_log_extra_fields_are_scrubbed():
    line = _log_line(
        phone="0772123456",
        note="mail mary@example.com now",
        payload={"child_name": "Amara", "ok": 1, "url": "/x?token=SESSVALUE123"},
        raw=b"0772123456",
        job_id="job_1",
    )
    blob = json.dumps(line)
    for needle in ("0772123456", "mary@example.com", "Amara", "SESSVALUE123"):
        assert needle not in blob
    assert line["job_id"] == "job_1" and line["payload"]["ok"] == 1


def test_log_extra_cycle_does_not_hang_or_crash():
    d: dict = {}
    d["self"] = d
    d["x"] = d
    line = timed(lambda: _log_line(obj=d))
    assert "obj" in line
