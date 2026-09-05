from pathlib import Path
from sys import argv, path as import_path

PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if PROJECT_ROOT not in import_path:
    import_path.insert(0, PROJECT_ROOT)

from utilities.graph import count_roots
from utilities.load_source import load_bench

source = load_bench(argv[1], argv[2])
print(count_roots(source))
