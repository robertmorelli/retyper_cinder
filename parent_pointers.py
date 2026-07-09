from ast import walk, iter_child_nodes

def build_parents(tree):
    parents = {}
    for node in walk(tree):
        for child in iter_child_nodes(node):
            parents[child] = node
    return parents
