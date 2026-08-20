# DeltaBlue crashes while collecting Apple performance counters

## Summary

The compiled DeltaBlue benchmark ran successfully by itself, but its process
terminated with `SIGSEGV` while `xctrace` was attached and collecting the Apple
CPU Counters instrument configured for **L1D Cache Metrics**. This happened for
every DeltaBlue typedness mask attempted during the probe, including the fully
erased mask.

This is currently an observation, not a diagnosed CinderX bug. The available
data establishes that attaching this counter configuration is a necessary
part of the failure we saw. It does not yet establish whether the counters,
the act of tracing/attaching, a timing or scheduling change, or another part of
the Instruments environment exposes the fault.

DeltaBlue was therefore excluded from the cache-counter sweep and has no purple
cache-miss series in `typedness4.png`.

## Environment

- Machine: Apple M1 Pro
- macOS: 27.0 beta, build `26A5388g`
- Xcode: 26.6, build `17F113`
- Tracing program: `xcrun xctrace`
- Instrument: Apple CPU Counters
- Counting configuration: `L1D Cache Metrics`
- Counting level: `EL0`
- Benchmark execution: statically compiled through the repository's CinderX
  environment, using `profile_compiled.py`
- Collection method: start and warm the Python target first, then attach
  `xctrace` to its PID

The attach method matters operationally: launching the target directly through
`xctrace` with this custom metric configuration left Python suspended in the
dynamic loader, so the cache sweep used attachment to an already started
process.

## Observed failing masks

The following decimal masks were tried and all exited with process return code
`-11`, meaning termination by `SIGSEGV`:

| Mask | Observation |
|---:|---|
| `68719476735` | Fully erased DeltaBlue mask; crashes under counter attachment |
| `67645210611` | Crashes under counter attachment |
| `59852452853` | Crashes under counter attachment |
| `66434625312` | Crashes under counter attachment |
| `5823781304` | Crashes under counter attachment |

The fully erased value is `2^36 - 1`, consistent with DeltaBlue having 36
detyping units in this sweep.

## Isolated variables

What was held constant:

- The same emitted benchmark source and selected mask
- The same CinderX Python executable
- Static compilation in both the control and traced run
- The same benchmark entry point (`main`)
- The same profiling harness (`profile_compiled.py`)

What changed between the successful control and the observed crash:

- Control: execute the compiled target normally
- Failure: execute the compiled target while `xctrace` is attached with the
  CPU Counters instrument set to L1D Cache Metrics

The emitted fully erased mask was explicitly checked outside the hardware
counter attachment and completed successfully. Its failure is therefore not a
plain compilation failure or an unconditional crash in that emitted program.

Variables that were **not** independently isolated:

- L1D event selection versus other hardware counter event sets
- Hardware counters versus a counter-free Instruments attachment
- Attaching `xctrace` versus tracing from process launch
- Warm-up duration and the exact attach point
- Sampling/interrupt frequency
- Changes in thread scheduling or signal delivery caused by Instruments
- macOS/Xcode beta behavior versus a stable toolchain
- Whether the failure occurs on non-Apple-Silicon machines

## Reasoning from the available evidence

The strongest current inference is that performance-counter attachment exposes
a latent unsafe interaction in the compiled DeltaBlue process. That wording is
deliberately broad.

It is unlikely to be caused solely by one particular detyper transformation,
because five substantially different masks failed, including the mask with all
36 units erased. It is also unlikely to be an ordinary deterministic benchmark
failure, because the same emitted fully erased program succeeds without the
counter attachment.

Plausible classes of explanation include:

1. A CinderX/runtime memory-safety defect whose timing changes under tracing.
2. An unsafe interaction between generated code and asynchronous PMU sampling,
   signal handling, stack walking, or thread suspension.
3. An Instruments or beta-toolchain defect while inspecting this particular
   generated workload.
4. A latent benchmark/runtime race or lifetime error exposed by the changed
   scheduling environment.

There is not enough evidence to choose among these. In particular, the data
does **not** show that an L1 cache miss itself causes the crash, nor that the
DeltaBlue algorithm is incorrect.

## Reproduction shape used by the sweep

For each mask, the sweep:

1. Loaded the advanced DeltaBlue source.
2. Applied the mask with `typedness_sweep2.build`.
3. Removed the source's `if __name__ == "__main__"` guard from the temporary
   emitted module.
4. Started `.venv/bin/python profile_compiled.py <module> <repetitions> 5 main`.
5. Waited one second while the target warmed up and entered its synchronization
   delay.
6. Attached `xcrun xctrace record` using a generated L1D Cache Metrics template.
7. Observed the Python target terminate with return code `-11`.

The relevant implementation is in `typedness_cache_sweep.py`. DeltaBlue is
listed in its `COUNTER_UNSAFE` set so ordinary sweeps skip it.

## Limits of this record

No stack trace was captured and no source-level minimization was completed.
There is consequently no manually minimized example yet. The smallest recorded
reproducer is the generated DeltaBlue benchmark at one of the masks above,
with the fully erased mask (`68719476735`) being the conceptually simplest.

No claim beyond the observations above should be made until the crash is
reproduced with preserved crash reports or debugger backtraces and the tracing
variables are varied one at a time.
