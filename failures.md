# Detyper failure catalogue

88 hand-written edge cases, each mask-swept exhaustively (every non-empty subset of
its roots; sampled for the few cases above 11 roots). Two failure modes:

- **DETYPE** — `detype()` itself raises. Always a real bug.
- **REBIND** — `detype()` succeeds but the emitted program no longer compiles:
  `get_ast_data(ast.parse(out))` raises `TypedSyntaxError`.

```
88 cases:  69 ok   16 failing   3 invalid source (my snippets, not detyper bugs)
```

> **Correction.** The first version of this document attributed the checked-container,
> `Array` and `@inline` failures to stage-one type-table errors — the
> `types[read] = dyn` assumption in `update_type_tables.py`. That was wrong, and the
> error was mine: I read the missing coercion out of the *final* output without
> checking whether stage one had emitted it. It had. Re-running every failing case
> with `do_stage_two=False` shows stage one is correct on all of them. The document
> below is rewritten around the measurement.

## The headline measurement

Every failing case run with and without stage two, on the unmodified detyper:

| | failing masks |
|---|---|
| stage 1 + 2 (current) | **101 / 444** |
| stage 1 only | **40 / 444** |

**61 of 101 failures are introduced by stage two.** The 40 that survive are entirely
the lambda syntax bug. There are exactly two root causes in this corpus, not four.

Everything here is compile-time. This checkout has no working cinderx runtime
(`cinderx` resolves to the source tree, `__static__` will not import), so nothing was
executed. Runtime-only miscompiles — a program that compiles but computes the wrong
answer — are **not** covered and remain unmeasured.

---

# Cause 1 — `simplify_coercions` strips erasure wraps (61 masks, 11 cases)

`_cast(Any, x)` is not a coercion tower. It *is* the erasure: it forces a value
dynamic, defeating Static Python's narrowing of a declared-`Any` local to its
assigned type. `extract_coerced` (`tower_simplifier.py:11`) matches it by name
alongside `box` and `cast` and unwraps it, destroying the only thing holding the
erasure in place.

Stage one is correct in every one of these cases. Three examples, stage-one output on
the left binding cleanly, stage-two output on the right rejected:

### Checked containers
```python
# stage one only — OK              # after stage two — broken
xs: Any = _cast(Any, [])           xs: Any = []
return xs                          return xs
```
```
TypedSyntaxError: mismatched types: expected chklist[int] because of return type,
                  found list instead
```

### Array
```python
# stage one only — OK
a: Any = _cast(Any, Array[int64](2))
a[_cast(Any, 0)] = _cast(Any, box(int64(5)))
return box(int64(a[_cast(Any, 0)]))

# after stage two — broken
a: Any = Array[int64](2)
a[0] = 5
return a[0]
```
```
TypedSyntaxError: mismatched types: expected int because of return type,
                  found int64 instead
```

### @inline
```python
# stage one only — OK              # after stage two — broken
@inline                            @inline
def dbl(a: int64) -> Any:          def dbl(a: int64) -> Any:
    return _cast(Any, box(a * int64(2)))   return box(a * 2)
def f() -> int:                    def f() -> int:
    return box(int64(dbl(int64(3))))       return dbl(3)
```
```
TypedSyntaxError: can't box non-primitive: Literal[6]
```

### Cases in this family

| case | stage 1+2 | stage 1 only |
|---|---|---|
| `inline-nested-call` | 32/255 | **0/255** |
| `inline-decorator` | 8/63 | **0/63** |
| `inline-prim-arg` | 4/15 | **0/15** |
| `chklist-param` | 4/15 | **0/15** |
| `array-loop` | 4/7 | **0/7** |
| `chklist-local-to-local` | 2/7 | **0/7** |
| `array` / `array-store-load` | 2/3 | **0/3** |
| `chklist-return` | 1/3 | **0/3** |
| `chkdict-return` | 1/3 | **0/3** |
| `chklist-nested` | 1/3 | **0/3** |

### Second, compounding defect

`TowerSimplifier` runs one pass against tables computed before any edit. Removing an
inner wrap changes the type that justified removing the outer one, so a per-node
decision that was correct against the snapshot can be invalidated by an earlier
removal in the same pass. Visible when stage one is made more explicit — it emits

```python
xs: Any = _cast(Any, cast(list, []))
return cast(CheckedList[int], xs)      # binds OK
```

and stage two still reduces it to the broken form: after dropping the inner `_cast`,
the outer `cast` looks redundant against stale tables.

