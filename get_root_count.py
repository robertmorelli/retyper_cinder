from sys import argv
from load_source import load_bench
from root_count import count_roots

source = load_bench(argv[1], argv[2])
print(count_roots(source))
