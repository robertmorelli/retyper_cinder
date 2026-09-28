import __static__
from typing import Optional


class Fish:
    pass


def get_fish() -> Fish:
    return Fish()


def main() -> None:
    x: Optional[Fish] = get_fish()
    y: Fish = x
