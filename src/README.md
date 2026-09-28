# Detyper source

- `annotation_remover.py` erases selected annotations.
- `graph/` predicts resulting types and type constraints.
- `mediate_ast.py` walks the transformed AST.
- `optimal_mediation.py` compares multi-operand candidates.
- `select_mediation.py` resolves individual requests.
- `edits.py` implements deferred AST edits.
- `mediate_expression.py` dispatches between mediation strategies.


wide things and tall things:
unops "spines" are optimized together since they require one mediation
multiops are optimized together since they require all the same mediations

we use cinderx to get reaching definitions. but since that isnt quite right we also use predicated narrowing edges 


what:
The detyper takes in a fully typed program and a boolean set and removes type annotation according to that boolean set and then restores the resulting program to type and runtime correctness using only an allowed set of operations that we call mediations.
why:
In order to benchmark partially typed programs in cinderx, a large number of examples have to be producable by automation. Since simply removing type annotations from a cinderx program is unlikely to produce a correct or even typechecking program, a better tool is needed
how:
multiple stages:
1. fact collecting
    1. 
    2. 
2. graph construction
    1. adding edges
    2. conditional edges, narrowing and "narrowing"
3. annotation removal/implicit to explicit constructor conversion
    1. remove annotations
    2. implicit to explicit construction
        3. why not for all types? (loop iterators)
4. type mediation
    1. 
5. import additions













cbool(a) or cbool(b) or c

cbool(a or b or box(c))



95% confidence interval on chart
grid chart
descriptions
examples for description

