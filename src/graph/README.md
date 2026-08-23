# Typedness graph

## Visualizer

Run the local benchmark visualizer from the repository root:

```sh
python -m src.graph.graph_server
```

It opens `http://127.0.0.1:8000`. Choose a benchmark and source variant, then
select annotation- or
function-level masking, and enter a binary mask. The page reports the number
of available units at both granularities and redraws the detyped source graph
as the mask changes. The random-mask bar can generate an unconstrained mask or
one containing an exact selected proportion of detyped units. Generated masks
remain in the mask field so they can be copied and reproduced. Use `--no-open`,
`--host`, or `--port` to change startup behavior.

## Topology


The graph tracks two related questions for each expression: what type it
produces, and what type its position expects. Links describe how losing one
answer can invalidate another. The rules below are the complete vocabulary
used to build that dependency graph.

Notation:

- `A.type -> B.type`: the type of `A` feeds the type of `B`.
- `A.type -> B.context`: the type of `A` feeds the type expected at `B`.
- `A.context -> B.context`: the context of `A` feeds the context of `B`.
- `A.context -> B.type`: the mediated type at `A` feeds the type of `B`.
- `union(A, B)`: `A` and `B` are erased as one annotation unit.
- `predicate(E, A)`: edge `E` exists when annotation `A` is erased.
- No edge connects two cells belonging to the same AST node.

### Binder links

The CinderX binder supplies the broad annotation-to-expression relationships.
These form the backbone of the graph; the syntax rules below fill in the
expression-to-expression links which the binder does not expose directly.

- For binder outflow from annotation root `A` to expression `E`:
  - `A.type -> E.type`
- For binder inflow from annotation root `A` to expression `E`:
  - `A.type -> E.context`
- For roots `A`, `B` in the same binder component:
  - `union(A, B)`
- If read `R` has reaching definitions `D` which do not include its
  annotation root:
  - `D.type -> R.type`
  - This models CinderX typing a read from its reaching assignments.
- If binder inflow for `x = value` points directly at `value`:
  - `annotation.type -> x.type`
  - This models the missing store-context name in the binder output.

### Functions

Function annotations act at the call boundary: parameter annotations constrain
values entering a function, and return annotations constrain values leaving
it.

#### Defaults

For positional parameter `P` with default `D`:

- `P.type -> D.context`

This models the default satisfying the parameter annotation.

#### Returns

For `return value` inside function `F`:

- `F.type -> value.context`

If `F` is decorated with `inline` and has exactly one value-returning
`return`:

- `value.type -> F.type`

This models CinderX substituting the returned expression at an inline call
site.

### Names and assignments

Assignments either transfer a type into a new name or ask a value to satisfy a
type which already belongs to a declared slot. The shape of the target decides
which relationship applies.

#### Unresolved name reads

For unresolved load `R` of name `x`:

- If CinderX supplies reaching definitions `D`:
  - `D.type -> R.type`
- Otherwise, for bindings `B` in the first scope containing `x`, searching
  the current function and then module scope:
  - `B.type -> R.type`
- A function, class, or imported name counts as a lexical binding, but does
  not add a graph edge of its own.
- A load with neither a source nor a lexical binding is recorded as a missing
  read source for validation.

#### Ordinary assignment

For `target = value`:

- If `target` is an attribute or subscript:
  - `target.type -> value.context`
  - This models a store satisfying its slot type.
- If `target` is a name with an annotation:
  - `target.type -> value.context`
  - `value.type -> target.type`, predicated on the target's declaration
  - This models the annotation demanding the stored value while present and
    CinderX narrowing the name from the value when the annotation is erased.
- Otherwise:
  - `value.type -> target.type`

Apply the rule independently to every target in a chained assignment.

#### Annotated assignment

For `target: T = value`, with declaration `D`:

- `D.type -> value.context`
- `D.type -> target.type`
- `value.type -> D.type`, predicated on `D`
- Uses of `target` are fed by `target.type`.

The predicated edge models CinderX inferring the declaration from its initializer
when `T` is erased.

For `target: T` without an initializer:

- `D.type -> target.type`

#### Augmented assignment

For `target op= value`:

- `target.type -> value.context`
- If `target` has no annotation:
  - `value.type -> target.type`

#### Named expression

For `(target := value)`:

- `value.type -> target.type`
- `value.type -> named-expression.type`

### Attributes and subscripts

Member and element access depend on the container being typed, while stores
flow in the opposite direction as demands on the value being stored.

#### Attribute

For `receiver.member`:

