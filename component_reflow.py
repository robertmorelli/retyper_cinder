from ast import walk, For, AnnAssign, Name, Load, Attribute, Subscript, Call, Slice, Store
from fun_grouping import func_of, union_find


def reflow(tree, parents, components, reverse_outflow, inflow, outflow):
    """Extend the component / inflow / outflow graph across loop variables and
    member-access chains. Returns the (rebuilt) components mapping; mutates
    reverse_outflow / inflow / outflow in place."""

    # loop var <-> iterator: unify an annotated loop var's root with the iterator
    # (co-removal), and redirect the loop var's uses to the iterator so the
    # member-access reflow below clobbers `x.attr` / `x.method()`. Matches cinder's
    # visitFor, which re-derives the loop var from the container's element type.
    name_anno = {}
    for n in walk(tree):
        if isinstance(n, AnnAssign) and isinstance(n.target, Name):
            name_anno[(func_of(n, parents), n.target.id)] = n
    for n in walk(tree):
        if isinstance(n, For) and isinstance(n.target, Name):
            it = reverse_outflow.get(n.iter)
            if it is None:
                continue
            tg = name_anno.get((func_of(n, parents), n.target.id))
            if tg is not None:
                components.setdefault(tg, set()).add(it)
                components.setdefault(it, set()).add(tg)
            for u in walk(n):
                if isinstance(u, Name) and u.id == n.target.id and isinstance(u.ctx, Load):
                    reverse_outflow[u] = it

    # union-find closure of components
    parent, find, union = union_find(components)
    for a, nbrs in components.items():
        for b in nbrs:
            union(a, b)
    closed = {}
    for n in parent:
        closed.setdefault(find(n), set()).add(n)
    components = {n: closed[find(n)] for n in parent}

    # member-access reflow: walk up `.attr` / `x[..]` / `f(..)` from each leaf,
    # extending the decl's inflow (args/subscripts) and outflow through the chain.
    def base(p, child):
        if isinstance(p, (Attribute, Subscript)): return p.value is child
        if isinstance(p, Call): return p.func is child
        return False
    for leaf, decl in list(reverse_outflow.items()):
        node = leaf
        while base(p := parents.get(node), node):
            if isinstance(p, Call):
                inflow.setdefault(decl, set()).update(p.args)
            elif isinstance(p, Subscript):
                s = p.slice
                for part in (s.lower, s.upper, s.step) if isinstance(s, Slice) else (s,):
                    if part: inflow.setdefault(decl, set()).add(part)
            # `base` guarantees p is Attribute/Subscript/Call; only the first two
            # carry a ctx, so a Store here means we hit an assignment target
            if isinstance(p, (Attribute, Subscript)) and isinstance(p.ctx, Store):
                inflow.setdefault(decl, set()).add(parents[p].value)
                break
            outflow.setdefault(decl, set()).add(p)
            reverse_outflow[p] = decl
            node = p
    return components


#
#
#
#
#
#
#
#
#
