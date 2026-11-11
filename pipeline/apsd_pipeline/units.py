"""Unit handling.

Onshape feature parameters carry their value ONLY in the ``expression`` string
(observed in this document: ``"value": 0.0, "units": ""`` for every quantity), e.g.
``"13.000 mm"``, ``"13.0*mm"``, ``"(7.98/2) mm"``, ``"90 degree"``.

We evaluate literal arithmetic only. Expressions containing variables (``#d``),
functions or unknown unit words are NOT guessed: they return ``None`` and the
caller records UNKNOWN with the original expression.
"""

from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass

LENGTH_TO_MM = {
    "mm": 1.0,
    "millimeter": 1.0,
    "millimeters": 1.0,
    "millimetre": 1.0,
    "cm": 10.0,
    "centimeter": 10.0,
    "centimeters": 10.0,
    "m": 1000.0,
    "meter": 1000.0,
    "meters": 1000.0,
    "metre": 1000.0,
    "in": 25.4,
    "inch": 25.4,
    "inches": 25.4,
    "ft": 304.8,
    "foot": 304.8,
    "feet": 304.8,
    "yd": 914.4,
    "yard": 914.4,
    "yards": 914.4,
}
ANGLE_TO_DEG = {"deg": 1.0, "degree": 1.0, "degrees": 1.0, "rad": 57.29577951308232, "radian": 57.29577951308232}

_ALLOWED_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
_ALLOWED_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


@dataclass(frozen=True)
class Quantity:
    value: float  # numeric value in the ORIGINAL unit
    unit: str  # original unit word, '' if dimensionless
    expression: str  # original expression text

    def to_mm(self) -> float | None:
        f = LENGTH_TO_MM.get(self.unit.lower())
        return None if f is None else self.value * f

    def to_deg(self) -> float | None:
        f = ANGLE_TO_DEG.get(self.unit.lower())
        return None if f is None else self.value * f

    @property
    def kind(self) -> str:
        u = self.unit.lower()
        if u in LENGTH_TO_MM:
            return "length"
        if u in ANGLE_TO_DEG:
            return "angle"
        return "dimensionless" if u == "" else "unknown"


def _eval_literal(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_literal(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        return _ALLOWED_BINOPS[type(node.op)](_eval_literal(node.left), _eval_literal(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
        return _ALLOWED_UNARY[type(node.op)](_eval_literal(node.operand))
    raise ValueError("non-literal expression")


_UNIT_WORDS = sorted(set(LENGTH_TO_MM) | set(ANGLE_TO_DEG), key=len, reverse=True)
_TRAILING_UNIT_RE = re.compile(r"^(?P<num>.*?)\s*\*?\s*(?P<unit>" + "|".join(_UNIT_WORDS) + r")\s*$", re.IGNORECASE)


def parse_expression(expr: str | None) -> Quantity | None:
    """Parse '<literal arithmetic> [*] <unit>' -> Quantity, else None (=UNKNOWN).

    Examples: '13.000 mm' -> 13 mm ; '13.0*mm' -> 13 mm ; '(7.98/2) mm' -> 3.99 mm ;
    '0.5' -> 0.5 (dimensionless) ; '#len + 2 mm' -> None ; '5 furlong' -> None.
    """
    if expr is None:
        return None
    text = str(expr).strip()
    if not text or "#" in text:
        return None
    m = _TRAILING_UNIT_RE.match(text)
    num_text, unit = (m.group("num"), m.group("unit")) if m else (text, "")
    num_text = num_text.strip()
    if not num_text:
        return None
    # Reject anything with letters left (identifiers, functions, unsupported units).
    if re.search(r"[A-Za-z_]", num_text):
        return None
    try:
        value = _eval_literal(ast.parse(num_text, mode="eval"))
    except (ValueError, SyntaxError, ZeroDivisionError):
        return None
    return Quantity(value=value, unit=unit.lower(), expression=text)


def length_mm_from_expression(expr: str | None) -> tuple[float | None, dict]:
    """Return (value_mm or None, evidence dict with original value/unit/conversion)."""
    q = parse_expression(expr)
    if q is None:
        return None, {"original_expression": expr, "conversion": None, "reason": "expression not a literal length"}
    mm = q.to_mm()
    if mm is None:
        return None, {"original_expression": expr, "conversion": None, "reason": f"unit '{q.unit}' is not a length"}
    factor = LENGTH_TO_MM[q.unit]
    return round(mm, 6), {
        "original_expression": expr,
        "original_value": q.value,
        "original_unit": q.unit,
        "conversion": f"{q.value:g} {q.unit} x {factor:g} = {mm:g} mm",
    }


def m_to_mm(value_m: float) -> float:
    return round(value_m * 1000.0, 6)
