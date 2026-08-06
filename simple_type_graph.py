"""A tiny type/context binder: two cells per AST node and plain flow edges."""
import ast
from collections import defaultdict, deque
from dataclasses import dataclass
TYPE, CONTEXT = "type", "context"
RULES = {}
def rule(*kinds):
    def register(fn):
        for kind in kinds:
            RULES[kind] = fn
        return fn
    return register
@dataclass(frozen=True)
class Edge:
    source: tuple
    target: tuple
class Graph:
    def __init__(self):
        self.edges = set()
        self.values = defaultdict(set)
        self.labels = {}
        self.anchors = {}
    def cell(self, node, slot):
        cell = (node, slot)
        self.labels[cell] = node_label(node, slot)
        self.anchors.setdefault(cell, node)
        return cell
    def anchor(self, cell, node):
        self.anchors[cell] = node
        self.labels[cell] = node_label(node, cell[1])

    def set(self, cell, value):
        if value:
            self.values[cell].add(value)
        return cell
    def flow(self, source, target):
        self.edges.add(Edge(source, target))
        return target
    def solve(self):
        outgoing = defaultdict(list)
        for edge in self.edges:
            outgoing[edge.source].append(edge.target)
        todo = deque(self.values)
        queued = set(todo)
        while todo:
            source = todo.popleft()
            queued.discard(source)
            for target in outgoing[source]:
                old = len(self.values[target])
                self.values[target].update(self.values[source])
                if len(self.values[target]) != old and target not in queued:
                    todo.append(target)
                    queued.add(target)
        return self
class Binder:
    def __init__(self):
        self.graph = Graph()
        self.scopes = [{}]
        self.functions = {}
        self.function = None
    def type(self, node):
        return self.graph.cell(node, TYPE)
    def context(self, node):
        return self.graph.cell(node, CONTEXT)
    def source(self, node, value, slot=TYPE):
        return self.graph.set(self.graph.cell(node, slot), value)
    def flow(self, source, target):
        self.graph.flow(source, target)
    def define(self, name, node):
        self.scopes[-1][name] = node
    def lookup(self, name):
        return next((scope[name] for scope in reversed(self.scopes)
                     if name in scope), None)
    def visit(self, node):
        return RULES.get(type(node), generic)(self, node)
    def children(self, node):
        for child in ast.iter_child_nodes(node):
            self.visit(child)
    def bind(self, tree):
        self.visit(tree)
        return self.graph.solve()
def annotation(node):
    return ast.unparse(node) if node is not None else None
def node_label(node, slot):
    line = getattr(node, "lineno", 0)
    text = ast.unparse(node).replace("\n", " ")[:48]
    return f"{line}: {text} · {slot}"
def generic(b, node):
    b.children(node)
@rule(ast.Module)
def module(b, node):
    for statement in node.body:
        b.visit(statement)
@rule(ast.Constant)
def constant(b, node):
    b.source(node, type(node.value).__name__)
    b.flow(b.context(node), b.type(node))
@rule(ast.Name)
def name(b, node):
    if type(node.ctx) is ast.Load:
        origin = b.lookup(node.id)
        if origin is None:
            b.source(node, "Any")
        else:
            b.flow(b.type(origin), b.type(node))
def store(b, target, value=None, declared=None):
    if type(target) is ast.Name:
        b.define(target.id, target)
    if declared:
        b.source(target, declared)
    elif value is not None:
        b.flow(b.type(value), b.type(target))
    if value is not None:
        b.flow(b.type(target), b.context(value))
    b.visit(target)
@rule(ast.Assign, ast.NamedExpr)
def assign(b, node):
    targets = node.targets if type(node) is ast.Assign else [node.target]
    b.visit(node.value)
    for target in targets:
        store(b, target, node.value)
@rule(ast.AnnAssign)
def annassign(b, node):
    if node.value is not None:
        b.visit(node.value)
    store(b, node.target, node.value, annotation(node.annotation))
@rule(ast.FunctionDef, ast.AsyncFunctionDef)
def function(b, node):
    b.define(node.name, node)
    b.functions[node.name] = node
    if node.returns is not None:
        b.source(node, annotation(node.returns))
        b.graph.anchor(b.type(node), node.returns)
    previous = b.function
    b.function = node
    b.scopes.append({})
    for arg in node.args.args:
        b.define(arg.arg, arg)
        b.source(arg, annotation(arg.annotation) or "Any")
    for statement in node.body:
        b.visit(statement)
    b.scopes.pop()
    b.function = previous
@rule(ast.Return)
def return_(b, node):
    if node.value is None:
        return
    b.visit(node.value)
    if b.function is not None:
        if b.function.returns is None:
            b.flow(b.type(node.value), b.type(b.function))
        b.flow(b.type(b.function), b.context(node.value))
@rule(ast.Call)
def call(b, node):
    b.visit(node.func)
    for arg in node.args:
        b.visit(arg)
    callee = b.functions.get(node.func.id) if type(node.func) is ast.Name else None
    if callee is None:
        b.source(node, "Any")
    else:
        b.flow(b.type(callee), b.type(node))
        for arg, param in zip(node.args, callee.args.args):
            b.flow(b.type(param), b.context(arg))
@rule(ast.BinOp, ast.BoolOp, ast.IfExp)
def expression(b, node):
    parts = ([node.left, node.right] if type(node) is ast.BinOp else
             node.values if type(node) is ast.BoolOp else [node.body, node.orelse])
    for part in parts:
        b.visit(part)
        b.flow(b.type(part), b.type(node))
        b.flow(b.context(node), b.context(part))
@rule(ast.Compare)
def compare(b, node):
    b.source(node, "bool")
    b.children(node)
@rule(ast.List, ast.Tuple, ast.Set, ast.Dict)
def container(b, node):
    b.source(node, type(node).__name__.lower())
    b.children(node)
def bind(source):
    return Binder().bind(ast.parse(source))
