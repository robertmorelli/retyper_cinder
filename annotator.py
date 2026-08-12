from re import compile
from ast import NodeTransformer, AnnAssign, Assign, Name, Store, Load, Global, Nonlocal, FunctionDef, AsyncFunctionDef, unparse, parse, walk, iter_child_nodes
from cinderx_binding import get_ast_data

_MAP = {"chklist": "CheckedList", "chkdict": "CheckedDict", "chkset": "CheckedSet"}
_OPT = compile(r"Optional\[(.+)\]$")

def _readable(value, dyn):
    # settled type -> writable Static Python annotation, or None if not annotatable
    if value is None or value is dyn:
        return None
    name = value.klass.type_name.readable_name
    for k, v in _MAP.items():
        name = name.replace(k, v)
    m = _OPT.match(name)          # cinder rejects `Optional[X]`, accepts `X | None`
    if m:
        name = m.group(1) + " | None"
    if "dynamic" in name or "Any" in name or name.startswith("__"):
        return None
    try:
        parse(name, mode="eval")  # must be a parseable annotation expression
    except SyntaxError:
        return None
    return name


class Annotator(NodeTransformer):
    def __init__(self, tree, declared, types, dyn):
        self.declared = declared      # target Name node -> settled type (from cinder)
        self.types = types            # expr node -> inferred type (covers reassigned locals)
        self.dyn = dyn
        self.parents = {c: p for p in walk(tree) for c in iter_child_nodes(p)}
        # names we must not annotate (params / already annotated / global / nonlocal)
        self.blocked = {}
        for fn in walk(tree):
            if isinstance(fn, (FunctionDef, AsyncFunctionDef)):
                b = {a.arg for a in fn.args.args + fn.args.posonlyargs + fn.args.kwonlyargs}
                for n in walk(fn):
                    if isinstance(n, AnnAssign) and isinstance(n.target, Name):
                        b.add(n.target.id)
                    if isinstance(n, (Global, Nonlocal)):
                        b.update(n.names)
                self.blocked[fn] = b

    def _func_of(self, n):
        p = self.parents.get(n)
        while p is not None and not isinstance(p, (FunctionDef, AsyncFunctionDef)):
            p = self.parents.get(p)
        return p

    def _pin_type(self, target, value):
        # prefer cinder's settled declared type; fall back to the RHS inferred type
        # (which exists even for reassigned / flow-narrowed locals cinder won't settle)
        return _readable(self.declared.get(target) or self.types.get(value), self.dyn)

    def visit_Assign(self, node):
        self.generic_visit(node)
        if len(node.targets) != 1 or not isinstance(node.targets[0], Name):
            return node
        target = node.targets[0]
        fn = self._func_of(node)
        if fn is None or target.id in self.blocked.get(fn, ()):   # module/class scope skipped
            return node
        name = self._pin_type(target, node.value)
        if name is None:
            return node
        self.blocked[fn].add(target.id)          # only annotate the first binding
        target.ctx = Store()
        return AnnAssign(target=target, annotation=parse(name, mode="eval").body,
                         value=node.value, simple=1)


def _valid(source):
    try:
        get_ast_data(parse(source))
        return True
    except Exception:
        return False

def annotate_once(source):
    data = get_ast_data(parse(source))
    tree, types = data.tree, data.types
    dyn, declared = data.dynamic, data.declared_types
    Annotator(tree, declared, types, dyn).visit(tree)
    out = unparse(tree)
    return out if _valid(out) else source     # never emit an invalid annotation pass

def annotate_source(source):
    # iterate to a fixed point: a fresh annotation can let cinder infer more
    prev = None
    while source != prev:
        prev, source = source, annotate_once(source)
    return source
