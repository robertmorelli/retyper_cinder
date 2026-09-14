"""Tests for the import-excluding AST node counter."""

import ast
import unittest

from utilities.count_ast_nodes_tool import count_nodes_excluding_imports


class CountAstNodesTests(unittest.TestCase):
    def test_prunes_import_nodes_and_aliases(self):
        tree = ast.parse("import one as two\nfrom three import four as five\nx = 1\n")
        assignment_only = ast.parse("x = 1\n")

        self.assertEqual(
            count_nodes_excluding_imports(tree),
            count_nodes_excluding_imports(assignment_only),
        )

    def test_counts_all_non_import_descendants(self):
        tree = ast.parse("def f(value):\n    return value + 1\n")
        expected = sum(1 for _ in ast.walk(tree))

        self.assertEqual(count_nodes_excluding_imports(tree), expected)


if __name__ == "__main__":
    unittest.main()
