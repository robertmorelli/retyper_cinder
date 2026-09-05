# Benchmarking

```sh
python benchmarking/detype.py BENCHMARK VARIANT [MASK]
python benchmarking/exp_maker.py MAX_MASKS_PER_LEVEL
python benchmarking/sample_tc.py [TIMESTAMP]
python benchmarking/run_exp.py BENCHMARK MAX_MASKS
python samples/write_samples_tool.py [--benchmark NAME] [--check] [--run]
python utilities/run_compiled_tool.py FILE [--no-inliner]
python utilities/run_plain_tool.py FILE [--no-inliner]
python utilities/timing_runner_tool.py FILE [--require-static] [--no-jit]
python utilities/profile_compiled_tool.py FILE REPETITIONS [DELAY] [FUNCTION]
```

The repository-level `data/` indexes benchmarks and stores test results,
`samples/` holds generated samples and their generator, and
`static-python-perf/` is the benchmark suite. Repository-wide helpers and
command-line tools live in `utilities/`.

`exp_maker.py` creates `exp_<timestamp>/` at the repository root. Experiment
JSON is nested as benchmark, variant, then mask; every experiment includes the
advanced, shallow, and untyped variants. Typechecking and timing can run
independently: `sample_tc.py` records which masks compile, while `run_exp.py`
times planned masks without consulting those results. `run_exp.py` caps masks
per variant and uses SciPy BCa bootstrap intervals to collect timing batches
until the mean is stable within a 10% relative margin or reaches 160 samples.
