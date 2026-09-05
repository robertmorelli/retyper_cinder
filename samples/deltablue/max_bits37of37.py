# deltablue/advanced  granularity=benchmark
# mask=137438953471  (37/37 units erased)

"""
main.py
============

Ported for the PyPy project.
Contributed by Daniel Lindsley

This implementation of the DeltaBlue benchmark was directly ported
from the `V8's source code`_, which was in turn derived
from the Smalltalk implementation by John Maloney and Mario
Wolczko. The original Javascript implementation was licensed under the GPL.

It's been updated in places to be more idiomatic to Python (for loops over
collections, a couple magic methods, ``OrderedCollection`` being a list & things
altering those collections changed to the builtin methods) but largely retains
the layout & logic from the original. (Ugh.)

.. _`V8's source code`: (https://github.com/v8/v8/blob/master/benchmarks/deltablue.js)
"""
from __future__ import annotations
import __static__
from typing import Any
from enum import IntEnum
from __static__ import CheckedList, cast, clen, inline
from typing import final
import time
import cinderx.jit
cinderx.jit.compile_after_n_calls(0)

@inline
def stronger(s1: Any, s2: Any) -> Any:
    return s1.strength < s2.strength

@inline
def weaker(s1: Any, s2: Any) -> Any:
    return s1.strength > s2.strength

@inline
def weakest_of(s1: Any, s2: Any) -> Any:
    return s1 if s1.strength > s2.strength else s2

@final
class Strength:

    def __init__(self, strength: Any, name: Any) -> None:
        self.strength: Any = strength
        self.name: Any = name

    def next_weaker(self) -> Any:
        return STRENGTHS[self.strength]
REQUIRED: Any = Strength(0, 'required')
STRONG_PREFERRED: Any = Strength(1, 'strongPreferred')
PREFERRED: Any = Strength(2, 'preferred')
STRONG_DEFAULT: Any = Strength(3, 'strongDefault')
NORMAL: Any = Strength(4, 'normal')
WEAK_DEFAULT: Any = Strength(5, 'weakDefault')
WEAKEST: Any = Strength(6, 'weakest')
STRENGTHS: Any = CheckedList[Strength]([WEAKEST, WEAK_DEFAULT, NORMAL, STRONG_DEFAULT, PREFERRED, REQUIRED])

class Constraint(object):

    def __init__(self, strength: Any) -> Any:
        self.strength: Any = strength

    def add_constraint(self) -> Any:
        planner: Any = get_planner()
        self.add_to_graph()
        planner.incremental_add(self)

    def satisfy(self, mark: Any) -> Any:
        planner: Any = get_planner()
        self.choose_method(mark)
        if not self.is_satisfied():
            if self.strength == REQUIRED:
                print('Could not satisfy a required constraint!')
            return None
        self.mark_inputs(mark)
        out: Any = self.output()
        overridden = out.determined_by
        if overridden is not None:
            overridden.mark_unsatisfied()
        out.determined_by = self
        if not planner.add_propagate(self, mark):
            print('Cycle encountered')
        out.mark = mark
        return overridden

    def destroy_constraint(self) -> Any:
        planner: Any = get_planner()
        if self.is_satisfied():
            planner.incremental_remove(self)
        else:
            self.remove_from_graph()

    def is_input(self) -> Any:
        return False

    def mark_inputs(self, mark: Any) -> Any:
        pass

    def inputs_known(self, mark: Any) -> Any:
        return True

    def choose_method(self, mark: Any) -> Any:
        pass

    def output(self) -> Any:
        raise NotImplementedError()

    def execute(self) -> Any:
        pass

