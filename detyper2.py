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

Two phases. `remove_annotations` decides what the program says, across the
whole tree, because whether a value needs coercing depends on what every other
annotation became. Then `coerce_tree` decides what it needs.

A rebind used to sit between them so the coercion pass could work from ground
truth. Measured across 830 masks, the predicted tables reach the same verdict
on every one, so it is gone. A fresh Compiler brings a fresh DYNAMIC
sentinel, so a caller that rebinds must not carry ours across -- every
`is dyn` test would silently be false.
"""
from ast import fix_missing_locations, parse

from annotation_remover import remove_annotations
from cinderx_binding import get_ast_data
from import_adder import add_imports
from inline_call_analysis import find_inline_args
from type_mediator import coerce_tree
from typedness_graph import build_binding_graph


def detype(source, mask=0, bench=False, less_any=False):
    written = get_ast_data(parse(source))
    graph = build_binding_graph(written)
    granularity = "benchmark" if bench else "annotation"
    # mask=0 means erase everything; callers wanting no erasure use the source
    effective = mask or ((1 << len(graph.units(granularity))) - 1)
    erased = graph.nodes_for_mask(effective, granularity)
    predicted = graph.settle(written, erased)

    # erasure first, across the whole tree: whether a value needs coercing
    # depends on what every other annotation became
    tree = remove_annotations(written.tree, erased, predicted.types,
                              predicted.contexts, less_any)
    tree = coerce_tree(tree, predicted.types, predicted.contexts,
                       written.dynamic, written.valid_pair,
                       find_inline_args(tree, written.reverse_outflow,
                                        predicted.types), graph,
                       written.constructors)
    return fix_missing_locations(add_imports(tree))
