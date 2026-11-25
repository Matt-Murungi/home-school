"""N02 (#127): strict single-address validator and its wiring into EmailChannel (N01 note)."""

import pytest

from hs_core.notifications.address import normalize_address, validate_address
from hs_core.notifications.channels import EmailChannel, FailureKind, FakeEmailSender

GOOD = [
    "a@b.co",
    "parent@example.org",
    "first.last@example.org",
    "first+tag@example.org",
    "o'brien@example.org",
    "UPPER@Example.ORG",
    "a_b-c@sub.example.co.ug",
    "x@xn--bcher-kva.example",  # punycode IDN domain is accepted
    "{a}@example.org",
    "a" * 64 + "@example.org",
    "user@" + "d" * 63 + ".org",
    "1@2.ab",
    "a.b.c@d.e.fg",
]
BAD = {
    "empty": "",
    "no at": "plain",
    "no domain": "a@",
    "no local": "@b.co",
    "two at": "a@b@c.co",
    "comma list": "a@b.co,c@d.co",
    "semicolon list": "a@b.co;c@d.co",
    "space list": "a@b.co c@d.co",
    "display name": "Name <a@b.co>",
    "display name no space": "Name<a@b.co>",
    "angle only": "<a@b.co>",
    "quoted local": '"a b"@example.org',
    "quoted local plain": '"a"@example.org',
    "comment": "a(comment)@example.org",
    "lf": "a@b.co\nBcc: x@y.co",
    "cr": "a@b.co\rBcc: x@y.co",
    "crlf": "a@b.co\r\nSubject: x",
    "trailing newline": "a@b.co\n",
    "leading space": " a@b.co",
    "trailing space": "a@b.co ",
    "inner space": "a b@c.co",
    "tab": "a\t@b.co",
    "nul": "a\x00@b.co",
    "del": "a\x7f@b.co",
    "c1 control": "a\x85@b.co",
    "zero width space": "a​@b.co",
    "zero width joiner": "a‍@b.co",
    "zero width non-joiner": "a@b‌.co",
    "bom": "﻿a@b.co",
    "bom inside": "a@﻿b.co",
    "word joiner": "a⁠@b.co",
    "bidi override": "a‮@b.co",
    "line separator": "a @b.co",
    "nbsp": "a @b.co",
    "unicode local": "josé@example.org",
    "unicode domain": "a@bücher.example",
    "fullwidth at": "a＠b.co",
    "cyrillic homoglyph domain": "a@аpple.com",
    "too long total": "a" * 64 + "@" + ("d" * 63 + ".") * 3 + "com",
    "local too long": "a" * 65 + "@example.org",
    "label too long": "a@" + "d" * 64 + ".org",
    "domain too long": "a@" + ".".join(["d" * 60] * 5) + ".org",
    "leading dot local": ".a@b.co",
    "trailing dot local": "a.@b.co",
    "double dot local": "a..b@c.co",
    "double dot domain": "a@b..co",
    "leading dot domain": "a@.b.co",
    "trailing dot domain": "a@b.co.",
    "hyphen start label": "a@-b.co",
    "hyphen end label": "a@b-.co",
    "underscore domain": "a@b_c.co",
    "no dot domain": "a@localhost",
    "ip literal": "a@[127.0.0.1]",
    "ip domain": "a@127.0.0.1",
    "numeric tld": "a@b.123",
    "one letter tld": "a@b.c",
    "percent hack": "a%b@c.co",
    "bang path": "a!b@c.co@d.co",
    "backslash": "a\\@b.co",
    "colon": "a:b@c.co",
    "paren": "a@b.co)",
    "not a string": None,
    "bytes": b"a@b.co",
    "int": 5,
}


@pytest.mark.parametrize("addr", GOOD)
def test_good_addresses(addr):
    assert validate_address(addr) == addr


@pytest.mark.parametrize("name", list(BAD))
def test_bad_addresses(name):
    assert validate_address(BAD[name]) is None, name


def test_matrix_size():
    assert len(GOOD) + len(BAD) >= 60


def test_normalize_lowercases_everything_for_suppression_matching():
    assert normalize_address("A.B@Example.ORG") == "a.b@example.org"
    assert normalize_address("bad address") is None


def test_total_length_cap_is_254():
    local = "a" * 64
    ok = local + "@" + "d" * 63 + "." + "e" * 63 + "." + "f" * 57 + ".org"  # 64+1+63+1+63+1+57+4
    assert len(ok) == 254 and validate_address(ok) == ok
    assert validate_address("x" + ok) is None


@pytest.mark.parametrize("name", list(BAD))
def test_email_channel_rejects_every_bad_address_without_a_provider_call(name):
    sender = FakeEmailSender()
    res = EmailChannel(sender).send(BAD[name], "s", "t", "<p>t</p>", {}, "k")  # type: ignore[arg-type]
    assert not res.accepted and res.failure is FailureKind.PERMANENT
    assert res.detail == "invalid_address" and sender.calls == []


def test_email_channel_accepts_a_good_address():
    sender = FakeEmailSender()
    assert EmailChannel(sender).send("p@example.org", "s", "t", "<p>t</p>", {}, "k").accepted
