# Three cinderx crashes found by the typedness sweep

`typedness_sweep.py` ran 714 masks across the 12 advanced benchmarks and 181 of
them failed. None of the failures are erasure bugs. Three benchmarks --
deltablue, nbody, chaos -- crash on their *own untouched, fully annotated
source*, so the sweep has no fully typed baseline for them and deltablue has no
data at all. Each has been reduced to a standalone reproducer under
`jit_bugs/`, and two of the three are JIT bugs.

| | what happens | minimal trigger | clears with | deterministic |
|---|---|---|---|---|
| **A** | `SIGABRT`, `JIT: cinderx/Jit/codegen/autogen.h:49 -- Abort / incorrect register type.` | a `double` local declared inside a loop body | `--no-jit` only | 20/20 |
| **B** | `SIGSEGV`, or a wrong object arriving where a value should be | a function that stores to a dynamic global, returns it, has a return annotation, and is called from another function | `--no-inliner`, `--no-jit` | 20/20 |
| **C** | `SIGSEGV`, or a `CheckedList[T]` holding `[('builtins', 'int')]` | constructing an object at module scope with a list display passed to a `CheckedList[UserClass]` parameter | nothing (not the JIT) | 18/20, 39/40 |

A and B are JIT bugs. C reproduces with the JIT off, so it is the static
compiler; it is in here because the sweep hits all three the same way and
because it is what breaks chaos.

Reproduce everything:

```
.venv/bin/python jit_bugs/force.py --runs 20
```

---

## Environment

```
Python 3.14.3 (main, Feb  3 2026, 15:32:20) [Clang 17.0.0 (clang-1700.6.3.2)]
cinderx 2026.5.26.0                     (.venv/lib/python3.14/site-packages)
cinderx submodule e76ac5c507e709ecd7a6ffc41d6a0516b0e1db50 (heads/main)
  e76ac5c5 "Visit instruction arguments in reverse in register allocator when
            calculating last-use data"
Darwin 27.0.0 arm64 (xnu-13432.0.94.501.4~1/RELEASE_ARM64_T6000), Apple M1 Pro
cinderx.jit defaults in this build: is_enabled()=True
                                    is_hir_inliner_enabled()=True
                                    get_compile_after_n_calls()=None
```

Every run below goes through `timing_runner.py`, which installs the strict
loader, sets `cinderx.jit.compile_after_n_calls(0)`, imports the module, and
asserts `__static__.is_static_module(module)`. Flags: `--no-jit` calls
`cinderx.jit.disable()`, `--no-inliner` calls
`cinderx.jit.disable_hir_inliner()`.

```
.venv/bin/python timing_runner.py <file> --require-static [--no-jit|--no-inliner]
```

Note that the JIT is on and compiling from the first call in all of this, which
is what `static_runner.py` (the pre-existing harness) does too, and what nbody's
own source does -- nbody line 19 is `cinderx.jit.compile_after_n_calls(0)`.

---

## Bug A -- codegen abort: "incorrect register type."

### Reproducer: `jit_bugs/a_double_local_in_loop.py`

```python
import __static__
from __static__ import double


def f() -> None:
    for i in range(3):
        m: double = 2.5


f()
print('ok')
```

```
rc=-6 (SIGABRT)
JIT: /Users/robertmorelli/Documents/personal-repos/one_true_detyper/cinderx/cinderx/../cinderx/Jit/codegen/autogen.h:49 -- Abort
incorrect register type.
```

Nothing is printed and nothing runs -- the abort happens when `f` is compiled,
i.e. on the first call.

### What is and is not required

| variant | result |
|---|---|
| `for i in range(3): m: double = 2.5` | **SIGABRT** |
| `for i in [1, 2, 3]: m: double = 2.5` | **SIGABRT** |
| `while i < 3: m: double = 2.5` | **SIGABRT** |
| same, and `m` is then used (`t = t + m`) | **SIGABRT** |
| `m: double = body.mass` (attribute load instead of a literal) | **SIGABRT** |
| `for i in range(3): m: int64 = 2` | ok |
| `for i in range(3): m: int = 2` | ok |
| `m: double = 0.0` before the loop, plain `m = 2.5` inside | ok |
| `m: double = body.mass` with no loop around it | ok |
| `for body in bodies: b2: Body = body` (object local) | ok |
| `for i in range(0): m: double = 2.5` -- **loop body never runs** | **SIGABRT** |
| nested loops, declaration in the inner one | **SIGABRT** |
| `if flag: m: double = 2.5` | ok |
| `if flag: m: double = 2.5` / `else: m2: double = 1.5` | ok |
| `m: double = 2.5` then `m2: double = 1.5`, straight line | ok |
| `for i in range(3): m: int8 = 2` | ok |
| `for i in range(3): m: float = 2.5` (boxed float) | ok |
| `m: double = 2.5` before the loop, plain `m = 1.5` inside | ok |
| double *parameter* mutated in a loop, no declaration inside | ok |

