import sys
from get_ast_data import get_ast_data, is_const
from load_source import load_bench
from anno_remover import remove_annotations
from update_type_tables import update_type_context_pairs
from patch_adder import add_patches
from import_adder import add_imports

from tower_simplifier import simplify_coercions
from len_fixer import fix_len
from ast import fix_missing_locations, unparse

source = load_bench(sys.argv[1], sys.argv[2])
do_stage_two = eval(sys.argv[3]) if len(sys.argv) > 3 else True

roots, types, type_ctxs, _, components, outflow, inflow, valid_pair, source_ast, dyn = get_ast_data(source)

# TODO: add partial detyping

all_items_to_remove = set()
for root in roots[:]:
    all_items_to_remove |= components.get(root) or set()
    all_items_to_remove.add(root)

inflow_nodes = set()
for decl in all_items_to_remove:
    inflow_nodes |= inflow.get(decl) or set()

update_type_context_pairs(dyn, types, type_ctxs, components, outflow, inflow, all_items_to_remove)
fixed_ast = fix_len(source_ast, types, dyn)
detyped_ast = remove_annotations(fixed_ast, all_items_to_remove)
patched_ast = add_patches(detyped_ast, types, type_ctxs, valid_pair, inflow_nodes)
patched_ast_with_imports = add_imports(patched_ast)
patched_ast_with_imports = fix_missing_locations(patched_ast_with_imports)

if do_stage_two:
    _, detyped_types, detyped_type_ctxs, constructors, _, _, _, valid_pair_detyped, unsimplified_detyped_ast, _ = get_ast_data(unparse(patched_ast_with_imports))
    simplified_detyped_ast = simplify_coercions(unsimplified_detyped_ast, constructors, valid_pair_detyped, detyped_types, detyped_type_ctxs)
    print(unparse(simplified_detyped_ast))
else:
    print(unparse(patched_ast_with_imports))
