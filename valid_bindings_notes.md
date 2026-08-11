# What we learned about edges

Notes from the session that took level-mask failures from 154 to 91. Every
claim here was measured, and the ones that were only argued are marked as such.

## The shape that works

- An edge is a claim about typedness, not about a type. `settle` can only ever
  say "this cell is dynamic now". It cannot say "this is a boxed int instead of
  an int64". Every rule that needed the second kind of answer was wrong.
- Type is an AND over inputs: an expression is typed only while everything
  feeding it is typed. One dynamic input makes it dynamic.
- Context is not the mirror of that. The OR -- a slot stays typed while
  anything typed still asks for it -- is the honest reading and costs 38 extra
  failures, because a node has one context cell and several consumers that can
  want different things at once. Taking the weakest demand over-boxes, and the
  stronger consumer can re-coerce a boxed value; under-boxing is unrecoverable.
- Link adjacent things and let composition do the rest. A link that jumps over
  an intermediate node duplicates a rule that already exists and skips exactly
  the node whose type erasure changes.

## Edges that are false, with the evidence

- A comparison's type does NOT follow its operands. `x is not None` is a
  boolean whatever `x` is. Linking them left the comparison looking typed,
  which held its sibling at cbool and produced `Union[dynamic, cbool]`.
- `not x` is the same: a boolean whatever `x` is. Measured at 3 extra failures.
- A cinder intrinsic's result does NOT follow its argument. `int64(x)` is an
  int64 whatever goes in. Only `box` and `unbox` genuinely derive from the
  operand. The code still links all of them, knowingly, because marking
  `int64(x)` dynamic is currently the only thing stopping the patcher boxing a
  value the tables still call int64 when it is really an int. Narrowing it to
  box/unbox alone costs 11 failures until the staleness is fixed.
- Unioning two annotations because one is a machine type is wrong. Union-find
  is transitive, so `x: int64 = y` merging x and y merges the whole primitive
  tower of a file. held_karp went 26 units to 6 and failures went 20 to 73.
  A union is only right where nothing can bridge the two sides -- an override
  must keep its base's shape, two branches that meet must produce one type.
  `box` and `unbox` bridge the primitive tower, so being primitive is a reason
  NOT to union.

## Edges that turned out to matter

- The store context. `o.a = v` and `c[i] = v` need the slot's type to reach the
  value's context. Dropping this in a rewrite cost silently until it was found.
- Result links, but only three of them: `l + r` follows the left operand
  (`"" * 2` is a str, `[1] * 2` is a list), and `a and b` / `a if c else b`
  follow their arms because the result really is one of them.
- Those result links do nothing on their own. They need all three of: the edge,
  a propagate step that carries a type along it when a patch lands, and a
  bottom-up patcher so the operand is decided before the expression containing
  it. Any one missing and the other two are inert.
- cinderx's own inflow/outflow edges are load-bearing. They are transitive
  shortcuts computed on the fully annotated program, so they bypass exactly the
  nodes erasure changes -- but removing them in favour of our own chain costs
  96 to 190 and breaks three otherwise perfect benchmarks.

## What erasure actually means

This is the current frontier and the source of most remaining failures.

- Erasing an annotation does not simply make the variable dynamic. cinderx
  narrows a declaration to its initializer whether or not the annotation is
  there, so `out: Variable = self.output()` keeps its type when erased.
- But only when the initializer really reproduces the annotation's type.
  `taskTab: List[Task] = [None] * N` infers as a plain list, so that slot does
  lose its type, and everything reached through it has to know.
- And never for machine types. `size: Any = int64(3)` cannot hold a primitive
  in a dynamic slot; there is no narrowing to recover. Applying recovery to
  machine types costs 56 failures.
- So the current rule is: recover when the value has a type, it equals the
  target's type, and neither is a machine type.

## The structural limit (argued, not measured)

152 of 192 roots -- every AnnAssign, parameter and return -- have no entry in
`bound.types` at all. A declaration's cell holds no type; it is a switch,
erased or not. `decide_type` never runs on one.

That is why every attempt to be more precise hit a wall. The truthful answer is
often "boxed int" or "narrowed to Strength", and the model can only say the
original type or dynamic. Each time, a special case was added to encode a type
into a boolean, and each special case was later measured wrong.

Giving declaration cells a real type -- seeded from the annotation, recomputed
from inputs when it goes -- would make those answers expressible. Untested.

## How to work on this

- Measure every change on its own. Combined changes hid a 38-failure regression
  behind a 34-failure improvement for two rounds.
- `ablate.py` needs `max_tasks_per_child=1`. Workers are reused and the config
  is applied by mutating the class, so without it results depend on scheduling
  order. Two full result tables were void before this was caught.
- Never edit the source while a sweep is running. It produced a run whose two
  phases measured different code.
- Select the failing node by the error's line number, not by name. Picking the
  first AST match cost two rounds of tracing the wrong subscript.
- Benchmarks that print their own elapsed time make stdout comparison
  meaningless. held_karp differs from itself run to run.

## Rules that were tried and measured wrong

The graph cites these by number. Each was implemented, measured, and removed.

**#60, a comparison's type follows its left operand.** False. `x is not None`
is a boolean whatever `x` is. Claiming otherwise left the comparison looking
typed, which held its sibling at cbool and produced `Union[dynamic, cbool]`.

**#61, `not x` follows its operand.** Same reason, 3 extra failures. A sign
change like `-x` does follow its operand and is still drawn; `not` shares the
visitor and does not.

**#70, a machine constant whose demands all died becomes dynamic.** Changes
nothing in the output -- the literal is still written `2.0` and cinderx types
it a double on the rebind -- while silencing the sibling demand that would
have coerced the other operand.

**Boxing a machine literal in a dynamic slot**, as `box(double(2.0))`. Clears
nbody's three `dynamic / 2.0` failures and breaks richards and deltablue at
import with `can't box non-primitive: Literal[True]`: the coercion target is a
real cbool but the constant itself is a Literal, which cinderx will not box.
Guarding on the target type's name does not help; the guard has to be on the
constant's own type. Only the strict loader sees this, not stage two's rebind.

**Context as an OR over live demands.** The honest reading -- a slot is typed
while anything typed still asks for it -- and 38 extra failures. There is one
context cell per node and a node can have several consumers wanting different
things at once: `city` passed both to a parameter whose annotation was erased
and to one that survived. The OR keeps the surviving demand, no coercion is
inserted for the erased one, and a primitive reaches a dynamic slot. Taking
the weakest demand instead over-boxes, which the stronger consumer can
re-coerce. The real fix is a context per use, not per node.

**Exempting sibling operands from the machine-demand clause**, in three
forms: any literal sibling, machine-typed literal siblings, and arithmetic
siblings. 83, 83 and 84 failures respectively, all with the same error
profile, so all three were the same change. The clause is load bearing for
arithmetic operands, not only comparisons.

**Recovery decided before the fixpoint.** `survives_erasure` reading the
initializer's original type says `indices: Array[int64] = create_array(...)`
recovers, but it does not if `create_array` lost its return annotation.
Recoverable declarations have to join the fixpoint so the #65 edge can take
the recovery back. Worth 13 failures when fixed.

**#20 narrowed to box and unbox.** Strictly right -- `int64(x)` is an int64
whatever goes in -- and 11 extra failures, because marking `int64(x)` dynamic
is currently the only thing stopping the patcher boxing a value the tables
still call int64 when it is really an int. Fix the staleness first.
