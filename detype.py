"""python detype.py <benchmark> <variant> [mask] [--less-any]"""
from ast import unparse
from sys import argv

from detyper import detype
from load_source import load_bench

args = [a for a in argv[1:] if a != "--less-any"]
less_any = "--less-any" in argv
mask = eval(args[2]) if len(args) > 2 else 0

print(unparse(detype(load_bench(args[0], args[1]), mask=mask, bench=True,
                     less_any=less_any)))
