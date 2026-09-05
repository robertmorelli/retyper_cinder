# Typedness graph topology


The graph tracks two related questions for each expression: what type it
produces, and what type constraint its position imposes. Links describe how losing one
answer can invalidate another. The rules below are the complete vocabulary
used to build that dependency graph.

Notation:

- `A.type -> B.type`: the type of `A` feeds the type of `B`.
- `A.type -> B.type_constraint`: the type of `A` feeds the type constraint at `B`.
- `A.type_constraint -> B.type_constraint`: the constraint of `A` feeds the constraint of `B`.
- `A.type_constraint -> B.type`: the mediated type at `A` feeds the type of `B`.
- `union(A, B)`: `A` and `B` are erased as one annotation unit.
- No edge connects two cells belonging to the same AST node.

### Binder links

The CinderX binder supplies the broad annotation-to-expression relationships.
These form the backbone of the graph; the syntax rules below fill in the
expression-to-expression links which the binder does not expose directly.

- For binder outflow from annotation root `A` to expression `E`:
  - `A.type -> E.type`
- For binder inflow from annotation root `A` to expression `E`:
  - `A.type -> E.type_constraint`
- For roots `A`, `B` in the same binder component:
  - `union(A, B)`
- If read `R` has reaching definitions `D` which do not include its
  annotation root:
  - `D.type -> R.type`
  - The reaching definition always types the read.
- If binder inflow for `x = value` points directly at `value`:
  - The inflow is omitted because ordinary-assignment rules route the
    dependency through the store target.

### Functions

Function annotations act at the call boundary: parameter annotations constrain
values entering a function, and return annotations constrain values leaving
it.

#### Defaults

For positional parameter `P` with default `D`:

- `P.type -> D.type_constraint`

This models the default satisfying the parameter annotation.

#### Returns

For `return value` inside function `F`:

- `F.type -> value.type_constraint`

When CinderX identifies an annotated function as inline, its value-returning
`return` is a narrowing choice:

- while `value` narrows the return declaration:
  - `value.type -> F.type`
- otherwise:
  - `F.type -> value.type_constraint`

This prevents a dynamic inline expression from replacing a return annotation,
while still modeling CinderX substituting a narrowing expression at the call
site.

### Names and assignments

Assignments either transfer a type into a new name or ask a value to satisfy a
type which already belongs to a declared slot. The shape of the target decides
which relationship applies.

#### Unresolved name reads

For unresolved load `R` of name `x`:

- If CinderX supplies reaching definitions `D`:
  - `D.type -> R.type`
- If CinderX already supplies annotation outflow for the read, no additional
  syntax edge is added.
- A visible function, class, or imported name is definitionless in this graph:
  it is known to exist but has no definition cell which feeds the read.
- A load with neither a source nor a lexical binding is recorded as a missing
  read source for validation.

#### Ordinary assignment

For `target = value`:

- If `target` is an attribute or subscript:
  - `target.type -> value.type_constraint`
  - This models a store satisfying its slot type.
- If `target` is a name with an annotation:
  - `target.type -> value.type_constraint`
  - If the value remains typed and narrows the local:
    - `value.type -> target.type`
  - Otherwise:
    - `declaration-target.type -> target.type`
  - `target.type -> read.type` for each read reached by this assignment
  - The annotation reaches later stores only through its own target cell.
- Otherwise:
  - `value.type -> target.type`

Apply the rule independently to every target in a chained assignment.
Tuple and list targets apply it recursively. Matching literal shapes link
corresponding elements; otherwise the whole value feeds every leaf target.

#### Annotated assignment

For `target: T = value`, with declaration `D`:

- `D.type -> target.type -> value.type_constraint`
- `D.type -> target.type`
- When `D` is erased and its initializer can recover it:
  - `value.type -> D.type`
- Uses of `target` are fed by `target.type`.

The mask-dependent edge models CinderX inferring the declaration from its
initializer when `T` is erased.

For `target: T` without an initializer:

- `D.type -> target.type`

#### Augmented assignment

For `target op= value`:

- `target.type -> value.type_constraint`
- `reaching-definition.type -> target.type`
- `value.type -> target.type`

An augmented assignment is both a read and a write, so its result depends on
the previous target and the right operand.

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
- CinderX outflow supplies `D.type -> attribute.type` when it resolves a member
  declaration `D`.

