# TODO: rewrite
from ast import FunctionDef, AsyncFunctionDef


def func_of(n, parents):
    p = parents.get(n)
    while p is not None and not isinstance(p, (FunctionDef, AsyncFunctionDef)):
        p = parents.get(p)
    return p


def union_find(nodes=()):
    parent = {n: n for n in nodes}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            x = parent[x]
            parent.setdefault(x, x)
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    return parent, find, union


def group_roots(roots, components, parents):
    root_func = {r: func_of(r, parents) for r in roots}

    _, find, union = union_find()

    for root in roots:
        comp = components.get(root) or {root}
        comp_funcs = {func_of(m, parents) for m in comp}
        first = next(iter(comp_funcs))
        for f in comp_funcs:
            union(first, f)

    groups = {}
    for r in roots:
        g = find(root_func[r])
        groups.setdefault(g, set()).add(r)

    return list(groups.values())
