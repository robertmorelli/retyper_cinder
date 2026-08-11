# held_karp/advanced  granularity=benchmark
# mask=4735241  (6/26 units erased)

from __future__ import annotations
import __static__
from __static__ import CheckedList, box, int64
from typing import Tuple
import time
import random
import cinderx.jit
cinderx.jit.compile_after_n_calls(0)
INF_WEIGHT = 1e+18
REFERENCE_NODE_COUNT = 5
RANDOM_NODE_COUNT = 14

class Set64:

    def __init__(self, bits: int64) -> None:
        self.bits: int64 = bits

    @classmethod
    def from_city(cls, city: int64):
        return Set64(int64(1) << city)

    @classmethod
    def first_of_size(cls, size):
        return Set64(int64((1 << size) - 1 << 1))

    @classmethod
    def full_for_node_count(cls, node_count: int64):
        return Set64((int64(1) << node_count) - 2)

    def remove(self, other: Set64):
        return Set64(self.bits & ~other.bits)

    def next_same_size(self):
        value: int64 = self.bits
        low_bit: int64 = value & -value
        carry: int64 = value + low_bit
        return Set64(carry + ((carry ^ value) // low_bit >> 2))

    def fill_members(self, scratch: CheckedList[int]):
        count: int64 = 0
        remaining: int64 = self.bits
        while remaining != 0:
            low_bit: int64 = remaining & -remaining
            index: int64 = 0
            value: int64 = low_bit
            while value > 1:
                value >>= 1
                index += 1
            scratch[box(count)] = box(index)
            count += 1
            remaining &= remaining - 1
        return box(count)

class HeldKarpDP:

    def __init__(self, subset_count, node_count, fill_value) -> None:
        self.node_count = node_count
        total_slots = subset_count * node_count
        self.values = CheckedList[float]([fill_value] * total_slots)

    def offset(self, subset: Set64, city: int64):
        return box(subset.bits) * self.node_count + box(city)

    def get(self, subset: Set64, city: int64):
        return self.values[self.offset(subset, city)]

    def set(self, subset: Set64, city: int64, value: float):
        self.values[self.offset(subset, city)] = value

    def transition_cost(self, distances: DistanceMatrix, subset_without_city: Set64, members: CheckedList[int], member_count: int64, city: int64):
        best: float = INF_WEIGHT
        index: int64 = 0
        while index < member_count:
            previous_city: int64 = int64(members[box(index)])
            current: float = self.get(subset_without_city, previous_city) + distances.get(previous_city, city)
            if current < best:
                best = current
            index += 1
        return best

    def initialize_base_cases(self, distances: DistanceMatrix):
        start_city: int64 = 0
        city: int64 = 1
        while box(city) < self.node_count:
            subset: Set64 = Set64.from_city(city)
            self.set(subset, city, distances.get(start_city, city))
            city += 1

    def solve_subsets_of_size(self, distances, subset_size, scratch):
        subset_limit = 1 << self.node_count
        subset = Set64.first_of_size(subset_size)
        while subset.bits < subset_limit:
            member_count = subset.fill_members(scratch)
            index = 0
            while index < member_count:
                city = scratch[index]
                city_set = Set64.from_city(int64(city))
                subset_without_city = subset.remove(city_set)
                best = self.transition_cost(distances, subset_without_city, scratch, int64(member_count), int64(city))
                self.set(subset, int64(city), best)
                index += 1
            subset = subset.next_same_size()

    def close_tour(self, distances: DistanceMatrix):
        visited: Set64 = Set64.full_for_node_count(int64(self.node_count))
        best: float = INF_WEIGHT
        start_city: int64 = 0
        city: int64 = 1
        while box(city) < self.node_count:
            current: float = self.get(visited, city) + distances.get(city, start_city)
            if current < best:
                best = current
            city += 1
        return best

class DistanceMatrix:

    def __init__(self, node_count: int64) -> None:
        self.node_count: int64 = node_count
        self.values: CheckedList[float] = CheckedList[float]([0.0] * box(node_count * node_count))

    def offset(self, row: int64, column: int64):
        return box(row * self.node_count + column)

    def get(self, row: int64, column: int64):
        return self.values[self.offset(row, column)]

    def set(self, row, column, value):
        self.values[self.offset(int64(row), int64(column))] = value

    def fill_reference_values(self):
        values: Tuple[float, ...] = (0.0, 3.0, 4.0, 2.0, 7.0, 3.0, 0.0, 4.0, 6.0, 3.0, 4.0, 4.0, 0.0, 5.0, 8.0, 2.0, 6.0, 5.0, 0.0, 6.0, 7.0, 3.0, 8.0, 6.0, 0.0)
        row: int64 = 0
        while row < self.node_count:
            column: int64 = 0
            while column < self.node_count:
                value: float = values[self.offset(row, column)]
                if row == column:
                    self.set(box(row), box(column), INF_WEIGHT)
                else:
                    self.set(box(row), box(column), value)
                column += 1
            row += 1

def fill_random_values(matrix: DistanceMatrix):
    row: int64 = 0
    while row < matrix.node_count:
        column: int64 = 0
        while column < matrix.node_count:
            if row == column:
                matrix.set(box(row), box(column), INF_WEIGHT)
            else:
                random_dist: float = float(random.uniform(1.0, 100.0))
                matrix.set(box(row), box(column), random_dist)
            column += 1
        row += 1

def held_karp(node_count, distances):
    subset_capacity = 1 << node_count
    dp = HeldKarpDP(subset_capacity, node_count, INF_WEIGHT)
    scratch = CheckedList[int]([0] * node_count)
    dp.initialize_base_cases(distances)
    subset_size = 2
    while subset_size < node_count:
        dp.solve_subsets_of_size(distances, subset_size, scratch)
        subset_size += 1
    return dp.close_tour(distances)

def run_reference_case():
    node_count: int64 = int64(REFERENCE_NODE_COUNT)
    distances: DistanceMatrix = DistanceMatrix(node_count)
    distances.fill_reference_values()
    return held_karp(box(node_count), distances)

def run_random_benchmark(node_count: int64):
    distances: DistanceMatrix = DistanceMatrix(node_count)
    fill_random_values(distances)
    return held_karp(box(node_count), distances)

def main():
    reference_result: float = run_reference_case()
    assert int(reference_result) == 19
    random_node_count: int64 = int64(RANDOM_NODE_COUNT)
    start_time: float = time.time()
    random_result: float = run_random_benchmark(random_node_count)
    end_time: float = time.time()
    print(end_time - start_time)
if __name__ == '__main__':
    main()