So the trigger is a **`double`-annotated local declaration lexically inside a
loop body**. Refinements:

- It is a **compile-time** failure. `for i in range(0)` still aborts, so the
  offending code never executes -- compiling the function is enough.
- It is the loop specifically, not a declaration reachable by more than one
  path: the same declaration in an `if` branch, in both arms of an `if/else`,
  or twice in a straight line, all compile.
- It is `double` specifically. `int64`, `int8`, `int`, and boxed `float` in the
  same position are all fine.
- The loop kind (`for`-range, `for`-list, `while`, nested), the value source
  (literal or attribute load), and whether the local is ever read all make no
  difference.

Which is why the suite mostly survives it. Annotated primitive declarations
inside loops are everywhere in these benchmarks -- fannkuch 4, held_karp 10,
nqueens 6, richards 1 -- but every one of those is `int64`. nbody is the only
benchmark that declares `double` locals inside a loop, and it has 16 of them.

`--no-inliner` still aborts (20/20), so this is not the HIR inliner.

### How it was found: nbody

nbody aborts on its untouched source. Bisected with a JIT list
(`CINDERX_JIT_LIST_FILE`, one `module:qualname` per line) to see which function
fails to compile:

```
only combinations     -> ok (3.125s)
only advance          -> Abort: incorrect register type.
only report_energy    -> Abort: incorrect register type.
only offset_momentum  -> Abort: incorrect register type.
only bench_nbody      -> ok
only Vector.__init__  -> ok (3.352s)
only Body.__init__    -> ok (3.155s)
```

Three functions independently trigger it. Delta-debugging the smallest one,
`offset_momentum`, keeping only a prefix of its statements and JIT-compiling
only that function:

```
offset_momentum statements:
  0 body: Body
  1 for body in bodies:
  2 m = ref.mass
  3 v = ref.v
  4 v.x = px / m
  5 v.y = py / m
  6 v.z = pz / m
  keep=[0]     rc=0
  keep=[0, 1]  rc=-6  incorrect register type.

loop body statements:
  ['v: Vector = body.v', 'm: double = body.mass', 'px -= v.x * m',
   'py -= v.y * m', 'pz -= v.z * m']
  keep=[0]     rc=0        # v: Vector = body.v
  keep=[1]     rc=-6       # m: double = body.mass  <-- one statement
  keep=[0, 1]  rc=-6
```

One statement, `m: double = body.mass` inside `for body in bodies:`, is the
whole bug. The original:

```python
def offset_momentum(ref: Body, bodies: CheckedList[Body], px: double=0.0,
                    py: double=0.0, pz: double=0.0):
    body: Body
    for body in bodies:
        v: Vector = body.v
        m: double = body.mass      # <-- abort
        px -= v.x * m
        ...
```

Guesses that did **not** reproduce it, before the delta-debug (all ok): double
params with omitted defaults, `px -= v.x * m` on a double parameter, augmented
assignment to a double attribute, `** double(-1.5)`, `** 0.5`, double attribute
stores, iteration over `CheckedList[Body]`.

### Effect on the sweep

nbody: 15/80 masks ran. 36 masks aborted this way, spread over every proportion
including the untouched source (`typedness=1.00`, 1/1 aborted). The other 29
nbody failures are a *detyper* limitation, not a JIT bug --
`StrictModuleError: ('cannot divide dynamic and double', ..., 103, 0)`.

