"""Run the detyper backwards: put annotations back into a file that has none.

The forward tool starts from a fully annotated module and erases a mask of its
annotations, mediating the disagreements the erasure creates. This asks the
mirror question. Erase everything first, throw the typed module's binder data
away, and then hand a mask to what is left: can annotations be put back at the
locations the mask names, and does the result still compile?

Four stages, and which data each is allowed to see is the whole point.

  detype      the advanced variant, mask = everything. The output text is
              kept. Its AST, its types, its contexts and the graph that
              produced them are not.
  remember    the one thing carried across: a table from a site -- a
              qualified name and which slot of it, `Body.__init__:arg:x`,
              `advance:return` -- to the annotation source text that used to
              be there. Names and text, no binder objects, and nothing that
              says what any expression's type was.
  choose      the mask indexes `units()` of a typedness graph built on the
              detyped file. The detyped file still has an annotation at every
              erased site, spelled `Any`, so the same unit machinery runs on
              it and the mask means the same kind of thing it does forward --
              except it selects what to restore rather than what to erase.
  mediate     the restored tree is bound and run through the mediator, the
              same pass the forward direction ends with. Restoring `x: int64`
              is what makes the callers that pass a dynamic wrong, and the
              patches the erasure left behind may now be the pointless ones.

The last stage is ground truth about the file it is given, not a prediction:
the reverse direction has no equivalent of `settle`, which propagates the loss
of an annotation and cannot propagate a gain. So this measures what the
question asks -- whether the sites a graph on the detyped file picks out can
take their annotations back and still compile -- and not how well a prediction
of the restored types would have done. That would be the next experiment.

Every run starts at mask 0, which restores nothing and so retypes nothing. It
is not a wasted row: it is the baseline that says whether re-mediating this
file at all leaves it standing, and six of the twelve benchmarks fail there.
Re-mediating an already mediated module is not the identity, and not because
anything can tell our wrappers from the author's -- nothing tries to, and
`_record` files an inserted constructor in the same `constructors` map so that
nothing can. It is that a wrapper is justified by the state without it. Run
again over the emitted file and `collapse_tower` asks the picker to rebuild
`box(double(self.x))` from the position it now occupies: `self.x` is dynamic,
the slot wants `double`, and `check_can_assign_from(double, dynamic)` is True,
so the picker answers "nothing needed" and the `double` comes out. `box` of a
bare dynamic is what the strict loader then refuses. A masked row from a
benchmark whose baseline fell says nothing about retyping, so the summary
counts those separately.

  python retyper.py                       every benchmark, 3 masks each
  python retyper.py --benchmark nbody --count 8
  python retyper.py --benchmark nbody --mask 5 --emit out.py
  python retyper.py --full                restore everything (should re-type)
"""
from argparse import ArgumentParser
from ast import (AnnAssign, AsyncFunctionDef, ClassDef, FunctionDef, Import,
                 ImportFrom, Name, alias, arg, fix_missing_locations, parse,
                 unparse, walk)
from json import dump
from random import Random
from sys import stderr

from binding_data import BoundData
# cinderx_binding is what puts the patched cinderx on the path, so it has to be
# imported before anything reaches into that package
from cinderx_binding import get_ast_data
from cinderx.compiler.errors import CollectingErrorSink
from cinderx.compiler.static import StaticCodeGenerator
from cinderx.compiler.static.compiler import Compiler
from cinderx.compiler.static.type_binder import TypeBinder
from detyper import detype
from import_adder import _insert_point, add_imports
from inline_call_analysis import find_inline_args
from list_benchmarks import get_bench_list
from load_source import load_bench
from test import _error, _run_module
from type_mediator import coerce_tree
from typedness_graph import build_binding_graph

VARIANT = "advanced"
GRANULARITY = "benchmark"
SKIP = {"scratch"}


# --------------------------------------------------------- a tolerant binder

