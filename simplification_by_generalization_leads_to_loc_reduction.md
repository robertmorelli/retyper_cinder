# Twenty theories for collapsing the graph

The graph went from 244 to 875 lines in one session, almost all of it special
cases discovered one failure at a time. Most of them are instances of a smaller
number of rules. Each theory below names a rule, the special cases it would
absorb, and a rough line saving. Nothing here is measured.

## Position, not construct

1. **Coerced-result positions versus must-agree positions.** Arithmetic, an
   assignment, and a call argument all coerce the result, so one side may move.
   A comparison and a boolean operator require their operands to agree, so
   neither may. This single property is currently spelled out three times under
   three names: the `sibling_edges` clause in `decide_context`, the `compared`
   flag in `patch_picker`, and the same flag threaded into `tower_simplifier`.
   Compute it once on the node and consult it everywhere. ~40 lines.

2. **A demand is either satisfiable by coercion or not.** `decide_context` asks
   whether a feed is dead, whether it is a sibling edge, and whether the type
   is a machine type. All three are approximations of one question: can the
   value reaching this slot be made to fit. Ask that directly. ~25 lines.

3. **One edge kind with a tag instead of three sets.** `result_edges`,
   `sibling_edges`, and the plain edge set are the same relation with different
   permissions. An `Edge` with a `kind` field removes two set lookups, two
   membership tests, and the bookkeeping that keeps them in sync. ~20 lines.

## Callables

4. **One signature table.** `CINDER_CONVERSIONS`, `FIXED_RESULT`,
   `CONTAINER_READS`, and `CONTAINER_WRITES` all answer "what does this callable
   do to types". Four sets, four branches in `visit_Call`. Replace with one map
   from name to (argument demand, result rule). ~35 lines.

5. **A source `def` is just another signature.** `callees` and the intrinsic
   tables are two paths to the same answer. If a resolved `FunctionDef` is
   turned into the same (parameters, result) record the table holds, the two
   arms of `bind_parameters` merge into one. ~30 lines.

6. **Resolution is one lookup with a scope.** `callees`, `receiver_class`,
   `related`, `initializers`, and `owning_class` are five methods implementing
   "which definition does this name mean here". One resolver with a scope
   argument would absorb all five. ~60 lines.

7. **Constructors are calls to `__init__`.** The class branch in `callees`, the
   `skip_self` flag, and the explicit-receiver special case all exist because a
   constructor is modelled separately. Normalise it to a call and the flags go.
   ~25 lines.

## Erasure and recovery

8. **`survives_erasure` is one question, not five clauses.** Attributes,
   module globals, dynamic values, and machine types all fail to recover for
   the same reason: cinderx records narrowing per scope in `local_types`, and
   these are the cases the record does not reach. Model the scope rule and the
   clauses fall out. ~30 lines.

9. **Recovery is a type source, not an exemption.** Recoverable declarations
   are currently a set subtracted from the seeds, then re-added to
   `decided_nodes`, then exempted again for checked containers. If a
   declaration simply had an incoming edge from whatever gives it a type, the
   fixpoint would decide it like any other node. ~25 lines.

10. **The rewriter's guarantees belong in one place.** `_checked_ctor` in
    `anno_remover` decides that a container survives; `survives_erasure` has to
    know that independently, and `settle` has to exempt it from the fixpoint.
    One predicate, asked by both. ~20 lines.

11. **Seeds and reachability are the same set at different times.** `erased`,
    `recoverable`, `seeds`, and `dead` are four collections describing one
    evolving fact. Two would do. ~15 lines.

## Types versus typedness

12. **Cells could hold types instead of a boolean.** Nearly every special case
    exists because a cell can only be its original type or dynamic, so
    "boxed int" and "narrowed to Strength" are unsayable and have to be faked.
    This is the largest theory and the riskiest. Possibly negative lines, but
    it would remove the reason most of the others exist.

13. **`decide_type`'s construct branches are one join.** `BoolOp` takes the
    join of its arms, `Compare` the AND of its operands, a binop the AND of
    both. All are "combine the inputs"; only the combining function differs.
    ~20 lines.

14. **Intrinsic results are a type source, not a branch.** The `FIXED_RESULT`
    check inside `decide_type` is asking a signature question in the middle of
    a dataflow function. Move it to where signatures live. ~15 lines.

## The passes

15. **`patch_adder` and `tower_simplifier` share a decision.** Both call
    `pick_patch`, both need the comparison guard, both now need the graph. The
    difference is only that one adds and one removes. One pass with a direction
    flag. ~50 lines.

16. **Insertion and deletion should both record.** `_record` writes the tables
    on insert; `collapse` had to be written separately to do the same on
    delete. One function taking the before and after node. ~15 lines.

17. **`propagate` and `settle` are the same walk.** One follows edges from a
    changed cell, the other from a seed set. Same traversal, same visited set,
    different starting points. ~20 lines.

## Bookkeeping

18. **`index` builds six dictionaries in one pass, and five of them are
    scoped lookups.** `bindings`, `functions`, `classes`, `slots`,
    `owning_class`, and `owners` are all "what does this name mean in this
    scope". One scope object per function and class would replace the six flat
    maps and the tuple keys. ~50 lines.

