from get_ast_data import get_ast_data
from anno_remover import remove_annotations
from update_type_tables import update_type_context_pairs
from patch_adder import add_patches
from import_adder import add_imports
from find_needs_exact import find_needs_exact

from tower_simplifier import simplify_coercions
from len_fixer import fix_len
from ast import arg, fix_missing_locations, unparse

def detype(source, do_stage_two=True, mask=0, exclude_self=True):
    roots, types, type_ctxs, _, components, outflow, inflow, valid_pair, source_ast, dyn, _, source_reverse_outflow = get_ast_data(source)

    all_items_to_remove = set()
    for root in roots if not mask else [r for r, b in zip(roots, f"{mask:b}"[::-1]) if bool(int(b))]:
        all_items_to_remove |= components.get(root) or set()
        all_items_to_remove.add(root)

    if exclude_self:
        # TODO: do by construction. no more self in _cinder
        all_items_to_remove = {n for n in all_items_to_remove
                               if not (isinstance(n, arg) and n.arg == "self")}

    inflow_nodes = set()
    for decl in all_items_to_remove:
        inflow_nodes |= inflow.get(decl) or set()

    update_type_context_pairs(dyn, types, type_ctxs, outflow, inflow, all_items_to_remove)
    fixed_ast = fix_len(source_ast, types, dyn)
    detyped_ast = remove_annotations(fixed_ast, all_items_to_remove)
    needs_exact_patch = find_needs_exact(detyped_ast, source_reverse_outflow)
    patched_ast = add_patches(detyped_ast, types, type_ctxs, valid_pair, inflow_nodes, needs_exact_patch)
    patched_ast_with_imports = add_imports(patched_ast)
    patched_ast_with_imports = fix_missing_locations(patched_ast_with_imports)

    if do_stage_two:
        _, detyped_types, detyped_type_ctxs, constructors, _, _, _, valid_pair_detyped, unsimplified_detyped_ast, _, _, detyped_reverse_outflow = get_ast_data(unparse(patched_ast_with_imports))
        needs_exact_tower = find_needs_exact(unsimplified_detyped_ast, detyped_reverse_outflow)
        simplified_detyped_ast = simplify_coercions(unsimplified_detyped_ast, constructors, valid_pair_detyped, detyped_types, detyped_type_ctxs, needs_exact_tower)
        return unparse(simplified_detyped_ast)
    else:
        return unparse(patched_ast_with_imports)
