"""Remove annotations from a Static Python module and keep it compiling.

Returns an AST. Unparsing is the caller's business, and so is checking the
result: this module transforms, it does not validate.

There are three different notions of "the types" around this code and most
confusion comes from mixing them up:

  as written   `written.types` from the bind of the original source -- what
               the fully annotated program means.
  predicted    `predicted.types` -- what we believe the program means once a
               mask of annotations is erased. The graph's answer, and the
               only one of the three that can be wrong.
  rebound      what cinderx makes of the text we emit. Ground truth, and no
               longer computed here; a harness that wants it re-parses and
               re-binds the unparsed result.

Stage two used to rebind internally so the tower simplifier could work from
ground truth. Measured across 830 masks, running it on the predicted tables
instead reaches the same verdict on every one, so the round trip is gone and
the check belongs to whoever wants it. A fresh Compiler brings a fresh
DYNAMIC sentinel, so a caller that does rebind must not carry ours across --
every `is dyn` test would silently be false.
"""
from ast import fix_missing_locations, parse

from anno_remover import remove_annotations
from find_needs_exact import find_needs_exact
from get_ast_data import get_ast_data
from import_adder import add_imports
from len_fixer import fix_len
from patch_adder import add_patches
from simple_type_graph import build_binding_graph
from tower_simplifier import simplify_coercions


def detype(source, mask=0, bench=False):
    written = get_ast_data(parse(source))
    graph = build_binding_graph(written)
    granularity = "benchmark" if bench else "annotation"
    # mask=0 means erase everything; callers wanting no erasure use the source
    effective = mask or ((1 << len(graph.units(granularity))) - 1)
    erased = graph.nodes_for_mask(effective, granularity)
    predicted = graph.settle(written, erased)

    tree = fix_len(written.tree, predicted.types, written.dynamic)
    tree = remove_annotations(tree, erased, predicted.types, predicted.contexts)
    tree = add_patches(tree, predicted.types, predicted.contexts,
                       written.dynamic, written.valid_pair,
                       find_needs_exact(tree, written.reverse_outflow), graph)
    tree = fix_missing_locations(add_imports(tree))
    return simplify_coercions(
        tree, written.constructors, written.valid_pair,
        predicted.types, predicted.contexts,
        find_needs_exact(tree, written.reverse_outflow),
        written.dynamic, graph)
