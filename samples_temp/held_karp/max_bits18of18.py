# held_karp/advanced  granularity=benchmark
# mask=262143  (18/18 units erased)

from __future__ import annotations
import __static__
from typing import Any
from __static__ import Array, box, inline, int64
import random
import time
import cinderx.jit
cinderx.jit.compile_after_n_calls(0)
INF_WEIGHT: Any = 1152921504606846976
REFERENCE_NODE_COUNT: Any = 5
RANDOM_NODE_COUNT: Any = 14

@inline
def infinity() -> Any:
    return INF_WEIGHT

@inline
def set_from_city(city: Any) -> Any:
    return 1 << city

@inline
def first_set_of_size(size: Any) -> Any:
    return (1 << size) - 1 << 1

@inline
def full_set_for_node_count(node_count: Any) -> Any:
    return (1 << node_count) - 2

@inline
def remove_from_set(bits: Any, other: Any) -> Any:
    return bits & ~other

@inline
def ctz(low_bit: Any, bit_indexes: Any) -> Any:
    return bit_indexes[low_bit]

def next_set_of_same_size(bits: Any, bit_indexes: Any) -> Any:
    value: Any = bits >> 1
    low_bit: Any = value & -value
    carry: Any = value + low_bit
    shift: Any = bit_indexes[low_bit]
    return (carry | (carry ^ value) >> 2 >> shift) << 1

def fill_set_members(bits: Any, scratch: Any, bit_indexes: Any) -> Any:
    count: Any = 0
    remaining: Any = bits
    while int64(remaining) != 0:
        low_bit: Any = remaining & -remaining
        index: Any = ctz(low_bit, bit_indexes)
        scratch[count] = index
        count += 1
        remaining &= remaining - 1
    return count

class DistanceMatrix:

    def __init__(self, node_count: Any) -> None:
        self.node_count: Any = node_count
        total_slots: Any = node_count * node_count
        self.values: Any = Array[int64](total_slots)

    @inline
    def offset(self, row: Any, column: Any) -> Any:
        return row * self.node_count + column

    @inline
    def get(self, row: Any, column: Any) -> Any:
        return self.values[self.offset(row, column)]

    def set(self, row: Any, column: Any, value: Any) -> Any:
        self.values[self.offset(row, column)] = value

def fill_reference_values(matrix: Any) -> Any:
    values: Any = (0, 3, 4, 2, 7, 3, 0, 4, 6, 3, 4, 4, 0, 5, 8, 2, 6, 5, 0, 6, 7, 3, 8, 6, 0)
    row: Any = 0
    while int64(row) < int64(matrix.node_count):
        column: Any = 0
        while int64(column) < int64(matrix.node_count):
            offset: Any = matrix.offset(row, column)
            value: Any = values[offset]
            if int64(row) == int64(column):
                matrix.set(row, column, infinity())
            else:
                matrix.set(row, column, value)
            column += 1
        row += 1

def fill_random_values(matrix: Any) -> Any:
    row: Any = 0
    while int64(row) < int64(matrix.node_count):
        column: Any = 0
        while int64(column) < int64(matrix.node_count):
            if int64(row) == int64(column):
                matrix.set(row, column, infinity())
            else:
                matrix.set(row, column, random.randint(1, 100))
            column += 1
        row += 1

def held_karp(node_count: Any, distances: Any) -> Any:
    n: Any = node_count
    start_city: Any = 0
    subset_capacity: Any = 1 << node_count
    total_slots: Any = subset_capacity * node_count
    g: Any = Array[int64](total_slots)
    members: Any = Array[int64](node_count)
    bit_indexes: Any = Array[int64](subset_capacity)
    bit_index: Any = 0
    while int64(bit_index) < int64(n):
        bit_indexes[1 << int64(bit_index)] = int64(bit_index)
        bit_index += 1
    slot: Any = 0
    machine_slots: Any = total_slots
    while int64(slot) < int64(machine_slots):
        g[slot] = int64(infinity())
        slot += 1
    city: Any = 1
    while int64(city) < int64(n):
        singleton: Any = set_from_city(city)
        g[int64(singleton) * int64(n) + int64(city)] = int64(distances.get(start_city, city))
        city += 1
    subset_size: Any = 2
    subset_limit: Any = 1 << n
    while int64(subset_size) < int64(n):
        subset: Any = first_set_of_size(subset_size)
        while int64(subset) < int64(subset_limit):
            member_count: Any = fill_set_members(subset, members, bit_indexes)
            city_index: Any = 0
            while int64(city_index) < int64(member_count):
                city = box(members[city_index])
                city_set: Any = set_from_city(city)
                subset_without_city: Any = remove_from_set(subset, city_set)
                previous_offset: Any = subset_without_city * n
                best: Any = infinity()
                previous_index: Any = 0
                while int64(previous_index) < int64(member_count):
                    previous_city: Any = box(members[previous_index])
                    if int64(previous_city) != int64(city):
                        current: Any = box(g[int64(previous_offset) + int64(previous_city)]) + distances.get(previous_city, city)
                        if int64(current) < int64(best):
                            best = current
                    previous_index += 1
                g[int64(subset) * int64(n) + int64(city)] = int64(best)
                city_index += 1
            subset = next_set_of_same_size(subset, bit_indexes)
        subset_size += 1
    full: Any = full_set_for_node_count(n)
    best = infinity()
    city = 1
    while int64(city) < int64(n):
        current = box(g[int64(full) * int64(n) + int64(city)]) + distances.get(city, start_city)
        if int64(current) < int64(best):
            best = current
        city += 1
    return best

def run_reference_case() -> Any:
    node_count: Any = REFERENCE_NODE_COUNT
    distances: Any = DistanceMatrix(node_count)
    fill_reference_values(distances)
    return held_karp(node_count, distances)

def run_random_benchmark(node_count: Any) -> Any:
    distances: Any = DistanceMatrix(node_count)
    fill_random_values(distances)
    return held_karp(node_count, distances)

def main() -> Any:
    reference_result: Any = run_reference_case()
    assert int64(reference_result) == 19
    random_node_count: Any = RANDOM_NODE_COUNT
    start_time: Any = time.time()
    random_result: Any = run_random_benchmark(random_node_count)
    end_time: Any = time.time()
    print(end_time - start_time)
if __name__ == '__main__':
    main()
