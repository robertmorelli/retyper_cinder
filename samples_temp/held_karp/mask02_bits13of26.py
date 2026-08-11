# held_karp/advanced  granularity=benchmark
# mask=21613528  (13/26 units erased)

from __future__ import annotations
import __static__
from typing import Any
from __static__ import CheckedList, box, int64
from typing import Tuple
import time
import random
import cinderx.jit
cinderx.jit.compile_after_n_calls(0)
INF_WEIGHT: float = 1e+18
REFERENCE_NODE_COUNT: int = 5
RANDOM_NODE_COUNT: int = 14

class Set64:

    def __init__(self, bits: int64) -> None:
        self.bits: int64 = bits

    @classmethod
    def from_city(cls, city: int64) -> Set64:
        return Set64(int64(1) << city)

    @classmethod
    def first_of_size(cls, size: Any) -> Set64:
        return Set64(int64((1 << size) - 1 << 1))

    @classmethod
    def full_for_node_count(cls, node_count: Any) -> Set64:
        return Set64(int64((1 << node_count) - 2))

    def remove(self, other: Set64) -> Set64:
        return Set64(self.bits & ~other.bits)

    def next_same_size(self) -> Set64:
        value: Any = box(self.bits)
        low_bit: Any = value & -value
        carry: Any = value + low_bit
        return Set64(int64(carry + ((carry ^ value) // low_bit >> 2)))

    def fill_members(self, scratch: Any) -> int64:
        count: Any = 0
        remaining: Any = box(self.bits)
        while remaining != 0:
            low_bit: Any = remaining & -remaining
            index: Any = 0
            value: Any = low_bit
            while value > 1:
                value >>= 1
                index += 1
            scratch[count] = index
            count += 1
            remaining &= remaining - 1
        return int64(count)

class HeldKarpDP:

    def __init__(self, subset_count: Any, node_count: Any, fill_value: Any) -> None:
        self.node_count: Any = node_count
        total_slots: Any = subset_count * node_count
        self.values: Any = CheckedList[float]([fill_value] * total_slots)

    def offset(self, subset: Any, city: Any) -> int64:
        return int64(subset.bits * self.node_count + city)

    def get(self, subset: Set64, city: int64) -> float:
        return self.values[box(self.offset(subset, box(city)))]

    def set(self, subset: Any, city: Any, value: Any) -> None:
        self.values[box(self.offset(subset, city))] = value

    def transition_cost(self, distances: DistanceMatrix, subset_without_city: Set64, members: CheckedList[int], member_count: int64, city: int64) -> float:
        best: float = INF_WEIGHT
        index: int64 = 0
        while index < member_count:
            previous_city: int64 = int64(members[box(index)])
            current: float = self.get(subset_without_city, previous_city) + distances.get(previous_city, city)
            if current < best:
                best = current
            index += 1
        return best

    def initialize_base_cases(self, distances: DistanceMatrix) -> None:
        start_city: int64 = 0
        city: int64 = 1
        while box(city) < self.node_count:
            subset: Set64 = Set64.from_city(city)
            self.set(subset, box(city), distances.get(start_city, city))
            city += 1

    def solve_subsets_of_size(self, distances: Any, subset_size: Any, scratch: Any) -> None:
        subset_limit: Any = 1 << self.node_count
        subset: Any = Set64.first_of_size(subset_size)
        while box(subset.bits) < subset_limit:
            member_count: Any = box(subset.fill_members(scratch))
            index: Any = 0
            while index < member_count:
                city: Any = scratch[index]
                city_set: Any = Set64.from_city(int64(city))
                subset_without_city: Any = subset.remove(city_set)
                best: Any = self.transition_cost(distances, subset_without_city, scratch, int64(member_count), int64(city))
                self.set(subset, city, best)
                index += 1
            subset = subset.next_same_size()

    def close_tour(self, distances: Any) -> float:
        visited: Any = Set64.full_for_node_count(self.node_count)
        best: Any = INF_WEIGHT
        start_city: Any = 0
        city: Any = 1
        while city < self.node_count:
            current: Any = self.get(visited, int64(city)) + distances.get(city, start_city)
            if current < best:
                best = current
            city += 1
        return best

class DistanceMatrix:

    def __init__(self, node_count: Any) -> None:
        self.node_count: Any = node_count
        self.values: Any = CheckedList[float]([0.0] * (node_count * node_count))

    def offset(self, row: int64, column: int64) -> int64:
        return int64(box(row) * self.node_count + box(column))

    def get(self, row: int64, column: int64) -> float:
        return self.values[box(self.offset(row, column))]

    def set(self, row: Any, column: Any, value: Any) -> None:
        self.values[box(self.offset(int64(row), int64(column)))] = value

    def fill_reference_values(self) -> None:
        values: Tuple[float, ...] = (0.0, 3.0, 4.0, 2.0, 7.0, 3.0, 0.0, 4.0, 6.0, 3.0, 4.0, 4.0, 0.0, 5.0, 8.0, 2.0, 6.0, 5.0, 0.0, 6.0, 7.0, 3.0, 8.0, 6.0, 0.0)
        row: int64 = 0
        while box(row) < self.node_count:
            column: int64 = 0
            while box(column) < self.node_count:
                value: float = values[box(self.offset(row, column))]
                if row == column:
                    self.set(box(row), box(column), INF_WEIGHT)
                else:
                    self.set(box(row), box(column), value)
                column += 1
            row += 1

def fill_random_values(matrix: DistanceMatrix) -> None:
    row: int64 = 0
    while box(row) < matrix.node_count:
        column: int64 = 0
        while box(column) < matrix.node_count:
            if row == column:
                matrix.set(box(row), box(column), INF_WEIGHT)
            else:
                random_dist: float = float(random.uniform(1.0, 100.0))
                matrix.set(box(row), box(column), random_dist)
            column += 1
        row += 1

def held_karp(node_count: Any, distances: Any) -> float:
    subset_capacity: Any = 1 << node_count
    dp: Any = HeldKarpDP(subset_capacity, node_count, INF_WEIGHT)
    scratch: Any = CheckedList[int]([0] * node_count)
    dp.initialize_base_cases(distances)
    subset_size: Any = 2
    while subset_size < node_count:
        dp.solve_subsets_of_size(distances, subset_size, scratch)
        subset_size += 1
    return dp.close_tour(distances)

def run_reference_case() -> float:
    node_count: int64 = int64(REFERENCE_NODE_COUNT)
    distances: DistanceMatrix = DistanceMatrix(box(node_count))
    distances.fill_reference_values()
    return held_karp(box(node_count), distances)

def run_random_benchmark(node_count: Any) -> float:
    distances: Any = DistanceMatrix(node_count)
    fill_random_values(distances)
    return held_karp(node_count, distances)

def main() -> None:
    reference_result: float = run_reference_case()
    assert int(reference_result) == 19
    random_node_count: int64 = int64(RANDOM_NODE_COUNT)
    start_time: float = time.time()
    random_result: float = run_random_benchmark(box(random_node_count))
    end_time: float = time.time()
    print(end_time - start_time)
if __name__ == '__main__':
    main()
