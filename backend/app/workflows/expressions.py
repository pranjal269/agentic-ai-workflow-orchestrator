"""Expression evaluation for the execution engine.

Node params may embed ``{{ ... }}`` expressions - ``if`` conditions
(``{{ fetch.statusCode == 200 }}``), ``set`` values, prompt interpolation, and
edge guards. This module evaluates them against a *context*: a mapping of node
id -> that node's output document, plus the calling node's resolved binding
names.

The evaluator walks a parsed AST and only permits a small, side-effect-free
subset of Python: literals, names, attribute/index access, arithmetic, boolean
logic, comparisons, and the ternary. There are no function calls, no attribute
access to dunder names, and no comprehensions - a hostile or careless planner
cannot reach the host through an expression.
"""

from __future__ import annotations

import ast
import re
from typing import Any

_TEMPLATE = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
_WHOLE_TEMPLATE = re.compile(r"^\s*\{\{(.*?)\}\}\s*$", re.DOTALL)

_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a ** b,
}


class ExpressionError(ValueError):
    """Raised when an expression is malformed or uses a disallowed construct."""


def render(template: Any, context: dict[str, Any]) -> Any:
    """Resolve ``{{ expressions }}`` in ``template`` against ``context``.

    * A string that is *entirely* one ``{{ ... }}`` block returns the raw
      evaluated value (so ``"{{ fetch.body }}"`` yields the dict, not its repr).
    * A string with embedded blocks has each block replaced by its stringified
      value (``None`` becomes ``""``).
    * Non-strings are returned unchanged.
    """

    if not isinstance(template, str):
        return template

    whole = _WHOLE_TEMPLATE.match(template)
    if whole:
        return evaluate(whole.group(1), context)

    def _sub(match: re.Match[str]) -> str:
        value = evaluate(match.group(1), context)
        return "" if value is None else str(value)

    return _TEMPLATE.sub(_sub, template)


def render_object(value: Any, context: dict[str, Any]) -> Any:
    """Recursively :func:`render` every string inside a dict/list structure."""

    if isinstance(value, dict):
        return {key: render_object(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [render_object(item, context) for item in value]
    return render(value, context)


def truthy(value: Any) -> bool:
    """Python truthiness, with the empty/zero/``None`` values all falsy."""

    return bool(value)


def evaluate(expression: str, context: dict[str, Any]) -> Any:
    """Evaluate a single (marker-free) expression string against ``context``."""

    source = expression.strip()
    if not source:
        return None
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:  # noqa: TRY003 - message is the useful part
        raise ExpressionError(f"cannot parse expression {expression!r}: {exc.msg}") from exc
    return _eval(tree.body, context)


# --------------------------------------------------------------------------- #
# AST walker
# --------------------------------------------------------------------------- #


def _eval(node: ast.AST, ctx: dict[str, Any]) -> Any:  # noqa: C901 - flat dispatch
    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Name):
        return ctx.get(node.id)

    if isinstance(node, ast.Attribute):
        if node.attr.startswith("__"):
            raise ExpressionError(f"attribute {node.attr!r} is not allowed")
        return _member(_eval(node.value, ctx), node.attr)

    if isinstance(node, ast.Subscript):
        return _member(_eval(node.value, ctx), _eval(node.slice, ctx))

    if isinstance(node, ast.BoolOp):
        values = node.values
        if isinstance(node.op, ast.And):
            result: Any = True
            for value in values:
                result = _eval(value, ctx)
                if not truthy(result):
                    return result
            return result
        result = False
        for value in values:
            result = _eval(value, ctx)
            if truthy(result):
                return result
        return result

    if isinstance(node, ast.UnaryOp):
        operand = _eval(node.operand, ctx)
        if isinstance(node.op, ast.Not):
            return not truthy(operand)
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.UAdd):
            return +operand
        raise ExpressionError("unsupported unary operator")

    if isinstance(node, ast.BinOp):
        handler = _BINOPS.get(type(node.op))
        if handler is None:
            raise ExpressionError("unsupported binary operator")
        try:
            return handler(_eval(node.left, ctx), _eval(node.right, ctx))
        except (TypeError, ZeroDivisionError):
            return None

    if isinstance(node, ast.Compare):
        left = _eval(node.left, ctx)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval(comparator, ctx)
            if not _compare(op, left, right):
                return False
            left = right
        return True

    if isinstance(node, ast.IfExp):
        branch = node.body if truthy(_eval(node.test, ctx)) else node.orelse
        return _eval(branch, ctx)

    if isinstance(node, ast.List):
        return [_eval(item, ctx) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_eval(item, ctx) for item in node.elts)
    if isinstance(node, ast.Dict):
        return {
            _eval(key, ctx): _eval(val, ctx)
            for key, val in zip(node.keys, node.values)
        }

    raise ExpressionError(f"expression construct {type(node).__name__} is not allowed")


def _member(value: Any, key: Any) -> Any:
    """Look ``key`` up in ``value`` for dicts, lists, strings - else ``None``."""

    if isinstance(value, dict):
        return value.get(key)
    if isinstance(value, (list, tuple, str)):
        if isinstance(key, bool) or not isinstance(key, int):
            return None
        try:
            return value[key]
        except IndexError:
            return None
    return None


def _compare(op: ast.cmpop, left: Any, right: Any) -> bool:
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    if isinstance(op, ast.Is):
        return left is right
    if isinstance(op, ast.IsNot):
        return left is not right
    if isinstance(op, ast.In):
        try:
            return left in right
        except TypeError:
            return False
    if isinstance(op, ast.NotIn):
        try:
            return left not in right
        except TypeError:
            return False

    # ordering comparisons: a type mismatch is a falsy answer, not a crash
    try:
        if isinstance(op, ast.Lt):
            return left < right
        if isinstance(op, ast.LtE):
            return left <= right
        if isinstance(op, ast.Gt):
            return left > right
        if isinstance(op, ast.GtE):
            return left >= right
    except TypeError:
        return False

    raise ExpressionError("unsupported comparison operator")
