from dataclasses import dataclass
from typing import Any


@dataclass
class BoundData:
    """The named result of one cinderx bind.

    Keeping this as an object prevents positional tuple changes from silently
    turning (for example) resolved definitions into benchmark roots.
    """

    roots: list
    types: dict
    type_contexts: dict
    constructors: Any
    components: dict
    outflow: dict
    inflow: dict
    valid_pair: Any
    tree: Any
    dynamic: Any
    declared_types: dict
    reverse_outflow: dict
    annotation_roots: list
    benchmark_roots: list
    resolved_from: dict
