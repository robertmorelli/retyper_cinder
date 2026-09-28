import __static__
from __static__ import CheckedList

class Fish:
    pass

class BlueFish(Fish):
    pass

def foo(a: CheckedList[BlueFish]) -> CheckedList[Fish]:
    return [e for e in a]