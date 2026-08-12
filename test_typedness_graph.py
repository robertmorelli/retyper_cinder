import ast
import unittest
from types import SimpleNamespace

from cinderx_binding import get_ast_data
from simple_graph_view import graph_data
from typedness_graph import CONTEXT, TYPE, Graph, build_binding_graph


SOURCE = """
def twice(x: int) -> int:
    return x + x

y: int = twice(2)
z = y
"""

REFLOW_SOURCE = """
from __static__ import CheckedList
class Box:
    value: int

def use(items: CheckedList[Box], box: Box) -> Box:
    item: Box = box
    for item in items:
        box.value = item.value
    return box
"""


class SimpleTypeGraphTests(unittest.TestCase):
    def test_type_and_context_are_separate_cells(self):
        node = ast.parse("x").body[0].value
        graph = Graph()
        self.assertNotEqual(graph.cell(node, TYPE), graph.cell(node, CONTEXT))

    def test_poison_flows_only_along_edges(self):
        left, right = ast.parse("x + y").body[0].value.left, ast.parse("x + y").body[0].value.right
        graph = Graph()
        graph.flow(graph.cell(left, TYPE), graph.cell(right, TYPE))
        static, dynamic = object(), object()
        bound = SimpleNamespace(
            types={left: static, right: static},
            type_contexts={right: static},
            dynamic=dynamic,
        )
        settled = graph.settle(bound, {left})
        self.assertIs(settled.types[right], dynamic)
        self.assertIs(settled.contexts[right], static)

    def test_detyper_uses_one_declaration_source_for_both_slots(self):
        bound = get_ast_data(ast.parse(SOURCE))
        graph = build_binding_graph(bound)
        root = bound.roots[0]
        targets = {edge.target for edge in graph.edges
                   if edge.source == (root, TYPE)}
        self.assertTrue(any(slot == TYPE for _, slot in targets))
        self.assertTrue(any(slot == CONTEXT for _, slot in targets))
        self.assertFalse(any(edge.source == (root, CONTEXT)
                             for edge in graph.edges))

    def test_graph_absorbs_loop_and_member_reflow(self):
        bound = get_ast_data(ast.parse(REFLOW_SOURCE))
        graph = build_binding_graph(bound)
        argument = next(node for node in ast.walk(bound.tree)
                        if type(node) is ast.arg and node.arg == "items")
        declaration = next(node for node in ast.walk(bound.tree)
                           if type(node) is ast.AnnAssign
                           and type(node.target) is ast.Name
                           and node.target.id == "item")
        self.assertTrue(any({argument, declaration} <= unit
                            for unit in graph.annotation_units))
        self.assertTrue(any(type(edge.target[0]) is ast.Attribute
                            for edge in graph.edges))

    def test_view_contains_both_slots(self):
        data = graph_data(SOURCE)
        self.assertEqual({node["slot"] for node in data["nodes"]},
                         {TYPE, CONTEXT})


if __name__ == "__main__":
    unittest.main()
