# held_karp/advanced  granularity=benchmark
# mask=24768  (4/18 units erased)

from __future__ import annotations
import __static__
from typing import Any
from __static__ import Array, box, inline, int64, cast
import random
import time
from typing import Tuple
import cinderx.jit
cinderx.jit.compile_after_n_calls(0)
INF_WEIGHT: int = 1152921504606846976
REFERENCE_NODE_COUNT: int = 5
RANDOM_NODE_COUNT: int = 14

@inline
def infinity() -> int64:
    return int64(INF_WEIGHT)

@inline
def set_from_city(city: int64) -> int64:
    return 1 << city

@inline
def first_set_of_size(size: int64) -> int64:
    return (1 << size) - 1 << 1

@inline
def full_set_for_node_count(node_count: int64) -> int64:
    return (1 << node_count) - 2

@inline
def remove_from_set(bits: int64, other: int64) -> int64:
    return bits & ~other

@inline
def ctz(low_bit: int64, bit_indexes: Array[int64]) -> int64:
    return bit_indexes[low_bit]

def next_set_of_same_size(bits: Any, bit_indexes: Any) -> int64:
    value: Any = bits >> 1
    low_bit: Any = value & -value
    carry: Any = value + low_bit
    shift: Any = bit_indexes[low_bit]
    return int64(carry | (carry ^ value) >> 2 >> shift) << 1

def fill_set_members(bits: Any, scratch: Any, bit_indexes: Any) -> int64:
    count: Any = 0
    remaining: Any = bits
    while remaining != 0:
        low_bit: Any = remaining & -remaining
        index: Any = box(ctz(int64(low_bit), cast(Array[int64], bit_indexes)))
        scratch[count] = index
        count += 1
        remaining &= remaining - 1
    return int64(count)

class DistanceMatrix:

    def __init__(self, node_count: int) -> None:
        self.node_count: int64 = int64(node_count)
        total_slots: int = node_count * node_count
        self.values: Array[int64] = Array[int64](total_slots)

    @inline
    def offset(self, row: int64, column: int64) -> int64:
        return row * self.node_count + column

    @inline
    def get(self, row: int64, column: int64) -> int64:
        return self.values[self.offset(row, column)]

    def set(self, row: int64, column: int64, value: int64):
        self.values[self.offset(row, column)] = value

def fill_reference_values(matrix: DistanceMatrix) -> None:
    values: Tuple[int, ...] = (0, 3, 4, 2, 7, 3, 0, 4, 6, 3, 4, 4, 0, 5, 8, 2, 6, 5, 0, 6, 7, 3, 8, 6, 0)
    row: int64 = 0
    while row < matrix.node_count:
        column: int64 = 0
        while column < matrix.node_count:
            offset: int64 = matrix.offset(row, column)
            value: int64 = int64(values[box(offset)])
            if row == column:
                matrix.set(row, column, infinity())
            else:
                matrix.set(row, column, value)
            column += 1
        row += 1

def fill_random_values(matrix: Any) -> None:
    row: Any = 0
    while row < matrix.node_count:
        column: Any = 0
        while column < matrix.node_count:
            if row == column:
                matrix.set(row, column, box(infinity()))
            else:
                matrix.set(row, column, random.randint(1, 100))
            column += 1
        row += 1

def held_karp(node_count: Any, distances: Any) -> int64:
    n: Any = node_count
    start_city: Any = 0
    subset_capacity: Any = 1 << node_count
    total_slots: Any = subset_capacity * node_count
    g: Any = Array[int64](total_slots)
    members: Any = Array[int64](node_count)
    bit_indexes: Any = Array[int64](subset_capacity)
    bit_index: Any = 0
    while bit_index < n:
        bit_indexes[1 << bit_index] = int64(bit_index)
        bit_index += 1
    slot: Any = 0
    machine_slots: Any = total_slots
    while slot < machine_slots:
        g[slot] = infinity()
        slot += 1
    city: Any = 1
    while city < n:
        singleton: Any = box(set_from_city(int64(city)))
        g[singleton * n + city] = int64(distances.get(start_city, city))
        city += 1
    subset_size: Any = 2
    subset_limit: Any = 1 << n
    while subset_size < n:
        subset: Any = box(first_set_of_size(int64(subset_size)))
        while subset < subset_limit:
            member_count: Any = box(fill_set_members(subset, members, bit_indexes))
            city_index: Any = 0
            while city_index < member_count:
                city = box(members[city_index])
                city_set: Any = box(set_from_city(int64(city)))
                subset_without_city: Any = box(remove_from_set(int64(subset), int64(city_set)))
                previous_offset: Any = subset_without_city * n
                best: Any = box(infinity())
                previous_index: Any = 0
                while previous_index < member_count:
                    previous_city: Any = box(members[previous_index])
                    if previous_city != city:
                        current: Any = box(g[previous_offset + previous_city]) + distances.get(previous_city, city)
                        if current < best:
                            best = current
                    previous_index += 1
                g[subset * n + city] = int64(best)
                city_index += 1
            subset = box(next_set_of_same_size(subset, bit_indexes))
        subset_size += 1
    full: Any = box(full_set_for_node_count(int64(n)))
    best = box(infinity())
    city = 1
    while city < n:
        current = box(g[full * n + city]) + distances.get(city, start_city)
        if current < best:
            best = current
        city += 1
    return int64(best)

def run_reference_case() -> int64:
    node_count: int = REFERENCE_NODE_COUNT
    distances: DistanceMatrix = DistanceMatrix(node_count)
    fill_reference_values(distances)
    return held_karp(node_count, distances)

def run_random_benchmark(node_count: int) -> int64:
    distances: DistanceMatrix = DistanceMatrix(node_count)
    fill_random_values(distances)
    return held_karp(node_count, distances)

def main() -> None:
    reference_result: int64 = run_reference_case()
    assert reference_result == 19
    random_node_count: int = RANDOM_NODE_COUNT
    start_time: float = time.time()
    random_result: int64 = run_random_benchmark(random_node_count)
    end_time: float = time.time()
    print(end_time - start_time)
if __name__ == '__main__':
    main()
