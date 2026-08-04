# TODO: rewrite
"""Insert cinder-inferred annotations into benchmark source in place.

Unlike annotate_source (which round-trips through ast.unparse and so drops
comments/formatting), this splices `: T` directly at each target's source
location, leaving the rest of the file byte-for-byte unchanged.
"""
from ast import Assign, Name, walk, parse
from list_benchmarks import get_bench_list
from get_ast_data import get_ast_data
from annotator import _annotatable

def annotate_in_place(source):
    *_, tree, dyn, declared, _, _ = get_ast_data(parse(source))

    edits = []  # (offset, text) insertions
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))

    for node in walk(tree):
        if not (isinstance(node, Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if not isinstance(target, Name) or target not in declared:
            continue
        name = _annotatable(declared[target], dyn)
        if name is None:
            continue
        offset = starts[target.end_lineno - 1] + target.end_col_offset
        edits.append((offset, f": {name}"))

    for offset, text in sorted(edits, reverse=True):
        source = source[:offset] + text + source[offset:]
    return source

if __name__ == "__main__":
    for bench, variant, path in get_bench_list():
        if "static-python-perf" not in path:
            continue
        src = open(path).read()
        try:
            out = annotate_in_place(src)
        except Exception as e:
            print(f"SKIP  {bench}/{variant}: {type(e).__name__}")
            continue
        open(path, "w").write(out)
        print(f"WROTE {bench}/{variant}")
