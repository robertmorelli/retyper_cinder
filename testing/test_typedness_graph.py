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
from src.type_graph import TYPE_CONSTRAINT, TYPE, TypeGraph
from visualizer.graph_data import graph_data


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

    def test_type_and_constraint_are_separate_cells(self):
        node = ast.parse("x").body[0].value
        graph = TypeGraph()
        self.assertNotEqual(graph.cell(node, TYPE), graph.cell(node, TYPE_CONSTRAINT))

    def test_poison_flows_only_along_edges(self):
        left, right = ast.parse("x + y").body[0].value.left, ast.parse("x + y").body[0].value.right
        graph = TypeGraph()
        static, dynamic = object(), object()
        bound = SimpleNamespace(
            types={left: static, right: static},
            type_constraints={right: static},
            dynamic=dynamic,
        )
        graph.bound = bound
        graph.add_edge(graph.cell(left, TYPE), graph.cell(right, TYPE))
        settled = graph._propagate(
            graph.edges,
            {graph.cell(left, TYPE)},
        )
        self.assertIs(settled.types[right], dynamic)
        self.assertIs(settled.constraints[right], static)

    def test_detyper_uses_one_declaration_source_for_both_slots(self):
        bound = get_ast_data(ast.parse(SOURCE))
        graph = TypeGraph(bound)
        root = graph.roots[0]
        targets = {edge.target for edge in graph.edges
                   if edge.source == (root, TYPE)}
        self.assertTrue(any(slot == TYPE for _, slot in targets))
        self.assertTrue(any(slot == TYPE_CONSTRAINT for _, slot in targets))
        self.assertFalse(any(edge.source == (root, TYPE_CONSTRAINT)
                             for edge in graph.edges))

    def test_either_binop_operand_can_make_result_dynamic(self):
        expression = ast.parse("left + right").body[0].value
        left, right = expression.left, expression.right
        graph = TypeGraph()
        graph.add_edge(graph.cell(expression, TYPE_CONSTRAINT),
                       graph.cell(left, TYPE_CONSTRAINT))
        graph.add_edge(graph.cell(left, TYPE_CONSTRAINT),
                       graph.cell(right, TYPE_CONSTRAINT))
        graph.add_edge(graph.cell(right, TYPE_CONSTRAINT),
                       graph.cell(expression, TYPE))
        static, dynamic = object(), object()
        bound = SimpleNamespace(
            types={left: static, right: static, expression: static},
            type_constraints={left: static, right: static, expression: static},
            dynamic=dynamic,
        )
        graph.bound = bound
        for erased in ({left}, {right}):
            with self.subTest(erased=next(iter(erased)).id):
                initial_dynamic = {
                    graph.cell(node, slot)
                    for node in erased
                    for slot in (TYPE, TYPE_CONSTRAINT)
                }
                settled = graph._propagate(
                    graph.edges,
                    initial_dynamic,
                )
                self.assertIs(settled.types[expression], dynamic)

    def test_graph_absorbs_loop_and_member_reflow(self):
        bound = get_ast_data(ast.parse(REFLOW_SOURCE))
        graph = TypeGraph(bound)
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
                         {TYPE, TYPE_CONSTRAINT})
        bound = get_ast_data(ast.parse(SOURCE))
        graph = TypeGraph(bound)
        self.assertEqual(data["annotation_units"],
                         len(graph.units("annotation")))
        self.assertEqual(data["function_units"],
                         len(graph.units("benchmark")))


if __name__ == "__main__":
    unittest.main()
