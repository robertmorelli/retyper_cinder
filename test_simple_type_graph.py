import ast
import unittest

from simple_graph_view import graph_data
from simple_type_graph import Binder, CONTEXT, TYPE


SOURCE = """
def twice(x: int) -> int:
    return x + x

y: int = twice(2)
z = y
"""


class SimpleTypeGraphTests(unittest.TestCase):
    def setUp(self):
        self.tree = ast.parse(SOURCE)
        self.graph = Binder().bind(self.tree)

    def find(self, kind, line):
        return next(node for node in ast.walk(self.tree)
                    if type(node) is kind and getattr(node, "lineno", 0) == line)

    def test_keeps_type_and_context_in_separate_cells(self):
        literal = self.find(ast.Constant, 5)
        self.assertEqual(self.graph.values[(literal, TYPE)], {"int"})
        self.assertEqual(self.graph.values[(literal, CONTEXT)], {"int"})

    def test_types_flow_through_calls_and_assignments(self):
        z = next(node for node in ast.walk(self.tree)
                 if type(node) is ast.Name and node.id == "z")
        self.assertEqual(self.graph.values[(z, TYPE)], {"int"})

    def test_return_type_is_drawn_on_return_annotation(self):
        function = self.find(ast.FunctionDef, 2)
        self.assertIs(self.graph.anchors[(function, TYPE)], function.returns)

    def test_view_data_contains_both_slots_and_edges(self):
        data = graph_data(SOURCE)
        self.assertEqual({node["slot"] for node in data["nodes"]},
                         {TYPE, CONTEXT})
        self.assertTrue(data["edges"])


if __name__ == "__main__":
    unittest.main()
