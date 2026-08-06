"""Legacy smoke-test entry point; delegates to the one test runner."""
from os import cpu_count
from subprocess import call
from sys import executable

raise SystemExit(call([
    executable, "test_graph.py", "compile", "--plan", "smoke",
    "--granularity", "annotation", "--workers", str(cpu_count() or 1),
    "--repetitions", "1",
]))
