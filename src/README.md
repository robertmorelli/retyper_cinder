# Detyper source

- `annotation_remover.py` erases selected annotations.
- `graph/` predicts resulting types and type constraints.
- `mediate_ast.py` walks the transformed AST.
- `optimal_mediation.py` compares multi-operand candidates.
- `select_mediation.py` resolves individual requests.
- `edits.py` implements deferred AST edits.
- `mediate_expression.py` dispatches between mediation strategies.