- `receiver.type -> attribute.type`
- When the receiver class is known, for each declaration `D` of `member`
  compatible with that class:
  - `D.type -> attribute.type`
- When the receiver class is unknown, no declaration edge is added.

This models member access depending on the receiver and declared slot.

#### Subscript

For `container[index]`:

- `container.type -> subscript.type`
- `container.type -> index.context`

For `container[lower:upper:step]`:

- No subscript or slice-part links are added.

This models CinderX producing a plain list for a slice even when the source is
a checked list.

### Operators

Operators connect sibling contexts because their operands are mediated as a
group. Result links are separate: some operators preserve an operand's value,
while others produce a new boolean value.

#### Binary operation

For `left op right`, producing `result`:

- `left.context -> right.context`
- `right.context -> result.type`
- Unless `op` is `Pow`:
  - `result.context -> left.context`

This models the mediator processing operands from left to right. The final
mediated context represents the whole chain, so only the right context needs
to feed the result. `Pow` does not pass the result context into its left
operand.

#### Comparison

For operands `A`, `B`, `C` in `A < B < C`:

- `A.context -> B.context`
- `B.context -> C.context`
- `C.context -> comparison.type`

Only the final mediated context feeds the comparison type; earlier operands
reach it through the context chain. No operand type feeds the comparison
type. A comparison produces a boolean rather than one of its operands.

#### Unary operation

For unary `op value`, when `op` is not `not`:

- `unary.context -> value.context`
- `value.type -> unary.type`

For `not value`:

- `value.type -> unary.type`

`not` produces a boolean rather than preserving the operand's kind, but its
boolean is statically typed only while the operand remains typed.

#### Boolean operation

For arms `A`, `B`, `C`:

- `A.context -> B.context`
- `B.context -> C.context`
- `C.context -> result.type`

The final mediated context feeds the result; earlier arms reach it through the
context chain.

#### Conditional expression

For `body if test else other`:

- `body.context -> other.context`
- `other.context -> result.type`

The final mediated arm context feeds the result.

### Calls

A call combines three sources of information: the callee expression, any
resolved parameter and return annotations, and the semantics of known CinderX
operations.

For `callee(arguments)`, producing `call`:

- `callee.type -> call.type`
- Unless `callee` is a representation-changing coercion:
  - `callee.type -> argument.context` for every positional argument

This models a dynamic callee accepting boxed objects.

For each resolved callee definition:

- `parameter.type -> argument.context` for each positional pair
- `callee-definition.type -> call.type` when it has a return annotation
- Omit leading `self` for an ordinary bound method call.
- Keep the leading parameter for an explicit `Class.method(...)` call.

Constructor calls use the class's `__init__`, or its inherited initializer if
it has none. A known receiver limits same-named method candidates to compatible
classes when possible.

#### Known calls

- `box`, `unbox`:
  - `argument.type -> call.type`
  - No resolved-callee links
- `pop`, `get`, `copy`, `index`, `count`:
  - `receiver.type -> call.type`
- `append`, `add`, `insert`, `extend`, `remove`, `discard`:
  - `receiver.type -> argument.context` for every positional argument
- These produce their own type rather than deriving it from an argument:
  - `clen`, `cbool`, `double`, `Array`, `cast`
  - `int8`, `int16`, `int32`, `int64`
  - `uint8`, `uint16`, `uint32`, `uint64`
- These are representation-changing coercions and do not receive
  `callee.type -> argument.context`:
  - `box`, `cast`, `double`, `cbool`
  - all listed signed and unsigned integer conversions

### Formatting

For interpolated value `V`:

- `formatted-value.type -> V.context`

This models formatting accepting an object and boxing a surviving primitive.

### Iteration and collections

Containers carry type information into iteration targets and collect type
information from their elements. Comprehensions need both directions: elements
determine the result, and the expected result constrains the elements.

#### `for`

For `for target in iterable`, when `target` is a plain name:

- `iterable.type -> target.type`
- If `iterable` resolves to annotation `I` and `target` has annotation `T`:
  - `union(T, I)`

Tuple, list, attribute, and subscript loop targets receive no links from this
rule. CinderX does not narrow names unpacked from an iterator.

The same rules apply to `async for`.

#### Comprehension

For generator `for target in iterable` whose target is a plain name:

- `iterable.type -> target.type`

For produced element `E` of comprehension `C`:

- `E.type -> C.type`
- `C.context -> E.context`

A dictionary comprehension has two produced elements: its key and value.

#### Collection literal

For item `E` in list, set, or tuple literal `C`:

- `E.type -> C.type`

For key or value `E` in dictionary literal `C`:

- `E.type -> C.type`

