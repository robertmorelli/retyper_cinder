"""Count Python AST nodes while excluding complete import subtrees.

Usage:
    python utilities/count_ast_nodes_tool.py FILE [FILE ...]

``Import`` and ``ImportFrom`` nodes are not counted, and neither are their
children (such as ``alias`` nodes).  Every other node produced by ``ast`` is
included in the count.
"""

from argparse import ArgumentParser
from ast import AST, Import, ImportFrom, iter_child_nodes, parse
from pathlib import Path


IMPORT_NODES = (Import, ImportFrom)


def count_nodes_excluding_imports(node: AST) -> int:
    """Count *node* and descendants, pruning every import subtree."""
    if isinstance(node, IMPORT_NODES):
        return 0
    return 1 + sum(
        count_nodes_excluding_imports(child)
        for child in iter_child_nodes(node)
    )


def count_file(path: Path) -> int:
    """Parse *path* and count its non-import AST nodes."""
    return count_nodes_excluding_imports(parse(path.read_text(), filename=str(path)))


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path, metavar="FILE")
    args = parser.parse_args()

    counts = [(path, count_file(path)) for path in args.files]
    for path, count in counts:
        print(f"{path}: {count}")
    if len(counts) > 1:
        print(f"total: {sum(count for _, count in counts)}")


if __name__ == "__main__":
    main()