def bind_tolerantly(proto_tree):
    """`get_ast_data`, but the binder collects its errors instead of raising.

    The reverse direction needs this and the forward one does not. Forward,
    the module the mediator works from is the fully annotated one, which
    binds; the erasure is a prediction laid over a valid bind. Backwards, the
    module the mediator has to fix is the half-retyped one, and that is
    exactly a module that does not bind -- `x: double` is back and the callers
    still pass a dynamic. A throwing sink stops at the first of those, which
    is the first thing the mediator was going to repair.

    A collecting sink lets the bind finish, so `expr_types` covers the whole
    tree and the disagreements are in the tables rather than in an exception.
    Local to this file: `cinderx_binding.get_ast_data` is what every other
    caller wants, and this is an experiment.
    """
    sink = CollectingErrorSink()
    compiler = Compiler(StaticCodeGenerator, error_sink=sink)
    compiler.bind("", "", proto_tree, proto_tree, optimize=0)
    tree = compiler.ast_cache.get(proto_tree)
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

    types, type_ctxs = module.expr_types, module.expr_ctx_types
    for node in types:
        if not valid_pair(types[node], type_ctxs[node], node):
            type_ctxs[node] = dyn

    roots = sorted({node for node in {*module.outflow, *module.inflow,
                                      *module.components} if node is not None},
                   key=lambda node: (node.lineno, node.col_offset))
    anno_roots = [
        root for root in roots
        if (isinstance(root, (AnnAssign, arg)) and root.annotation is not None)
        or (isinstance(root, (FunctionDef, AsyncFunctionDef))
            and root.returns is not None)]
    return BoundData(
        roots=roots, types=types, type_contexts=type_ctxs,
        constructors=module.constructors, components=module.components,
        outflow=module.outflow, inflow=module.inflow, valid_pair=valid_pair,
        tree=tree, dynamic=dyn, declared_types=module.declared_types,
        declaration_types=module.declaration_types,
        iteration_types=module.iteration_types,
        reverse_outflow=module.reverse_outflow, annotation_roots=anno_roots,
        benchmark_roots=[], resolved_from=module.resolved_from), sink


# ------------------------------------------------------------------ the table

def _sites(tree):
    """Every annotated slot in a tree, as (site key, node, slot name).

    The key has to survive unparsing and a full erasure, so it is made of
    names: the scopes a definition sits in, its own name, and which of its
    slots this is. Positions would not survive -- the detyped text is
    unparsed and every line has moved.
    """
    found = []

    def walk_body(body, scope):
        for node in body:
            if isinstance(node, (FunctionDef, AsyncFunctionDef)):
                qualified = scope + [node.name]
                name = ".".join(qualified)
                if node.returns is not None:
                    found.append((f"{name}:return", node, "returns"))
                args = node.args
                for parameter in (args.posonlyargs + args.args
                                  + ([args.vararg] if args.vararg else [])
                                  + args.kwonlyargs
                                  + ([args.kwarg] if args.kwarg else [])):
                    if parameter.annotation is not None:
                        found.append((f"{name}:arg:{parameter.arg}",
                                      parameter, "annotation"))
                walk_body(node.body, qualified)
            elif isinstance(node, ClassDef):
                walk_body(node.body, scope + [node.name])
            elif isinstance(node, AnnAssign) and isinstance(node.target, Name):
                found.append((f"{'.'.join(scope + [node.target.id])}:var",
                              node, "annotation"))
            else:
                for child in getattr(node, "body", []) or []:
                    walk_body([child], scope)
                for child in getattr(node, "orelse", []) or []:
                    walk_body([child], scope)
    walk_body(tree.body, [])
    return found


def remember(source):
    """site key -> annotation source text, from the fully typed module."""
    return {key: unparse(getattr(node, slot))
            for key, node, slot in _sites(parse(source))}


def imports_of(source):
    """name -> the `from module import name` that supplied it.

    Erasure takes the imports the annotations needed with it: nothing in the
    detyped pystone mentions `Final`, so nothing imports it, and putting
    `LOOPS: Final[int]` back is `Name 'Final' is not defined` until the import
    comes back too. Names and module names -- the same kind of thing the
    annotation table is.
    """
    found = {}
    for node in walk(parse(source)):
        if isinstance(node, ImportFrom):
            for name in node.names:
                found.setdefault(name.asname or name.name,
                                 (node.module, name.name, name.asname,
                                  node.level))
    return found


