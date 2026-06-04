import ast
import sys
from get_ast_data import get_ast_data, is_const
from load_source import load_bench
from anno_remover import remove_annotations

source = load_bench(sys.argv[1], sys.argv[2])

roots, types, type_ctxs, components, reads, writes, valid_pair, source_ast = get_ast_data(source)

all_items_to_remove = set()
for root in roots:
    all_items_to_remove |= components.get(root) or set()
    all_items_to_remove.add(root)

detyped_ast, table = remove_annotations(source_ast, all_items_to_remove)
print(ast.unparse(detyped_ast))

# get ast data
# pick anno set from roots
# remove annotations
# mod expr tables
# filter by invalid
# call patch adder with mod table
