"""python benchmarking/detype.py <benchmark> <variant> [mask]"""
from ast import unparse
from pathlib import Path
from sys import argv, path as import_path

ROOT = str(Path(__file__).resolve().parent.parent)
for directory in (str(Path(ROOT) / "src"), ROOT):
    if directory not in import_path:
        import_path.insert(0, directory)

from benchmarking.utilities.load_source import load_bench
from src import detype

args = argv[1:]
mask = eval(args[2]) if len(args) > 2 else -1

print(unparse(detype(load_bench(args[0], args[1]), mask=mask, bench=True)))