### Inheritance

An override cannot be erased independently of the contract it overrides, so
matching annotations across visible base and subclass definitions share a
unit.

For a subclass and visible base class:

- Same-named annotated attributes `A`, `B`:
  - `union(A, B)`
- Same-named methods:
  - `union(subclass-parameter, base-parameter)` for each positional pair
  - `union(subclass-return, base-return)` when both returns are annotated

### Predicates

Most links describe the fully annotated program and are always present. A
predicated link describes inference which becomes available only after its
predicate annotation has been erased.

- `value.type -> declaration.type` for `target: T = value` is predicated on
  the declaration.
- `binding.type -> read.type` is predicated on the read's declaration when
  the binding is not that declaration.
- A predicated edge is included only when its predicate annotation is erased.

### Units

Units are the choices exposed to an erasure mask. Unions make annotations one
choice when the typechecker requires their contracts to remain aligned.

- Binder component unions and inheritance unions form annotation units.
- Roots not unioned with another root form one-member units.
- Units are ordered by their first root's source position.
- Benchmark granularity combines annotation units owned by the same function.
- If one annotation unit has owners from several functions, those functions
  belong to one benchmark unit.
- Each unit occupies one bit in an erasure mask.

## Clipping


Clipping specializes the fixed topology for one erasure choice. It starts with
the links which always exist, activates the inference links opened by erased
annotations, and identifies the nodes which must begin propagation as dynamic.

### Active links

- Every unconditional topology edge is active for every mask.
- An edge predicated on annotation `A` is active when `A` is erased.
- Active edges targeting a type cell are indexed as:
  - `target node -> source cells feeding target.type`
- Active edges targeting a context cell are indexed as:
  - `target node -> source cells feeding target.context`
- The unconditional indexes are cached once.
- Each clipping operation copies the unconditional indexes and inserts the
  edges predicated on nodes in the current erased set.

### Erased annotations

Erasing syntax does not have one uniform type effect. Some annotations are
restored by rewriting, some local declarations can be inferred again, and the
rest lose their type immediately.

Only these erased nodes are classified:

- annotated assignments;
- parameters;
- functions with return annotations.

Other nodes may belong to the same erasure unit. They are settled through the
active graph rather than classified directly.

Every classified annotation belongs to exactly one set:

- `rebuilt`
- `recovers`
- `seeds`

### `rebuilt`

An annotated assignment is `rebuilt` when the annotation remover recognizes
its initializer as a checked-container constructor case.

This models the annotation remover replacing the initializer with an explicit
typed constructor. The container retains its type independently of graph
propagation.

### `recovers`

An annotated assignment is `recovers` when all of these are true:

- it has an initializer;
- its target is a plain name;
- it is owned by a function;
- the initializer has a type;
- the initializer's type is not dynamic;
- the initializer type permits narrowing, when it exposes
  `klass.can_be_narrowed`;
- neither the initializer nor target has a machine type.

This models CinderX inferring a local name from its initializer after the
annotation is erased.

A recoverable declaration remains linked to its initializer during settling.
It becomes dynamic if the initializer becomes dynamic.

### `seeds`

Every classified annotation which is neither `rebuilt` nor `recovers` is a
`seed`.

This includes:

- parameters;
- return annotations;
- annotated assignments without initializers;
- annotated attribute assignments;
- module-level annotated assignments;
- assignments initialized from an absent or dynamic type;
- assignments whose initializer does not permit narrowing;
- assignments whose initializer or target has a machine type.

A seed begins settling as dynamic.

For a seeded annotated assignment to a name or attribute:

- add the assignment target to `seeds`.

Reads of the declaration are fed by that target, so the target begins settling
as dynamic with the declaration.

## Flow

Flow activates the unconditional topology plus every edge whose predicate
annotation is in the erased set, indexes those active edges by target type and
context cell, and propagates loss of typedness to a fixed point.

The initial dead set contains both cells of every clipping seed. Every node in
the original type table participates in type settlement, along with recoverable
declarations. A context participates only when an active edge feeds it and the
original binder recorded a context for that node.

For either slot, one dead source is sufficient to make the target cell dead.
Type and context cells settle in the same loop because context cells can depend
on other context cells. Settlement stops when a full pass adds no dead cells.

The result starts from copies of the binder's original type and context tables:

- a dead type cell receives the binder's dynamic type;
- a live type cell retains its original type;
- an erased seed receives a dynamic context;
- every other demanded context is dynamic exactly when one of its active feeds
  is dead.

`typedness_graph.Graph.flow` composes clipping and propagation and is the public
entry point used by callers.