19. **`visit_Attribute` and `visit_Subscript` are the same rule.** Both link
    the base to the result and the base to the demands. They were one function
    before the rewrite and were split only to add the slot lookup. ~15 lines.

20. **The `machine` and `narrows` helpers wrap the same two properties.**
    `is_primative` and `can_be_narrowed` are both attributes of a class, both
    wrapped in try/except, both asked in three places. One accessor. ~10 lines.

## Order to attempt

Theory 1 first: it is the rule that took deltablue 39 to 4 and then 2 to 0, and
it is currently written three times in three files. Then 4, 6, and 18, which
are pure bookkeeping and cannot change behaviour. Then 8 and 9. Leave 12 alone
until everything else is done, since it changes what a cell means.


## Twenty more

None of these overlap the first twenty.

### What the visitors are actually doing

21. **Every visitor is "draw edges between a node and its parts".** Fifteen
    `visit_*` methods differ only in which parts and which slot. A table from
    node type to a list of (source, target, slot) selectors would replace the
    method bodies with data. ~80 lines.

22. **`generic_visit` is called first in every visitor except two.** The two
    exceptions are historical. Hoist the traversal into one `visit` and the
    line disappears fifteen times. ~15 lines.

23. **The aliases are a taxonomy nobody wrote down.** `visit_AsyncFor =
    visit_For`, `visit_SetComp = visit_ListComp`, `visit_Tuple = visit_List`.
    Grouping node types by behaviour once removes the aliases and the pyright
    complaints they generate. ~10 lines.

24. **Comprehensions are for loops with an expression.** `visit_ListComp` and
    `visit_For` model the same binding; only the result differs. ~20 lines.

25. **Slice parts are just more index expressions.** `slice_parts` exists to
    flatten one case that the subscript rule then treats uniformly anyway.
    ~10 lines.

### Where the type information lives

26. **`bound.types` is consulted through four different accessors.**
    `self.types`, `bound.types`, `data.types`, and the settled copy. One
    accessor with an explicit "before or after erasure" argument. ~20 lines.

27. **Declarations have no type cell, so every rule about them is a special
    case.** Giving a declaration node an entry in the type table -- its
    annotation -- would let `decide_type` run on it like anything else and
    remove the `decided_nodes` splice. ~25 lines.

28. **`settle` rebuilds two full dictionaries every call.** For a mask sweep
    that is 830 copies of every type in the program. A layer over the original
    with only the differences would cut both the code and the time. ~20 lines.

29. **The dynamic sentinel is compared by identity in eleven places.** One
    `is_dynamic` accessor removes the repetition and the comment explaining why
    stage two rebinds with a fresh compiler. ~15 lines.

### The erasure boundary

30. **`nodes_for_mask` and `units` are a partition being re-derived per call.**
    The mask to node-set mapping is stable for a given granularity; computing
    it once per graph would remove the recomputation and the granularity
    argument threaded through five call sites. ~20 lines.

31. **Annotation and benchmark granularity are the same partition at two
    resolutions.** `group_functions` builds a second union-find over the first.
    One partition with a level would do. ~25 lines.

32. **`owners` exists only to group units by function.** It is maintained
    across the whole walk for one use at the end. Derivable from the scope
    index instead. ~15 lines.

### The rewriters

33. **`anno_remover`, `len_fixer`, `import_adder`, and `patch_adder` are four
    tree walks in sequence.** They do not interact except through the tables.
    One walk with four rules would cut three traversals and three visitor
    classes. ~60 lines.

34. **`find_needs_exact` runs twice on two trees for the same reason.** Once
    before patching and once in stage two. If "must keep its exact type" were a
    property recorded on the node, it would survive the rebind. ~20 lines.

35. **`_narrows_optional` and `_narrowing_cast` are the same predicate in two
    files.** Both ask whether a cast is load-bearing; one matches a string
    pattern, the other now asks the graph. ~20 lines.

36. **`TO_PRIMITIVE` duplicates part of the intrinsic table.** It lists the
    coercions that produce a primitive, which the signature table already
    knows. ~10 lines.

### Things that are only there for one benchmark

37. **`fix_len` is a whole pass for one builtin.** If `len` were an entry in
    the signature table, the pass would be a lookup. ~30 lines.

38. **`CHECKED` and the checked-container exemption exist for one deltablue
    declaration shape.** Generalising to "the rewriter guarantees this node a
    type" would cover it and anything similar later. ~15 lines.

### The measurement side

39. **`test.py` and `test_graph.py` overlap.** `test.py` reuses `execute` but
    re-implements planning, reporting, and the problem-mask record. One runner
    with a plan argument. ~80 lines.

40. **The scratchpad harnesses converged on the same shape four times.**
    `ablate.py`, `feat.py`, `shortcut.py`, and `errors.py` all fan masks over a
    process pool and count failures. One harness taking a config and a reporter
    would have saved three rewrites and the worker-reuse bug that voided two
    result tables. ~100 lines outside the repo.
