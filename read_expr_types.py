import ast
import sys
from get_ast_data import get_ast_data, is_const
from load_source import load_bench

source = load_bench(sys.argv[1], sys.argv[2])

roots, types, type_ctxs, components, reads, writes, valid_pair, source_ast = get_ast_data(source)

def node_repr(node):
    type = types[node]
    type_ctx = type_ctxs[node]
    type_string_proto = type.klass.type_name.readable_name
    type_string = type_string_proto if not is_const(node) else "const"
    type_ctx_string = type_ctx.klass.type_name.readable_name
    expr = " ".join(ast.get_source_segment(source, node).split())
    return (
        f"expr={expr:<40}"
        f"type={type_string:<30} "
        f"ctx={type_ctx_string:<30}"
    )

def link_repr(kind, decl, uses):
    out = []
    for use in uses:
        for node in list(components.get(decl) or set()) + [decl]:
            out.append(f"from: {" ".join(ast.get_source_segment(source, node).split())}")
        out.append(f"{kind}: {" ".join(ast.get_source_segment(source, use).split())}")
        out.append("")
    return "\n".join(out)

expr_types = [node_repr(key) for key in types.keys()]
linked_reads = [link_repr("reads", decl, uses) for decl, uses in reads.items()]
linked_writes = [link_repr("writes", decl, uses) for decl, uses in writes.items()]

print("\n".join(expr_types))
# print("\n-- uses --")
# print("\n".join(linked_reads))
# print("\n".join(linked_writes))


