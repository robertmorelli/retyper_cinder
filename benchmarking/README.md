# Benchmarking

```sh
python benchmarking/detype.py BENCHMARK VARIANT [MASK]
python benchmarking/exp_maker.py MAX_MASKS_PER_LEVEL
python benchmarking/grow_exp.py MAX_MASKS_PER_LEVEL [TIMESTAMP]
python benchmarking/sample_tc.py [TIMESTAMP]
python benchmarking/run_exp.py BENCHMARK MAX_MASKS
python benchmarking/run_exp.py --which START END [--timestamp TIMESTAMP]
python samples/write_samples_tool.py [--benchmark NAME] [--check] [--run]
python utilities/run_compiled_tool.py FILE [--no-inliner]
python utilities/run_plain_tool.py FILE [--no-inliner]
python utilities/timing_runner_tool.py FILE [--require-static] [--no-jit]
python utilities/profile_compiled_tool.py FILE REPETITIONS [DELAY] [FUNCTION]
python benchmarking/cloudlab.py prepare
python benchmarking/cloudlab.py check
python benchmarking/cloudlab.py submit exp_TIMESTAMP --which START END
python benchmarking/cloudlab.py status JOB_ID
python benchmarking/cloudlab.py collect JOB_ID
python benchmarking/cloudlab.py cancel JOB_ID
```

The repository-level `data/` indexes benchmarks and stores test results,
`samples/` holds generated samples and their generator, and
`static-python-perf/` is the benchmark suite. Repository-wide helpers and
command-line tools live in `utilities/`.

`exp_maker.py` creates `exp_<timestamp>/` at the repository root. Experiment
JSON is nested as benchmark, variant, then mask; every experiment includes the
advanced, shallow, and untyped variants. Typechecking and timing can run
independently: `sample_tc.py` records which masks compile, while `run_exp.py`
times planned masks without consulting those results. `run_exp.py` selects masks per detype level
for each variant and uses SciPy BCa bootstrap intervals to collect timing batches
until the mean is stable within a 10% relative margin or reaches 160 samples.

`grow_exp.py` expands an existing experiment in place to the requested target
number of unique masks per level (or all possible masks when fewer exist).
For example, `python benchmarking/grow_exp.py 20` grows the latest experiment
to 20 masks per level. Supply a timestamp or `exp_<timestamp>` name to select
another experiment. Existing masks, typecheck statuses, and timing samples are
preserved; new masks start unchecked with empty timing samples. A smaller target
never removes masks.

`run_exp.py --which 2 5` runs mask positions 2, 3, 4, and 5 at every level,
for every benchmark and variant. Positions are 1-based within each level in
insertion order, so growing an experiment leaves existing positions unchanged.
Short levels run only positions that exist (the endpoints each have one mask).
`run_exp.py BENCHMARK N` runs the first N masks at each level for that benchmark.
You can also combine a benchmark with `--which` to restrict a range to it.

For example, after running positions 1–20, grow and run only the new range:

```sh
python benchmarking/grow_exp.py 40 exp_TIMESTAMP
python benchmarking/run_exp.py --which 21 40 --timestamp exp_TIMESTAMP
```

Run one benchmark process per machine, assigning different ranges to different
machines. Share the grown experiment plan before running its new ranges so
positions refer to the same masks everywhere.

## CloudLab

`cloudlab.py` reads SSH destinations from the ignored `data/cloudlab.json` file:

```json
{
  "hosts": [
    "user@host1.example",
    "user@host2.example"
  ]
}
```

Each entry is an SSH destination without an `ssh` command prefix. Submission
requires a clean working tree. Every worker clones the repository and all its
submodules, checks out the exact submitted commit, verifies the modified
`_cinderx` instrumentation, and then starts with `nohup`. The first host
typechecks all masks; the remaining hosts divide the requested `--which`
positions. The controller does not need to remain connected. Local job
manifests live in `.cloudlab/jobs/`, while each remote job
lives in `~/.one_true_detyper/jobs/` and retains its log, state, exit code, and
result file.

Run `prepare` once for fresh nodes. It installs build prerequisites and a
managed Python 3.14, then builds CinderX into
`~/.one_true_detyper/runtime/bin/python`. It must import both `cinderx` and
`__static__`; preparation itself is detached, so verify every machine with
`cloudlab.py check` before submission.
`status` reconnects briefly. `collect` retrieves successful timing and
typecheck shards over SSH, merges the nonempty mask samples from each shard, and
obtains `sample_tc.json` from the dedicated typechecker. Use `--partial` to
recover incremental results from workers that have not completed successfully.
`cancel` terminates a job and verifies its saved process, process group, state,
exit marker, and remote working directory are inactive.
