"""A tiny arithmetic parser for band ratios.

Expressions may use band names, ``+ - * /``, parentheses, and numbers.
Names are resolved by the caller. There is no access to Python builtins.
"""

from __future__ import annotations


class ExpressionError(Exception):
    """The expression is empty, unbalanced, or names an unknown band."""


class _Parser:
    def __init__(self, source: str, names: dict[str, float]) -> None:
        self.source = source.replace(" ", "")
        self.names = names
        self.index = 0

    def parse(self) -> float:
        if not self.source:
            raise ExpressionError("expression is empty")
        value = self._expr()
        if self.index != len(self.source):
            raise ExpressionError(f"unexpected input at position {self.index}")
        return value

    def _peek(self) -> str:
        if self.index >= len(self.source):
            return ""
        return self.source[self.index]

    def _at(self, charset: str) -> bool:
        # Empty is a substring of every string, so don't use `peek in charset`.
        char = self._peek()
        return bool(char) and char in charset

    def _expr(self) -> float:
        value = self._term()
        while self._at("+-"):
            op = self._peek()
            self.index += 1
            right = self._term()
            value = value + right if op == "+" else value - right
        return value

    def _term(self) -> float:
        value = self._unary()
        while self._at("*/"):
            op = self._peek()
            self.index += 1
            right = self._unary()
            if op == "/" and right == 0.0:
                return float("nan")
            value = value * right if op == "*" else value / right
        return value

    def _unary(self) -> float:
        if self._at("+-"):
            op = self._peek()
            self.index += 1
            value = self._unary()
            return value if op == "+" else -value
        return self._primary()

    def _primary(self) -> float:
        char = self._peek()
        if char == "(":
            self.index += 1
            value = self._expr()
            if self._peek() != ")":
                raise ExpressionError("missing closing parenthesis")
            self.index += 1
            return value
        if char.isdigit() or char == ".":
            return self._number()
        if char.isalpha() or char == "_":
            return self._name()
        raise ExpressionError(f"unexpected character {char!r}")

    def _number(self) -> float:
        start = self.index
        seen_dot = False
        while self.index < len(self.source):
            char = self.source[self.index]
            if char.isdigit():
                self.index += 1
                continue
            if char == "." and not seen_dot:
                seen_dot = True
                self.index += 1
                continue
            break
        token = self.source[start : self.index]
        try:
            return float(token)
        except ValueError as exc:
            raise ExpressionError(f"bad number {token}") from exc

    def _name(self) -> float:
        start = self.index
        while self.index < len(self.source):
            char = self.source[self.index]
            if char.isalnum() or char == "_":
                self.index += 1
                continue
            break
        name = self.source[start : self.index]
        if name not in self.names:
            raise ExpressionError(f"unknown name {name}")
        return float(self.names[name])


def eval_expr(expression: str, names: dict[str, float]) -> float:
    """Evaluate ``expression`` with the provided numeric names."""

    return _Parser(expression, names).parse()
