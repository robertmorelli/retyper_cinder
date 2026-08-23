import ast
import unittest
from pathlib import Path
from sys import path as import_path
from types import SimpleNamespace

ROOT = str(Path(__file__).resolve().parent.parent)
SRC = str(Path(ROOT) / "src")
for directory in (SRC, ROOT):
    if directory not in import_path:
        import_path.insert(0, directory)

from src.cinderx_binding import get_ast_data
from src.detyper import detype
from src.graph.graph_flow import flow
from src.graph.simple_graph_view import graph_data
from src.graph.typedness_graph import (
    CONTEXT, TYPE, Graph, build_binding_graph)


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
    def test_negative_one_mask_erases_everything(self):
        untouched = ast.unparse(detype(SOURCE, mask=0))
        erased = ast.unparse(detype(SOURCE, mask=-1))
        self.assertIn("x: int", untouched)
        self.assertNotIn("x: int", erased)

    def test_type_and_context_are_separate_cells(self):
        node = ast.parse("x").body[0].value
        graph = Graph()
        self.assertNotEqual(graph.cell(node, TYPE), graph.cell(node, CONTEXT))

    def test_poison_flows_only_along_edges(self):
        left, right = ast.parse("x + y").body[0].value.left, ast.parse("x + y").body[0].value.right
        graph = Graph()
        static, dynamic = object(), object()
        bound = SimpleNamespace(
            types={left: static, right: static},
            type_contexts={right: static},
            dynamic=dynamic,
        )
        graph.add_edge(graph.cell(left, TYPE), graph.cell(right, TYPE))
        settled = flow(graph, set(), {left}, {}, bound)
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

    def test_either_binop_operand_can_make_result_dynamic(self):
        expression = ast.parse("left + right").body[0].value
        left, right = expression.left, expression.right
        graph = Graph()
        graph.add_edge(graph.cell(expression, CONTEXT),
                       graph.cell(left, CONTEXT))
        graph.add_edge(graph.cell(left, CONTEXT),
                       graph.cell(right, CONTEXT))
        graph.add_edge(graph.cell(right, CONTEXT),
                       graph.cell(expression, TYPE))
        static, dynamic = object(), object()
        bound = SimpleNamespace(
            types={left: static, right: static, expression: static},
            type_contexts={left: static, right: static, expression: static},
            dynamic=dynamic,
        )
        for erased in ({left}, {right}):
            with self.subTest(erased=next(iter(erased)).id):
                settled = flow(graph, set(), erased, {}, bound)
                self.assertIs(settled.types[expression], dynamic)

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
        bound = get_ast_data(ast.parse(SOURCE))
        graph = build_binding_graph(bound)
        self.assertEqual(data["annotation_units"],
                         len(graph.units("annotation")))
        self.assertEqual(data["function_units"],
                         len(graph.units("benchmark")))


if __name__ == "__main__":
    unittest.main()
