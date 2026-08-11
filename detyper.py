from get_ast_data import get_ast_data
from anno_remover import remove_annotations
from simple_type_graph import build_binding_graph
from patch_adder import add_patches
from import_adder import add_imports
from find_needs_exact import find_needs_exact

from tower_simplifier import simplify_coercions
from len_fixer import fix_len
from ast import fix_missing_locations, unparse, parse
from time import perf_counter_ns

def detype(source, do_stage_two=True, mask=0, bench=False, metrics=None):
    metrics = metrics if metrics is not None else {}
    started = perf_counter_ns()
    data = get_ast_data(parse(source))
    metrics["bind_ns"] = perf_counter_ns() - started
    types, type_ctxs = data.types, data.type_contexts
    source_ast, dyn = data.tree, data.dynamic

    started = perf_counter_ns()
    graph = build_binding_graph(data)
    metrics["graph_build_ns"] = perf_counter_ns() - started
    granularity = "benchmark" if bench else "annotation"
    # Preserve the public API: mask=0 means full erasure; callers use the
    # original source directly for the no-erasure case.
    unit_count = len(graph.units(granularity))
    effective_mask = mask or ((1 << unit_count) - 1)
    all_items_to_remove = graph.nodes_for_mask(effective_mask, granularity)
    started = perf_counter_ns()
    settlement = graph.settle(data, all_items_to_remove)
    types, type_ctxs = settlement.types, settlement.contexts
    metrics["graph_settle_ns"] = perf_counter_ns() - started
    started = perf_counter_ns()
    fixed_ast = fix_len(source_ast, types, dyn)
    detyped_ast = remove_annotations(fixed_ast, all_items_to_remove, types, type_ctxs)
    needs_exact_patch = find_needs_exact(detyped_ast, data.reverse_outflow)
    patched_ast = add_patches(detyped_ast, types, type_ctxs, dyn,
                              data.valid_pair, needs_exact_patch, graph)
    patched_ast_with_imports = add_imports(patched_ast)
    patched_ast_with_imports = fix_missing_locations(patched_ast_with_imports)
    metrics["rewrite_ns"] = perf_counter_ns() - started

    if do_stage_two:
        started = perf_counter_ns()
        # stage two rebinds with a fresh Compiler, so its DYNAMIC is a different
        # object -- carrying stage one's would make every `is dyn` test false
        rebound = get_ast_data(parse(unparse(patched_ast_with_imports)))
        needs_exact_tower = find_needs_exact(rebound.tree, rebound.reverse_outflow)
        # the simplifier decides what to delete, so it needs the same view of
        # what depends on what that the patcher has
        rebound_graph = build_binding_graph(rebound)
        simplified_detyped_ast = simplify_coercions(
            rebound.tree, rebound.constructors, rebound.valid_pair,
            rebound.types, rebound.type_contexts, needs_exact_tower,
            rebound.dynamic, rebound_graph,
        )
        result = unparse(simplified_detyped_ast)
        metrics["stage_two_ns"] = perf_counter_ns() - started
        return result
    else:
        metrics["stage_two_ns"] = 0
        return unparse(patched_ast_with_imports)