```
   erased= 9 typedness=0.00 ok=1/1
   erased= 8 typedness=0.11 ok=6/9   [abort 2, cannot-divide 1]
   erased= 7 typedness=0.22 ok=4/10  [abort 4, cannot-divide 2]
   erased= 6 typedness=0.33 ok=3/10  [abort 4, cannot-divide 3]
   erased= 5 typedness=0.44 ok=1/10  [abort 4, cannot-divide 5]
   erased= 4 typedness=0.56 ok=0/10  [abort 3, cannot-divide 7]
   erased= 3 typedness=0.67 ok=0/10  [abort 6, cannot-divide 4]
   erased= 2 typedness=0.78 ok=0/10  [abort 5, cannot-divide 5]
   erased= 1 typedness=0.89 ok=0/9   [abort 7, cannot-divide 2]
   erased= 0 typedness=1.00 ok=0/1   [abort 1]
```

Erasing annotations *removes* the trigger -- an erased `m: double = ...` is no
longer a double declaration -- which is why the erased end of nbody has data and
the typed end does not.

---

## Bug B -- the HIR inliner hands the callee a bogus frame

### Reproducer: `jit_bugs/b_inlined_global_store.py`

```python
import __static__


def bump() -> int:
    global counter
    counter = 1
    return counter


def drive() -> None:
    bump()


counter = None
drive()
print('ok', counter)
```

```
jit                20/20  rc=-11 (SIGSEGV)
jit --no-inliner   20/20  ok
--no-jit           20/20  ok
```

### What is and is not required

| variant | result |
|---|---|
| as above | **SIGSEGV** |
| the same with a class: `planner = Planner()` in the callee, `-> Planner` | **SIGSEGV** |
| callee has no return annotation and no `return` | ok |
| callee returns `Planner()` without touching a global | ok |
| global declared `planner: Planner \| None = None` (not dynamic) | ok |
| global first assigned at module level *above* the functions | rejected at compile time: `StrictModuleError: type mismatch: Planner cannot be assigned to None` |
| calls made from module level instead of from `drive()` | ok |
| `get_planner()`-only (read, no store) called from a function | ok |

So all of these are needed: the callee **stores to a module global** it declared
with `global`, **returns** it, **has a return annotation**, the global's declared
type is **dynamic**, and the call comes **from another function** (the one the JIT
compiles and inlines into). Nothing about the value's type matters -- `int` and a
user class behave identically.

The declaration-order detail is why real code trips over this: writing
`counter = None` *before* the functions is a compile error, so the working
spelling is the one that puts the module-level assignment at the bottom, which is
exactly what deltablue does (`planner = None`, line 633 of 651).

### The corruption, observed

The bogus value that reaches the caller depends on the surrounding code. All of
these are the same bug:

```
AttributeError: 'int' object has no attribute 'get'              # got the loop counter
AttributeError: 'range_iterator' object has no attribute 'get'   # got the loop's iterator
cinderx.StaticTypeError: incremental_add expected 'Planner' for argument self, got 'tuple'
TypeError: expected 'Planner', got 'tuple'
AttributeError: 'tuple' object has no attribute 'incremental_add'
SIGSEGV
```

`.get` is called on nothing in these programs -- it is the global-store path
being handed a non-dict. The traceback points at the *call site of the inlined
function*:

```
  File ".../repro_T1_no_inner_while.py", line 31, in repro_T1_no_inner_while
    drive(3)
  File ".../repro_T1_no_inner_while.py", line 26, in drive
    recreate_planner()
AttributeError: 'int' object has no attribute 'get'
```

Instrumenting deltablue itself shows the caller receiving the **empty tuple** --
which is what a 0-argument callee's argument tuple is -- where the callee's
return value belongs. `get_planner()` returns the right object; the caller's
local, annotated `Planner`, holds `()`:

```
get_planner: global is Planner <deltablue_probe.Planner object at 0x107267750>
add_constraint: planner is Planner <deltablue_probe.Planner object at 0x107267750>
...  (thousands of correct calls)
get_planner: global is Planner <deltablue_probe.Planner object at 0x107ab5c70>
add_constraint: planner is tuple ()
```

macOS crash report for the segfaulting reproducer, faulting thread:

