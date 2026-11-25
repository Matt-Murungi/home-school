"""N01 (#126): the safe template engine. Placeholders only, strict variables, no execution."""

from datetime import UTC, date, datetime

import pytest

from hs_core.notifications import engine
from hs_core.notifications.errors import (
    ExtraVariables,
    MissingVariables,
    TemplateSyntaxError,
    VariableTypeError,
)

S = engine.VarSpec


def specs(*pairs):
    return {n: S(n, t) for n, t in pairs}


def test_renders_declared_variables():
    out = engine.render_text(
        "Hi {{ name }}, UGX {{amount}}",
        {"name": "Amy", "amount": 5000},
        {"name": S("name", "str"), "amount": S("amount", "int")},
    )
    assert out == "Hi Amy, UGX 5000"


def test_missing_variable_fails_loudly_and_names_it():
    with pytest.raises(MissingVariables) as e:
        engine.validate_values({"a": 1}, specs(("a", "int"), ("b", "str"), ("c", "str")))
    assert e.value.names == ("b", "c")


def test_extra_variable_fails_loudly():
    with pytest.raises(ExtraVariables) as e:
        engine.validate_values({"a": 1, "phone": "0700"}, specs(("a", "int")))
    assert e.value.names == ("phone",)


@pytest.mark.parametrize(
    "typ,bad",
    [
        ("int", "5"),
        ("int", True),
        ("int", 1.5),
        ("ugx", -1),
        ("ugx", 2.5),
        ("str", 5),
        ("str", "x" * 201),
        ("str", "line\nbreak"),
        ("str", "nul\x00"),
        ("datetime", "2027-01-01"),
        ("datetime", datetime(2027, 1, 1)),
        ("date", "x"),
        ("date", datetime(2027, 1, 1, tzinfo=UTC)),
    ],
)
def test_wrong_types_fail(typ, bad):
    with pytest.raises(VariableTypeError):
        engine.format_value(S("v", typ), bad)


def test_formatting_of_money_and_times_uses_kampala():
    assert engine.format_value(S("v", "ugx"), 1250000) == "UGX 1,250,000"
    assert engine.format_value(S("v", "datetime"), datetime(2027, 1, 19, 14, 0, tzinfo=UTC)) == (
        "Tue 19 Jan 2027, 17:00"
    )
    assert engine.format_value(S("v", "date"), date(2027, 12, 7)) == "7 Dec 2027"


def test_no_code_execution_and_no_second_pass():
    # values are data: braces inside a value are never evaluated again
    out = engine.render_text("A {{x}} B", {"x": "{{y}} ${7*7} {% if %}"}, {"x": S("x", "str")})
    assert out == "A {{y}} ${7*7} {% if %} B"
    for bad in (
        "{{ x.__class__ }}",
        "{{ x | upper }}",
        "{{ 7*7 }}",
        "{% if x %}",
        "${x}",
        "{{x",
        "x}}",
        "{{ }}",
        "{{X}}",
        "{{ x y }}",
    ):
        with pytest.raises(TemplateSyntaxError):
            engine.parse_placeholders(bad)


def test_placeholders_are_listed_in_order_without_duplicates():
    assert engine.parse_placeholders("{{a}} {{b}} {{a}}") == ["a", "b"]


def test_html_escaping():
    assert engine.escape_html('<a href="x">&\'') == "&lt;a href=&quot;x&quot;&gt;&amp;&#x27;"


def test_truncate_adds_ellipsis_and_respects_limit():
    assert engine.truncate("abc", 5) == "abc"
    t = engine.truncate("word " * 50, 20)
    assert len(t) <= 20 and t.endswith("…")