### Fix direction

1. Don't treat `_cast(Any, x)` as unwrappable in `extract_coerced`. `box`/`cast`/
   `T(...)` towers are genuinely collapsible; the erasure is not.
2. Re-derive types after each edit, or iterate to a fixpoint, instead of deciding
   every node against one pre-edit snapshot.

---

# Cause 2 — lambda parameters get annotated (40 masks, 5 cases)

**Mode: DETYPE crash — the detyper emits text that is not Python.** Unaffected by
stage two; fails identically with `do_stage_two=False`.

`remove_annotations` rewrites an erased parameter's annotation to `Any`. Valid on a
`def`, illegal on a `lambda`, and `ast.unparse` emits it anyway.

```python
h = lambda a: a + 1       ->  h = lambda a: Any: a + 1
h = lambda a=1: a         ->  h = lambda a: Any=1: a
```
```
SyntaxError: invalid syntax
SyntaxError: cannot assign to lambda
SyntaxError: expression cannot contain assignment, perhaps you meant "=="?
```

When the lambda is also erasure-wrapped, the wrap nests around the broken text:
```python
h = _cast(Any, lambda a: Any=1: a)
```

| case | failing |
|---|---|
| `lambda-as-arg` | 16/31 |
| `lambda-two-args` | 12/15 |
| `lambda` (1 param) | 4/7 |
| `lambda-default` | 4/7 |
| `lambda-nested` | 4/7 |
| `lambda-noarg` | **0/3** |

`lambda-noarg` passing confirms the parameter is the trigger.

**Fix direction:** skip `arg` nodes whose parent is a `Lambda`. A lambda parameter is
already untyped, so erasing it is a no-op and writing `Any` is never correct.

---

# What passed

69 cases, including much of what I expected to be fragile:

`class-slots`, `inheritance-override`, `super-call`, `property`, `classvar`,
`static-method`, `default-arg`, `kwargs-call`, `star-args`, `prim-kwonly`,
`prim-default-arg`, `nested-closure`, `recursion`, `walrus`, `listcomp`, `dictcomp`,
`genexp`, `nested-comprehension`, `comprehension-cond`, `tuple-unpack`,
`starred-assign`, `multi-assign`, `del-stmt`, `assert-stmt`, `except-as`,
`try-except`, `global-stmt`, `nonlocal-stmt`, `yield-gen`, `async-fn`,
`chained-compare`, `slice-expr`, `unary`, `fstring`, `optional`, `union-narrow`,
`cast-explicit`, `final`, `future-annotations`, `string-annotation`, `none-return`,
`int64-return`, `int64-arith`, `int64-augassign`, `int64-loop`, `cbool-branch`,
`double`, `primitive-compare-chain`, `prim-in-tuple`, `prim-augassign-attr`, `clen`,
`for-over-chklist`, `chklist-attr`, `chklist-subscript-store`, `chklist-classvar`,
`chklist-augassign`, `chklist-arg-literal`, `chklist-default-arg`, `chklist-ifexp`,
`chklist-return-attr`, `chkdict-subscript`, `chklist-in-op`, `chkset`, `exact-type`,
`inline-noprim`, `lambda-noarg`, `array-param`, `boolop`, `ifexp`.

Diagnostically useful negatives:

- `chklist-attr` and `chklist-return-attr` pass while `chklist-return` fails —
  instance attributes are not narrowed the way locals are, so there is no erasure
  wrap for stage two to strip.
- `chklist-arg-literal` (`take([])`, literal at the call site) passes while
  `chklist-param` (literal bound to a local first) fails — same reason.
- `inline-noprim` passes 0/15, so the `@inline` failures are inline × primitive
  boxing, not inline alone.

# Excluded

Three snippets were rejected by cinderx before detyping — my errors, not the
detyper's:

- `with-stmt` — `Function has declared return type 'int' but can implicitly return None`
- `prim-to-dyn-call` — `int64 received for positional arg 'v'`
- `prim-return-from-nested` — `can't box non-primitive: dynamic`

# What this is *not*

Two hypotheses tested and rejected, recorded so they don't get re-proposed:

**Better fact-gathering / a type-consequence graph fixes these.** No. Stage one's
tables are 99.6% accurate against a rebind and its output binds on 404/444 masks. A
graph would chase the remaining 80 wrong entries, none of which cause a failure here.
Tested directly: replacing the blanket `types[read] = dyn` with "reads take the
erased value's type" made stage one emit a *stronger* program and the totals went
101 → 103.