```
exception:   {'type': 'EXC_BAD_ACCESS', 'signal': 'SIGSEGV',
              'subtype': 'KERN_INVALID_ADDRESS at 0x0000000000000008'}
termination: Segmentation fault: 11

  Python      _PyObject_GetMethod
  Python      _PyEval_MatchKeys
  _cinderx.so Ci_EvalFrame
  _cinderx.so _PyEval_EvalFrame(_ts*, _PyInterpreterFrame*, int)
  _cinderx.so jit::codegen::(anonymous namespace)::resumeInInterpreter(_PyInterpreterFrame*...)
  <jit code>  0x109204080
  <jit code>  0x109ab8dc8
  Python      PyObject_Vectorcall
  _cinderx.so Ci_EvalFrame
  Python      PyEval_EvalCode
  Python      builtin_exec
```

Reading `0x8` is a null-pointer-plus-offset dereference. The frame below the JIT
code is `resumeInInterpreter`, so the JIT deopts back into the interpreter with a
bad value in the frame and the interpreter then dereferences it. That is
consistent with the Python-visible symptoms: sometimes the bad value is a live
object (an int, a range_iterator, `()`), sometimes it is garbage.

### Deltablue's full traceback (untouched source, JIT on)

```
Traceback (most recent call last):
  File ".../timing_runner.py", line 36, in <module>
    module = import_module(path.splitext(path.basename(module_path))[0])
  ...
  File ".../.venv/lib/python3.14/site-packages/cinderx/compiler/strict/loader.py", line 625, in exec_module
    exec(code, new_dict)
  File ".../deltablue_typed.py", line 528, in deltablue_typed
    delta_blue(n)
  File ".../deltablue_typed.py", line 525, in delta_blue
    projection_test(n)
  File ".../deltablue_typed.py", line 485, in projection_test
    StayConstraint(src, NORMAL)
  File ".../deltablue_typed.py", line 118, in __init__
    self.add_constraint()
  File ".../deltablue_typed.py", line 67, in add_constraint
    planner.incremental_add(self)
cinderx.StaticTypeError: incremental_add expected 'Planner' for argument self, got 'tuple'
```

The deltablue code involved, verbatim:

```python
def add_constraint(self) -> None:            # Constraint.add_constraint
    planner: Planner = get_planner()
    self.add_to_graph()
    planner.incremental_add(self)

def recreate_planner() -> Planner:
    global planner
    planner = Planner()
    return planner

def get_planner() -> Planner:
    global planner
    return planner

# HOORAY FOR GLOBALS... Oh wait.
# In spirit of the original, we'll keep it, but ugh.
planner = None
```

### Effect on the sweep

deltablue: **0 of 82 masks ran**, including `typedness=1.00`. Error shapes over
all 82:

```
49  exit -11 (SIGSEGV)
22  cinderx.StaticTypeError: incremental_add expected 'Planner' for argument self, got 'tuple'
 6  TypeError: expected 'Planner', got 'tuple'
 5  AttributeError: 'tuple' object has no attribute 'incremental_add'
```

The mix shifts with typedness -- erasure changes which way it breaks, not
whether:

```
   erased=35 typedness=0.00  [segv 1]
   erased=31 typedness=0.11  [segv 10]
   erased=27 typedness=0.23  [segv 9, StaticTypeError 1]
   erased=23 typedness=0.34  [segv 8, StaticTypeError 1, TypeError 1]
   erased=19 typedness=0.46  [segv 6, StaticTypeError 3, AttributeError 1]
   erased=16 typedness=0.54  [segv 6, StaticTypeError 3, TypeError 1]
   erased=12 typedness=0.66  [segv 5, StaticTypeError 3, TypeError 1, AttributeError 1]
   erased= 8 typedness=0.77  [segv 2, StaticTypeError 3, TypeError 2, AttributeError 3]
   erased= 4 typedness=0.89  [segv 2, StaticTypeError 7, TypeError 1]
   erased= 0 typedness=1.00  [StaticTypeError 1]
```

What deltablue is worth when it runs, same two masks, three configurations:

| | typed (mask 0) | fully erased (35/35) |
|---|---|---|
| JIT | crash | crash |
| JIT, `--no-inliner` | 0.279s | 0.335s |
| `--no-jit` | 2.380s | 1.527s |

The inliner is not the source of deltablue's speed -- turning it off still gives
an 8.5x speedup over the interpreter, and it is the only configuration in which
deltablue runs at all under the JIT.

---

## Bug C -- `CheckedList[UserClass]` filled with const-pool data (not the JIT)

