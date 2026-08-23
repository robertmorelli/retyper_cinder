from pathlib import Path
from sys import argv, path as import_path

PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if PROJECT_ROOT not in import_path:
    import_path.insert(0, PROJECT_ROOT)

from benchmarking.utilities.load_source import load_bench
from .root_count import count_roots

source = load_bench(argv[1], argv[2])
print(count_roots(source))
