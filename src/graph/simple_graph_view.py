"""Build the data rendered by the typedness graph server."""
import ast
from types import SimpleNamespace

from ..annotation_remover import remove_annotations
from ..cinderx_binding import get_ast_data
from ..import_adder import add_imports
from ..patch_picker import readable_name
from ..type_mediator import coerce_tree
from .typedness_graph import build_binding_graph


def graph_data(source, mask=0, bench=False):
    """Return graph cells and edges for source under an optional mask."""
    bound = written = get_ast_data(ast.parse(source))
    graph = build_binding_graph(bound)
    placed = {}
    active_edges = set(graph.edges)
    if mask:
        granularity = "benchmark" if bench else "annotation"
        erased = graph.nodes_for_mask(mask, granularity)
        predicted = graph.flow(bound, erased)
        active_edges = set(predicted.active_edges)
        active_edges.update(predicted.selected_edges)
        bound = SimpleNamespace(types=predicted.types,
                                type_contexts=predicted.contexts,
                                tree=bound.tree)
        detyped = remove_annotations(written.tree, erased, predicted.types,
                                     predicted.contexts)
        detyped = coerce_tree(detyped, predicted.types, predicted.contexts,
                              written.dynamic, written.valid_pair,
                              written.reverse_outflow, graph)
        detyped = ast.fix_missing_locations(add_imports(detyped))
        source = ast.unparse(detyped)

        def pair(left, right):
            if type(left) is not type(right):
                return
            placed[left] = right
            for field, value in ast.iter_fields(left):
                twin = getattr(right, field, None)
                if isinstance(value, list) and isinstance(twin, list):
                    if len(value) != len(twin):
                        continue
                    for kid, mate in zip(value, twin):
                        if isinstance(kid, ast.AST):
                            pair(kid, mate)
                elif isinstance(value, ast.AST) and isinstance(twin, ast.AST):
                    pair(value, twin)

        pair(detyped, ast.parse(source))
    else:
        active_edges.update(graph.flow(bound).selected_edges)

    cells = {
        cell
        for edge in active_edges
        for cell in (edge.source, edge.target)
    }
    if mask:
        cells = {cell for cell in cells if cell[0] in placed}

    def anchor(cell):
        node = placed.get(cell[0], cell[0])
        return (getattr(node, "annotation", None)
                or getattr(node, "returns", None) or node)

    def label(cell):
        node = anchor(cell)
        text = ast.unparse(node).replace("\n", " ")[:48]
        return f"{getattr(node, 'lineno', 0)}: {text} · {cell[1]}"

    ordered = sorted(cells, key=lambda cell: (
        getattr(anchor(cell), "lineno", 0), cell[1], label(cell)))
    ids = {cell: index for index, cell in enumerate(ordered)}

    def value(cell):
        table = bound.types if cell[1] == "type" else bound.type_contexts
        return table.get(cell[0])

    nodes = [{"id": ids[cell], "slot": cell[1],
              "line": getattr(anchor(cell), "lineno", 0),
              "col": getattr(anchor(cell), "col_offset", 0),
              "endline": getattr(anchor(cell), "end_lineno", 0),
              "endcol": getattr(anchor(cell), "end_col_offset", 0),
              "label": label(cell),
              "values": [readable_name(value(cell))]
              if value(cell) is not None else []}
             for cell in ordered]
    edges = [[ids[edge.source], ids[edge.target]] for edge in active_edges
             if edge.source in ids and edge.target in ids]
    return {"nodes": nodes, "edges": edges, "source": source.splitlines(),
            "annotation_units": len(graph.units("annotation")),
            "function_units": len(graph.units("benchmark"))}