**Rebind the erased program to get true tables ("oracle").** Can't be done. The
erased-but-unpatched intermediate is ill-typed by construction — `return xs` with
`xs` a `list` against a `chklist[int]` return type is exactly the error we're trying
to avoid — so the true post-erasure typing is not observable. Any consequence graph
would have to compute a hypothetical typing for an ill-typed program.

# The real benchmark set

The same sweep over all 27 typed benchmark variants — 341–697 masks each (full,
every single-bit, every inverse-single-bit, plus 300 random), each run with and
without stage two:

```
11665 masks tested
stage 1 + 2 :  2323 failures  (19.9%)
stage 1 only :    0 failures  (0.0%)
```

**Zero stage-one failures across the entire suite.** Every one of the 2323 is
introduced by `simplify_coercions`.

| variant | roots | tried | stage 1+2 | stage 1 only |
|---|---|---|---|---|
| deltablue/advanced | 198 | 697 | **697 (100%)** | 0 |
| deltablue/shallow | 194 | 689 | **689 (100%)** | 0 |
| fannkuch/advanced | 21 | 343 | 308 | 0 |
| nqueens/advanced | 40 | 381 | 288 | 0 |
| scratch/1 | 29 | 359 | 133 | 0 |
| held_karp/advanced | 116 | 533 | 96 | 0 |
| scratch/3 | 10 | 321 | 75 | 0 |
| nbody/advanced | 80 | 461 | 35 | 0 |
| richards/advanced | 129 | 559 | 2 | 0 |
| *18 other variants* | | | 0 | 0 |

## Full detyping is broken too

This is not only a partial-mask problem. `detype(source)` with no mask — the default
path, everything erased — emits an uncompilable program for four variants:

```
deltablue/advanced   TypedSyntaxError: Optional[Variable]: 'NoneType' object has no attribute 'value'
deltablue/shallow    TypedSyntaxError: Optional[Variable]: 'NoneType' object has no attribute 'value'
fannkuch/advanced    TypedSyntaxError: type mismatch: int cannot be assigned to int64
nqueens/advanced     TypedSyntaxError: type mismatch: dynamic cannot be assigned to int64
```

All four pass with `do_stage_two=False`. This was missed earlier in the session
because output was only ever compared for *equality* against a baseline, never
checked for validity.

## What stage two actually does here

Diffing stage-one against stage-1+2 output for `deltablue/shallow`: **116 changed
hunks, every one of them deleting a `_cast(Any, ...)`.**

```diff
-    REQUIRED = _cast(Any, None)          +    REQUIRED = None
-    STRONG_PREFERRED = _cast(Any, None)  +    STRONG_PREFERRED = None
-        self.strength = _cast(Any, strength)   +        self.strength = strength
-        return _cast(Any, s1.strength < s2.strength)  +        return s1.strength < s2.strength
-        if cls.weaker(_cast(Any, s1), _cast(Any, s2)):  +        if cls.weaker(s1, s2):
```

`REQUIRED = _cast(Any, None)` → `REQUIRED = None` is precisely the
`Optional[Variable]` family: without the wrap the class attribute infers as
`None`/`Optional` rather than dynamic, and every downstream use that expected a
`Variable` fails.

## Error families beyond the hand-written corpus

| count | error | benchmarks |
|---|---|---|
| 1081 | `Optional[X]` narrowing lost | deltablue |
| ~600 | `int`/`float`/`dynamic` cannot be assigned to `int64`/`double` | fannkuch, nqueens, nbody, scratch/1 |
| 88 | wrong primitive/boxed arg type at a call | held_karp, scratch/1 |
| 75 | `expected chklist[int] ... found list` | scratch/3 |
| 2 | `Call argument cannot be a primitive` | richards |

`Optional` narrowing is a family the 88-case corpus did not contain — the `optional`
and `union-narrow` cases both passed there because neither had a class attribute
initialised to `None`.

# Reproducing

Corpora and harnesses are in the scratchpad (`corpus.py`, `corpus2.py`, `split.py`):

```python
out = detype(source, mask=mask, do_stage_two=True)
get_ast_data(ast.parse(out))     # raises if the detyped program no longer binds
```

Runtime-free, catches every case above, and flipping `do_stage_two` attributes the
failure to a stage. Worth adding to `run_bench.run` as a pre-flight — it would let
`crashtest_random.py` find these without a working cinderx.
