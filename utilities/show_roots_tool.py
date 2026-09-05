from ast import get_source_segment
from pathlib import Path
from sys import argv, path as import_path

PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if PROJECT_ROOT not in import_path:
    import_path.insert(0, PROJECT_ROOT)

from utilities.graph import graph
from utilities.load_source import load_bench

source = load_bench(argv[1], argv[2])

roots = graph(source).roots

print("\n".join([get_source_segment(source, root) for root in roots]))
# print("\n".join([str(root) for root in roots]))