### Reproducer: `jit_bugs/c_checkedlist_module_scope.py`

```python
import __static__
from __static__ import CheckedList


class GVector(object):
    def __init__(self, x: float) -> None:
        self.x: float = x


class Spline(object):
    def __init__(self, points: CheckedList[GVector]) -> None:
        self.points: CheckedList[GVector] = points


print('types', [type(p).__name__ for p in Spline([GVector(1.0)]).points], flush=True)
```

```
jit                18/20  rc=-11 (SIGSEGV)   2/20 ok
jit --no-inliner   17/20  rc=-11 (SIGSEGV)   3/20 ok
--no-jit           18/20  rc=-11 (SIGSEGV)   2/20 ok
```

Unaffected by the JIT, and the only one of the three that is not 100%
reproducible -- consistent with reading whatever happens to be at a wrong
index.

### What is and is not required

| variant | result |
|---|---|
| constructor call at module level, list display -> `CheckedList[GVector]` | **SIGSEGV** |
| same, two such parameters (`points`, `knots`) | **SIGSEGV** |
| same, wrapped in `def main(): ...` and called | ok |
| explicit `CheckedList[GVector]([GVector(1.0)])` argument | ok |
| `CheckedList[int]` instead of `CheckedList[UserClass]` | ok |
| plain function (not a constructor) with the same annotation, module level | ok |
| `CheckedDict[str, int]` from a dict display, module level | ok |
| the same shapes with `points: list` (no CheckedList) | ok |

### The corruption, observed

chaos, untouched source, driver hoisted to module level, probes added before the
failing call (identical output with and without the JIT):

```
splines list 3
spl0 Spline
spl0.points chklist[GVector] 7
spl0.points[0] list [('builtins', 'int')]
spl0.degree 3 knots chklist[int]
```

Element 0 of a `chklist[GVector]` of length 7 is the list
`[('builtins', 'int')]` -- a Static Python type descriptor, i.e. an object from
the module's constant pool, not anything the program constructed. Then:

```
  File ".../chaos_probe.py", line 194, in chaos_probe
    c: Chaosgame = Chaosgame(splines, 0.25, 1000, 1200, ITERATIONS)
  File ".../chaos_probe.py", line 123, in __init__
    self.minx = min([p.x for spl in splines for p in spl.points])
                     ^^^
AttributeError: 'list' object has no attribute 'x'
```

The chaos code involved:

```python
class Spline(object):
    def __init__(self, points: CheckedList[GVector], degree: int,
                 knots: CheckedList[int]) -> None:
        ...

class Chaosgame(object):
    def __init__(self, splines: List[Spline], thickness: float, w: int, h: int,
                 n: int) -> None:
        self.splines: CheckedList[Spline] = CheckedList[Spline](splines)
        self.thickness = thickness
        self.minx = min([p.x for spl in splines for p in spl.points])

# hoisted out of `if __name__ == "__main__":`
splines: list = [
    Spline([GVector(1.597350, 3.304460, 0.000000), ...], 3, [0, 0, 0, 1, 1, 1, 2, 2, 2]),
    ...]
c: Chaosgame = Chaosgame(splines, 0.25, 1000, 1200, ITERATIONS)
```

### Effect on the sweep, and on the harness

chaos: 48/82 masks ran, `typedness=1.00` failed 1/1.

```
   erased=11 typedness=0.00 ok=1/1
   erased=10 typedness=0.09 ok=9/10
   erased= 9 typedness=0.18 ok=9/10
   erased= 7 typedness=0.36 ok=8/10
   erased= 6 typedness=0.45 ok=8/10
   erased= 5 typedness=0.55 ok=6/10
   erased= 4 typedness=0.64 ok=3/10
   erased= 2 typedness=0.82 ok=3/10
   erased= 1 typedness=0.91 ok=1/10
   erased= 0 typedness=1.00 ok=0/1
```

This one is partly the sweep harness's doing and should be read with that in
mind. `typedness_sweep.hoist_main_guard` moves each benchmark's
`if __name__ == "__main__":` block to module level, because Static Python only
compiles imported modules and an import never runs that block. For chaos that
puts the `Spline(...)` calls at module scope, which is the trigger. Wrapping the
same block in a function instead runs chaos fine (0.1223s, 0.1209s, 0.1221s over
three runs), and does *not* rescue nbody or deltablue -- those two fail either
way:

