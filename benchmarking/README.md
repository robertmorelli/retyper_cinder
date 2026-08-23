# Benchmarking

```sh
python benchmarking/detype.py BENCHMARK VARIANT [MASK]
python benchmarking/write_samples.py [--benchmark NAME] [--check] [--run]
python benchmarking/utilities/run_compiled.py FILE [--no-inliner]
python benchmarking/utilities/run_plain.py FILE [--no-inliner]
python benchmarking/utilities/timing_runner.py FILE [--require-static] [--no-jit]
python benchmarking/utilities/profile_compiled.py FILE REPETITIONS [DELAY] [FUNCTION]
```

`data/` indexes benchmarks, `samples/` holds generated samples, and
`static-python-perf/` is the benchmark suite. `utilities/` contains runners
and benchmark-loading helpers.