def ensure_imports(tree, imports):
    """Import whatever the annotations now mention and the file does not have.

    Only names the remembered imports can speak for, and only ones nothing in
    the tree already binds.
    """
    bound = {alias.asname or alias.name
             for node in walk(tree) if isinstance(node, (Import, ImportFrom))
             for alias in node.names}
    bound.update(node.name for node in walk(tree)
                 if isinstance(node, (FunctionDef, AsyncFunctionDef, ClassDef)))
    wanted = {}
    for node in walk(tree):
        annotation = (getattr(node, "annotation", None)
                      or getattr(node, "returns", None))
        if annotation is None:
            continue
        for name in walk(annotation):
            if (isinstance(name, Name) and name.id not in bound
                    and name.id in imports):
                module, original, asname, level = imports[name.id]
                wanted.setdefault((module, level), []).append(
                    alias(name=original, asname=asname))
    for (module, level), names in wanted.items():
        tree.body.insert(_insert_point(tree.body),
                         ImportFrom(module=module, names=names, level=level))
    return tree


# --------------------------------------------------------------- the pipeline

def erase_all(source):
    """The advanced variant with every annotation gone, as text."""
    return unparse(detype(source, mask=0, bench=True))


def restorable(tree, table):
    """The `Any` slots of a detyped tree that the table can speak for.

    Keyed by node, because that is what a mask over graph units hands back.
    A site whose annotation was already `Any` in the typed source is dropped:
    restoring it would change nothing and would make a bit of the mask a
    no-op that still counted as restored.
    """
    out = {}
    for key, node, slot in _sites(tree):
        written = table.get(key)
        current = getattr(node, slot)
        if (written is not None and written != "Any"
                and isinstance(current, Name) and current.id == "Any"):
            out.setdefault(node, []).append((key, slot, written))
    return out


def restore(tree, sites, chosen):
    """Put the remembered annotation back at every chosen site. Counts them."""
    restored = 0
    for node in chosen:
        for _, slot, written in sites.get(node, ()):
            setattr(node, slot, parse(written, mode="eval").body)
            restored += 1
    return fix_missing_locations(tree), restored


def mediate(tree):
    """Bind the restored tree and run the mediator over it.

    The same pass the forward direction ends with, and for the same reason:
    an annotation that is back makes some of the values reaching it wrong, and
    some of the patches the erasure inserted pointless.
    """
    bound, sink = bind_tolerantly(parse(unparse(tree)))
    coerced = coerce_tree(bound.tree, bound.types, bound.type_contexts,
                          bound.dynamic, bound.valid_pair,
                          find_inline_args(bound.tree, bound.reverse_outflow,
                                           bound.types),
                          build_binding_graph(bound), bound.constructors)
    return (unparse(fix_missing_locations(add_imports(coerced))),
            len(sink.errors))


def prepare(bench):
    """Detyped text, its graph's units, and the annotation table.

    Everything the typed module knew is dropped here except `table`, which is
    strings.
    """
    typed = load_bench(bench, VARIANT)
    table, imports = remember(typed), imports_of(typed)
    detyped = erase_all(typed)
    # from this line on, only `detyped` and `table` are in play
    data = get_ast_data(parse(detyped))
    graph = build_binding_graph(data)
    return (detyped, table, imports, graph, graph.units(GRANULARITY),
            restorable(data.tree, table))


def keys_for_mask(graph, graph_sites, mask):
    """The site keys a mask over the detyped file's units names.

    Keys rather than nodes: the tree the graph was built on and the tree an
    annotation is written into are different parses of the same text, so a
    node from one is not a node of the other. The key is what they share.
    """
    keys = set()
    for index, unit in enumerate(graph.units(GRANULARITY)):
        if not mask & (1 << index):
            continue
        for node in unit:
            for key, _, _ in graph_sites.get(node, ()):
                keys.add(key)
    return keys


