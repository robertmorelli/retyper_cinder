# Detyper failures under real Static Python compilation

These only became visible once the harness started compiling detyped output
statically. Previously `run_bench.py` piped source to `python3 -`, which is
`__main__` and therefore never goes through the Static Python loader, so no
detyped source was ever typechecked.

Reproduce a single case:

```bash
.venv/bin/python -c "
from load_source import load_bench
from detyper import detype
print(detype(load_bench('fannkuch','advanced'), mask=0, bench=True))" > /tmp/out.py
.venv/bin/python static_runner.py /tmp/out.py --require-static
```

Sweep everything:

```bash
.venv/bin/python -c "
from list_benchmarks import get_bench_list
from load_source import load_bench
from root_count import count_bench_roots
from run_bench import run
f = []
for b, v, p in get_bench_list():
    if v == 'untyped':
        continue
    s = load_bench(b, v)
    n = count_bench_roots(s)
    run(b, v, s, 0, 'all-detyped', f, bench_mode=True, require_static=True)
    run(b, v, s, (1 << n) - 1, 'max', f, bench_mode=True, require_static=True)
print('\n'.join(f))"
```

Status: 23 of 26 typed variants pass at both mask=0 and full mask. Three fail,
in two distinct categories.

## A. Primitive-element containers

`Array[int64]` and `CheckedList[int64]` hold unboxed primitives. The detyper
erases the annotation on the *variable*, but the object it points at keeps its
primitive element type, so the store no longer typechecks.

### fannkuch/advanced — `type mismatch: int cannot be assigned to int64`

Detyped output:

```python
count: Any = Array[int64](nb)     # still a real Array[int64]
i: Any = 0
while int64(i) < int64(n):
    count[i] = box(int64(i) + 1)  # box() produces int, slot wants int64
```

The annotation on `count` became `Any`, so the detyper boxes the value being
stored. The array underneath is unchanged and rejects it.

### nqueens/advanced — `type mismatch: dynamic cannot be assigned to int64`

```python
a: Any = Array[int64](size)
while int64(i) < int64(size):
    a[i] = c                      # c is dynamic, slot wants int64
```

Same shape, except the value arrives dynamic rather than boxed.

### Precedent: held_karp/advanced

The original port used `scratch: CheckedList[int64]` and failed at *every* mask
with `bad value 'int' for chklist[int64]`. Worked around by switching to
`CheckedList[int]` with explicit `box`/`int64` at the boundary. No other
benchmark in the golden 11 used a primitive-element `CheckedList`.

### Fix directions

Either teach the detyper to rewrite the allocation alongside the annotation
(`Array[int64](n)` -> `[0] * n`), or treat a primitive-element container as an
atomic unit and refuse to detype the variable without it. The first is also
what a representation-control experiment would need.

## B. Dropped narrowing cast

### deltablue/advanced and deltablue/shallow — `Optional[Variable]: 'NoneType' object has no attribute 'value'`

Source (`advanced/main.py:519-553`):

```python
first: Variable | None = None
...
    first = v
first = cast(Variable, first)     # narrows Optional[Variable] -> Variable
...
    first.value = i
```

Detyped output:

```python
first: Any = None
...
    first = v
first = first                     # narrowing gone
...
    first.value = i               # rejected
```

The detyper reduces `cast(Variable, first)` to the no-op `first = first`,
removing the only thing that narrowed the optional. The later attribute store
is then rejected.

Note this is the one failure that also hits a `shallow` variant, because the
cast is plain `typing.cast`-style narrowing rather than anything primitive.

### Fix direction

Treat a cast whose target type is a narrowing of the current declared type as
load-bearing, and keep it even when the annotations around it are erased.