| driver placement | chaos | nbody | deltablue |
|---|---|---|---|
| hoisted to module level (what the sweep does) | SIGSEGV | Abort | StaticTypeError |
| wrapped in `def main()` | 0.122s | Abort | StaticTypeError |

So chaos's numbers in the sweep come from masks that happened to survive a
harness-induced crash, and its missing points are not evidence about erasure.
nbody's and deltablue's are genuine.

---

## Reproducing

```
.venv/bin/python jit_bugs/force.py --runs 20          # all three, all configs
.venv/bin/python jit_bugs/force.py --only b --runs 50
.venv/bin/python timing_runner.py jit_bugs/b_inlined_global_store.py --require-static
.venv/bin/python timing_runner.py jit_bugs/b_inlined_global_store.py --require-static --no-inliner
```

Two independent forcing passes, verbatim. 20 runs each:

```
a_double_local_in_loop.py
  jit                20/20  rc=-6 (SIGABRT) incorrect register type.
  jit --no-inliner   20/20  rc=-6 (SIGABRT) incorrect register type.
  --no-jit           20/20  ok: ok

b_inlined_global_store.py
  jit                20/20  rc=-11 (SIGSEGV)
  jit --no-inliner   20/20  ok: ok 1
  --no-jit           20/20  ok: ok 1

c_checkedlist_module_scope.py
  jit                18/20  rc=-11 (SIGSEGV)
  jit                2/20  ok: types ['GVector']
  jit --no-inliner   17/20  rc=-11 (SIGSEGV)
  jit --no-inliner   3/20  ok: types ['GVector']
  --no-jit           18/20  rc=-11 (SIGSEGV)
  --no-jit           2/20  ok: types ['GVector']
```

And 40 runs each, a separate pass: A and B repeat exactly, C's survival rate
wanders (0/40 to 6/40) as an out-of-bounds read should.

```
a_double_local_in_loop.py
  jit                40/40  rc=-6 (SIGABRT) incorrect register type.
  jit --no-inliner   40/40  rc=-6 (SIGABRT) incorrect register type.
  --no-jit           40/40  ok: ok

b_inlined_global_store.py
  jit                40/40  rc=-11 (SIGSEGV)
  jit --no-inliner   40/40  ok: ok 1
  --no-jit           40/40  ok: ok 1

c_checkedlist_module_scope.py
  jit                39/40  rc=-11 (SIGSEGV)   1/40 ok
  jit --no-inliner   34/40  rc=-11 (SIGSEGV)   6/40 ok
  --no-jit           40/40  rc=-11 (SIGSEGV)
```

Reproducing from the benchmarks rather than the reduced cases:

```
.venv/bin/python -c "
from typedness_sweep import run_mask
from load_source import load_bench
print(run_mask(load_bench('deltablue', 'advanced'), 0, 300, False, False))"
```

## What this means for the sweep

- deltablue's panel is empty and nbody's stops at `typedness=0.44` because of
  bugs A and B, not because of erasure. Nothing about those two panels is a
  statement about typedness.
- chaos's gaps are bug C, triggered by the harness's own hoist; treat that panel
  as unreliable in both directions.
- The other nine benchmarks ran every mask and are unaffected.
- `--no-jit` gets all 12 benchmarks running, but interpreted numbers are a
  different measurement (deltablue 2.380s vs 0.279s JIT'd) and cannot be mixed
  with the JIT'd ones.
- The one configuration that would add deltablue to a JIT'd sweep is
  `--no-inliner`. It is not currently a `typedness_sweep.py` flag; the runner
  accepts it.

## Not JIT bugs, for completeness

Two other failure classes showed up and belong to the detyper, not to cinderx:

- `StrictModuleError: ('cannot divide dynamic and double', ..., 103, 0)` -- 29
  nbody masks. Erasing an annotation left a dynamic value in a division with a
  `double` and the coercion pass did not bridge it.
- `StrictModuleError: type mismatch: X cannot be assigned to None` -- hit while
  minimising bug B, when a module global's `= None` is placed above the
  functions that assign it a real type. Static Python rejects it outright; this
  is the compiler working as intended, and it is the reason deltablue's
  `planner = None` has to sit at the bottom of the file.
