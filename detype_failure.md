# `simplify_coercions` strips erasure wraps that are load-bearing

Detyping a `CheckedList` local emits code the static compiler rejects. **Stage one
gets it right; stage two breaks it.**

Reproduced on `data/scratch3.py`. **256 of 1023 masks fail**, all with the same error,
and all 256 pass with `do_stage_two=False`.

> **Correction.** An earlier version of this document blamed the
> `types[read] = dyn` assumption in `update_type_tables.py` and presented the missing
> cast as a stage-one prediction error. That was wrong. I traced the missing cast in
> the *final* output without checking whether stage one had emitted it — it had.
> Stage one's output binds cleanly; `simplify_coercions` removes the wrap that made
> it bind. The narrowing behaviour described below is real and worth knowing, but it
> is not what causes this failure.

## Minimal repro — 4 lines, 2 roots, 1 of 3 masks fails

```python
from __static__ import CheckedList

def f() -> CheckedList[int]:
    xs: CheckedList[int] = []
    return xs
```

### Stage one only (`do_stage_two=False`) — binds OK

```python
def f() -> CheckedList[int]:
    xs: Any = _cast(Any, [])
    return xs
```

### After stage two — broken

```python
def f() -> CheckedList[int]:
    xs: Any = []
    return xs
```
```
TypedSyntaxError: mismatched types: expected chklist[int] because of return type,
                  found list instead (line 6)
```

The only difference is the removed `_cast(Any, ...)`.

## Why the wrap is load-bearing

`xs: CheckedList[int] = []` compiles the bare literal *into* a `CheckedList` — the
annotation supplies the type. Erase the annotation and the literal binds as a plain
`list`.

Crucially, `: Any` alone does **not** make the local dynamic. Static Python narrows a
declared-`Any` local to the type of whatever was assigned to it:

```python
from typing import Any
def produce():
    xs: Any = []
    xs.append(1)
    return xs
```
```
xs (Store) line4: type=<list>      # not <dynamic>
literal []:       type=<list>
xs (Load) line5:  type=<list>
xs (Load) line6:  type=<list>
```

So the erasure has to be carried by the *value*, not the annotation. That is exactly
what `_cast(Any, [])` does: it forces the assigned value dynamic, which defeats the
narrowing and makes `return xs` legal against a `chklist[int]` return type. Stage one
inserts it correctly.

## Why stage two removes it

`extract_coerced` (`tower_simplifier.py:11`) matches `_cast` by name alongside `box`
and `cast`:

```python
elif node.func.id in ("cast", "_cast"):
    return extract_coerced(second, constructors) or second
```

But `_cast(Any, x)` is not a coercion tower — it *is* the erasure. Treating it as
removable discards the only thing keeping the value dynamic.

There is a second, compounding defect: `TowerSimplifier` runs a single pass against
tables computed before any edit. Removing an inner wrap changes the type that
justified removing the outer one, so even a correct per-node decision can be
invalidated by an earlier removal in the same pass.

Both are visible together when stage one is made *more* explicit. With a corrected
read rule, stage one emits:

```python
xs: Any = _cast(Any, cast(list, []))
return cast(CheckedList[int], xs)      # binds OK
```

and stage two still reduces it to the broken form, because after dropping the inner
`_cast` the outer `cast` looks redundant against stale tables.

## Which masks fail

```
failing masks (stage 1+2): 256 / 1023
failing masks (stage 1 only): 0 / 1023
all failing have bit1 set:   True     (xs: CheckedList[int] = [])
all failing have bit0 clear: True     (def produce() -> CheckedList[int])
masks with bit1 set and bit0 clear: 256
```

Erase the local's annotation while keeping the enclosing function's return
annotation, and stage two breaks it — every time. Erase both and the return type
goes too, so there is nothing left to mismatch.

The general shape: **it needs a surviving typed boundary next to an erased one.**
Full detyping never exposes it because every boundary goes at once, which is why the
benchmark suite (always detyped in full during measurement) looked clean.

## What is and isn't required to trigger it

| variant | fails? | why |
|---|---|---|
| `xs: CheckedList[int] = []` then `return xs` | **yes** | baseline |
| `xs: CheckedList[int] = [1, 2]` | **yes** | not about the literal being empty |
| `d: CheckedDict[int, int] = {}` | **yes** | whole checked-container family |
| parameter boundary instead of return | **yes** | `list received for positional arg 'a'` |
| two locals, no function boundary | **yes** | `list cannot be assigned to chklist[int]` |
| `xs: CheckedList[int] = CheckedList[int]()` | no | value's type is self-supplied |
| `xs: list = []` then `-> list` | no | erasure changes nothing |
| `n: int64 = 1` then `-> int64` | no | primitives survive on the box path |
| module-level `x`/`y` instead of locals | no | globals are not narrowed like locals |

## Fix direction

1. `extract_coerced` should not treat `_cast(Any, x)` as a coercion to unwrap. It is
   the erasure itself. `box`/`cast`/`T(...)` towers are genuinely collapsible;
   `_cast` is not.
2. `TowerSimplifier` should re-derive types after each edit, or run to a fixpoint,
   rather than deciding every node against one pre-edit snapshot.

Neither is a fact-gathering problem. Stage one's tables are 99.6% accurate against a
rebind and its output binds on every mask of this file.
