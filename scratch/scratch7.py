import __static__
from __static__ import int64, inline

class Point:
    def __init__(self, x: int64, y: int64):
        self.x: int64 = x
        self.y: int64 = y

class Square:
    def __init__(self, topleft: Point, bottomright: Point):
        self.topleft: Point = topleft
        self.bottomright: Point = bottomright

    @inline
    def top(self) -> int64:
        return self.topleft.y


# Inline return narrowing choice for `pick`.
# Units: 0 `def pick` return, 1 `d: Dog` (pick), 2 `def use` return, 3 `d: Dog` (use), 4 tmp
#   mask 0b00000: `d` stays Dog, narrows `-> Animal`  => d.type -> pick.type (expr feeds annotation)
#   mask 0b00010: `d` erased to dynamic, `-> Animal` kept => pick.type -> d.type_constraint (annotation feeds expr)
class Animal:
    pass

class Dog(Animal):
    pass

@inline
def pick(d: Dog) -> Animal:
    return d

def use(d: Dog) -> Animal:
    return pick(d)
