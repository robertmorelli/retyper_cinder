import ast
import unittest

from src.cinderx_binding import get_ast_data
from src.detyper import detype


class MediationTests(unittest.TestCase):
    def test_test_conversion_is_removed_before_comparing_costs(self):
        source = (
            "from __static__ import int64\n"
            "def f(i: int64):\n"
            "    while i < 10:\n"
            "        i += 1\n"
        )
        output = detype(source, mask=-1)
        loop = next(n for n in ast.walk(output) if isinstance(n, ast.While))
        self.assertEqual(ast.unparse(loop.test), "i < 10")
        get_ast_data(ast.parse(ast.unparse(output)))

    def test_operator_candidates_include_surrounding_coercion_cost(self):
        for expression, expected in (
            ("box(a + b)", "a + box(b)"),
            ("box(-(a + b))", "-(a + box(b))"),
            ("box(a < b)", "a < box(b)"),
        ):
            with self.subTest(expression=expression):
                source = (
                    "from typing import Any\n"
                    "from __static__ import int64, box\n"
                    "def f(a: int64, b: int64) -> Any:\n"
                    f"    return {expression}\n"
                )
                output = detype(source, mask=2)
                returned = next(n for n in ast.walk(output)
                                if isinstance(n, ast.Return))
                self.assertEqual(ast.unparse(returned.value), expected)
                get_ast_data(ast.parse(ast.unparse(output)))

    def test_primitive_operands_can_still_win_under_box(self):
        source = (
            "from typing import Any\n"
            "from __static__ import int64, box\n"
            "def f(a: int64, b: int64) -> Any:\n"
            "    return box(-(a + b))\n"
        )
        output = detype(source, mask=0)
        returned = next(n for n in ast.walk(output)
                        if isinstance(n, ast.Return))
        self.assertEqual(ast.unparse(returned.value), "box(-(a + b))")
        get_ast_data(ast.parse(ast.unparse(output)))