def retype(detyped, table, imports, graph, graph_sites, mask):
    """Restore what `mask` names, mediate, and say how much came back."""
    keys = keys_for_mask(graph, graph_sites, mask)
    tree = parse(detyped)
    sites = restorable(tree, table)
    chosen = [node for node, entries in sites.items()
              if any(key in keys for key, _, _ in entries)]
    tree, restored = restore(tree, sites, chosen)
    tree = ensure_imports(tree, imports)
    source, disagreements = mediate(tree)
    return source, restored, len(keys), disagreements


def main():
    parser = ArgumentParser()
    parser.add_argument("--benchmark", action="append", default=[])
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260820)
    parser.add_argument("--mask", type=int)
    parser.add_argument("--full", action="store_true",
                        help="one mask: restore every unit")
    parser.add_argument("--emit", help="write the retyped source here")
    parser.add_argument("--out", help="write a json report here")
    args = parser.parse_args()

    wanted = set(args.benchmark)
    benches = [b for b, v, _ in get_bench_list()
               if v == VARIANT and b not in SKIP
               and (not wanted or b in wanted)]

    rows = []
    for bench in benches:
        try:
            (detyped, table, imports, graph, units,
             graph_sites) = prepare(bench)
        except Exception as exc:
            rows.append({"benchmark": bench, "status": "prepare_failure",
                         "error": _error(exc)})
            print(f"{bench}: prepare_failure {_error(exc)}", file=stderr)
            continue
        n = len(units)
        full = (1 << n) - 1
        if args.mask is not None:
            masks = [args.mask]
        elif args.full:
            masks = [0, full]
        else:
            rng = Random(args.seed)
            masks = sorted({sum(1 << i for i in rng.sample(range(n),
                                                           rng.randint(1, n)))
                            for _ in range(args.count * 4)})[:args.count]
            # mask 0 restores nothing, so it retypes nothing: it is the
            # baseline that says whether re-mediating this file at all leaves
            # it standing. A benchmark that fails there is telling us about
            # the mediator's idempotence, not about retyping.
            masks = [0] + masks + [full]
        for mask in masks:
            row = {"benchmark": bench, "units": n, "mask": mask,
                   "bits": bin(mask).count("1"),
                   "baseline": mask == 0}
            try:
                source, restored, named, disagreements = retype(
                    detyped, table, imports, graph, graph_sites, mask)
            except Exception as exc:
                row.update(status="retype_failure", error=_error(exc))
                rows.append(row)
                print(f"{bench} mask={mask}: retype_failure {row['error']}",
                      file=stderr)
                continue
            row["sites_named"] = named
            row["bind_errors"] = disagreements
            row["annotations_restored"] = restored
            proc, _ = _run_module(source, compile_only=True)
            if proc.returncode:
                row.update(status="compile_failure",
                           error=proc.stderr.strip().splitlines()[-1])
            else:
                row.update(status="compiles", error=None)
            rows.append(row)
            if args.emit:
                with open(args.emit, "w") as stream:
                    stream.write(source)
            print(f"{bench:18} mask={mask:<12} {row['bits']:2}/{n:2} units  "
                  f"{row['annotations_restored']:3} annotations  "
                  f"{row['bind_errors']:3} bind errors  "
                  f"{'baseline ' if row['baseline'] else ''}{row['status']}"
                  f"{'  ' + row['error'] if row.get('error') else ''}")

    # a masked row only says something about retyping if the benchmark's
    # baseline stood: otherwise the failure was already there with nothing
    # restored.
    standing = {r["benchmark"] for r in rows
                if r.get("baseline") and r.get("status") == "compiles"}
    masked = [r for r in rows if not r.get("baseline")]
    judged = [r for r in masked if r["benchmark"] in standing]
    ok = sum(1 for r in judged if r["status"] == "compiles")
    print(f"\nbaseline holds for {len(standing)} benchmarks; "
          f"{ok}/{len(judged)} masked retypes compile there "
          f"({sum(1 for r in masked if r['status'] == 'compiles')}"
          f"/{len(masked)} over all benchmarks)", file=stderr)
    if args.out:
        with open(args.out, "w") as stream:
            dump(rows, stream, indent=1)
    return 0 if judged and ok == len(judged) else 1


if __name__ == "__main__":
    raise SystemExit(main())