This models member access depending on the receiver and declared slot.

#### Subscript

For `container[index]`:

- `container.type -> subscript.type`
- `container.type -> index.type_constraint`

For `container[lower:upper:step]`:

- `container.type -> subscript.type`
- No slice-part constraint links are added.

This models CinderX producing a plain list for a slice even when the source is
a checked list.

### Operators

Operators connect sibling constraints because their operands are mediated as a
group. Result links are separate: some operators preserve an operand's value,
while others produce a new boolean value.

#### Binary operation

For `left op right`, producing `result`:

- `left.type_constraint -> right.type_constraint`
- `right.type_constraint -> result.type`
- Unless `op` is `Pow`:
  - `result.type_constraint -> left.type_constraint`

This models the mediator processing operands from left to right. The final
mediated constraint represents the whole chain, so only the right constraint needs
to feed the result. `Pow` does not pass the result constraint into its left
operand.

#### Comparison

For operands `A`, `B`, `C` in `A < B < C`:

- `A.type_constraint -> B.type_constraint`
- `B.type_constraint -> C.type_constraint`
- `C.type_constraint -> comparison.type`

Only the final mediated constraint feeds the comparison type; earlier operands
reach it through the constraint chain. No operand type feeds the comparison
type. A comparison produces a boolean rather than one of its operands.

#### Unary operation

For unary `op value`, when `op` is not `not`:

- `unary.type_constraint -> value.type_constraint`
- `value.type -> unary.type`

For `not value`:

- `value.type -> unary.type`

`not` produces a boolean rather than preserving the operand's kind, but its
boolean is statically typed only while the operand remains typed.

#### Boolean operation

For arms `A`, `B`, `C`:

- `A.type_constraint -> B.type_constraint`
- `B.type_constraint -> C.type_constraint`
- `C.type_constraint -> result.type`

The final mediated constraint feeds the result; earlier arms reach it through the
constraint chain.

#### Conditional expression

For `body if test else other`:

- `body.type_constraint -> other.type_constraint`
- `other.type_constraint -> result.type`

The final mediated arm constraint feeds the result.

### Calls

A call combines the callee expression, relationships exported by the CinderX
binder, and the semantics of known CinderX operations.

For `callee(arguments)`, producing `call`:

- `callee.type -> call.type`
- Unless `callee` is a representation-changing coercion:
  - `callee.type -> argument.type_constraint` for every positional argument

This models a dynamic callee accepting boxed objects.

Parameter-to-argument and return-to-call relationships come only from the
CinderX binder tables. Topology does not guess callees from names or receiver
syntax.

#### Known calls

- `box`, `unbox`:
  - `argument.type -> call.type`
  - No resolved-callee links
- `pop`, `get`, `copy`, `index`, `count`:
  - `receiver.type -> call.type`
- `append`, `add`, `insert`, `extend`, `remove`, `discard`:
  - `receiver.type -> argument.type_constraint` for every positional argument
- These produce their own type rather than deriving it from an argument:
  - `clen`, `cbool`, `double`, `Array`, `cast`
  - `int8`, `int16`, `int32`, `int64`
  - `uint8`, `uint16`, `uint32`, `uint64`
- These are representation-changing coercions and do not receive
  `callee.type -> argument.type_constraint`:
  - `box`, `cast`, `double`, `cbool`
  - all listed signed and unsigned integer conversions

### Formatting

For interpolated value `V`:

- `formatted-value.type -> V.type_constraint`

This models formatting accepting an object and boxing a surviving primitive.

### Iteration and collections

Containers carry type information into iteration targets and collect type
information from their elements. Comprehensions need both directions: elements
determine the result, and the result's type constraint constrains the elements.

#### `for`

For each name target in `for target in iterable`:

- If the iterator element narrows the local:
  - `iterable.type -> target.type`
- Otherwise, for a declared target:
  - `declaration-target.type -> target.type`
- `target.type -> read.type` for each read reached by the loop write
- If the iterable and target both resolve to annotations, their erasure units
  are joined.

Tuple and list targets apply the rule recursively to their name elements.
Attribute and subscript targets receive no link from this rule.

The same rules apply to `async for`.

#### Comprehension

For each name target in generator `for target in iterable`:

