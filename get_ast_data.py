from ast import Constant, parse, Attribute, Subscript, Call, Slice, Store, walk, For, AnnAssign, Name, FunctionDef, AsyncFunctionDef, Starred, List, Tuple
from json import load
from sys import path
from parent_pointers import build_parents

path.insert(0, "_cinderx/cinderx/PythonLib")

from cinderx.compiler.static.compiler import Compiler
from cinderx.compiler.static import StaticCodeGenerator
from cinderx.compiler.static.types import CType
from cinderx.compiler.symbols import SymbolVisitor
from cinderx.compiler.static.type_binder import TypeBinder

with open("data/benchmark_locations.json") as f:
    sources = load(f)


def get_ctx(node):
    if isinstance(node, (Name, Attribute, Subscript, Starred, List, Tuple)):
        return node.ctx
    return None

def is_const(node):
    return isinstance(node, Constant)

def is_primative(node):
    return isinstance(node.klass, CType)

def get_ast_data(source):
    proto_tree = parse(source)
    compiler = Compiler(StaticCodeGenerator)

    compiler.bind("", "", proto_tree, source, optimize=0)
    tree = compiler.ast_cache.get(source)
    symbols = StaticCodeGenerator._SymbolVisitor(0)
    symbols.visit(tree)
    module = compiler.modules[""]
    binder = TypeBinder(symbols, "", compiler, "", optimize=0)
    dyn = compiler.type_env.DYNAMIC

    def valid_pair(t, tc, node):
        try:
            binder.check_can_assign_from(tc.klass, t.klass, node)
            return True
        except Exception:
            return False

    # for convenience
    types = module.expr_types
    type_ctxs = module.expr_ctx_types
    components = module.components
    outflow = module.outflow
    inflow = module.inflow
    constructors = module.constructors
    reverse_outflow = module.reverse_outflow

    parents = build_parents(tree)

    # TODO: refactor \/
    # reflow stage 1
    def _func_of(n):
        p = parents.get(n)
        while p is not None and not isinstance(p, (FunctionDef, AsyncFunctionDef)):
            p = parents.get(p)
        return p
    _name_anno = {}
    for n in walk(tree):
        if isinstance(n, AnnAssign) and isinstance(n.target, Name):
            _name_anno[(_func_of(n), n.target.id)] = n
    for n in walk(tree):
        if isinstance(n, For) and isinstance(n.target, Name):
            it_root = reverse_outflow.get(n.iter)
            tg_root = _name_anno.get((_func_of(n), n.target.id))
            if it_root is not None and tg_root is not None:
                components.setdefault(tg_root, set()).add(it_root)
                components.setdefault(it_root, set()).add(tg_root)

    # reflow stage 2
    parent = {n: n for n in components}
    def find(x):
        while parent[x] != x:
            x = parent[x]
        return x
    for a, nbrs in components.items():
        for b in nbrs:
            parent[find(a)] = find(b)
    closed = {}
    for n in parent:
        closed.setdefault(find(n), set()).add(n)
    components = {n: closed[find(n)] for n in parent}

    # reflow stage 3
    def base(p, child):
        if isinstance(p, (Attribute, Subscript)): return p.value is child
        if isinstance(p, Call): return p.func is child
        return False
    for leaf, decl in list(reverse_outflow.items()):
        node = leaf
        while base(p := parents.get(node), node):
            if isinstance(p, Call):
                for arg in p.args:
                    inflow.setdefault(decl, set()).add(arg)
            elif isinstance(p, Subscript):
                s = p.slice
                for part in (s.lower, s.upper, s.step) if isinstance(s, Slice) else (s,):
                    if part: inflow.setdefault(decl, set()).add(part)
            if isinstance(get_ctx(p), Store):
                inflow.setdefault(decl, set()).add(parents[p].value)
                break
            outflow.setdefault(decl, set()).add(p)
            reverse_outflow[p] = decl
            node = p
    # TODO: refactor /\

    # clean contexts. not sure this is completely chill
    for node in types.keys():
        if not valid_pair(types[node], type_ctxs[node], node):
            # type_ctxs[node] = types[node]
            type_ctxs[node] = dyn

    roots = []
    all_seen = set()

    all_roots = sorted([*outflow.keys(), *inflow.keys(), *components.keys()], key=lambda e: (e.lineno, e.col_offset))

    for root in all_roots:
        if root in all_seen: continue
        all_seen |= (components.get(root) or set()) | set([root])
        roots.append(root)

    return roots, types, type_ctxs, constructors, components, outflow, inflow, valid_pair, tree, dyn, module.declared_types, reverse_outflow
