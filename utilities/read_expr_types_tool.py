from ast import get_source_segment, parse
from pathlib import Path
from sys import argv, path as import_path

PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if PROJECT_ROOT not in import_path:
    import_path.insert(0, PROJECT_ROOT)

from src.cinderx_binding import get_ast_data
from utilities.ast_nodes import is_constant
from utilities.load_source import load_bench

source = load_bench(argv[1], argv[2])

data = get_ast_data(parse(source))
types, type_constraints = data.types, data.type_constraints
components, outflow, inflow = data.components, data.outflow, data.inflow

def node_repr(node):
    type = types[node]
    type_constraint = type_constraints[node]
    type_string_proto = type.klass.type_name.readable_name
    type_string = type_string_proto if not is_constant(node) else "const"
    type_constraint_string = type_constraint.klass.type_name.readable_name
    expr = " ".join(get_source_segment(source, node).split())
    return (
        f"expr={expr:<40}"
        f"type={type_string:<30} "
        f"type_constraint={type_constraint_string:<30}"
    )

def link_repr(base, kind, decl, uses):
    out = []
    for use in uses:
        for node in list(components.get(decl) or set()) + [decl]:
            out.append(f"{base}: {" ".join(get_source_segment(source, node).split())}")
        out.append(f"{kind}: {" ".join(get_source_segment(source, use).split())}")
        out.append("")
    return "\n".join(out)

expr_types = [node_repr(key) for key in types.keys()]
linked_reads = [link_repr("from", "outflow", decl, uses) for decl, uses in outflow.items()]
linked_writes = [link_repr("to", "inflow", decl, uses) for decl, uses in inflow.items()]

print("\n".join(expr_types))
print("\n-- uses --")
print("\n".join(linked_reads))
print("\n".join(linked_writes))
