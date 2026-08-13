# Bug B, the visible-error variant. Same inliner bug as b_inlined_global_store.py,
# but it raises instead of segfaulting:
#
#   File "...", line 27, in drive
#     recreate_planner()
#   AttributeError: 'int' object has no attribute 'get'
#
# Nothing in this program calls .get on anything. That is the global-store path
# inside the inlined recreate_planner() being handed the caller's local `i` --
# an int -- where the module's globals should be.
import __static__


class Planner:
    def __init__(self) -> None:
        self.count: int = 0

    def add(self, k: int) -> None:
        self.count = self.count + k


def recreate_planner() -> Planner:
    global planner
    planner = Planner()
    return planner


def get_planner() -> Planner:
    global planner
    return planner


def use() -> None:
    p: Planner = get_planner()
    p.add(1)


def drive(n: int) -> None:
    i: int = 0
    while i < n:
        recreate_planner()
        use()
        i = i + 1


planner = None
drive(5)
print('done', get_planner().count)
