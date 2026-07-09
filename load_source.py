from json import load

def load_bench(bench, variant):
    try:
        with open("data/benchmark_locations.json") as f:
            sources = load(f)
        path = sources[bench][variant]
        with open(path) as f:
            source = f.read()
    except:
        pass
    return source