- `iterable.type -> target.type`

For produced element `E` of comprehension `C`:

- `E.type -> C.type`
- `C.type_constraint -> E.type_constraint`

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

From CinderX's override components:

- Same-named annotated attributes `A`, `B`:
  - `union(A, B)`
- Same-named methods:
  - `union(subclass-parameter, base-parameter)` for each positional pair
  - `union(subclass-return, base-return)` when both returns are annotated

### Mask-dependent links

Most links are fixed by the annotated program's structure. Clipping adds
`value.type -> declaration.type` for an erased `target: T = value` when CinderX
can infer the local definition from its initializer. A later binding selects
either its assigned value or the declaration's target after upstream flow
determines whether the value remains typed. `binding.type -> read.type` is
unconditional.

### Units

Units are the choices exposed to an erasure mask. Unions make annotations one
choice when the typechecker requires their contracts to remain aligned.

- Binder component unions form annotation units, including CinderX-resolved
  overrides.
- Roots not unioned with another root form one-member units.
- Units are ordered by their first root's source position.
- Benchmark granularity combines annotation units owned by the same function.
- If one annotation unit has owners from several functions, those functions
  belong to one benchmark unit.
- Each unit occupies one bit in an erasure mask.

## Clipping


Clipping specializes the fixed topology for one erasure choice. It copies the
fixed links, adds initializer-inference links opened by erased annotations, and
identifies the cells which must begin propagation as dynamic.

### Clipped result

- Every fixed topology edge is active for every mask.
- An erased recoverable declaration adds its initializer-to-definition edge.
- Active edges targeting a type cell are indexed as:
  - `target node -> source cells feeding target.type`
- Active edges targeting a constraint cell are indexed as:
  - `target node -> source cells feeding target.type_constraint`
- Clipping also returns the type and constraint cells made dynamic directly by
  erasure.

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

Each erased annotation takes exactly one of three paths.

### Definition remains typed

An annotated assignment is `rebuilt` when the annotation remover recognizes
its initializer as a checked-container constructor case.

This models the annotation remover replacing the initializer with an explicit
typed constructor. The container retains its type independently of graph
propagation.

### RHS narrows

An annotated assignment is narrowed by its RHS when all of these are true:

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

A recoverable declaration is linked to its initializer during settling. It
becomes dynamic if the initializer becomes dynamic.

### Initially dynamic

Every classified annotation which neither remains typed nor narrows from its
RHS becomes dynamic immediately.

This includes:

- parameters;
- return annotations;
- annotated assignments without initializers;
- annotated attribute assignments;
- module-level annotated assignments;
- assignments initialized from an absent or dynamic type;
- assignments whose initializer does not permit narrowing;
- assignments whose initializer or target has a machine type.

Its type and constraint cells begin settling as dynamic.

For such an annotated assignment to a name or attribute:

- its assignment target's type and constraint cells also begin dynamic.

Reads of the declaration are fed by that target, so the target begins settling
as dynamic with the declaration.

## Flow

Flow combines the clipped edges with the currently selected narrowing edges,
indexes them by target type and constraint cell, and propagates loss of typedness
to a fixed point. Assignment and inline-return choices use the same table; each
entry stores its narrowing edge and its declared-type edge.

After narrowing choices settle, their edges and the clipped fixed edges become
the graph's edge set. Mediation therefore reads the same active topology that
flow used. A caller which needs another mask builds a fresh graph; the
visualizer does this for every request.

The initial dead set is the dynamic-cell set returned by clipping. Every node
in the original type table participates in type settlement, along with active
type-edge targets. A constraint participates only when an active edge feeds it and
the original binder recorded a constraint for that node.

For either slot, one dead source is sufficient to make the target cell dead.
Type and constraint cells settle in the same loop because constraint cells can depend
on other constraint cells. Settlement stops when a full pass adds no dead cells.

The result starts from copies of the binder's original type and constraint tables:

- a dead type cell receives the binder's dynamic type;
- a live type cell retains its original type;
- an initially dynamic constraint cell receives a dynamic constraint;
- every other demanded constraint is dynamic exactly when one of its active feeds
  is dead.

`TypeGraph(bound, mask, granularity)` composes clipping and propagation while
constructing the mask-specific graph used by callers.
