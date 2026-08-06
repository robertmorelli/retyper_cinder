"""Legacy benchmark-granularity entry point."""
from os import cpu_count
from subprocess import call
from sys import executable

raise SystemExit(call([
    executable, "test_graph.py", "compile", "--plan", "standard",
    "--granularity", "benchmark", "--workers", str(cpu_count() or 1),
    "--repetitions", "1",
]))
