# Minimum coercion set as a min cut

The patcher currently decides each coercion locally. The question this note
answers is what the *smallest* legal set of coercions is for a given mask, and
the answer is a minimum vertex cut on the typedness dependency graph, computed
by max flow.

## What the graph gives us

`typedness_graph.py` gives every expression two cells:

* `(node, TYPE)` -- what the expression yields.
* `(node, CONTEXT)` -- what the surrounding code demands of it.

Flow is per *expression*, not per cell kind. Typedness arrives at an expression
two different ways -- through its CONTEXT cell, from whatever declaration
demands a shape of it, and through its TYPE cell, from the values it is built
out of (the `result` links in `visit_BoolOp`, `visit_IfExp`, and the
`outflow`/TYPE branch in `construct`). Typedness departs from its TYPE cell
toward everything downstream. The two cells are the two ends of one expression.

## What a coercion does

`box(e)` sits *outside* `e`. Whatever `e` was built from and whatever was
demanded of `e`, the wrapped expression now yields the coerced type, and nothing
downstream can tell what went in. So a coercion severs every path through the
expression at once.

That is a vertex deletion, not an edge deletion, which is why the problem is a
minimum **vertex** cut and why a single edge in the raw graph is not the unit of
patching.

## Steps

1. Take the typedness dependency graph, where each expression owns a TYPE cell
   for what it yields and a CONTEXT cell for what the surrounding code demands
   of it.

2. See each expression as one thing with two sides: typedness arrives on it
   (from the values it is built from, and from the demand placed on it) and
   departs from it toward everything downstream.

3. Realize that wrapping an expression in a coercion severs all of that -- so
   the patch site is the expression, not any single edge.

4. Build the flow graph by giving each expression an in-node and an out-node,
   routing every arrival into the in-node and every departure out of the
   out-node, and joining them with one internal edge.

5. Give that internal edge capacity 1 when the expression may legally be
   wrapped, and infinite capacity when it may not, such as an annotation or an
   assignment target.

6. Give every edge that came from the original graph infinite capacity, since
   those are facts about the program rather than places you can patch.

7. Add a SOURCE with infinite-capacity edges into the in-node of every
   expression that is primitive by declaration, like an `int64` annotation.

8. Add a SINK with infinite-capacity edges from the out-node of every
   expression whose CONTEXT demands a dynamic object, since those are the places
   a primitive must never arrive.

9. Run a max-flow algorithm from SOURCE to SINK, whose value is the fewest wraps
   needed, with an infinite value meaning no legal patch set exists.

10. Recover the sites by marking everything reachable from SOURCE through
    leftover capacity: each internal edge from a reached in-node to an unreached
    out-node is one expression to wrap.

11. Run it once more with the roles reversed -- SOURCE on the dynamic origins,
    SINK on contexts demanding a primitive -- and take the union, because that
    direction calls for unboxes rather than boxes.

## Justification

**Why a cut at all.** A program is ill-typed exactly when primitive typedness
reaches a position that demands a dynamic object (or the mirror). Every such
violation is a path in the graph from a primitive origin to a dynamic demand.
Coercions are what break those paths. A legal patch set is therefore a set of
expressions meeting every source-to-sink path -- the definition of an s-t vertex
cut. The smallest legal patch set is the minimum cut.

**Why the gadget, and why the existing cell pair is not enough.** The
`(e, TYPE)` / `(e, CONTEXT)` pair looks like a free node split: CONTEXT is the
in-copy, TYPE the out-copy, the link between them the wrap. It is almost right,
and it is wrong in one way that matters. Result links deliver typedness directly
to the TYPE cell, so under the raw pair that traffic lands on the out-side and
bypasses the capacity-1 edge, and the solver concludes those paths cannot be
stopped at `e`. But `box(e)` does stop them -- see "What a coercion does". So the
in-node has to absorb both the CONTEXT arrivals and the TYPE arrivals. The
existing CONTEXT cell is one contributor to the in-node, not the in-node itself.

Where the existing link *is* the right idea: a wrap is exactly the point where
the type an expression yields stops having to match the context demanded of it.
That is why cutting between the two sides models a coercion. It just has to
dominate all inbound paths.

**Why splitting is sound.** Replacing `v` with `v_in -> v_out` and putting the
vertex's capacity on the internal edge, with all original edges set to infinity,
turns vertex cuts into edge cuts. Any finite edge cut in the split graph consists
only of internal edges, which name a set of vertices in the original; any vertex
cut gives an edge cut of the same size. So min edge cut in the split graph = min
vertex cut in the original, and max-flow min-cut computes it. Menger's theorem is
the combinatorial form of the same statement.

**Why unpatchable positions need no special handling.** An annotation or an
assignment target cannot be wrapped, and infinite capacity says so: any cut
containing an infinite edge has infinite value, so it is dominated by any finite
cut and the algorithm never picks it. Use a sentinel of `patchable_count + 1`
rather than a float, so the flow stays integral. A max flow that reaches the
sentinel means every source-to-sink path is unpatchable end to end -- the mask is
un-realizable, which is a real answer about that mask and not a solver failure.

**Why the split graph is cheap.** It has `2|V|` nodes and `|E| + |V|` edges, and
all cuttable capacities are 1, which is the unit-vertex-capacity case where
Dinic's algorithm runs in `O(E * sqrt(V))`. On graphs the size of these
benchmarks that is negligible.

**Why two runs.** A dynamic value arriving where a primitive is demanded needs an
unbox, not a box. That is the same computation with the roles of SOURCE and SINK
swapped. Running it twice and taking the union is easier to trust than a single
graph carrying typedness in both directions, and the two cut sets are disjoint in
purpose even when they touch the same expression.

## Caveats

* The union of two minimum cuts is not necessarily a minimum solution to the
  combined problem. If both directions occur in one program, the two-run answer
  is an upper bound, not proven optimal.
* A wrap site is an expression *occurrence*. If one expression's result is
  consumed by several places and they could be coerced independently, a single
  capacity-1 vertex undercounts; those uses want one unit edge each. Vertex
  splitting is exact only when the wrap is shared by all consumers, which is the
  case for `box(e)` written in place.
* This finds the fewest coercions, which is not the same as the fastest program.
  The typedness sweep shows coercion count and runtime moving together, but the
  min cut optimizes the count.
