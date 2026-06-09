import ast
import sys
from get_ast_data import get_ast_data, is_const
from load_source import load_bench
from anno_remover import remove_annotations
from update_type_tables import update_type_context_pairs
from patch_adder import add_patches

source = load_bench(sys.argv[1], sys.argv[2])

roots, types, type_ctxs, components, reads, writes, valid_pair, source_ast, dyn = get_ast_data(source)

# TODO: add partial detyping

all_items_to_remove = set()
for root in roots:
    all_items_to_remove |= components.get(root) or set()
    all_items_to_remove.add(root)

update_type_context_pairs(dyn, types, type_ctxs, components, reads, writes, all_items_to_remove)
detyped_ast = remove_annotations(source_ast, all_items_to_remove)
patched_ast = add_patches(detyped_ast, types, type_ctxs, valid_pair)
ast.fix_missing_locations(patched_ast)

print(ast.unparse(patched_ast))
