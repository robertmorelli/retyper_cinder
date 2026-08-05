from get_ast_data import get_ast_data
from anno_remover import remove_annotations
from graph_construction import build_graph
from graph_solve import settle_tables
from parent_pointers import build_parents
from patch_adder import add_patches
from import_adder import add_imports
from find_needs_exact import find_needs_exact

from tower_simplifier import simplify_coercions
from len_fixer import fix_len
from ast import fix_missing_locations, unparse, parse, walk

def detype(source, do_stage_two=True, mask=0, bench=False):
    roots, types, type_ctxs, constructors, components, outflow, inflow, valid_pair, source_ast, dyn, _, source_reverse_outflow, _, bench_roots, resolved_from = get_ast_data(parse(source))

    units = bench_roots if bench else [{r} for r in roots]

    all_items_to_remove = set()
    for grp in units if not mask else [u for u, b in zip(units, f"{mask:b}"[::-1]) if bool(int(b))]:
        for root in grp:
            all_items_to_remove |= components.get(root) or set()
            all_items_to_remove.add(root)

    graph, deps, dyn_ctx, elem_ctx = build_graph(source_ast, build_parents(source_ast), types,
                                       source_reverse_outflow,
                                       [n for n in walk(source_ast)], resolved_from)
    types, type_ctxs, _ = settle_tables(source_ast, graph, types, type_ctxs,
                                        constructors, dyn, all_items_to_remove,
                                        deps=deps, dyn_ctx=dyn_ctx,
                                        elem_ctx=elem_ctx)
    fixed_ast = fix_len(source_ast, types, dyn)
    detyped_ast = remove_annotations(fixed_ast, all_items_to_remove, types, type_ctxs)
    needs_exact_patch = find_needs_exact(detyped_ast, source_reverse_outflow)
    patched_ast = add_patches(detyped_ast, types, type_ctxs, dyn, valid_pair, needs_exact_patch)
    patched_ast_with_imports = add_imports(patched_ast)
    patched_ast_with_imports = fix_missing_locations(patched_ast_with_imports)

    if do_stage_two:
        # stage two rebinds with a fresh Compiler, so its DYNAMIC is a different
        # object -- carrying stage one's would make every `is dyn` test false
        _, detyped_types, detyped_type_ctxs, constructors, _, _, _, valid_pair_detyped, unsimplified_detyped_ast, detyped_dyn, _, detyped_reverse_outflow, _, _, _ = get_ast_data(parse(unparse(patched_ast_with_imports)))
        needs_exact_tower = find_needs_exact(unsimplified_detyped_ast, detyped_reverse_outflow)
        simplified_detyped_ast = simplify_coercions(unsimplified_detyped_ast, constructors, valid_pair_detyped, detyped_types, detyped_type_ctxs, needs_exact_tower, detyped_dyn)
        return unparse(simplified_detyped_ast)
    else:
        return unparse(patched_ast_with_imports)
