# held_karp/advanced  granularity=benchmark
# mask=67108863  (26/26 units erased)

from __future__ import annotations
import __static__
from typing import Any
from __static__ import CheckedList
import time
import random
import cinderx.jit
cinderx.jit.compile_after_n_calls(0)
INF_WEIGHT: Any = 1e+18
REFERENCE_NODE_COUNT: Any = 5
RANDOM_NODE_COUNT: Any = 14

class Set64:

    def __init__(self, bits: Any) -> None:
        self.bits: Any = bits

    @classmethod
    def from_city(cls, city: Any) -> Any:
        return Set64(1 << city)

    @classmethod
    def first_of_size(cls, size: Any) -> Any:
        return Set64((1 << size) - 1 << 1)

    @classmethod
    def full_for_node_count(cls, node_count: Any) -> Any:
        return Set64((1 << node_count) - 2)

    def remove(self, other: Any) -> Any:
        return Set64(self.bits & ~other.bits)

    def next_same_size(self) -> Any:
        value: Any = self.bits
        low_bit: Any = value & -value
        carry: Any = value + low_bit
        return Set64(carry + ((carry ^ value) // low_bit >> 2))

    def fill_members(self, scratch: Any) -> Any:
        count: Any = 0
        remaining: Any = self.bits
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
        return count

class HeldKarpDP:

    def __init__(self, subset_count: Any, node_count: Any, fill_value: Any) -> None:
        self.node_count: Any = node_count
        total_slots: Any = subset_count * node_count
        self.values: Any = CheckedList[float]([fill_value] * total_slots)

    def offset(self, subset: Any, city: Any) -> Any:
        return subset.bits * self.node_count + city

    def get(self, subset: Any, city: Any) -> Any:
        return self.values[self.offset(subset, city)]

    def set(self, subset: Any, city: Any, value: Any) -> Any:
        self.values[self.offset(subset, city)] = value

    def transition_cost(self, distances: Any, subset_without_city: Any, members: Any, member_count: Any, city: Any) -> Any:
        best: Any = INF_WEIGHT
        index: Any = 0
        while index < member_count:
            previous_city: Any = members[index]
            current: Any = self.get(subset_without_city, previous_city) + distances.get(previous_city, city)
            if current < best:
                best = current
            index += 1
        return best

    def initialize_base_cases(self, distances: Any) -> Any:
        start_city: Any = 0
        city: Any = 1
        while city < self.node_count:
            subset: Any = Set64.from_city(city)
            self.set(subset, city, distances.get(start_city, city))
            city += 1

    def solve_subsets_of_size(self, distances: Any, subset_size: Any, scratch: Any) -> Any:
        subset_limit: Any = 1 << self.node_count
        subset: Any = Set64.first_of_size(subset_size)
        while subset.bits < subset_limit:
            member_count: Any = subset.fill_members(scratch)
            index: Any = 0
            while index < member_count:
                city: Any = scratch[index]
                city_set: Any = Set64.from_city(city)
                subset_without_city: Any = subset.remove(city_set)
                best: Any = self.transition_cost(distances, subset_without_city, scratch, member_count, city)
                self.set(subset, city, best)
                index += 1
            subset = subset.next_same_size()

    def close_tour(self, distances: Any) -> Any:
        visited: Any = Set64.full_for_node_count(self.node_count)
        best: Any = INF_WEIGHT
        start_city: Any = 0
        city: Any = 1
        while city < self.node_count:
            current: Any = self.get(visited, city) + distances.get(city, start_city)
            if current < best:
                best = current
            city += 1
        return best

class DistanceMatrix:

    def __init__(self, node_count: Any) -> None:
        self.node_count: Any = node_count
        self.values: Any = CheckedList[float]([0.0] * (node_count * node_count))

    def offset(self, row: Any, column: Any) -> Any:
        return row * self.node_count + column

    def get(self, row: Any, column: Any) -> Any:
        return self.values[self.offset(row, column)]

    def set(self, row: Any, column: Any, value: Any) -> Any:
        self.values[self.offset(row, column)] = value

    def fill_reference_values(self) -> Any:
        values: Any = (0.0, 3.0, 4.0, 2.0, 7.0, 3.0, 0.0, 4.0, 6.0, 3.0, 4.0, 4.0, 0.0, 5.0, 8.0, 2.0, 6.0, 5.0, 0.0, 6.0, 7.0, 3.0, 8.0, 6.0, 0.0)
        row: Any = 0
        while row < self.node_count:
            column: Any = 0
            while column < self.node_count:
                value: Any = values[self.offset(row, column)]
                if row == column:
                    self.set(row, column, INF_WEIGHT)
                else:
                    self.set(row, column, value)
                column += 1
            row += 1

def fill_random_values(matrix: Any) -> Any:
    row: Any = 0
    while row < matrix.node_count:
        column: Any = 0
        while column < matrix.node_count:
            if row == column:
                matrix.set(row, column, INF_WEIGHT)
            else:
                random_dist: Any = float(random.uniform(1.0, 100.0))
                matrix.set(row, column, random_dist)
            column += 1
        row += 1

def held_karp(node_count: Any, distances: Any) -> Any:
    subset_capacity: Any = 1 << node_count
    dp: Any = HeldKarpDP(subset_capacity, node_count, INF_WEIGHT)
    scratch: Any = CheckedList[int]([0] * node_count)
    dp.initialize_base_cases(distances)
    subset_size: Any = 2
    while subset_size < node_count:
        dp.solve_subsets_of_size(distances, subset_size, scratch)
        subset_size += 1
    return dp.close_tour(distances)

def run_reference_case() -> Any:
    node_count: Any = REFERENCE_NODE_COUNT
    distances: Any = DistanceMatrix(node_count)
    distances.fill_reference_values()
    return held_karp(node_count, distances)

def run_random_benchmark(node_count: Any) -> Any:
    distances: Any = DistanceMatrix(node_count)
    fill_random_values(distances)
    return held_karp(node_count, distances)

def main() -> Any:
    reference_result: Any = run_reference_case()
    assert int(reference_result) == 19
    random_node_count: Any = RANDOM_NODE_COUNT
    start_time: Any = time.time()
    random_result: Any = run_random_benchmark(random_node_count)
    end_time: Any = time.time()
    print(end_time - start_time)
if __name__ == '__main__':
    main()