class UrnaryConstraint(Constraint):

    def __init__(self, v: Any, strength: Any) -> None:
        Constraint.__init__(self, strength)
        self.my_output: Any = v
        self.satisfied: Any = False
        self.add_constraint()

    def add_to_graph(self) -> None:
        self.my_output.add_constraint(self)
        self.satisfied = False

    def choose_method(self, mark: Any) -> Any:
        if self.my_output.mark != mark and stronger(self.strength, self.my_output.walk_strength):
            self.satisfied = True
        else:
            self.satisfied = False

    def is_satisfied(self) -> Any:
        return self.satisfied

    def output(self) -> Any:
        return self.my_output

    def recalculate(self) -> None:
        self.my_output.walk_strength = self.strength
        self.my_output.stay = not self.is_input()
        if self.my_output.stay:
            self.execute()

    def mark_unsatisfied(self) -> None:
        self.satisfied = False

    def remove_from_graph(self) -> None:
        if self.my_output is not None:
            self.my_output.remove_constraint(self)
            self.satisfied = False

@final
class StayConstraint(UrnaryConstraint):
    pass

@final
class EditConstraint(UrnaryConstraint):

    def is_input(self) -> Any:
        return True

@final
class Direction(IntEnum):
    NONE = 0
    FORWARD = 1
    BACKWARD = -1

class BinaryConstraint(Constraint):

    def __init__(self, v1: Any, v2: Any, strength: Any) -> Any:
        Constraint.__init__(self, strength)
        self.v1: Any = v1
        self.v2: Any = v2
        self.direction: Any = Direction.NONE
        self.add_constraint()

    def choose_method(self, mark: Any) -> Any:
        if self.v1.mark == mark:
            if self.v2.mark != mark and stronger(self.strength, self.v2.walk_strength):
                self.direction = Direction.FORWARD
            else:
                self.direction = Direction.BACKWARD
        if self.v2.mark == mark:
            if self.v1.mark != mark and stronger(self.strength, self.v1.walk_strength):
                self.direction = Direction.BACKWARD
            else:
                self.direction = Direction.NONE
        if weaker(self.v1.walk_strength, self.v2.walk_strength):
            if stronger(self.strength, self.v1.walk_strength):
                self.direction = Direction.BACKWARD
            else:
                self.direction = Direction.NONE
        elif stronger(self.strength, self.v2.walk_strength):
            self.direction = Direction.FORWARD
        else:
            self.direction = Direction.BACKWARD

    def add_to_graph(self) -> Any:
        self.v1.add_constraint(self)
        self.v2.add_constraint(self)
        self.direction = Direction.NONE

    def is_satisfied(self) -> Any:
        if self.direction != Direction.NONE:
            return True
        return False

    def mark_inputs(self, mark: Any) -> Any:
        self.input().mark = mark

    def input(self) -> Any:
        return self.v1 if self.direction == Direction.FORWARD else self.v2

    def output(self) -> Any:
        return self.v2 if self.direction == Direction.FORWARD else self.v1

    def recalculate(self) -> Any:
        ihn: Any = self.input()
        out: Any = self.output()
        out.walk_strength = weakest_of(self.strength, ihn.walk_strength)
        out.stay = ihn.stay
        if out.stay:
            self.execute()

    def mark_unsatisfied(self) -> None:
        self.direction = Direction.NONE

    def inputs_known(self, mark: Any) -> Any:
        i: Any = self.input()
        return i.mark == mark or i.stay or i.determined_by is None

    def remove_from_graph(self) -> Any:
        if self.v1 is not None:
            self.v1.remove_constraint(self)
        if self.v2 is not None:
            self.v2.remove_constraint(self)
        self.direction = Direction.NONE

@final
class ScaleConstraint(BinaryConstraint):

    def __init__(self, src: Any, scale: Any, offset: Any, dest: Any, strength: Any) -> None:
        self.direction: Any = Direction.NONE
        self.scale: Any = scale
        self.offset: Any = offset
        BinaryConstraint.__init__(self, src, dest, strength)

    def add_to_graph(self) -> Any:
        BinaryConstraint.add_to_graph(self)
        self.scale.add_constraint(self)
        self.offset.add_constraint(self)

    def remove_from_graph(self):
        BinaryConstraint.remove_from_graph(self)
        if self.scale is not None:
            self.scale.remove_constraint(self)
        if self.offset is not None:
            self.offset.remove_constraint(self)

    def mark_inputs(self, mark: Any) -> Any:
        BinaryConstraint.mark_inputs(self, mark)
        self.scale.mark = mark
        self.offset.mark = mark

    def execute(self) -> Any:
        if self.direction == Direction.FORWARD:
            self.v2.value = self.v1.value * self.scale.value + self.offset.value
        else:
            self.v1.value = (self.v2.value - self.offset.value) // self.scale.value

    def recalculate(self) -> Any:
        ihn: Any = self.input()
        out: Any = self.output()
        out.walk_strength = weakest_of(self.strength, ihn.walk_strength)
        out.stay = ihn.stay and self.scale.stay and self.offset.stay
        if out.stay:
            self.execute()

