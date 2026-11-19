"""The expression evaluator: safe subset, template rendering, truthiness."""

from __future__ import annotations

import pytest

from app.workflows.expressions import ExpressionError, evaluate, render, render_object, truthy

CONTEXT = {
    "fetch": {"statusCode": 200, "body": {"items": [{"name": "a"}, {"name": "b"}]}},
    "trigger": {"startedAt": "2026-01-01T00:00:00Z"},
    "count": 3,
    "flag": False,
}


@pytest.mark.parametrize(
    "expr, expected",
    [
        ("fetch.statusCode == 200", True),
        ("fetch.statusCode != 200", False),
        ("fetch.statusCode >= 500", False),
        ("count + 1", 4),
        ("count * 2 - 1", 5),
        ("fetch.body.items[0].name", "a"),
        ("fetch.body.items[1]['name']", "b"),
        ("fetch.missing.deep", None),
        ("fetch.body.items[9]", None),
        ("count > 1 and fetch.statusCode == 200", True),
        ("flag or count", 3),
        ("not flag", True),
        ("'ok' if fetch.statusCode == 200 else 'bad'", "ok"),
        ("count in [1, 2, 3]", True),
        ("5 / 0", None),
        ("'x' < 3", False),
    ],
)
def test_evaluate(expr, expected):
    assert evaluate(expr, CONTEXT) == expected


def test_render_whole_template_returns_raw_value():
    assert render("{{ fetch.body }}", CONTEXT) == CONTEXT["fetch"]["body"]


def test_render_interpolates_embedded_expressions():
    assert render("status={{ fetch.statusCode }} name={{ fetch.body.items[0].name }}", CONTEXT) == (
        "status=200 name=a"
    )


def test_render_missing_value_becomes_empty_string():
    assert render("[{{ fetch.nope }}]", CONTEXT) == "[]"


def test_render_passes_non_strings_through():
    assert render(42, CONTEXT) == 42


def test_render_object_walks_nested_structures_keeping_raw_types():
    # a value that is entirely one {{ ... }} block keeps its evaluated type
    template = {"a": "{{ count }}", "b": ["{{ fetch.statusCode }}", {"c": "{{ flag }}"}]}
    assert render_object(template, CONTEXT) == {"a": 3, "b": [200, {"c": False}]}


def test_render_object_interpolates_when_text_surrounds_the_block():
    assert render_object({"a": "n={{ count }}"}, CONTEXT) == {"a": "n=3"}


def test_disallowed_calls_raise():
    with pytest.raises(ExpressionError):
        evaluate("__import__('os').system('x')", CONTEXT)
    with pytest.raises(ExpressionError):
        evaluate("open('secret')", CONTEXT)


def test_dunder_attribute_access_raises():
    with pytest.raises(ExpressionError):
        evaluate("fetch.__class__", CONTEXT)


def test_truthy():
    assert truthy("x") and truthy(1) and truthy([0])
    assert not truthy("") and not truthy(0) and not truthy(None) and not truthy([])
