# `cbool.boxed` returns `int`, but the binder types it `bool`

## Claim

`CIntType.boxed` is hardcoded to `int`. `cbool` is constructed as a `CIntType`, so
`cbool.boxed` yields `int` — while `TypeBinder` independently assigns `bool` to the
same expressions. The two disagree, and the binder is the one that's right.

## The code

`cbool` is not its own class. It's a `CIntType` with a name override
(`compiler/static/types.py:237`):

```python
self.cbool: CIntType = CIntType(TYPED_BOOL, self, name_override="cbool")
```

`CIntType.boxed` ignores the constant it was built with (`types.py:9710`):

```python
    @property
    def boxed(self) -> Class:
        return self.type_env.int
```

There is no `CBoolType`, and no override anywhere for `TYPED_BOOL`. The base
`CType.boxed` (`types.py:2422`) raises `NotImplementedError`, and the only two
implementations in the file are `CIntType` → `int` and `CDoubleType` → `float`.
So every `CIntType`, `cbool` included, boxes to `int`.

Meanwhile the binder types boolean-valued primitives as `cbool` and, once the
primitive context is gone, as `bool` — `type_env.bool` exists and is used
(`types.py:236`, and `types.py:599` maps the `bool` pytype to `self.bool.instance`).
Comparisons explicitly produce `cbool` (`types.py:9493`, `9505`, `9521`).

## How we hit it

Detyping wraps erased values in `_cast(Any, ...)`, which destroys the primitive
context, so the value boxes. We predict the post-detyping type as
`t.klass.boxed.instance`. For `int64` that gives `int` and matches the rebind. For
`cbool` it gives `int` and the rebind says `bool`.

42 expressions across the benchmark suite, all of this shape:

```python
# deltablue
return _cast(Any, False)                                         # predicted int, actual bool
self.satisfied: Any = _cast(Any, False)                          # predicted int, actual bool
return _cast(Any, box(int64(s1.strength) < int64(s2.strength)))  # predicted int, actual bool
```

34 are `True`/`False` constants; 8 are `box(...)` of a comparison, which is `cbool`
one level up. Every one of the 42 flipped to correct once `cbool` was special-cased,
with zero regressions elsewhere.

## Why this looks like a cinderx bug and not a quirk we should absorb

`boxed` is meant to answer "what does this primitive become when it leaves primitive
context." For `cbool` the answer the rest of the compiler gives is `bool`:

- `box(cbool_expr)` is typed `bool` by the binder, not `int`.
- `type_env.bool` is a real `BoolClass` (`types.py:236`) — the boxed counterpart
  already exists, `boxed` just never points at it.
- `bool` is a subclass of `int` in Python, so returning `int` is *sound* but strictly
  less precise. Anything downstream that reads `boxed` to decide a runtime
  conversion or a narrowing gets a weaker answer than the binder's.

The reason it survives inside cinderx is presumably that `boxed` is only consulted
where widening to `int` is harmless. It stops being harmless as soon as an external
consumer uses `boxed` to *predict* what the binder will say, which is exactly what
we do.

## Suggested upstream fix

Consult the constant rather than hardcoding:

```python
    @property
    def boxed(self) -> Class:
        return self.type_env.bool if self.constant == TYPED_BOOL else self.type_env.int
```

## What we did instead

Local workaround in `patch_picker.py`, since we don't want to patch the submodule:

```python
# cbool is a CIntType, and CIntType.boxed hardcodes int -- see cbool_not_int_bro.md
def boxed_instance(t):
    env = t.klass.type_env
    return env.bool.instance if t.klass is env.cbool else t.klass.boxed.instance
```

Used by `BoxWrapper` and by the erasure path in `pick_erasure_wrap`. If the upstream
property is ever fixed, `boxed_instance` collapses back to `t.klass.boxed.instance`.

## Caveat

This is inferred from reading `types.py` and from 42 agreeing observations on our
benchmark set — not from a cinderx test or issue. The identity check `t.klass is
env.cbool` is deliberately narrow: it only diverts the one class known to be
mistyped, so if `boxed` is wrong for some other `CIntType` we haven't exercised, this
won't mask it.