@final
class EqualityConstraint(BinaryConstraint):

    def execute(self) -> Any:
        self.output().value = self.input().value

@final
class Variable(object):

    def __init__(self, name: Any, initial_value: Any=0) -> None:
        self.name: Any = name
        self.value: Any = initial_value
        self.constraints: Any = CheckedList[Constraint]([])
        self.determined_by: Any = None
        self.mark: Any = 0
        self.walk_strength: Any = WEAKEST
        self.stay: Any = True

    def add_constraint(self, constraint: Any) -> Any:
        self.constraints.append(constraint)

    def remove_constraint(self, constraint: Any) -> Any:
        self.constraints.remove(constraint)
        if self.determined_by == constraint:
            self.determined_by = None

@final
class Planner(object):

    def __init__(self) -> None:
        self.current_mark: Any = 0

    def incremental_add(self, constraint: Any) -> Any:
        mark: Any = self.new_mark()
        overridden: Any = constraint.satisfy(mark)
        while overridden is not None:
            overridden = overridden.satisfy(mark)

    def incremental_remove(self, constraint: Any) -> Any:
        out: Any = constraint.output()
        constraint.mark_unsatisfied()
        constraint.remove_from_graph()
        unsatisfied = self.remove_propagate_from(out)
        strength = REQUIRED
        repeat = True
        while repeat:
            for u in unsatisfied:
                if u.strength == strength:
                    self.incremental_add(u)
                strength = strength.next_weaker()
            repeat = strength != WEAKEST

    def new_mark(self) -> Any:
        x: Any = self.current_mark + 1
        self.current_mark = x
        return self.current_mark

    def make_plan(self, sources: Any) -> Any:
        mark: Any = self.new_mark()
        plan: Any = Plan()
        todo: Any = CheckedList[Constraint]([cast(Constraint, s) for s in sources])
        while clen(todo):
            c: Any = todo.pop(0)
            if c.output().mark != mark and c.inputs_known(mark):
                plan.add_constraint(c)
                c.output().mark = mark
                self.add_constraints_consuming_to(c.output(), todo)
        return plan

    def extract_plan_from_constraints(self, constraints: Any) -> Any:
        sources: Any = CheckedList[UrnaryConstraint]([])
        for c in constraints:
            if c.is_input() and c.is_satisfied():
                sources.append(c)
        return self.make_plan(sources)

    def add_propagate(self, c: Any, mark: Any) -> Any:
        todo: Any = CheckedList[Constraint]([])
        todo.append(c)
        while clen(todo):
            d: Any = todo.pop(0)
            if d.output().mark == mark:
                self.incremental_remove(c)
                return False
            d.recalculate()
            self.add_constraints_consuming_to(d.output(), todo)
        return True

    def remove_propagate_from(self, out: Any) -> Any:
        out.determined_by = None
        out.walk_strength = WEAKEST
        out.stay = True
        unsatisfied: Any = CheckedList[Constraint]([])
        todo: Any = CheckedList[Variable]([])
        todo.append(out)
        while clen(todo):
            v: Any = todo.pop(0)
            cs = v.constraints
            for c in cs:
                if not c.is_satisfied():
                    unsatisfied.append(c)
            determining = v.determined_by
            for c in cs:
                if c != determining and c.is_satisfied():
                    c.recalculate()
                    todo.append(c.output())
        return unsatisfied

    def add_constraints_consuming_to(self, v: Any, coll: Any) -> Any:
        determining = v.determined_by
        cc = v.constraints
        for c in cc:
            if c != determining and c.is_satisfied():
                coll.append(c)

