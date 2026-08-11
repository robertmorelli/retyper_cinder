# held_karp/advanced  granularity=benchmark
# mask=67108863  (26/26 units erased)

from __future__ import annotations
import __static__
from __static__ import CheckedList
import time
import random
import cinderx.jit
cinderx.jit.compile_after_n_calls(0)
INF_WEIGHT = 1e+18
REFERENCE_NODE_COUNT = 5
RANDOM_NODE_COUNT = 14

class Set64:

    def __init__(self, bits) -> None:
        self.bits = bits

    @classmethod
    def from_city(cls, city):
        return Set64(1 << city)

    @classmethod
    def first_of_size(cls, size):
        return Set64((1 << size) - 1 << 1)

    @classmethod
    def full_for_node_count(cls, node_count):
        return Set64((1 << node_count) - 2)

    def remove(self, other):
        return Set64(self.bits & ~other.bits)

    def next_same_size(self):
        value = self.bits
        low_bit = value & -value
        carry = value + low_bit
        return Set64(carry + ((carry ^ value) // low_bit >> 2))

    def fill_members(self, scratch):
        count = 0
        remaining = self.bits
        while remaining != 0:
            low_bit = remaining & -remaining
            index = 0
            value = low_bit
            while value > 1:
                value >>= 1
                index += 1
            scratch[count] = index
            count += 1
            remaining &= remaining - 1
        return count

class HeldKarpDP:

    def __init__(self, subset_count, node_count, fill_value) -> None:
        self.node_count = node_count
        total_slots = subset_count * node_count
        self.values = CheckedList[float]([fill_value] * total_slots)

    def offset(self, subset, city):
        return subset.bits * self.node_count + city

    def get(self, subset, city):
        return self.values[self.offset(subset, city)]

    def set(self, subset, city, value):
        self.values[self.offset(subset, city)] = value

    def transition_cost(self, distances, subset_without_city, members, member_count, city):
        best = INF_WEIGHT
        index = 0
        while index < member_count:
            previous_city = members[index]
            current = self.get(subset_without_city, previous_city) + distances.get(previous_city, city)
            if current < best:
                best = current
            index += 1
        return best

    def initialize_base_cases(self, distances):
        start_city = 0
        city = 1
        while city < self.node_count:
            subset = Set64.from_city(city)
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
                city_set = Set64.from_city(city)
                subset_without_city = subset.remove(city_set)
                best = self.transition_cost(distances, subset_without_city, scratch, member_count, city)
                self.set(subset, city, best)
                index += 1
            subset = subset.next_same_size()

    def close_tour(self, distances):
        visited = Set64.full_for_node_count(self.node_count)
        best = INF_WEIGHT
        start_city = 0
        city = 1
        while city < self.node_count:
            current = self.get(visited, city) + distances.get(city, start_city)
            if current < best:
                best = current
            city += 1
        return best

class DistanceMatrix:

    def __init__(self, node_count) -> None:
        self.node_count = node_count
        self.values = CheckedList[float]([0.0] * (node_count * node_count))

    def offset(self, row, column):
        return row * self.node_count + column

    def get(self, row, column):
        return self.values[self.offset(row, column)]

    def set(self, row, column, value):
        self.values[self.offset(row, column)] = value

    def fill_reference_values(self):
        values = (0.0, 3.0, 4.0, 2.0, 7.0, 3.0, 0.0, 4.0, 6.0, 3.0, 4.0, 4.0, 0.0, 5.0, 8.0, 2.0, 6.0, 5.0, 0.0, 6.0, 7.0, 3.0, 8.0, 6.0, 0.0)
        row = 0
        while row < self.node_count:
            column = 0
            while column < self.node_count:
                value = values[self.offset(row, column)]
                if row == column:
                    self.set(row, column, INF_WEIGHT)
                else:
                    self.set(row, column, value)
                column += 1
            row += 1

def fill_random_values(matrix):
    row = 0
    while row < matrix.node_count:
        column = 0
        while column < matrix.node_count:
            if row == column:
                matrix.set(row, column, INF_WEIGHT)
            else:
                random_dist = float(random.uniform(1.0, 100.0))
                matrix.set(row, column, random_dist)
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
    node_count = REFERENCE_NODE_COUNT
    distances = DistanceMatrix(node_count)
    distances.fill_reference_values()
    return held_karp(node_count, distances)

def run_random_benchmark(node_count):
    distances = DistanceMatrix(node_count)
    fill_random_values(distances)
    return held_karp(node_count, distances)

def main():
    reference_result = run_reference_case()
    assert int(reference_result) == 19
    random_node_count = RANDOM_NODE_COUNT
    start_time = time.time()
    random_result = run_random_benchmark(random_node_count)
    end_time = time.time()
    print(end_time - start_time)
if __name__ == '__main__':
    main()