@final
class Plan(object):

    def __init__(self) -> None:
        self.v: Any = CheckedList[Constraint]([])

    def add_constraint(self, c: Any) -> Any:
        self.v.append(c)

    def __len__(self) -> Any:
        return len(self.v)

    def __getitem__(self, index: Any) -> Any:
        return self.v[index]

    def execute(self) -> Any:
        for c in self.v:
            c.execute()

def recreate_planner() -> Any:
    global planner
    planner = Planner()
    return planner

def get_planner() -> Any:
    global planner
    return planner

def chain_test(n: Any) -> Any:
    """
    This is the standard DeltaBlue benchmark. A long chain of equality
    constraints is constructed with a stay constraint on one end. An
    edit constraint is then added to the opposite end and the time is
    measured for adding and removing this constraint, and extracting
    and executing a constraint satisfaction plan. There are two cases.
    In case 1, the added constraint is stronger than the stay
    constraint and values must propagate down the entire length of the
    chain. In case 2, the added constraint is weaker than the stay
    constraint so it cannot be accomodated. The cost in this case is,
    of course, very low. Typical situations lie somewhere between these
    two extremes.
    """
    planner: Any = recreate_planner()
    prev: Any = None
    first: Any = None
    last: Any = None
    i: Any = 0
    end: Any = n + 1
    while i < n + 1:
        name = 'v%s' % i
        v: Any = Variable(name)
        if prev is not None:
            EqualityConstraint(prev, v, REQUIRED)
        if i == 0:
            first = v
        if i == n:
            last = v
        prev = v
        i = i + 1
    first = cast(Variable, first)
    last = cast(Variable, last)
    StayConstraint(last, STRONG_DEFAULT)
    edit: Any = EditConstraint(first, PREFERRED)
    edits: Any = CheckedList[UrnaryConstraint]([])
    edits.append(edit)
    plan: Any = planner.extract_plan_from_constraints(edits)
    i = 0
    while i < 100:
        first.value = i
        plan.execute()
        if last.value != i:
            print('Chain test failed.')
        i = i + 1

def projection_test(n: Any) -> Any:
    """
    This test constructs a two sets of variables related to each
    other by a simple linear transformation (scale and offset). The
    time is measured to change a variable on either side of the
    mapping and to change the scale and offset factors.
    """
    planner: Any = recreate_planner()
    scale: Any = Variable('scale', 10)
    offset: Any = Variable('offset', 1000)
    src: Any = None
    dests: Any = CheckedList[Variable]([])
    i: Any = 0
    dst = Variable('dst%s' % 0, 0)
    while i < n:
        bi: Any = i
        src = Variable('src%s' % bi, i)
        dst = Variable('dst%s' % bi, i)
        dests.append(dst)
        StayConstraint(src, NORMAL)
        ScaleConstraint(src, scale, offset, dst, REQUIRED)
        i = i + 1
    src = cast(Variable, src)
    change(src, 17)
    if dst.value != 1170:
        print('Projection 1 failed')
    change(dst, 1050)
    if src.value != 5:
        print('Projection 2 failed')
    change(scale, 5)
    i = 0
    while i < n - 1:
        if dests[i].value != i * 5 + 1000:
            print('Projection 3 failed')
        i = i + 1
    change(offset, 2000)
    i = 0
    while i < n - 1:
        if dests[i].value != i * 5 + 2000:
            print('Projection 4 failed')
        i = i + 1

def change(v: Any, new_value: Any) -> Any:
    planner: Any = get_planner()
    edit: Any = EditConstraint(v, PREFERRED)
    edits: Any = CheckedList[UrnaryConstraint]([])
    edits.append(edit)
    plan: Any = planner.extract_plan_from_constraints(edits)
    i: Any = 0
    while i < 10:
        v.value = new_value
        plan.execute()
        i = i + 1
    edit.destroy_constraint()
planner = None

def delta_blue(i: Any) -> Any:
    n: Any = i
    chain_test(n)
    projection_test(n)

def main() -> Any:
    n: Any = 10000
    startTime = time.time()
    delta_blue(n)
    endTime = time.time()
    runtime = endTime - startTime
    print(runtime)
if __name__ == '__main__':
    main()
